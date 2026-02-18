from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import httpx
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from inkfeed.archiver.base import ArchiveResult, Article, BaseArchiver, GroupResult
from inkfeed.config import SourceConfig
from inkfeed.templates import get_template
from inkfeed.utils.retry import with_retry

logger = logging.getLogger(__name__)

KAGI_API = "https://news.kagi.com"


class KagiNewsArchiver(BaseArchiver):
    def __init__(self, config: SourceConfig, output_dir: Path) -> None:
        super().__init__(config, output_dir)
        self.categories: list[str] = config.params.get("categories", [])
        self.language: str = config.params.get("language", "en")
        self.max_stories: int = config.params.get("max_stories_per_category", 50)

    # ------------------------------------------------------------------
    # fetch / process fulfil the BaseArchiver interface
    # ------------------------------------------------------------------

    def fetch(
        self,
        *,
        client: httpx.Client | None = None,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> list[dict]:
        """Fetch stories from Kagi News for every configured category.

        Returns a list of dicts, one per category::

            {"category_slug": str, "category_name": str, "stories": [...]}}
        """
        own_client = client is None
        if own_client:
            client = httpx.Client(timeout=30, follow_redirects=True)

        try:
            batch_id = self._latest_batch_id(client, max_retries=max_retries)
            category_map = self._fetch_category_map(
                client, batch_id, max_retries=max_retries,
            )

            # Map of index -> result dict, for preserving config order.
            indexed_results: dict[int, dict] = {}

            with Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[dim]{task.fields[status]}"),
                TimeElapsedColumn(),
                transient=False,
            ) as progress:
                task = progress.add_task(
                    "kaginews categories",
                    total=len(self.categories),
                    status="",
                )

                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {}
                    for idx, slug in enumerate(self.categories):
                        cat_uuid = category_map.get(slug)
                        if cat_uuid is None:
                            progress.update(task, status=f"[yellow]skip {slug}")
                            progress.advance(task)
                            continue
                        futures[pool.submit(
                            self._fetch_one_category,
                            client, batch_id, slug, cat_uuid,
                            category_map, max_retries,
                        )] = (idx, slug)

                    for future in as_completed(futures):
                        idx, slug = futures[future]
                        result = future.result()
                        if result is not None:
                            progress.update(task, status=slug)
                            indexed_results[idx] = result
                        else:
                            progress.update(task, status=f"[red]failed {slug}")
                        progress.advance(task)

            # Return results in the original config order.
            return [indexed_results[i] for i in sorted(indexed_results)]
        finally:
            if own_client:
                client.close()

    def _fetch_one_category(
        self,
        client: httpx.Client,
        batch_id: str,
        slug: str,
        cat_uuid: str,
        category_map: dict[str, str],
        max_retries: int,
    ) -> dict | None:
        """Fetch stories for a single category. Returns result dict or None."""
        try:
            stories = self._fetch_stories(
                client, batch_id, cat_uuid, max_retries=max_retries,
            )
            cat_name = category_map.get(
                f"{slug}__name", slug.replace("_", " ").title(),
            )
            return {
                "category_slug": slug,
                "category_name": cat_name,
                "stories": stories,
            }
        except Exception as exc:
            logger.debug("Failed to fetch category %s: %s", slug, exc)
            return None

    def process(self, raw_items: list[dict]) -> list[Article]:
        """Convert raw Kagi story dicts into Article objects.

        ``raw_items`` is a flat list of story dicts (all from a single
        category).  The caller is responsible for splitting by category
        before calling this.
        """
        articles: list[Article] = []
        now = datetime.now(timezone.utc)
        tpl = get_template("kagi_story.html")

        for story in raw_items:
            source_articles = story.get("articles") or []
            cmap = _build_citation_map(source_articles)
            content_html = tpl.render(story=story, cmap=cmap, cite=_cite)

            first_link = source_articles[0]["link"] if source_articles else ""
            publish_date = _earliest_article_date(source_articles)

            articles.append(Article(
                title=story.get("title", "Untitled"),
                author="Kagi News",
                source_url=first_link,
                content_html=content_html,
                snapshot_date=now,
                publish_date=publish_date,
                metadata={
                    "cluster_id": story.get("id", ""),
                    "category": story.get("category", ""),
                    "emoji": story.get("emoji", ""),
                    "unique_domains": story.get("unique_domains", 0),
                },
            ))

        return articles

    # ------------------------------------------------------------------
    # run() override – produces per-category output directories
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> ArchiveResult:
        """Execute the archive pipeline for every configured category.

        Returns an :class:`ArchiveResult` with one :class:`GroupResult`
        per category that had stories.
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        raw_by_category = self.fetch(
            max_workers=max_workers, max_retries=max_retries,
        )

        groups: list[GroupResult] = []

        for cat_data in raw_by_category:
            slug = cat_data["category_slug"]
            cat_name = cat_data["category_name"]
            stories = cat_data["stories"]

            if not stories:
                continue

            cache_dir = self.output_dir / ".cache" / self.config.name / date_str / slug
            cache_dir.mkdir(parents=True, exist_ok=True)

            articles = self.process(stories)
            groups.append(GroupResult(
                display_name=cat_name,
                rel_path=slug,
                cache_dir=cache_dir,
                articles=articles,
            ))

        return ArchiveResult(
            source_name=self.config.name,
            source_display_name=self.config.display_name,
            groups=groups,
        )

    # ------------------------------------------------------------------
    # Internal API helpers
    # ------------------------------------------------------------------

    def _latest_batch_id(
        self, client: httpx.Client, *, max_retries: int = 3,
    ) -> str:
        def _get() -> str:
            resp = client.get(f"{KAGI_API}/api/batches", params={"lang": self.language})
            resp.raise_for_status()
            batches = resp.json().get("batches", [])
            if not batches:
                raise RuntimeError("No batches available from Kagi News API")
            return batches[0]["id"]

        return with_retry(_get, max_retries=max_retries)

    def _fetch_category_map(
        self, client: httpx.Client, batch_id: str, *, max_retries: int = 3,
    ) -> dict[str, str]:
        """Return a dict mapping ``categoryId`` slug -> UUID.

        Also stores the human-readable name under ``{slug}__name``.
        """
        def _get() -> dict[str, str]:
            resp = client.get(
                f"{KAGI_API}/api/batches/{batch_id}/categories",
                params={"lang": self.language},
            )
            resp.raise_for_status()

            mapping: dict[str, str] = {}
            for cat in resp.json().get("categories", []):
                slug = cat["categoryId"]
                mapping[slug] = cat["id"]
                mapping[f"{slug}__name"] = cat.get("categoryName", slug)
            return mapping

        return with_retry(_get, max_retries=max_retries)

    def _fetch_stories(
        self,
        client: httpx.Client,
        batch_id: str,
        category_uuid: str,
        *,
        max_retries: int = 3,
    ) -> list[dict]:
        def _get() -> list[dict]:
            resp = client.get(
                f"{KAGI_API}/api/batches/{batch_id}/categories/{category_uuid}/stories",
                params={"lang": self.language, "limit": self.max_stories},
            )
            resp.raise_for_status()
            return resp.json().get("stories", [])

        return with_retry(_get, max_retries=max_retries)


# ------------------------------------------------------------------
# Citation helpers (used by kagi_story.html template via render vars)
# ------------------------------------------------------------------

_CITATION_RE = re.compile(r"\[([a-zA-Z0-9._-]+(?:\.[a-zA-Z]{2,}))#(\d+)\]")

CitationMap = dict[tuple[str, int], tuple[int, str, str]]


def _build_citation_map(articles: list[dict]) -> CitationMap:
    """Map ``(domain, occurrence_number)`` to ``(global_index, url, title)``.

    ``global_index`` is the 1-based position of the article in the
    source list, which matches the ``<ol>`` numbering in the rendered
    Sources section.
    """
    domain_counts: dict[str, int] = {}
    citation_map: CitationMap = {}
    for i, art in enumerate(articles, 1):
        domain = art.get("domain", "")
        if not domain:
            continue
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        n = domain_counts[domain]
        citation_map[(domain, n)] = (i, art.get("link", ""), art.get("title", ""))
    return citation_map


def _process_citations(html_text: str, cmap: CitationMap) -> str:
    """Replace ``[domain#N]`` patterns with superscript anchor links."""
    if not cmap:
        return html_text

    def _repl(m: re.Match) -> str:
        domain = m.group(1)
        n = int(m.group(2))
        entry = cmap.get((domain, n))
        if entry is None:
            return m.group(0)
        index, _url, title = entry
        safe_title = escape(title)
        return (
            f'<sup class="cite">'
            f'<a href="#src-{index}" title="{safe_title}">{index}</a>'
            f"</sup>"
        )

    return _CITATION_RE.sub(_repl, html_text)


def _cite(text: str, cmap: CitationMap) -> str:
    """HTML-escape *text* then convert ``[domain#N]`` citation markers."""
    return _process_citations(escape(text), cmap)


def _earliest_article_date(articles: list[dict]) -> datetime | None:
    """Return the earliest publication date from a list of article dicts."""
    dates: list[datetime] = []
    for art in articles:
        raw = art.get("date")
        if not raw:
            continue
        try:
            dates.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except (ValueError, TypeError):
            continue
    return min(dates) if dates else None

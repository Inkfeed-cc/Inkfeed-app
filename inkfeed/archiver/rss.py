from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import mktime

import feedparser
import httpx
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from inkfeed.archiver.base import Article, BaseArchiver
from inkfeed.config import SourceConfig
from inkfeed.templates import get_template
from inkfeed.utils.readability import extract_article
from inkfeed.utils.retry import with_retry

logger = logging.getLogger(__name__)


class RSSArchiver(BaseArchiver):
    """Generic RSS/Atom feed archiver.

    Fetches a feed, parses it with ``feedparser``, then concurrently
    retrieves each linked article and extracts readable content via
    Mozilla Readability.  Falls back to the RSS summary/description
    when full-content extraction fails.
    """

    def __init__(self, config: SourceConfig, output_dir: Path) -> None:
        super().__init__(config, output_dir)
        self.feed_url: str = config.params["url"]
        self.max_articles: int = config.params.get("max_articles", 30)
        self.include_article_content: bool = config.params.get(
            "include_article_content", True,
        )

    def fetch(
        self,
        *,
        client: httpx.Client | None = None,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> list[dict]:
        """Download the feed, parse entries, and fetch linked articles."""
        own_client = client is None
        if own_client:
            client = httpx.Client(timeout=30, follow_redirects=True)

        try:
            feed = self._fetch_feed(client, max_retries=max_retries)
            entries = list(feed.entries[: self.max_articles])

            if not self.include_article_content:
                return [dict(e) for e in entries]

            indexed_items: dict[int, dict] = {}

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
                    f"{self.config.display_name} articles",
                    total=len(entries),
                    status="",
                )

                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {
                        pool.submit(
                            self._fetch_one_article,
                            dict(entry),
                            client,
                            max_retries,
                        ): idx
                        for idx, entry in enumerate(entries)
                    }
                    for future in as_completed(futures):
                        idx = futures[future]
                        item = future.result()
                        if item is not None:
                            title_preview = (item.get("title") or "")[:40]
                            progress.update(task, status=title_preview)
                            indexed_items[idx] = item
                        else:
                            progress.update(
                                task, status="[red]failed",
                            )
                        progress.advance(task)

            return [indexed_items[i] for i in sorted(indexed_items)]
        finally:
            if own_client:
                client.close()

    def _fetch_feed(
        self,
        client: httpx.Client,
        *,
        max_retries: int = 3,
    ) -> feedparser.FeedParserDict:
        """Download and parse the RSS/Atom feed."""
        def _get() -> feedparser.FeedParserDict:
            resp = client.get(self.feed_url)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            if feed.bozo and not feed.entries:
                raise RuntimeError(
                    f"Feed parse error: {feed.bozo_exception}"
                )
            return feed

        return with_retry(_get, max_retries=max_retries)

    def _fetch_one_article(
        self,
        entry: dict,
        client: httpx.Client,
        max_retries: int,
    ) -> dict | None:
        """Fetch full article HTML for a single feed entry.

        Stores the raw HTML in ``entry["_article_html"]`` and returns
        the enriched entry dict, or ``None`` on failure.
        """
        try:
            url = entry.get("link")
            if url:
                article_html = self._fetch_article_html(
                    url, client, max_retries=max_retries,
                )
                if article_html:
                    entry["_article_html"] = article_html
            return entry
        except Exception as exc:
            logger.debug(
                "Failed to fetch article %s: %s",
                entry.get("link", "?"), exc,
            )
            return None

    @staticmethod
    def _fetch_article_html(
        url: str,
        client: httpx.Client,
        *,
        max_retries: int = 3,
    ) -> str | None:
        """Fetch raw HTML from *url*. Returns ``None`` on failure."""
        try:
            def _get() -> httpx.Response:
                resp = client.get(url, timeout=15, follow_redirects=True)
                resp.raise_for_status()
                return resp

            resp = with_retry(_get, max_retries=max_retries)
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type:
                return None
            if len(resp.content) > 2 * 1024 * 1024:
                return None
            return resp.text
        except (httpx.HTTPError, OSError):
            return None

    def process(self, raw_items: list[dict]) -> list[Article]:
        articles: list[Article] = []
        now = datetime.now(timezone.utc)
        tpl = get_template("rss_story.html")

        for entry in raw_items:
            url = entry.get("link", "")
            title = entry.get("title", "Untitled")
            author = _extract_author(entry)
            summary = entry.get("summary", entry.get("description", ""))

            article_content = ""
            if entry.get("_article_html"):
                extracted = extract_article(entry["_article_html"], url=url)
                if extracted:
                    article_content = extracted.content

            content_html = tpl.render(
                article_content=article_content,
                summary=summary,
                url=url,
                feed_name=self.config.display_name,
            )

            publish_date = _parse_entry_date(entry)

            articles.append(Article(
                title=title,
                author=author,
                source_url=url,
                content_html=content_html,
                snapshot_date=now,
                publish_date=publish_date,
                metadata={
                    "feed_url": self.feed_url,
                    "entry_id": entry.get("id", ""),
                },
            ))

        return articles


def _extract_author(entry: dict) -> str:
    """Extract author name from a feedparser entry."""
    if entry.get("author"):
        return entry["author"]
    if entry.get("author_detail", {}).get("name"):
        return entry["author_detail"]["name"]
    # Dublin Core creator
    creators = entry.get("authors", [])
    if creators and creators[0].get("name"):
        return creators[0]["name"]
    return "unknown"


def _parse_entry_date(entry: dict) -> datetime | None:
    """Parse the publication date from a feedparser entry."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            except (ValueError, TypeError, OverflowError):
                continue
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
    return None

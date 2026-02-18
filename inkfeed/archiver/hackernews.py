from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from rich.progress import Progress, SpinnerColumn, BarColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn

from inkfeed.archiver.base import Article, BaseArchiver
from inkfeed.config import SourceConfig
from inkfeed.templates import get_template
from inkfeed.utils.readability import extract_article
from inkfeed.utils.retry import with_retry

logger = logging.getLogger(__name__)

HN_API = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_API = "https://hn.algolia.com/api/v1"


@dataclass
class Comment:
    author: str
    text: str
    time: datetime
    children: list[Comment] = field(default_factory=list)


def _count_descendants(children: list[dict]) -> int:
    """Count total comments in a children / _comments tree.

    Works on both Algolia (``children``) and normalised (``_comments``)
    shapes so it can be used before *and* after normalisation.
    """
    total = 0
    for child in children or []:
        if not child:
            continue
        total += 1
        total += _count_descendants(
            child.get("children") or child.get("_comments") or []
        )
    return total


class HackerNewsArchiver(BaseArchiver):
    def __init__(self, config: SourceConfig, output_dir: Path) -> None:
        super().__init__(config, output_dir)
        self.top_stories = config.params.get("top_stories", 30)
        self.include_comments = config.params.get("include_comments", True)
        self.include_article_content = config.params.get("include_article_content", True)
        self.max_comment_depth = config.params.get("max_comment_depth", 3)
        self.max_comments_per_level = config.params.get("max_comments_per_level", 10)

    def fetch(
        self,
        *,
        client: httpx.Client | None = None,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> list[dict]:
        own_client = client is None
        if own_client:
            client = httpx.Client(timeout=30)

        try:
            def _get_top_stories() -> list[int]:
                resp = client.get(f"{HN_API}/topstories.json")
                resp.raise_for_status()
                return resp.json()[: self.top_stories]

            story_ids = with_retry(_get_top_stories, max_retries=max_retries)

            # Map of index -> story dict, for preserving original ranking.
            indexed_stories: dict[int, dict] = {}

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
                    "hackernews stories",
                    total=len(story_ids),
                    status="",
                )

                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {
                        pool.submit(
                            self._fetch_one_story, story_id, client, max_retries,
                        ): (idx, story_id)
                        for idx, story_id in enumerate(story_ids)
                    }
                    for future in as_completed(futures):
                        idx, story_id = futures[future]
                        item = future.result()
                        if item is not None:
                            title_preview = (item.get("title") or "")[:40]
                            progress.update(task, status=title_preview)
                            indexed_stories[idx] = item
                        else:
                            progress.update(
                                task, status=f"[red]failed {story_id}",
                            )
                        progress.advance(task)

            # Return stories sorted by original top-stories ranking.
            return [indexed_stories[i] for i in sorted(indexed_stories)]
        finally:
            if own_client:
                client.close()

    def _fetch_one_story(
        self,
        story_id: int,
        client: httpx.Client,
        max_retries: int,
    ) -> dict | None:
        """Fetch a single story from Algolia, including article content.

        Returns the story dict on success, or ``None`` on failure.
        """
        try:
            def _get_item() -> httpx.Response:
                resp = client.get(f"{ALGOLIA_API}/items/{story_id}")
                resp.raise_for_status()
                return resp

            resp = with_retry(_get_item, max_retries=max_retries)
            item = resp.json()

            if not item or item.get("type") != "story":
                return None

            # Algolia /items/{id} does not return num_comments; derive the
            # total from the full tree before we trim it.
            if "num_comments" not in item and "descendants" not in item:
                item["num_comments"] = _count_descendants(
                    item.get("children") or []
                )

            if not self.include_comments:
                item["children"] = []
            else:
                item["children"] = self._trim_comment_tree(
                    item.get("children") or [], depth=0,
                )

            if self.include_article_content:
                article_html = self._fetch_article(
                    item.get("url"), client, max_retries=max_retries,
                )
                if article_html:
                    item["_article_html"] = article_html

            return item
        except Exception as exc:
            logger.debug("Failed to fetch story %s: %s", story_id, exc)
            return None

    def _trim_comment_tree(self, children: list[dict], depth: int) -> list[dict]:
        """Recursively trim the comment tree to max_comment_depth and max_comments_per_level."""
        if depth >= self.max_comment_depth:
            return []
        trimmed = []
        for child in children[: self.max_comments_per_level]:
            if not child or child.get("type") != "comment":
                continue
            child = dict(child)
            child["children"] = self._trim_comment_tree(
                child.get("children") or [], depth + 1
            )
            trimmed.append(child)
        return trimmed

    @staticmethod
    def _fetch_article(
        url: str | None,
        client: httpx.Client,
        *,
        max_retries: int = 3,
    ) -> str | None:
        """Fetch the linked article HTML. Returns raw HTML string or None."""
        if not url or "news.ycombinator.com" in url:
            return None
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
        articles = []
        now = datetime.now(timezone.utc)
        tpl = get_template("hn_story.html")

        for item in raw_items:
            # Normalise Algolia field names to the shape process() expects
            item = _normalise(item)

            url = item.get("url", f"https://news.ycombinator.com/item?id={item['id']}")
            score = item.get("score", 0)
            num_comments = item.get("descendants", 0)

            article_content = ""
            if item.get("_article_html"):
                extracted = extract_article(item["_article_html"], url=url)
                if extracted:
                    article_content = extracted.content

            comments = item.get("_comments", []) if self.include_comments else []

            content_html = tpl.render(
                article_content=article_content,
                score=score,
                num_comments=num_comments,
                url=url,
                text=item.get("text", ""),
                comments=comments,
            )

            publish_time = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)

            articles.append(Article(
                title=item.get("title", "Untitled"),
                author=item.get("by", "anonymous"),
                source_url=url,
                content_html=content_html,
                snapshot_date=now,
                publish_date=publish_time,
                metadata={"hn_id": item["id"], "score": score, "num_comments": num_comments},
            ))

        return articles


def _normalise(item: dict) -> dict:
    """Convert Algolia field names to the Firebase-compatible shape used by process()."""
    if "author" not in item and "by" not in item:
        return item  # already normalised or unknown format

    out = dict(item)

    # Algolia → Firebase field mapping
    if "author" in out and "by" not in out:
        out["by"] = out.pop("author")
    if "points" in out and "score" not in out:
        out["score"] = out.pop("points") or 0
    if "num_comments" in out and "descendants" not in out:
        out["descendants"] = out.pop("num_comments") or 0
    if "created_at_i" in out and "time" not in out:
        out["time"] = out.pop("created_at_i") or 0

    # Recursively normalise children → _comments
    # If children is present (Algolia shape), convert it.
    # If only _comments is present (already-normalised / Firebase shape), leave it.
    if "children" in out:
        children = out.pop("children") or []
        out["_comments"] = [_normalise(c) for c in children if c]
    elif "_comments" in out:
        out["_comments"] = [_normalise(c) for c in out["_comments"] if c]

    # Fallback: if there is still no descendant count, derive it from the
    # comment tree.  This covers callers that pass raw Algolia data straight
    # to process() without going through fetch().
    if "descendants" not in out and "_comments" in out:
        out["descendants"] = _count_descendants(out["_comments"])

    return out

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from inkfeed.config import SourceConfig


@dataclass
class Article:
    title: str
    author: str
    source_url: str
    content_html: str
    snapshot_date: datetime
    publish_date: datetime | None = None
    metadata: dict | None = None


@dataclass
class GroupResult:
    """A single group of articles produced by an archiver."""

    display_name: str
    rel_path: str
    cache_dir: Path
    articles: list[Article]


@dataclass
class ArchiveResult:
    """Structured output from an archiver's run().

    *source_name* is the top-level identifier (e.g. ``"hackernews"`` or
    ``"kaginews"``).  *source_display_name* is the human-readable label
    (e.g. ``"Hacker News"``); it defaults to *source_name* when not set.
    *groups* contains one or more groups of articles.  Single-group
    archivers have exactly one entry whose ``rel_path`` equals
    *source_name*.  Multi-group archivers (e.g. Kagi News with
    per-category results) have one entry per sub-group whose ``rel_path``
    is the sub-group slug (e.g. ``"business"``).
    """

    source_name: str
    source_display_name: str = ""
    groups: list[GroupResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.source_display_name:
            self.source_display_name = self.source_name


class BaseArchiver(ABC):
    def __init__(self, config: SourceConfig, output_dir: Path) -> None:
        self.config = config
        self.output_dir = output_dir

    @abstractmethod
    def fetch(self, *, max_workers: int = 8, max_retries: int = 3, **kwargs) -> list[dict]:
        """Fetch raw data from the source. Returns list of raw items."""

    @abstractmethod
    def process(self, raw_items: list[dict]) -> list[Article]:
        """Transform raw data into structured Articles."""

    def run(
        self,
        *,
        max_workers: int = 8,
        max_retries: int = 3,
    ) -> ArchiveResult:
        """Execute the full archive pipeline: fetch -> process.

        Returns an :class:`ArchiveResult` containing the source name and
        one group with the cache directory and processed articles.
        """
        cache_dir = self._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)

        raw_items = self.fetch(max_workers=max_workers, max_retries=max_retries)
        articles = self.process(raw_items)

        return ArchiveResult(
            source_name=self.config.name,
            source_display_name=self.config.display_name,
            groups=[GroupResult(
                display_name=self.config.display_name,
                rel_path=self.config.name,
                cache_dir=cache_dir,
                articles=articles,
            )],
        )

    def _cache_dir(self) -> Path:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return self.output_dir / ".cache" / self.config.name / date_str

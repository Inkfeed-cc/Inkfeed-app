from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from inkfeed.archiver.base import Article
from inkfeed.output.html import write_html


def _make_article(title: str = "Test Article", **kwargs) -> Article:
    defaults = {
        "title": title,
        "author": "testuser",
        "source_url": "https://example.com",
        "content_html": "<p>Hello world</p>",
        "snapshot_date": datetime(2026, 2, 16, tzinfo=timezone.utc),
        "publish_date": datetime(2026, 2, 16, 10, 30, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return Article(**defaults)


class TestWriteHtml:
    def test_creates_index_html(self, tmp_path: Path) -> None:
        articles = [_make_article("First"), _make_article("Second")]
        write_html("test_source", articles, tmp_path)

        index = tmp_path / "index.html"
        assert index.exists()
        content = index.read_text()
        assert "test_source" in content
        assert "First" in content
        assert "Second" in content

    def test_creates_article_html_files(self, tmp_path: Path) -> None:
        articles = [_make_article("My Article")]
        write_html("test_source", articles, tmp_path)

        html_files = list(tmp_path.glob("*.html"))
        assert len(html_files) == 2  # index + 1 article
        article_files = [f for f in html_files if f.name != "index.html"]
        assert len(article_files) == 1

    def test_article_file_contains_content(self, tmp_path: Path) -> None:
        articles = [_make_article(content_html="<p>Article body here</p>")]
        write_html("test_source", articles, tmp_path)

        article_files = [f for f in tmp_path.glob("*.html") if f.name != "index.html"]
        content = article_files[0].read_text()
        assert "Article body here" in content
        assert "testuser" in content
        assert "example.com" in content

    def test_article_has_back_link(self, tmp_path: Path) -> None:
        articles = [_make_article()]
        write_html("test_source", articles, tmp_path)

        article_files = [f for f in tmp_path.glob("*.html") if f.name != "index.html"]
        content = article_files[0].read_text()
        assert "index.html" in content

    def test_index_links_to_article_files(self, tmp_path: Path) -> None:
        articles = [_make_article("First"), _make_article("Second")]
        write_html("test_source", articles, tmp_path)

        index_content = (tmp_path / "index.html").read_text()
        article_files = [f.name for f in tmp_path.glob("*.html") if f.name != "index.html"]
        for fname in article_files:
            slug = fname.replace(".html", "")
            assert slug in index_content

    def test_snapshot_date_in_index(self, tmp_path: Path) -> None:
        articles = [_make_article()]
        write_html("test_source", articles, tmp_path)

        content = (tmp_path / "index.html").read_text()
        assert "2026-02-16" in content

    def test_multiple_articles_numbered(self, tmp_path: Path) -> None:
        articles = [_make_article(f"Article {i}") for i in range(5)]
        write_html("test_source", articles, tmp_path)

        html_files = list(tmp_path.glob("*.html"))
        assert len(html_files) == 6  # index + 5 articles

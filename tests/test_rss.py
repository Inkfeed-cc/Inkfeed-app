from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from inkfeed.archiver.rss import RSSArchiver, _extract_author, _parse_entry_date
from inkfeed.config import SourceConfig


SAMPLE_RSS_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
  <link>https://example.com</link>
  <description>A test feed</description>
  <item>
    <title>First Article</title>
    <link>https://example.com/article-1</link>
    <description>Summary of first article</description>
    <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Alice</dc:creator>
    <pubDate>Mon, 10 Feb 2026 12:00:00 +0000</pubDate>
    <guid>https://example.com/article-1</guid>
  </item>
  <item>
    <title>Second Article</title>
    <link>https://example.com/article-2</link>
    <description>Summary of second article</description>
    <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Bob</dc:creator>
    <pubDate>Tue, 11 Feb 2026 14:00:00 +0000</pubDate>
    <guid>https://example.com/article-2</guid>
  </item>
  <item>
    <title>Third Article</title>
    <link>https://example.com/article-3</link>
    <description>Summary of third article</description>
    <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Charlie</dc:creator>
    <pubDate>Wed, 12 Feb 2026 09:00:00 +0000</pubDate>
    <guid>https://example.com/article-3</guid>
  </item>
</channel>
</rss>
"""

SAMPLE_ATOM_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <link href="https://example.com"/>
  <entry>
    <title>Atom Entry One</title>
    <link href="https://example.com/atom-1"/>
    <summary>Summary of atom entry</summary>
    <author><name>Dana</name></author>
    <updated>2026-02-10T12:00:00Z</updated>
    <id>https://example.com/atom-1</id>
  </entry>
</feed>
"""

SAMPLE_ARTICLE_BODY = """\
<html><head><title>Full Article</title></head><body>
<article>
<h1>Full Article Title</h1>
<p>This is a substantial article with enough content for readability to extract.
It discusses important topics in software engineering and provides detailed
analysis of various architectural patterns used in modern distributed systems.
The article covers multiple paragraphs of real content that would be found on
a typical blog post or news article on the web.</p>
<p>Furthermore, this second paragraph adds additional depth to the article,
exploring the trade-offs between consistency and availability in distributed
databases, and how eventual consistency models can provide better user
experience in certain scenarios.</p>
</article>
</body></html>
"""

FEED_URL = "https://example.com/rss.xml"


def _make_config(**overrides) -> SourceConfig:
    defaults = {
        "name": "testfeed",
        "type": "rss",
        "frequency": "daily",
        "enabled": True,
        "params": {
            "url": FEED_URL,
            "max_articles": 30,
            "include_article_content": True,
        },
    }
    defaults.update(overrides)
    return SourceConfig(**defaults)


def _mock_transport(
    feed_content: str = SAMPLE_RSS_FEED,
    article_responses: dict[str, tuple[int, str, str]] | None = None,
):
    """Mock transport returning feed XML and optional article HTML."""
    article_responses = article_responses or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if url == FEED_URL:
            return httpx.Response(
                200,
                content=feed_content.encode(),
                headers={"content-type": "application/rss+xml"},
            )

        if url in article_responses:
            status, ct, body = article_responses[url]
            return httpx.Response(
                status,
                content=body.encode() if isinstance(body, str) else body,
                headers={"content-type": ct},
            )

        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _make_client(
    feed_content: str = SAMPLE_RSS_FEED,
    article_responses: dict[str, tuple[int, str, str]] | None = None,
) -> httpx.Client:
    return httpx.Client(
        transport=_mock_transport(feed_content, article_responses),
    )


class TestRSSFetch:
    def test_fetches_all_entries(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 30,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client()

        items = archiver.fetch(client=client)

        assert len(items) == 3
        assert items[0]["title"] == "First Article"
        assert items[1]["title"] == "Second Article"
        assert items[2]["title"] == "Third Article"

    def test_respects_max_articles(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 2,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client()

        items = archiver.fetch(client=client)

        assert len(items) == 2
        assert items[0]["title"] == "First Article"
        assert items[1]["title"] == "Second Article"

    def test_fetches_article_html_when_enabled(self) -> None:
        article_responses = {
            "https://example.com/article-1": (
                200, "text/html; charset=utf-8", SAMPLE_ARTICLE_BODY,
            ),
            "https://example.com/article-2": (
                200, "text/html; charset=utf-8", SAMPLE_ARTICLE_BODY,
            ),
            "https://example.com/article-3": (
                200, "text/html; charset=utf-8", SAMPLE_ARTICLE_BODY,
            ),
        }
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 3,
            "include_article_content": True,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client(article_responses=article_responses)

        items = archiver.fetch(client=client)

        assert "_article_html" in items[0]
        assert "Full Article Title" in items[0]["_article_html"]

    def test_skips_article_html_when_disabled(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 3,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client()

        items = archiver.fetch(client=client)

        assert "_article_html" not in items[0]

    def test_skips_non_html_responses(self) -> None:
        article_responses = {
            "https://example.com/article-1": (
                200, "application/pdf", b"%PDF-1.4 fake",
            ),
        }
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 1,
            "include_article_content": True,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client(article_responses=article_responses)

        items = archiver.fetch(client=client)

        assert "_article_html" not in items[0]

    def test_handles_article_fetch_failure(self) -> None:
        article_responses = {
            "https://example.com/article-1": (
                500, "text/html", "Server Error",
            ),
        }
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 1,
            "include_article_content": True,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client(article_responses=article_responses)

        items = archiver.fetch(client=client)

        assert len(items) == 1
        assert "_article_html" not in items[0]

    def test_parses_atom_feed(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 10,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client(feed_content=SAMPLE_ATOM_FEED)

        items = archiver.fetch(client=client)

        assert len(items) == 1
        assert items[0]["title"] == "Atom Entry One"


class TestRSSProcess:
    def test_produces_articles_from_entries(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 30,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client()

        raw_items = archiver.fetch(client=client)
        articles = archiver.process(raw_items)

        assert len(articles) == 3
        first = articles[0]
        assert first.title == "First Article"
        assert first.source_url == "https://example.com/article-1"
        assert first.metadata["feed_url"] == FEED_URL

    def test_extracts_readability_content(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 1,
            "include_article_content": True,
        })
        archiver = RSSArchiver(config, Path("output"))

        raw_items = [{
            "title": "Test",
            "link": "https://example.com/article-1",
            "summary": "Short summary",
            "_article_html": SAMPLE_ARTICLE_BODY,
        }]
        articles = archiver.process(raw_items)

        html = articles[0].content_html
        assert 'class="article-content"' in html
        assert "distributed systems" in html

    def test_falls_back_to_summary(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 1,
            "include_article_content": True,
        })
        archiver = RSSArchiver(config, Path("output"))

        raw_items = [{
            "title": "Test",
            "link": "https://example.com/article-1",
            "summary": "This is the RSS summary fallback content",
        }]
        articles = archiver.process(raw_items)

        html = articles[0].content_html
        assert "summary-fallback" in html
        assert "RSS summary fallback" in html

    def test_falls_back_to_summary_when_readability_fails(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 1,
            "include_article_content": True,
        })
        archiver = RSSArchiver(config, Path("output"))

        raw_items = [{
            "title": "Test",
            "link": "https://example.com/article-1",
            "summary": "Fallback summary text here",
            "_article_html": "<html><body><p>x</p></body></html>",
        }]
        articles = archiver.process(raw_items)

        html = articles[0].content_html
        assert "summary-fallback" in html
        assert "Fallback summary text" in html

    def test_article_contains_original_link(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 1,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))

        raw_items = [{
            "title": "Test",
            "link": "https://example.com/article-1",
            "summary": "Summary content here",
        }]
        articles = archiver.process(raw_items)

        html = articles[0].content_html
        assert "https://example.com/article-1" in html
        assert "original link" in html

    def test_publish_date_parsed(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 30,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client()

        raw_items = archiver.fetch(client=client)
        articles = archiver.process(raw_items)

        assert articles[0].publish_date is not None
        assert articles[0].publish_date.year == 2026
        assert articles[0].publish_date.month == 2

    def test_author_extraction(self) -> None:
        config = _make_config(params={
            "url": FEED_URL,
            "max_articles": 30,
            "include_article_content": False,
        })
        archiver = RSSArchiver(config, Path("output"))
        client = _make_client()

        raw_items = archiver.fetch(client=client)
        articles = archiver.process(raw_items)

        assert articles[0].author == "Alice"


class TestRSSRun:
    def test_run_returns_archive_result(self, tmp_path) -> None:
        config = _make_config(
            name="testfeed",
            params={
                "url": FEED_URL,
                "max_articles": 2,
                "include_article_content": False,
            },
        )
        archiver = RSSArchiver(config, tmp_path)
        client = _make_client()

        original_fetch = archiver.fetch
        archiver.fetch = lambda **kwargs: original_fetch(client=client)

        result = archiver.run()

        assert result.source_name == "testfeed"
        assert len(result.groups) == 1

        group = result.groups[0]
        assert group.cache_dir.exists()
        assert ".cache" in str(group.cache_dir)
        assert "testfeed" in str(group.cache_dir)
        assert group.rel_path == "testfeed"
        assert len(group.articles) == 2


class TestHelpers:
    def test_extract_author_from_author_field(self) -> None:
        assert _extract_author({"author": "Alice"}) == "Alice"

    def test_extract_author_from_author_detail(self) -> None:
        entry = {"author_detail": {"name": "Bob"}}
        assert _extract_author(entry) == "Bob"

    def test_extract_author_from_authors_list(self) -> None:
        entry = {"authors": [{"name": "Charlie"}]}
        assert _extract_author(entry) == "Charlie"

    def test_extract_author_fallback(self) -> None:
        assert _extract_author({}) == "unknown"

    def test_parse_entry_date_from_published_parsed(self) -> None:
        import time
        entry = {
            "published_parsed": time.strptime(
                "2026-02-10 12:00:00", "%Y-%m-%d %H:%M:%S",
            ),
        }
        dt = _parse_entry_date(entry)
        assert dt is not None
        assert dt.year == 2026

    def test_parse_entry_date_returns_none_when_absent(self) -> None:
        assert _parse_entry_date({}) is None

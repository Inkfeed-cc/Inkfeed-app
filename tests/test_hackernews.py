from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from inkfeed.archiver.hackernews import HackerNewsArchiver, HN_API, ALGOLIA_API
from inkfeed.config import SourceConfig


def _make_config(**overrides) -> SourceConfig:
    defaults = {
        "name": "hackernews",
        "type": "api",
        "frequency": "daily",
        "enabled": True,
        "params": {
            "top_stories": 3,
            "include_comments": True,
            "max_comment_depth": 3,
        },
    }
    defaults.update(overrides)
    return SourceConfig(**defaults)


def _mock_transport(hn_top_stories, hn_algolia_items, article_responses=None):
    """Mock transport: Firebase for top stories list, Algolia for item trees.

    ``article_responses`` is an optional dict mapping article URLs to
    ``(status_code, content_type, body)`` tuples.
    """
    article_responses = article_responses or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if url == f"{HN_API}/topstories.json":
            return httpx.Response(200, json=hn_top_stories)

        m = re.search(r"/items/(\d+)$", url)
        if m:
            item_id = m.group(1)
            if item_id in hn_algolia_items:
                return httpx.Response(200, json=hn_algolia_items[item_id])
            return httpx.Response(404, json=None)

        if url in article_responses:
            status, ct, body = article_responses[url]
            return httpx.Response(status, content=body.encode() if isinstance(body, str) else body, headers={"content-type": ct})

        return httpx.Response(404, json=None)

    return httpx.MockTransport(handler)


def _make_client(hn_top_stories, hn_algolia_items, article_responses=None) -> httpx.Client:
    return httpx.Client(transport=_mock_transport(hn_top_stories, hn_algolia_items, article_responses))


class TestHackerNewsFetch:
    def test_fetches_correct_number_of_stories(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        config = _make_config(params={"top_stories": 3, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items)

        stories = archiver.fetch(client=client)

        assert len(stories) == 3
        assert stories[0]["id"] == 47033328
        assert stories[0]["title"] == "MessageFormat: Unicode standard for localizable message strings"

    def test_fetches_comments_when_enabled(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": True,
            "max_comment_depth": 2,
            "max_comments_per_level": 10,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items)

        stories = archiver.fetch(client=client)

        # children comes back trimmed but still in Algolia shape (normalisation is in process())
        children = stories[0]["children"]
        assert len(children) == 3  # 3 top-level comments

        first = children[0]
        assert first["author"] == "jp1016"
        # depth=2: first level children are kept, their children are dropped
        assert len(first["children"]) == 2
        assert first["children"][0]["children"] == []  # depth 2 trimmed

    def test_skips_comments_when_disabled(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": False,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items)

        stories = archiver.fetch(client=client)

        assert stories[0]["children"] == []

    def test_respects_max_comment_depth(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": True,
            "max_comment_depth": 1,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items)

        stories = archiver.fetch(client=client)

        # depth=1: top-level comments kept, their children dropped
        first_child = stories[0]["children"][0]
        assert first_child["children"] == []

    def test_respects_max_comments_per_level(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": True,
            "max_comment_depth": 2,
            "max_comments_per_level": 2,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items)

        stories = archiver.fetch(client=client)

        # Story 47033328 has 3 top-level comments, but we limit to 2
        assert len(stories[0]["children"]) == 2

    def test_handles_http_error_gracefully(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        """Stories that fail to fetch should be skipped, others continue."""
        def failing_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == f"{HN_API}/topstories.json":
                return httpx.Response(200, json=hn_top_stories)
            if f"/items/47033328" in url:
                return httpx.Response(200, json=hn_algolia_items["47033328"])
            return httpx.Response(500)

        config = _make_config(params={"top_stories": 3, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))
        client = httpx.Client(transport=httpx.MockTransport(failing_handler))

        stories = archiver.fetch(client=client)

        assert len(stories) == 1
        assert stories[0]["id"] == 47033328

    def test_single_request_per_story(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        """Fetch should make exactly 1 Algolia request per story (not one per comment)."""
        request_log: list[str] = []

        def counting_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            request_log.append(url)
            if url == f"{HN_API}/topstories.json":
                return httpx.Response(200, json=hn_top_stories)
            m = re.search(r"/items/(\d+)$", url)
            if m and m.group(1) in hn_algolia_items:
                return httpx.Response(200, json=hn_algolia_items[m.group(1)])
            return httpx.Response(404, json=None)

        config = _make_config(params={
            "top_stories": 3,
            "include_comments": True,
            "max_comment_depth": 3,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = httpx.Client(transport=httpx.MockTransport(counting_handler))

        archiver.fetch(client=client)

        algolia_calls = [u for u in request_log if ALGOLIA_API in u]
        # Should be exactly 3: one per story, not per comment
        assert len(algolia_calls) == 3


class TestHackerNewsProcess:
    def test_produces_articles_with_metadata(
        self, hn_stories
    ) -> None:
        config = _make_config(params={"top_stories": 3, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw_items = list(hn_stories.values())
        articles = archiver.process(raw_items)

        assert len(articles) == 3

        first = articles[0]
        assert first.title == "MessageFormat: Unicode standard for localizable message strings"
        assert first.author == "todsacerdoti"
        assert first.source_url == "https://github.com/unicode-org/message-format-wg"
        assert first.metadata["score"] == 92
        assert first.metadata["hn_id"] == 47033328

    def test_normalises_algolia_fields(self, hn_algolia_items) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw = [hn_algolia_items["47033328"]]
        articles = archiver.process(raw)

        assert articles[0].author == "todsacerdoti"
        assert articles[0].metadata["score"] == 92
        # The Algolia /items/{id} endpoint does not return num_comments;
        # the count is derived from the children tree (8 comments in fixture).
        assert articles[0].metadata["num_comments"] == 8

    def test_article_html_contains_score_and_comments(
        self, hn_stories
    ) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw_items = [hn_stories["47033328"]]
        articles = archiver.process(raw_items)

        html = articles[0].content_html
        assert "92 points" in html
        assert "37 comments" in html

    def test_article_with_comments_renders_html(
        self, hn_stories, hn_comments
    ) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": True})
        archiver = HackerNewsArchiver(config, Path("output"))

        story = dict(hn_stories["47033328"])
        story["_comments"] = [
            {**hn_comments["47033789"], "_comments": [
                {**hn_comments["47034139"], "_comments": []}
            ]},
        ]

        articles = archiver.process([story])
        html = articles[0].content_html
        assert "jp1016" in html
        assert "Vinnl" in html
        assert "Comments" in html

    def test_algolia_comments_render_via_normalise(self, hn_algolia_items) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": True})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw = [hn_algolia_items["47033328"]]
        articles = archiver.process(raw)
        html = articles[0].content_html
        assert "jp1016" in html
        assert "Comments" in html

    def test_story_without_url_uses_hn_link(self) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw = [{
            "by": "someone",
            "descendants": 0,
            "id": 12345,
            "score": 10,
            "time": 1700000000,
            "title": "Ask HN: Something",
            "type": "story",
            "text": "Some question text",
        }]
        articles = archiver.process(raw)
        assert "news.ycombinator.com/item?id=12345" in articles[0].source_url


class TestHackerNewsRun:
    def test_run_returns_snapshot_dir_and_articles(
        self, tmp_path, hn_top_stories, hn_algolia_items
    ) -> None:
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": False,
        })
        archiver = HackerNewsArchiver(config, tmp_path)
        client = _make_client(hn_top_stories, hn_algolia_items)

        original_fetch = archiver.fetch
        archiver.fetch = lambda **kwargs: original_fetch(client=client)

        result = archiver.run()

        assert result.source_name == "hackernews"
        assert len(result.groups) == 1

        group = result.groups[0]
        assert group.cache_dir.exists()
        assert ".cache" in str(group.cache_dir)
        assert "hackernews" in str(group.cache_dir)
        assert group.rel_path == "hackernews"
        assert len(group.articles) == 1


SAMPLE_ARTICLE_BODY = """\
<html><head><title>Sample Article</title></head><body>
<article>
<h1>Sample Article</h1>
<p>This is a substantial article with enough content for readability to extract.
It discusses important topics in software engineering and provides detailed
analysis of various architectural patterns used in modern distributed systems.
The article covers multiple paragraphs of real content that would be found on
a typical blog post or news article on the web.</p>
<p>Furthermore, this second paragraph adds additional depth to the article,
exploring the trade-offs between consistency and availability in distributed
databases, and how eventual consistency models can provide better user
experience in certain scenarios.</p>
<img src="/images/diagram.png" alt="Architecture diagram">
</article>
</body></html>
"""


class TestHackerNewsFetchArticleContent:
    def test_fetches_article_html_for_stories_with_urls(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        article_responses = {
            "https://github.com/unicode-org/message-format-wg": (
                200, "text/html; charset=utf-8", SAMPLE_ARTICLE_BODY,
            ),
        }
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": False,
            "include_article_content": True,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items, article_responses)

        stories = archiver.fetch(client=client)

        assert "_article_html" in stories[0]
        assert "Sample Article" in stories[0]["_article_html"]

    def test_skips_hn_internal_urls(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        # Modify fixture to have an HN-internal URL
        items = dict(hn_algolia_items)
        items["47033328"] = dict(items["47033328"])
        items["47033328"]["url"] = "https://news.ycombinator.com/item?id=12345"

        config = _make_config(params={
            "top_stories": 1,
            "include_comments": False,
            "include_article_content": True,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, items)

        stories = archiver.fetch(client=client)

        assert "_article_html" not in stories[0]

    def test_skips_non_html_content(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        article_responses = {
            "https://github.com/unicode-org/message-format-wg": (
                200, "application/pdf", b"%PDF-1.4 fake pdf content",
            ),
        }
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": False,
            "include_article_content": True,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items, article_responses)

        stories = archiver.fetch(client=client)

        assert "_article_html" not in stories[0]

    def test_graceful_on_article_fetch_failure(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        article_responses = {
            "https://github.com/unicode-org/message-format-wg": (
                500, "text/html", "Server Error",
            ),
        }
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": False,
            "include_article_content": True,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items, article_responses)

        stories = archiver.fetch(client=client)

        # Story still fetched, just no article HTML
        assert len(stories) == 1
        assert "_article_html" not in stories[0]

    def test_disabled_by_config(
        self, hn_top_stories, hn_algolia_items
    ) -> None:
        config = _make_config(params={
            "top_stories": 1,
            "include_comments": False,
            "include_article_content": False,
        })
        archiver = HackerNewsArchiver(config, Path("output"))
        client = _make_client(hn_top_stories, hn_algolia_items)

        stories = archiver.fetch(client=client)

        assert "_article_html" not in stories[0]


class TestHackerNewsProcessArticleContent:
    def test_extracted_content_appears_in_html(self, hn_algolia_items) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw = [dict(hn_algolia_items["47033328"])]
        raw[0]["_article_html"] = SAMPLE_ARTICLE_BODY

        articles = archiver.process(raw)

        html = articles[0].content_html
        assert 'class="article-content"' in html
        assert "distributed systems" in html

    def test_article_content_appears_before_story_meta(self, hn_algolia_items) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw = [dict(hn_algolia_items["47033328"])]
        raw[0]["_article_html"] = SAMPLE_ARTICLE_BODY

        articles = archiver.process(raw)

        html = articles[0].content_html
        article_pos = html.index("article-content")
        meta_pos = html.index("story-meta")
        assert article_pos < meta_pos

    def test_graceful_when_article_html_absent(self, hn_algolia_items) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw = [dict(hn_algolia_items["47033328"])]
        # No _article_html key at all

        articles = archiver.process(raw)

        html = articles[0].content_html
        assert "article-content" not in html
        assert "92 points" in html

    def test_graceful_when_article_html_is_garbage(self, hn_algolia_items) -> None:
        config = _make_config(params={"top_stories": 1, "include_comments": False})
        archiver = HackerNewsArchiver(config, Path("output"))

        raw = [dict(hn_algolia_items["47033328"])]
        raw[0]["_article_html"] = "<html><body><p>x</p></body></html>"

        articles = archiver.process(raw)

        html = articles[0].content_html
        # Readability returns None for too-short content, so no article section
        assert "article-content" not in html
        assert "92 points" in html

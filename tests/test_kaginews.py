from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from inkfeed.archiver.kaginews import KagiNewsArchiver
from inkfeed.config import SourceConfig


BATCH_ID = "a5271a19-6f23-453f-a6df-6a2e3ea7ebfe"


def _make_config(**overrides) -> SourceConfig:
    defaults = {
        "name": "kaginews",
        "type": "api",
        "frequency": "daily",
        "enabled": True,
        "params": {
            "language": "en",
            "max_stories_per_category": 50,
            "categories": ["tech", "world"],
        },
    }
    defaults.update(overrides)
    return SourceConfig(**defaults)


def _mock_transport(
    kagi_batches: dict,
    kagi_categories: dict,
    stories_by_uuid: dict[str, dict] | None = None,
):
    """Mock transport for Kagi News API.

    ``stories_by_uuid`` maps category UUID to the full stories response dict.
    """
    stories_by_uuid = stories_by_uuid or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path

        if path == "/api/batches" and "categories" not in path:
            return httpx.Response(200, json=kagi_batches)

        # /api/batches/{id}/categories/{uuid}/stories
        m = re.search(r"/api/batches/[^/]+/categories/([^/]+)/stories", path)
        if m:
            uuid = m.group(1)
            if uuid in stories_by_uuid:
                return httpx.Response(200, json=stories_by_uuid[uuid])
            return httpx.Response(404, json={"error": "Category not found"})

        # /api/batches/{id}/categories  (no trailing /stories)
        if re.search(r"/api/batches/[^/]+/categories$", path):
            return httpx.Response(200, json=kagi_categories)

        return httpx.Response(404, json={"error": "Not found"})

    return httpx.MockTransport(handler)


def _make_client(
    kagi_batches: dict,
    kagi_categories: dict,
    stories_by_uuid: dict[str, dict] | None = None,
) -> httpx.Client:
    return httpx.Client(
        transport=_mock_transport(kagi_batches, kagi_categories, stories_by_uuid),
    )


# ── category UUID constants (from fixture data) ──

TECH_UUID = "29d914dc-5faf-4f51-9135-35a50bfbb6e6"
WORLD_UUID = "54a49257-5a35-453c-8206-f5f73727b68a"


class TestKagiNewsFetch:
    def test_fetches_configured_categories(
        self, kagi_batches, kagi_categories, kagi_stories_tech, kagi_stories_world,
    ) -> None:
        config = _make_config(params={
            "categories": ["tech", "world"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, Path("output"))
        client = _make_client(
            kagi_batches, kagi_categories,
            {TECH_UUID: kagi_stories_tech, WORLD_UUID: kagi_stories_world},
        )

        results = archiver.fetch(client=client)

        assert len(results) == 2
        slugs = [r["category_slug"] for r in results]
        assert "tech" in slugs
        assert "world" in slugs

    def test_resolves_category_names(
        self, kagi_batches, kagi_categories, kagi_stories_tech,
    ) -> None:
        config = _make_config(params={
            "categories": ["tech"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, Path("output"))
        client = _make_client(
            kagi_batches, kagi_categories, {TECH_UUID: kagi_stories_tech},
        )

        results = archiver.fetch(client=client)

        assert results[0]["category_name"] == "Technology"

    def test_skips_unconfigured_categories(
        self, kagi_batches, kagi_categories, kagi_stories_tech,
    ) -> None:
        """Only configured categories should appear in results."""
        config = _make_config(params={
            "categories": ["tech"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, Path("output"))
        client = _make_client(
            kagi_batches, kagi_categories, {TECH_UUID: kagi_stories_tech},
        )

        results = archiver.fetch(client=client)

        assert len(results) == 1
        assert results[0]["category_slug"] == "tech"

    def test_skips_missing_categories_gracefully(
        self, kagi_batches, kagi_categories,
    ) -> None:
        """Categories in config but not in the API response should be skipped."""
        config = _make_config(params={
            "categories": ["nonexistent_category"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, Path("output"))
        client = _make_client(kagi_batches, kagi_categories, {})

        results = archiver.fetch(client=client)

        assert results == []

    def test_returns_stories_in_each_category(
        self, kagi_batches, kagi_categories, kagi_stories_tech, kagi_stories_world,
    ) -> None:
        config = _make_config(params={
            "categories": ["tech", "world"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, Path("output"))
        client = _make_client(
            kagi_batches, kagi_categories,
            {TECH_UUID: kagi_stories_tech, WORLD_UUID: kagi_stories_world},
        )

        results = archiver.fetch(client=client)

        tech = next(r for r in results if r["category_slug"] == "tech")
        world = next(r for r in results if r["category_slug"] == "world")
        assert len(tech["stories"]) == 2
        assert len(world["stories"]) == 1

    def test_handles_http_error_for_stories(
        self, kagi_batches, kagi_categories,
    ) -> None:
        """If fetching stories for one category fails, others still succeed."""
        def failing_handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/batches":
                return httpx.Response(200, json=kagi_batches)
            if re.search(r"/categories$", path):
                return httpx.Response(200, json=kagi_categories)
            # All story requests fail
            if "/stories" in path:
                return httpx.Response(500)
            return httpx.Response(404)

        config = _make_config(params={
            "categories": ["tech"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, Path("output"))
        client = httpx.Client(transport=httpx.MockTransport(failing_handler))

        results = archiver.fetch(client=client)

        assert results == []

    def test_no_batches_raises_runtime_error(self, kagi_categories) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))
        client = _make_client({"batches": []}, kagi_categories, {})

        with pytest.raises(RuntimeError, match="No batches available"):
            archiver.fetch(client=client)


class TestKagiNewsProcess:
    def test_produces_articles_from_stories(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        assert len(articles) == 2
        assert articles[0].title == "Linux Kernel 7.0 Released with Major Performance Improvements"
        assert articles[1].title == "Firefox 140 Introduces Tab Grouping and Vertical Tabs"

    def test_article_metadata(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        first = articles[0]
        assert first.author == "Kagi News"
        assert first.source_url == "https://www.phoronix.com/linux-7-0"
        assert first.metadata["cluster_id"] == "c370ef00-bab1-4990-a54a-15d20fb8d353"
        assert first.metadata["emoji"] == "🐧"
        assert first.metadata["unique_domains"] == 5

    def test_publish_date_is_earliest_article(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        # First story: earliest is 2026-02-16T08:00:00Z
        assert articles[0].publish_date is not None
        assert articles[0].publish_date.hour == 8

    def test_content_html_contains_summary(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "Linux Foundation" in html
        assert "story-summary" in html

    def test_content_html_wrapped_in_article_content(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert html.startswith('<div class="article-content">')
        assert html.rstrip().endswith("</div>")

    def test_content_html_contains_talking_points(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "talking-points" in html
        assert "CFS scheduler" in html
        assert "Rust driver support" in html
        assert "<ol>" in html  # numbered list for highlights

    def test_content_html_contains_perspectives(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "perspectives" in html
        assert "Enterprise IT" in html
        assert "Open Source Community" in html

    def test_content_html_contains_source_articles(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "source-articles" in html
        assert "phoronix.com" in html
        assert "lwn.net" in html
        assert "arstechnica.com" in html

    def test_content_html_contains_quote(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "story-quote" in html
        assert "Linus Torvalds" in html

    def test_content_html_contains_images(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "story-image" in html
        assert "linux-kernel.jpg" in html
        assert "Linux Foundation" in html  # credit

    def test_story_without_articles_has_empty_source_url(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Orphan Story",
            "short_summary": "No sources available.",
            "articles": [],
        }]
        articles = archiver.process(stories)

        assert articles[0].source_url == ""
        assert articles[0].publish_date is None

    def test_section_order_sources_before_highlights(self, kagi_stories_tech) -> None:
        """Sources should appear before highlights in the output."""
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        sources_pos = html.index("source-articles")
        highlights_pos = html.index("talking-points")
        assert sources_pos < highlights_pos

    def test_section_order_quote_before_perspectives(self, kagi_stories_tech) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = kagi_stories_tech["stories"]
        articles = archiver.process(stories)

        html = articles[0].content_html
        quote_pos = html.index("story-quote")
        perspectives_pos = html.index("perspectives")
        assert quote_pos < perspectives_pos

    def test_renders_did_you_know(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "did_you_know": "Honey never spoils.",
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "did-you-know" in html
        assert "Honey never spoils." in html
        assert "Did you know?" in html

    def test_renders_timeline(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "timeline": [
                {"date": "Jan 1, 2026", "content": "Event one"},
                {"date": "Feb 1, 2026", "content": "Event two"},
            ],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "timeline" in html
        assert "timeline-item" in html
        assert "timeline-dot" in html
        assert "Jan 1, 2026" in html
        assert "Event one" in html
        assert "Event two" in html

    def test_renders_timeline_string_format(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "timeline": ["Jan 2026:: Something happened"],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "Jan 2026" in html
        assert "Something happened" in html

    def test_renders_historical_background(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "historical_background": "This dates back to the 1800s.",
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "Historical Background" in html
        assert "1800s" in html
        assert "kagi-section" in html

    def test_renders_suggested_qna(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "suggested_qna": [
                {"question": "What happened?", "answer": "Something big."},
            ],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "suggested-qna" in html
        assert "<details>" in html
        assert "<summary>" in html
        assert "What happened?" in html
        assert "Something big." in html

    def test_renders_action_items(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "user_action_items": ["Contact your rep", "Stay informed"],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "action-items" in html
        assert "Contact your rep" in html
        assert "Stay informed" in html

    def test_renders_international_reactions(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "international_reactions": [
                "🇺🇸 US: Expressed strong support.",
                "🇪🇺 EU: Called for further coordination.",
            ],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "international-reactions" in html
        assert "Expressed strong support" in html

    def test_renders_business_angle(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "business_angle_text": "Investors should watch closely.",
            "business_angle_points": ["Revenue could double", "Market share grows"],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "Business Angle" in html
        assert "Investors should watch closely" in html
        assert "Revenue could double" in html

    def test_renders_scientific_significance(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "scientific_significance": ["Breakthrough in gene therapy"],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "Scientific Significance" in html
        assert "gene therapy" in html

    def test_renders_gameplay_mechanics(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "gameplay_mechanics": ["New crafting system", "Open world exploration"],
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "Gameplay Mechanics" in html
        assert "New crafting system" in html

    def test_renders_performance_statistics(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "performance_statistics": ["Scored 30 points"],
            "league_standings": "Currently 2nd in the conference.",
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "Performance Statistics" in html
        assert "Scored 30 points" in html
        assert "League Standings" in html
        assert "2nd in the conference" in html

    def test_missing_optional_fields_produce_no_empty_sections(self) -> None:
        """A story with only a summary should have no broken/empty sections."""
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Minimal Story",
            "short_summary": "Just a summary.",
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        assert "story-summary" in html
        # None of the optional sections should appear
        assert "talking-points" not in html
        assert "story-quote" not in html
        assert "perspectives" not in html
        assert "timeline" not in html
        assert "did-you-know" not in html
        assert "suggested-qna" not in html
        assert "action-items" not in html
        assert "kagi-section" not in html
        assert "international-reactions" not in html

    def test_did_you_know_appears_after_action_items(self) -> None:
        config = _make_config()
        archiver = KagiNewsArchiver(config, Path("output"))

        stories = [{
            "title": "Test Story",
            "short_summary": "A test.",
            "user_action_items": ["Do something"],
            "did_you_know": "Fun fact!",
            "articles": [],
        }]
        articles = archiver.process(stories)

        html = articles[0].content_html
        action_pos = html.index("action-items")
        dyk_pos = html.index("did-you-know")
        assert action_pos < dyk_pos


class TestKagiNewsRun:
    def test_run_returns_per_category_results(
        self, tmp_path, kagi_batches, kagi_categories,
        kagi_stories_tech, kagi_stories_world,
    ) -> None:
        config = _make_config(params={
            "categories": ["tech", "world"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, tmp_path)
        client = _make_client(
            kagi_batches, kagi_categories,
            {TECH_UUID: kagi_stories_tech, WORLD_UUID: kagi_stories_world},
        )

        original_fetch = archiver.fetch
        archiver.fetch = lambda **kwargs: original_fetch(client=client)

        result = archiver.run()

        assert result.source_name == "kaginews"
        assert len(result.groups) == 2

        names = [g.display_name for g in result.groups]
        assert "Technology" in names
        assert "World" in names

    def test_run_creates_category_snapshot_dirs(
        self, tmp_path, kagi_batches, kagi_categories,
        kagi_stories_tech, kagi_stories_world,
    ) -> None:
        config = _make_config(params={
            "categories": ["tech", "world"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, tmp_path)
        client = _make_client(
            kagi_batches, kagi_categories,
            {TECH_UUID: kagi_stories_tech, WORLD_UUID: kagi_stories_world},
        )

        original_fetch = archiver.fetch
        archiver.fetch = lambda **kwargs: original_fetch(client=client)

        result = archiver.run()

        for group in result.groups:
            assert group.cache_dir.exists()
            assert group.cache_dir.is_dir()

        # Verify directory structure: output/.cache/kaginews/{date}/{slug}/
        dirs = [g.cache_dir for g in result.groups]
        dir_names = sorted(d.name for d in dirs)
        assert dir_names == ["tech", "world"]

        # All share the same parent (date directory)
        parents = set(d.parent for d in dirs)
        assert len(parents) == 1

    def test_run_returns_correct_articles(
        self, tmp_path, kagi_batches, kagi_categories,
        kagi_stories_tech, kagi_stories_world,
    ) -> None:
        config = _make_config(params={
            "categories": ["tech", "world"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, tmp_path)
        client = _make_client(
            kagi_batches, kagi_categories,
            {TECH_UUID: kagi_stories_tech, WORLD_UUID: kagi_stories_world},
        )

        original_fetch = archiver.fetch
        archiver.fetch = lambda **kwargs: original_fetch(client=client)

        result = archiver.run()

        tech_group = next(g for g in result.groups if g.display_name == "Technology")
        world_group = next(g for g in result.groups if g.display_name == "World")

        assert len(tech_group.articles) == 2
        assert len(world_group.articles) == 1
        assert tech_group.articles[0].title == "Linux Kernel 7.0 Released with Major Performance Improvements"

    def test_run_skips_empty_categories(
        self, tmp_path, kagi_batches, kagi_categories,
    ) -> None:
        empty_stories = {
            "stories": [],
            "totalStories": 0,
        }
        config = _make_config(params={
            "categories": ["tech"],
            "language": "en",
        })
        archiver = KagiNewsArchiver(config, tmp_path)
        client = _make_client(
            kagi_batches, kagi_categories, {TECH_UUID: empty_stories},
        )

        original_fetch = archiver.fetch
        archiver.fetch = lambda **kwargs: original_fetch(client=client)

        result = archiver.run()

        assert result.source_name == "kaginews"
        assert result.groups == []

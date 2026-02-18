from __future__ import annotations

from pathlib import Path

import pytest

from inkfeed.utils.readability import extract_article, ReadabilityResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_article_html() -> str:
    return (FIXTURES_DIR / "sample_article.html").read_text()


class TestExtractArticle:
    def test_extracts_content_from_article_tag(self, sample_article_html) -> None:
        result = extract_article(sample_article_html)
        assert result is not None
        assert "WebAssembly" in result.content
        assert "Near-native performance" in result.content

    def test_extracts_content_from_div(self) -> None:
        html = """
        <html><head><title>Test Page</title></head><body>
        <div id="main">
            <p>This is a substantial article about distributed systems. It covers many
            aspects of building reliable and scalable software architectures that can
            handle millions of requests per second across data centers around the world.
            The key insight is that consistency and availability trade-offs must be carefully
            considered when designing these systems.</p>
            <p>We explored several important patterns including circuit breakers, bulkheads,
            and retry strategies. Each pattern addresses different failure modes and has
            trade-offs worth understanding deeply.</p>
        </div>
        <div id="sidebar"><p>Ad: Buy stuff</p></div>
        </body></html>
        """
        result = extract_article(html)
        assert result is not None
        assert "distributed systems" in result.content

    def test_returns_title(self, sample_article_html) -> None:
        result = extract_article(sample_article_html)
        assert result is not None
        assert "WebAssembly" in result.title

    def test_returns_short_title(self, sample_article_html) -> None:
        result = extract_article(sample_article_html)
        assert result is not None
        assert isinstance(result.short_title, str)
        assert len(result.short_title) > 0

    def test_returns_none_for_empty_input(self) -> None:
        assert extract_article("") is None

    def test_returns_none_for_garbage_input(self) -> None:
        assert extract_article("not html at all just random text") is None

    def test_returns_none_for_minimal_html(self) -> None:
        html = "<html><body><p>Hi</p></body></html>"
        assert extract_article(html) is None

    def test_handles_malformed_html(self) -> None:
        html = "<html><body><div><p>Unclosed tags <b>bold <i>italic"
        result = extract_article(html)
        # Should not crash; may return None for insufficient content
        assert result is None or isinstance(result, ReadabilityResult)

    def test_url_resolves_relative_images(self, sample_article_html) -> None:
        result = extract_article(sample_article_html, url="https://example.com/blog/wasm")
        assert result is not None
        # Relative image paths should be resolved to absolute
        assert "https://example.com/images/wasm-architecture.png" in result.content

    def test_realistic_fixture_has_images(self, sample_article_html) -> None:
        result = extract_article(sample_article_html, url="https://example.com/blog/wasm")
        assert result is not None
        assert "<img" in result.content

    def test_strips_nav_and_sidebar(self, sample_article_html) -> None:
        result = extract_article(sample_article_html)
        assert result is not None
        # Navigation and sidebar content should be removed
        assert "Newsletter" not in result.content
        assert "Subscribe" not in result.content

    def test_preserves_code_blocks(self, sample_article_html) -> None:
        result = extract_article(sample_article_html)
        assert result is not None
        assert "fibonacci" in result.content

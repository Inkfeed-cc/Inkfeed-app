"""Tests for the sleepscreen output module.

Tests that require Playwright (rendering to BMP via headless Chromium)
are marked with ``@pytest.mark.playwright`` and skipped when Playwright
is not installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from inkfeed.archiver.base import Article, ArchiveResult, GroupResult
from inkfeed.config import Config, SleepscreenConfig
from inkfeed.output.base import IndexEntry
from inkfeed.output.sleepscreen import (
    SourceSummary,
    SleepscreenWriter,
    _html_to_plaintext,
    _safe,
)

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

playwright = pytest.mark.skipif(
    not _HAS_PLAYWRIGHT,
    reason="Playwright is not installed",
)


def _make_article(title: str = "Test Article", **kwargs) -> Article:
    defaults = {
        "title": title,
        "author": "testuser",
        "source_url": "https://example.com",
        "content_html": "<p>Hello world</p>",
        "snapshot_date": datetime(2026, 2, 17, tzinfo=timezone.utc),
        "publish_date": datetime(2026, 2, 17, 10, 30, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return Article(**defaults)


def _make_config(**ss_overrides) -> Config:
    ss_defaults = dict(
        width=480,
        height=800,
        spotlight_count=2,
        max_headlines_per_card=10,
        max_excerpt_chars=350,
    )
    ss_defaults.update(ss_overrides)
    return Config(
        output_dir=Path("output"),
        sources=[],
        sleepscreen=SleepscreenConfig(**ss_defaults),
    )


# ------------------------------------------------------------------
# HTML-to-plaintext helper (no Playwright needed)
# ------------------------------------------------------------------


class TestHtmlToPlaintext:
    def test_strips_tags(self) -> None:
        assert _html_to_plaintext("<p>Hello <b>world</b></p>") == "Hello world"

    def test_strips_script_and_style(self) -> None:
        html = "<style>body{}</style><p>Keep</p><script>alert(1)</script>"
        assert _html_to_plaintext(html) == "Keep"

    def test_truncates_with_ellipsis(self) -> None:
        text = "word " * 100  # 500 chars
        result = _html_to_plaintext(f"<p>{text}</p>", max_chars=50)
        assert len(result) <= 55  # 50 + room for last word + ellipsis
        assert result.endswith("\u2026")

    def test_short_text_no_ellipsis(self) -> None:
        result = _html_to_plaintext("<p>Short text</p>", max_chars=200)
        assert result == "Short text"
        assert "\u2026" not in result

    def test_collapses_whitespace(self) -> None:
        html = "<p>  lots   of   spaces  </p>"
        assert _html_to_plaintext(html) == "lots of spaces"

    def test_empty_html(self) -> None:
        assert _html_to_plaintext("") == ""


# ------------------------------------------------------------------
# _safe filename helper
# ------------------------------------------------------------------


class TestSafe:
    def test_simple_name(self) -> None:
        assert _safe("Hacker News") == "hacker-news"

    def test_special_chars(self) -> None:
        assert _safe("Kagi News / Tech") == "kagi-news---tech"


# ------------------------------------------------------------------
# SleepscreenWriter unit tests (no Playwright)
# ------------------------------------------------------------------


class TestSleepscreenWriterUnit:
    def test_attributes(self) -> None:
        config = _make_config()
        w = SleepscreenWriter(config)
        assert w.name == "sleepscreen"
        assert w.ext == ".bmp"
        assert w.needs_images is False
        assert w.flat_output is True

    def test_group_entry_has_empty_rel_link(self) -> None:
        config = _make_config()
        w = SleepscreenWriter(config)
        group = MagicMock()
        group.display_name = "Hacker News"
        group.articles = [_make_article()]
        entry = w._group_entry(group, "2026-02-17")
        assert entry.rel_link == ""
        assert entry.article_count == 1

    def test_counter_increments(self) -> None:
        config = _make_config()
        w = SleepscreenWriter(config)
        w._counter = 1
        assert w._next_index() == 1
        assert w._next_index() == 2
        assert w._next_index() == 3

    def test_setup_raises_without_playwright(self) -> None:
        if _HAS_PLAYWRIGHT:
            pytest.skip("Playwright is installed; cannot test ImportError path")
        config = _make_config()
        w = SleepscreenWriter(config)
        with pytest.raises(ImportError, match="Playwright"):
            w.setup()


# ------------------------------------------------------------------
# SleepscreenWriter integration tests (require Playwright)
# ------------------------------------------------------------------


@playwright
class TestSleepscreenWriterIntegration:
    @pytest.fixture()
    def writer(self, tmp_path: Path):
        config = _make_config()
        w = SleepscreenWriter(config)
        w.setup()
        yield w
        w.teardown()

    def test_render_title_card(self, writer: SleepscreenWriter, tmp_path: Path) -> None:
        entries = [
            IndexEntry("Hacker News", "", 30),
            IndexEntry("Kagi News", "", 15),
        ]
        writer.write_date_index(tmp_path, "2026-02-17", entries)
        title_path = tmp_path / "00-inkfeed-title.bmp"
        assert title_path.exists()

        from PIL import Image
        img = Image.open(title_path)
        assert img.mode == "L"
        assert img.size == (480, 800)

    def test_render_headlines_and_spotlights(
        self, writer: SleepscreenWriter, tmp_path: Path,
    ) -> None:
        articles = [_make_article(f"Story {i}") for i in range(5)]
        group = GroupResult(
            display_name="Test Source",
            rel_path="test-source",
            cache_dir=tmp_path / "cache",
            articles=articles,
        )
        (tmp_path / "cache").mkdir()
        writer.write_group(group, tmp_path)

        bmp_files = sorted(tmp_path.glob("*.bmp"))
        # 1 headlines + 2 spotlights (default spotlight_count=2)
        assert len(bmp_files) == 3

    def test_zero_spotlight_count(self, tmp_path: Path) -> None:
        config = _make_config(spotlight_count=0)
        w = SleepscreenWriter(config)
        w.setup()
        try:
            articles = [_make_article("Only Story")]
            group = GroupResult(
                display_name="src",
                rel_path="src",
                cache_dir=tmp_path / "cache",
                articles=articles,
            )
            (tmp_path / "cache").mkdir()
            w.write_group(group, tmp_path)

            bmp_files = list(tmp_path.glob("*.bmp"))
            assert len(bmp_files) == 1  # headlines only
        finally:
            w.teardown()

    def test_empty_articles_no_output(
        self, writer: SleepscreenWriter, tmp_path: Path,
    ) -> None:
        group = GroupResult(
            display_name="src",
            rel_path="src",
            cache_dir=tmp_path / "cache",
            articles=[],
        )
        (tmp_path / "cache").mkdir()
        writer.write_group(group, tmp_path)
        bmp_files = list(tmp_path.glob("*.bmp"))
        assert len(bmp_files) == 0

    def test_sequential_numbering(
        self, writer: SleepscreenWriter, tmp_path: Path,
    ) -> None:
        articles_a = [_make_article("A1"), _make_article("A2")]
        articles_b = [_make_article("B1")]

        group_a = GroupResult(
            display_name="source_a", rel_path="source_a",
            cache_dir=tmp_path / "ca", articles=articles_a,
        )
        group_b = GroupResult(
            display_name="source_b", rel_path="source_b",
            cache_dir=tmp_path / "cb", articles=articles_b,
        )
        (tmp_path / "ca").mkdir()
        (tmp_path / "cb").mkdir()

        writer.write_group(group_a, tmp_path)
        writer.write_group(group_b, tmp_path)

        bmp_files = sorted(f.name for f in tmp_path.glob("*.bmp"))
        # source_a: 01-headlines, 02-spotlight-0, 03-spotlight-1
        # source_b: 04-headlines, 05-spotlight-0
        assert len(bmp_files) == 5
        assert bmp_files[0].startswith("01-")
        assert bmp_files[-1].startswith("05-")

    def test_all_files_are_grayscale_bmp(
        self, writer: SleepscreenWriter, tmp_path: Path,
    ) -> None:
        articles = [_make_article(f"Story {i}") for i in range(3)]
        group = GroupResult(
            display_name="src", rel_path="src",
            cache_dir=tmp_path / "cache", articles=articles,
        )
        (tmp_path / "cache").mkdir()
        writer.write_group(group, tmp_path)

        from PIL import Image
        for bmp in tmp_path.glob("*.bmp"):
            img = Image.open(bmp)
            assert img.mode == "L", f"{bmp.name} has mode {img.mode}"
            assert img.size == (480, 800), f"{bmp.name} has size {img.size}"

    def test_custom_dimensions(self, tmp_path: Path) -> None:
        config = _make_config(width=320, height=240, spotlight_count=0)
        w = SleepscreenWriter(config)
        w.setup()
        try:
            articles = [_make_article()]
            group = GroupResult(
                display_name="src", rel_path="src",
                cache_dir=tmp_path / "cache", articles=articles,
            )
            (tmp_path / "cache").mkdir()
            w.write_group(group, tmp_path)

            from PIL import Image
            img = Image.open(list(tmp_path.glob("*.bmp"))[0])
            assert img.size == (320, 240)
        finally:
            w.teardown()

    def test_grayscale_quantization(
        self, writer: SleepscreenWriter, tmp_path: Path,
    ) -> None:
        """Saved BMP should only contain pixel values from {0, 85, 170, 255}."""
        entries = [IndexEntry("Test", "", 5)]
        writer.write_date_index(tmp_path, "2026-02-17", entries)

        from PIL import Image
        img = Image.open(tmp_path / "00-inkfeed-title.bmp")
        pixels = img.tobytes()
        unique_values = set(pixels)
        allowed = {0, 85, 170, 255}
        assert unique_values.issubset(allowed), (
            f"Found unexpected gray values: {unique_values - allowed}"
        )

    def test_spotlight_with_rich_content(
        self, writer: SleepscreenWriter, tmp_path: Path,
    ) -> None:
        article = _make_article(
            title="Breakthrough Discovery",
            content_html=(
                "<p>Scientists at CERN have announced a groundbreaking finding "
                "that challenges our understanding of particle physics.</p>"
            ),
        )
        group = GroupResult(
            display_name="Science", rel_path="science",
            cache_dir=tmp_path / "cache", articles=[article],
        )
        (tmp_path / "cache").mkdir()
        writer.write_group(group, tmp_path)
        bmp_files = list(tmp_path.glob("*.bmp"))
        assert len(bmp_files) >= 2  # headlines + spotlight

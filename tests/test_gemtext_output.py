from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from inkfeed.archiver.base import Article
from inkfeed.output.gemtext import write_gemtext, html_to_gemtext


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


class TestHtmlToGemtext:
    def test_plain_paragraph(self) -> None:
        result = html_to_gemtext("<p>Hello world</p>")
        assert "Hello world" in result

    def test_headings(self) -> None:
        result = html_to_gemtext("<h1>Title</h1><h2>Sub</h2><h3>SubSub</h3>")
        assert "# Title" in result
        assert "## Sub" in result
        assert "### SubSub" in result

    def test_link_becomes_gemini_link(self) -> None:
        result = html_to_gemtext('<a href="https://example.com">Example</a>')
        assert "=> https://example.com Example" in result

    def test_image_becomes_link(self) -> None:
        result = html_to_gemtext('<img src="images/photo.jpg" alt="A photo">')
        assert "=> images/photo.jpg A photo" in result

    def test_blockquote(self) -> None:
        result = html_to_gemtext("<blockquote>Quoted text</blockquote>")
        assert "> Quoted text" in result

    def test_preformatted(self) -> None:
        result = html_to_gemtext("<pre>code here</pre>")
        assert "```" in result
        assert "code here" in result

    def test_strips_inline_formatting(self) -> None:
        result = html_to_gemtext("<p><b>bold</b> and <i>italic</i></p>")
        assert "bold and italic" in result
        assert "<b>" not in result
        assert "<i>" not in result

    def test_no_html_tags_leak(self) -> None:
        html = '<div class="story-meta"><span>100 points</span></div>'
        result = html_to_gemtext(html)
        assert "<" not in result

    def test_script_and_style_stripped(self) -> None:
        html = "<style>body{}</style><script>alert(1)</script><p>visible</p>"
        result = html_to_gemtext(html)
        assert "visible" in result
        assert "alert" not in result
        assert "body{}" not in result


class TestWriteGemtext:
    def test_creates_index_gmi(self, tmp_path: Path) -> None:
        articles = [_make_article("First"), _make_article("Second")]
        write_gemtext("test_source", articles, tmp_path)

        index = tmp_path / "index.gmi"
        assert index.exists()
        content = index.read_text()
        assert "test_source" in content
        assert "First" in content
        assert "Second" in content

    def test_creates_article_gmi_files(self, tmp_path: Path) -> None:
        articles = [_make_article("My Article")]
        write_gemtext("test_source", articles, tmp_path)

        gmi_files = list(tmp_path.glob("*.gmi"))
        assert len(gmi_files) == 2  # index + 1 article
        article_files = [f for f in gmi_files if f.name != "index.gmi"]
        assert len(article_files) == 1

    def test_article_file_contains_content(self, tmp_path: Path) -> None:
        articles = [_make_article(content_html="<p>Article body here</p>")]
        write_gemtext("test_source", articles, tmp_path)

        article_files = [f for f in tmp_path.glob("*.gmi") if f.name != "index.gmi"]
        content = article_files[0].read_text()
        assert "Article body here" in content
        assert "testuser" in content

    def test_article_has_source_link(self, tmp_path: Path) -> None:
        articles = [_make_article()]
        write_gemtext("test_source", articles, tmp_path)

        article_files = [f for f in tmp_path.glob("*.gmi") if f.name != "index.gmi"]
        content = article_files[0].read_text()
        assert "=> https://example.com" in content

    def test_index_has_links_to_articles(self, tmp_path: Path) -> None:
        articles = [_make_article("First"), _make_article("Second")]
        write_gemtext("test_source", articles, tmp_path)

        index_content = (tmp_path / "index.gmi").read_text()
        assert "=>" in index_content
        assert ".gmi" in index_content

    def test_snapshot_date_in_index(self, tmp_path: Path) -> None:
        articles = [_make_article()]
        write_gemtext("test_source", articles, tmp_path)

        content = (tmp_path / "index.gmi").read_text()
        assert "2026-02-16" in content

    def test_no_html_tags_in_output(self, tmp_path: Path) -> None:
        articles = [_make_article(content_html="<p>Plain <b>text</b> here</p>")]
        write_gemtext("test_source", articles, tmp_path)

        article_files = [f for f in tmp_path.glob("*.gmi") if f.name != "index.gmi"]
        content = article_files[0].read_text()
        assert "<p>" not in content
        assert "<b>" not in content

    def test_multiple_articles_numbered(self, tmp_path: Path) -> None:
        articles = [_make_article(f"Article {i}") for i in range(5)]
        write_gemtext("test_source", articles, tmp_path)

        gmi_files = list(tmp_path.glob("*.gmi"))
        assert len(gmi_files) == 6  # index + 5 articles

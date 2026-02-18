from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
import ebooklib
from ebooklib import epub
from PIL import Image

from inkfeed.archiver.base import Article
from inkfeed.output.epub import write_epub, _normalize_image


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


class TestWriteEpub:
    def test_creates_epub_file(self, tmp_path: Path) -> None:
        articles = [_make_article("First"), _make_article("Second")]
        write_epub("test_source", articles, tmp_path)

        epub_files = list(tmp_path.glob("*.epub"))
        assert len(epub_files) == 1
        assert "test_source" in epub_files[0].name

    def test_epub_filename_contains_date(self, tmp_path: Path) -> None:
        articles = [_make_article()]
        write_epub("test_source", articles, tmp_path)

        epub_files = list(tmp_path.glob("*.epub"))
        assert "2026-02-16" in epub_files[0].name

    def test_epub_is_readable(self, tmp_path: Path) -> None:
        articles = [_make_article("First"), _make_article("Second")]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        assert book is not None

    def test_epub_contains_chapters(self, tmp_path: Path) -> None:
        articles = [_make_article("First"), _make_article("Second")]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        html_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_DOCUMENT
        ]
        assert len(html_items) >= 2

    def test_epub_chapter_contains_content(self, tmp_path: Path) -> None:
        articles = [_make_article(content_html="<p>Unique content here</p>")]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        html_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_DOCUMENT
        ]
        all_content = "".join(
            item.get_content().decode("utf-8", errors="replace") for item in html_items
        )
        assert "Unique content here" in all_content

    def test_epub_has_title(self, tmp_path: Path) -> None:
        articles = [_make_article()]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        title = book.get_metadata("DC", "title")
        assert len(title) > 0

    def test_epub_bundles_local_images(self, tmp_path: Path) -> None:
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        img = Image.new("RGB", (4, 4), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        (img_dir / "test123.png").write_bytes(buf.getvalue())

        html = '<p><img src="images/test123.png" alt="test"></p>'
        articles = [_make_article(content_html=html)]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        image_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_IMAGE
        ]
        assert len(image_items) >= 1

    def test_multiple_articles(self, tmp_path: Path) -> None:
        articles = [_make_article(f"Article {i}") for i in range(5)]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        html_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_DOCUMENT
        ]
        assert len(html_items) >= 5

    def test_epub_converts_webp_to_png(self, tmp_path: Path) -> None:
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        img = Image.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        img.save(buf, format="WEBP")
        (img_dir / "photo.webp").write_bytes(buf.getvalue())

        html = '<p><img src="images/photo.webp" alt="photo"></p>'
        articles = [_make_article(content_html=html)]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        image_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_IMAGE
        ]
        assert len(image_items) == 1
        assert image_items[0].media_type == "image/png"
        assert image_items[0].file_name.endswith(".png")

    def test_epub_handles_bin_extension(self, tmp_path: Path) -> None:
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        img = Image.new("RGB", (10, 10), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        (img_dir / "mystery.bin").write_bytes(buf.getvalue())

        html = '<p><img src="images/mystery.bin" alt="pic"></p>'
        articles = [_make_article(content_html=html)]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        image_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_IMAGE
        ]
        assert len(image_items) == 1
        assert image_items[0].media_type == "image/jpeg"

    def test_epub_passes_through_jpeg_unchanged(self, tmp_path: Path) -> None:
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        img = Image.new("RGB", (10, 10), color="green")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()
        (img_dir / "photo.jpg").write_bytes(jpeg_bytes)

        html = '<p><img src="images/photo.jpg" alt="pic"></p>'
        articles = [_make_article(content_html=html)]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        image_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_IMAGE
        ]
        assert len(image_items) == 1
        assert image_items[0].media_type == "image/jpeg"
        assert image_items[0].get_content() == jpeg_bytes

    def test_epub_skips_unrecognizable_files(self, tmp_path: Path) -> None:
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        (img_dir / "garbage.bin").write_bytes(b"not an image at all")

        html = '<p><img src="images/garbage.bin" alt="bad"></p>'
        articles = [_make_article(content_html=html)]
        write_epub("test_source", articles, tmp_path)

        epub_file = list(tmp_path.glob("*.epub"))[0]
        book = epub.read_epub(str(epub_file))
        image_items = [
            item for item in book.get_items()
            if item.get_type() == ebooklib.ITEM_IMAGE
        ]
        assert len(image_items) == 0


class TestNormalizeImage:
    def test_jpeg_passthrough(self) -> None:
        img = Image.new("RGB", (4, 4), color="red")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        data = buf.getvalue()

        result = _normalize_image(data)
        assert result is not None
        content, mime, ext = result
        assert mime == "image/jpeg"
        assert ext == ".jpg"
        assert content == data

    def test_png_passthrough(self) -> None:
        img = Image.new("RGBA", (4, 4), color=(0, 0, 255, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()

        result = _normalize_image(data)
        assert result is not None
        content, mime, ext = result
        assert mime == "image/png"
        assert ext == ".png"
        assert content == data

    def test_gif_passthrough(self) -> None:
        img = Image.new("P", (4, 4))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        data = buf.getvalue()

        result = _normalize_image(data)
        assert result is not None
        content, mime, ext = result
        assert mime == "image/gif"
        assert ext == ".gif"
        assert content == data

    def test_webp_converted_to_png(self) -> None:
        img = Image.new("RGB", (4, 4), color="green")
        buf = io.BytesIO()
        img.save(buf, format="WEBP")
        data = buf.getvalue()

        result = _normalize_image(data)
        assert result is not None
        content, mime, ext = result
        assert mime == "image/png"
        assert ext == ".png"
        assert content != data
        # Verify the output is valid PNG
        out_img = Image.open(io.BytesIO(content))
        assert out_img.format == "PNG"

    def test_bmp_converted_to_png(self) -> None:
        img = Image.new("RGB", (4, 4), color="yellow")
        buf = io.BytesIO()
        img.save(buf, format="BMP")
        data = buf.getvalue()

        result = _normalize_image(data)
        assert result is not None
        _, mime, ext = result
        assert mime == "image/png"
        assert ext == ".png"

    def test_webp_with_transparency_preserved(self) -> None:
        img = Image.new("RGBA", (4, 4), color=(255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="WEBP")
        data = buf.getvalue()

        result = _normalize_image(data)
        assert result is not None
        content, mime, _ = result
        assert mime == "image/png"
        out_img = Image.open(io.BytesIO(content))
        assert out_img.mode == "RGBA"

    def test_svg_passthrough(self) -> None:
        svg_data = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
        result = _normalize_image(svg_data)
        assert result is not None
        content, mime, ext = result
        assert mime == "image/svg+xml"
        assert ext == ".svg"
        assert content == svg_data

    def test_svg_with_xml_declaration(self) -> None:
        svg_data = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
        result = _normalize_image(svg_data)
        assert result is not None
        _, mime, ext = result
        assert mime == "image/svg+xml"
        assert ext == ".svg"

    def test_garbage_returns_none(self) -> None:
        assert _normalize_image(b"not an image") is None

    def test_empty_returns_none(self) -> None:
        assert _normalize_image(b"") is None

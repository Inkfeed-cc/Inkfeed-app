from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from inkfeed.archiver.base import ArchiveResult, Article, GroupResult
from inkfeed.config import Config, SleepscreenConfig, SourceConfig
from inkfeed.main import main, _run_source
from inkfeed.output.base import FormatWriter, IndexEntry


def _mock_archive_result(
    source_name: str,
    articles: list[Article] | list[MagicMock],
    cache_dir: Path,
) -> ArchiveResult:
    """Build a single-group ArchiveResult for use in tests."""
    return ArchiveResult(
        source_name=source_name,
        groups=[GroupResult(
            display_name=source_name,
            rel_path=source_name,
            cache_dir=cache_dir,
            articles=articles,
        )],
    )


def _mock_multi_group_result(
    source_name: str,
    groups: list[tuple[str, str, Path, list]],
) -> ArchiveResult:
    """Build a multi-group ArchiveResult for use in tests."""
    return ArchiveResult(
        source_name=source_name,
        groups=[
            GroupResult(
                display_name=name,
                rel_path=rel_path,
                cache_dir=cache_dir,
                articles=articles,
            )
            for name, rel_path, cache_dir, articles in groups
        ],
    )


def _make_mock_article() -> MagicMock:
    mock_article = MagicMock(spec=Article)
    mock_article.content_html = "<p>test</p>"
    mock_article.title = "Test"
    mock_article.author = "user"
    mock_article.source_url = "https://example.com"
    mock_article.snapshot_date = MagicMock()
    mock_article.snapshot_date.strftime.return_value = "2026-02-16"
    mock_article.publish_date = None
    return mock_article


def _make_mock_writer(name: str = "html") -> MagicMock:
    """Create a mock FormatWriter instance."""
    w = MagicMock(spec=FormatWriter)
    w.name = name
    w.write_source.return_value = [
        IndexEntry(display_name="test", rel_link="test/index.html", article_count=1),
    ]
    return w


class TestRunSource:
    def test_run_source_calls_archiver_and_writer(self, tmp_path: Path) -> None:
        source = SourceConfig(
            name="hackernews", type="api", frequency="daily", enabled=True,
            params={"top_stories": 1, "include_comments": False},
        )

        mock_article = _make_mock_article()
        mock_archiver_cls = MagicMock()
        mock_archiver_instance = mock_archiver_cls.return_value
        mock_archiver_instance.run.return_value = _mock_archive_result(
            "hackernews", [mock_article], tmp_path,
        )

        mock_writer = _make_mock_writer("html")
        with patch("inkfeed.main.download_images", return_value="<p>test</p>"):
            _run_source(
                source, mock_archiver_cls, tmp_path,
                date_str="2026-02-16", writers=[mock_writer],
            )

        mock_archiver_cls.assert_called_once_with(source, tmp_path)
        mock_archiver_instance.run.assert_called_once()
        mock_writer.write_source.assert_called_once()

    def test_run_source_always_downloads_images(self, tmp_path: Path) -> None:
        source = SourceConfig(
            name="hackernews", type="api", frequency="daily", enabled=True,
            params={"top_stories": 1, "include_comments": False},
        )

        mock_article = _make_mock_article()
        mock_archiver_cls = MagicMock()
        mock_archiver_instance = mock_archiver_cls.return_value
        mock_archiver_instance.run.return_value = _mock_archive_result(
            "hackernews", [mock_article], tmp_path,
        )

        mock_writer = _make_mock_writer("html")
        with patch("inkfeed.main.download_images", return_value="<p>test</p>") as mock_dl:
            _run_source(
                source, mock_archiver_cls, tmp_path,
                date_str="2026-02-16", writers=[mock_writer],
            )

        mock_dl.assert_called_once()

    def test_run_source_calls_multiple_writers(self, tmp_path: Path) -> None:
        source = SourceConfig(
            name="hackernews", type="api", frequency="daily", enabled=True,
            params={"top_stories": 1, "include_comments": False},
        )

        mock_article = _make_mock_article()
        mock_archiver_cls = MagicMock()
        mock_archiver_instance = mock_archiver_cls.return_value
        mock_archiver_instance.run.return_value = _mock_archive_result(
            "hackernews", [mock_article], tmp_path,
        )

        writers = [
            _make_mock_writer("html"),
            _make_mock_writer("md"),
            _make_mock_writer("gemtext"),
            _make_mock_writer("epub"),
        ]
        with patch("inkfeed.main.download_images", return_value="<p>test</p>"):
            _run_source(
                source, mock_archiver_cls, tmp_path,
                date_str="2026-02-16", writers=writers,
            )

        for w in writers:
            w.write_source.assert_called_once()

    def test_run_source_catches_exceptions(self, tmp_path: Path) -> None:
        source = SourceConfig(
            name="broken", type="api", frequency="daily", enabled=True, params={},
        )
        mock_archiver_cls = MagicMock()
        mock_archiver_cls.return_value.run.side_effect = RuntimeError("boom")

        mock_writer = _make_mock_writer("html")
        _run_source(
            source, mock_archiver_cls, tmp_path,
            date_str="2026-02-16", writers=[mock_writer],
        )

    def test_run_source_returns_index_entries(self, tmp_path: Path) -> None:
        source = SourceConfig(
            name="hackernews", type="api", frequency="daily", enabled=True,
            params={"top_stories": 1, "include_comments": False},
        )

        mock_article = _make_mock_article()
        mock_archiver_cls = MagicMock()
        mock_archiver_instance = mock_archiver_cls.return_value
        mock_archiver_instance.run.return_value = _mock_archive_result(
            "hackernews", [mock_article], tmp_path,
        )

        expected_entries = [
            IndexEntry("hackernews", "hackernews/index.html", 1),
        ]
        mock_writer = _make_mock_writer("html")
        mock_writer.write_source.return_value = expected_entries

        with patch("inkfeed.main.download_images", return_value="<p>test</p>"):
            result = _run_source(
                source, mock_archiver_cls, tmp_path,
                date_str="2026-02-16", writers=[mock_writer],
            )

        assert "html" in result
        assert len(result["html"]) == 1
        entry = result["html"][0]
        assert entry.display_name == "hackernews"
        assert entry.article_count == 1


class TestMain:
    def test_skips_disabled_sources(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[sources.hackernews]
type = "api"
enabled = false
""")
        with patch("inkfeed.main._resolve_config_path", return_value=config_file), \
             patch("inkfeed.main._run_source", return_value={}) as mock_run:
            main()

        mock_run.assert_not_called()

    def test_skips_unknown_archivers(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[sources.unknown_source]
type = "api"
enabled = true
""")
        with patch("inkfeed.main._resolve_config_path", return_value=config_file), \
             patch("inkfeed.main._run_source", return_value={}) as mock_run:
            main()

        mock_run.assert_not_called()

    def test_runs_enabled_known_sources(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[sources.hackernews]
type = "api"
enabled = true
top_stories = 5
""")
        with patch("inkfeed.main._resolve_config_path", return_value=config_file), \
             patch("inkfeed.main._run_source", return_value={}) as mock_run:
            main()

        mock_run.assert_called_once()
        source_arg = mock_run.call_args[0][0]
        assert source_arg.name == "hackernews"

    def test_passes_writers_to_run_source(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
output_formats = ["html", "md"]

[sources.hackernews]
type = "api"
enabled = true
top_stories = 5
""")
        with patch("inkfeed.main._resolve_config_path", return_value=config_file), \
             patch("inkfeed.main._run_source", return_value={}) as mock_run:
            main()

        mock_run.assert_called_once()
        writers = mock_run.call_args[1]["writers"]
        assert len(writers) == 2
        names = {w.name for w in writers}
        assert names == {"html", "md"}

    def test_passes_date_str_to_run_source(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[sources.hackernews]
type = "api"
enabled = true
top_stories = 5
""")
        with patch("inkfeed.main._resolve_config_path", return_value=config_file), \
             patch("inkfeed.main._run_source", return_value={}) as mock_run:
            main()

        mock_run.assert_called_once()
        assert "date_str" in mock_run.call_args[1]

    def test_writer_teardown_called(self, tmp_path: Path) -> None:
        """Verify that teardown is called on all writers after processing."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[sources.hackernews]
type = "api"
enabled = true
top_stories = 5
""")
        mock_writer = MagicMock(spec=FormatWriter)
        mock_writer.name = "html"
        mock_cls = MagicMock(return_value=mock_writer)

        with patch("inkfeed.main._resolve_config_path", return_value=config_file), \
             patch.dict("inkfeed.main.WRITER_MAP", {"html": mock_cls}), \
             patch("inkfeed.main._run_source", return_value={}):
            main()

        mock_writer.setup.assert_called_once()
        mock_writer.teardown.assert_called_once()


class TestWriterDateIndices:
    """Test the write_date_index / write_source_index methods of each writer."""

    def _make_config(self) -> Config:
        return Config(
            output_dir=Path("output"),
            sources=[],
        )

    def test_html_date_index(self, tmp_path: Path) -> None:
        from inkfeed.output.html import HtmlWriter

        w = HtmlWriter(self._make_config())
        entries = [
            IndexEntry("hackernews", "hackernews/index.html", 30),
            IndexEntry("kaginews", "kaginews/index.html", 15),
        ]
        w.write_date_index(tmp_path, "2026-02-16", entries)

        index_path = tmp_path / "index.html"
        assert index_path.exists()
        content = index_path.read_text()
        assert "Inkfeed" in content
        assert "2026-02-16" in content
        assert "hackernews" in content
        assert "kaginews" in content
        assert "30 articles" in content
        assert "15 articles" in content

    def test_markdown_date_index(self, tmp_path: Path) -> None:
        from inkfeed.output.markdown import MarkdownWriter

        w = MarkdownWriter(self._make_config())
        entries = [
            IndexEntry("hackernews", "hackernews/index.md", 10),
        ]
        w.write_date_index(tmp_path, "2026-02-16", entries)

        index_path = tmp_path / "index.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "# Inkfeed" in content
        assert "hackernews" in content
        assert "hackernews/index.md" in content

    def test_gemtext_date_index(self, tmp_path: Path) -> None:
        from inkfeed.output.gemtext import GemtextWriter

        w = GemtextWriter(self._make_config())
        entries = [
            IndexEntry("hackernews", "hackernews/index.gmi", 5),
        ]
        w.write_date_index(tmp_path, "2026-02-16", entries)

        index_path = tmp_path / "index.gmi"
        assert index_path.exists()
        content = index_path.read_text()
        assert "# Inkfeed" in content
        assert "=> hackernews/index.gmi" in content

    def test_epub_date_index_as_html(self, tmp_path: Path) -> None:
        from inkfeed.output.epub import EpubWriter

        w = EpubWriter(self._make_config())
        entries = [
            IndexEntry("hackernews", "hackernews/hackernews-2026-02-16.epub", 20),
        ]
        w.write_date_index(tmp_path, "2026-02-16", entries)

        index_path = tmp_path / "index.html"
        assert index_path.exists()
        content = index_path.read_text()
        assert "hackernews" in content
        assert ".epub" in content

    def test_html_source_index(self, tmp_path: Path) -> None:
        from inkfeed.output.html import HtmlWriter

        w = HtmlWriter(self._make_config())
        children = [
            IndexEntry("Technology", "tech/index.html", 15),
            IndexEntry("World", "world/index.html", 10),
        ]
        w.write_source_index(tmp_path, "kaginews", "2026-02-16", children)

        index_path = tmp_path / "index.html"
        assert index_path.exists()
        content = index_path.read_text()
        assert "kaginews" in content
        assert "Technology" in content
        assert "World" in content
        assert "tech/index.html" in content
        assert "world/index.html" in content

    def test_markdown_source_index(self, tmp_path: Path) -> None:
        from inkfeed.output.markdown import MarkdownWriter

        w = MarkdownWriter(self._make_config())
        children = [
            IndexEntry("Business", "business/index.md", 12),
            IndexEntry("Science", "science/index.md", 8),
        ]
        w.write_source_index(tmp_path, "kaginews", "2026-02-16", children)

        index_path = tmp_path / "index.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "# kaginews" in content
        assert "Business" in content
        assert "business/index.md" in content

    def test_gemtext_source_index(self, tmp_path: Path) -> None:
        from inkfeed.output.gemtext import GemtextWriter

        w = GemtextWriter(self._make_config())
        children = [
            IndexEntry("Tech", "tech/index.gmi", 10),
        ]
        w.write_source_index(tmp_path, "kaginews", "2026-02-16", children)

        index_path = tmp_path / "index.gmi"
        assert index_path.exists()
        content = index_path.read_text()
        assert "=> tech/index.gmi" in content


class TestFormatWriterWriteSource:
    """Test the base FormatWriter.write_source method via concrete writers."""

    def _make_config(self, **overrides) -> Config:
        defaults = dict(
            output_dir=Path("output"),
            sources=[],
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_single_group_returns_flat_entries(self, tmp_path: Path) -> None:
        from inkfeed.output.html import HtmlWriter

        config = self._make_config()
        w = HtmlWriter(config)

        mock_article = _make_mock_article()
        result = _mock_archive_result("hackernews", [mock_article], tmp_path)

        with patch.object(w, "write_group"):
            entries = w.write_source(result, tmp_path, "2026-02-16")

        assert len(entries) == 1
        assert entries[0].display_name == "hackernews"
        assert entries[0].children is None

    def test_multi_group_returns_parent_with_children(self, tmp_path: Path) -> None:
        from inkfeed.output.html import HtmlWriter

        config = self._make_config()
        w = HtmlWriter(config)

        art1, art2 = _make_mock_article(), _make_mock_article()
        cache_a, cache_b = tmp_path / "a", tmp_path / "b"
        cache_a.mkdir()
        cache_b.mkdir()

        result = _mock_multi_group_result("kaginews", [
            ("Technology", "tech", cache_a, [art1]),
            ("World", "world", cache_b, [art2]),
        ])

        with patch.object(w, "write_group"):
            entries = w.write_source(result, tmp_path, "2026-02-16")

        assert len(entries) == 1
        parent = entries[0]
        assert parent.display_name == "kaginews"
        assert parent.children is not None
        assert len(parent.children) == 2
        child_names = [c.display_name for c in parent.children]
        assert "Technology" in child_names
        assert "World" in child_names

    def test_epub_group_entry_uses_epub_filename(self, tmp_path: Path) -> None:
        from inkfeed.output.epub import EpubWriter

        config = self._make_config()
        w = EpubWriter(config)

        mock_article = _make_mock_article()
        result = _mock_archive_result("hackernews", [mock_article], tmp_path)

        with patch.object(w, "write_group"):
            entries = w.write_source(result, tmp_path, "2026-02-16")

        assert len(entries) == 1
        assert ".epub" in entries[0].rel_link

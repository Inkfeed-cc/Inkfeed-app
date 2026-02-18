from __future__ import annotations

from pathlib import Path

import pytest

from inkfeed.config import Config, SourceConfig, load_config


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text("""\
[general]
output_dir = "my_output"

[sources.hackernews]
type = "api"
frequency = "daily"
enabled = true
top_stories = 30
include_comments = true
max_comment_depth = 3

[sources.weather]
type = "api"
frequency = "daily"
enabled = false
location = "Tokyo"
""")
    return p


def test_load_config_output_dir(config_file: Path) -> None:
    config = load_config(config_file)
    assert config.output_dir == Path("my_output")


def test_load_config_source_count(config_file: Path) -> None:
    config = load_config(config_file)
    assert len(config.sources) == 2


def test_load_config_hackernews_source(config_file: Path) -> None:
    config = load_config(config_file)
    hn = next(s for s in config.sources if s.name == "hackernews")
    assert hn.type == "api"
    assert hn.frequency == "daily"
    assert hn.enabled is True
    assert hn.params["top_stories"] == 30
    assert hn.params["include_comments"] is True
    assert hn.params["max_comment_depth"] == 3


def test_load_config_disabled_source(config_file: Path) -> None:
    config = load_config(config_file)
    weather = next(s for s in config.sources if s.name == "weather")
    assert weather.enabled is False
    assert weather.params["location"] == "Tokyo"


def test_load_config_display_name(config_file: Path) -> None:
    config = load_config(config_file)
    hn = next(s for s in config.sources if s.name == "hackernews")
    assert hn.display_name == "hackernews"


def test_load_config_explicit_display_name(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("""\
[sources.hackernews]
type = "api"
display_name = "Hacker News"
""")
    config = load_config(p)
    src = config.sources[0]
    assert src.name == "hackernews"
    assert src.display_name == "Hacker News"


def test_load_config_defaults(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("""\
[sources.test]
type = "scrape"
""")
    config = load_config(p)
    src = config.sources[0]
    assert src.frequency == "daily"
    assert src.enabled is True
    assert config.output_dir == Path("output")


def test_load_config_no_sources(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("[general]\noutput_dir = \"out\"\n")
    config = load_config(p)
    assert config.sources == []


def test_load_config_output_formats(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("""\
[general]
output_formats = ["html", "md", "gemtext", "epub"]

[sources.test]
type = "api"
""")
    config = load_config(p)
    assert config.output_formats == ["html", "md", "gemtext", "epub"]


def test_load_config_output_formats_defaults_to_html(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("""\
[sources.test]
type = "api"
""")
    config = load_config(p)
    assert config.output_formats == ["html"]

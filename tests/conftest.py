from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def hn_top_stories() -> list[int]:
    return json.loads((FIXTURES_DIR / "hn_top_stories.json").read_text())


@pytest.fixture
def hn_stories() -> dict[str, dict]:
    return json.loads((FIXTURES_DIR / "hn_stories.json").read_text())


@pytest.fixture
def hn_comments() -> dict[str, dict]:
    return json.loads((FIXTURES_DIR / "hn_comments.json").read_text())


@pytest.fixture
def hn_algolia_items() -> dict[str, dict]:
    return json.loads((FIXTURES_DIR / "hn_algolia_items.json").read_text())


@pytest.fixture
def sample_article_html() -> str:
    return (FIXTURES_DIR / "sample_article.html").read_text()


@pytest.fixture
def kagi_batches() -> dict:
    return json.loads((FIXTURES_DIR / "kagi_batches.json").read_text())


@pytest.fixture
def kagi_categories() -> dict:
    return json.loads((FIXTURES_DIR / "kagi_categories.json").read_text())


@pytest.fixture
def kagi_stories_tech() -> dict:
    return json.loads((FIXTURES_DIR / "kagi_stories_tech.json").read_text())


@pytest.fixture
def kagi_stories_world() -> dict:
    return json.loads((FIXTURES_DIR / "kagi_stories_world.json").read_text())

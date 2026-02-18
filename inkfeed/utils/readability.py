from __future__ import annotations

import re
from dataclasses import dataclass

from readability import Document


@dataclass
class ReadabilityResult:
    title: str
    content: str
    short_title: str


def extract_article(html: str, url: str | None = None) -> ReadabilityResult | None:
    """Extract article content from raw HTML using Mozilla's Readability algorithm.

    Returns a ReadabilityResult with cleaned HTML content, or None if extraction
    fails or yields essentially empty content.
    """
    try:
        doc = Document(html, url=url)
        content = doc.summary()
        title = doc.title()
        short_title = doc.short_title()

        # Guard against empty extractions
        text_only = re.sub(r"<[^>]+>", "", content).strip()
        if len(text_only) < 50:
            return None

        return ReadabilityResult(
            title=title,
            content=content,
            short_title=short_title,
        )
    except Exception:
        return None

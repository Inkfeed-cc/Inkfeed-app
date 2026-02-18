from __future__ import annotations

import base64
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from inkfeed.utils.retry import with_retry

logger = logging.getLogger(__name__)

_IMG_PATTERN = re.compile(r'(<img\s[^>]*?)src=["\']([^"\']+)["\']', re.IGNORECASE)


def download_images(
    html: str,
    output_dir: Path,
    *,
    client: httpx.Client | None = None,
    max_workers: int = 8,
    max_retries: int = 3,
) -> str:
    """Download all images referenced in HTML and rewrite src paths to local files.

    Images are downloaded concurrently using a thread pool.
    Returns the HTML with rewritten image paths.
    """
    img_dir = output_dir / "images"

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=15, follow_redirects=True)

    try:
        # First pass: collect unique image URLs that need downloading.
        urls_to_download: list[str] = []
        seen: set[str] = set()
        for match in _IMG_PATTERN.finditer(html):
            url = match.group(2)
            if url.startswith("data:") or url.startswith("images/"):
                continue
            if url not in seen:
                seen.add(url)
                urls_to_download.append(url)

        if not urls_to_download:
            return html

        # Download all images concurrently.
        url_to_rel: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _download_single, url, img_dir, client,
                    max_retries=max_retries,
                ): url
                for url in urls_to_download
            }
            for future in as_completed(futures):
                url = futures[future]
                local_path = future.result()
                if local_path is not None:
                    url_to_rel[url] = f"images/{local_path.name}"

        # Second pass: rewrite src attributes with local paths.
        def replace_src(match: re.Match) -> str:
            prefix = match.group(1)
            url = match.group(2)
            rel_path = url_to_rel.get(url)
            if rel_path is not None:
                return f'{prefix}src="{rel_path}"'
            return match.group(0)

        return _IMG_PATTERN.sub(replace_src, html)
    finally:
        if own_client:
            client.close()


def embed_images(html: str, *, client: httpx.Client | None = None) -> str:
    """Download all images referenced in HTML and embed as base64 data URIs.

    Returns the HTML with images inlined as data: URIs, making it fully
    self-contained with no external dependencies.
    """
    urls_seen: dict[str, str] = {}
    own_client = client is None

    if own_client:
        client = httpx.Client(timeout=15, follow_redirects=True)

    def replace_src(match: re.Match) -> str:
        prefix = match.group(1)
        url = match.group(2)

        if url.startswith("data:"):
            return match.group(0)

        if url in urls_seen:
            return f'{prefix}src="{urls_seen[url]}"'

        fetched = _fetch_image(url, client)
        if fetched:
            content, content_type = fetched
            mime = _mime_from_content_type(content_type) or "application/octet-stream"
            b64 = base64.b64encode(content).decode("ascii")
            data_uri = f"data:{mime};base64,{b64}"
            urls_seen[url] = data_uri
            return f'{prefix}src="{data_uri}"'

        return match.group(0)

    try:
        return _IMG_PATTERN.sub(replace_src, html)
    finally:
        if own_client:
            client.close()


def embed_local_images(html: str, output_dir: Path) -> str:
    """Replace local ``images/`` references with base64 data URIs.

    Operates on already-downloaded images so no network access is needed.
    """
    _EXT_TO_MIME: dict[str, str] = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }

    def replace_src(match: re.Match) -> str:
        prefix = match.group(1)
        src = match.group(2)

        if not src.startswith("images/"):
            return match.group(0)

        img_path = output_dir / src
        if not img_path.exists():
            return match.group(0)

        mime = _EXT_TO_MIME.get(img_path.suffix.lower(), "application/octet-stream")
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f'{prefix}src="data:{mime};base64,{b64}"'

    return _IMG_PATTERN.sub(replace_src, html)


def _fetch_image(
    url: str, client: httpx.Client, *, max_retries: int = 3,
) -> tuple[bytes, str] | None:
    """Fetch image bytes from a URL.

    Returns ``(content_bytes, content_type_header)`` on success, ``None`` on
    failure.
    """
    try:
        def _get() -> httpx.Response:
            resp = client.get(url)
            resp.raise_for_status()
            return resp

        resp = with_retry(_get, max_retries=max_retries)
        return resp.content, resp.headers.get("content-type", "")
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("Failed to download image %s: %s", url, exc)
        return None


def _download_single(
    url: str, img_dir: Path, client: httpx.Client, *, max_retries: int = 3,
) -> Path | None:
    fetched = _fetch_image(url, client, max_retries=max_retries)
    if fetched is None:
        return None

    content, content_type = fetched
    ext = _ext_from_content_type(content_type) or _ext_from_url(url) or ".bin"

    name_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    filename = f"{name_hash}{ext}"

    try:
        img_dir.mkdir(parents=True, exist_ok=True)
        path = img_dir / filename
        path.write_bytes(content)
        return path
    except OSError as exc:
        logger.debug("Failed to save image %s: %s", url, exc)
        return None


def _mime_from_content_type(ct: str) -> str | None:
    """Extract a clean MIME type from a content-type header value."""
    for mime in ("image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"):
        if mime in ct:
            return mime
    return None


def _ext_from_content_type(ct: str) -> str | None:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    for key, ext in mapping.items():
        if key in ct:
            return ext
    return None


def _ext_from_url(url: str) -> str | None:
    path = url.split("?")[0].split("#")[0]
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
        if path.lower().endswith(ext):
            return ext
    return None

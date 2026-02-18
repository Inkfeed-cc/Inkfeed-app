from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from inkfeed.utils.images import download_images, embed_images


def _mock_image_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "good-image.png" in url:
            return httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nfake",
                headers={"content-type": "image/png"},
            )
        if "good-image.jpg" in url:
            return httpx.Response(
                200,
                content=b"\xff\xd8\xff\xe0fake",
                headers={"content-type": "image/jpeg"},
            )
        if "no-content-type" in url:
            return httpx.Response(200, content=b"data", headers={})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_client() -> httpx.Client:
    return httpx.Client(transport=_mock_image_transport())


class TestDownloadImages:
    def test_rewrites_img_src_to_local(self, tmp_path: Path, mock_client) -> None:
        html = '<p><img src="https://example.com/good-image.png" alt="test"></p>'
        result = download_images(html, tmp_path, client=mock_client)

        assert 'src="images/' in result
        assert "example.com" not in result
        assert (tmp_path / "images").exists()
        assert len(list((tmp_path / "images").iterdir())) == 1

    def test_handles_multiple_images(self, tmp_path: Path, mock_client) -> None:
        html = (
            '<img src="https://example.com/good-image.png">'
            '<img src="https://example.com/good-image.jpg">'
        )
        result = download_images(html, tmp_path, client=mock_client)

        assert result.count('src="images/') == 2
        assert len(list((tmp_path / "images").iterdir())) == 2

    def test_deduplicates_same_url(self, tmp_path: Path, mock_client) -> None:
        html = (
            '<img src="https://example.com/good-image.png">'
            '<img src="https://example.com/good-image.png">'
        )
        result = download_images(html, tmp_path, client=mock_client)

        assert result.count('src="images/') == 2
        assert len(list((tmp_path / "images").iterdir())) == 1

    def test_skips_data_uris(self, tmp_path: Path, mock_client) -> None:
        html = '<img src="data:image/png;base64,abc123">'
        result = download_images(html, tmp_path, client=mock_client)
        assert "data:image/png" in result
        assert not (tmp_path / "images").exists()

    def test_skips_already_local_paths(self, tmp_path: Path, mock_client) -> None:
        html = '<img src="images/existing.png">'
        result = download_images(html, tmp_path, client=mock_client)
        assert 'src="images/existing.png"' in result

    def test_keeps_original_on_download_failure(self, tmp_path: Path, mock_client) -> None:
        html = '<img src="https://example.com/missing.png">'
        result = download_images(html, tmp_path, client=mock_client)
        assert "example.com/missing.png" in result

    def test_no_images_returns_unchanged(self, tmp_path: Path, mock_client) -> None:
        html = "<p>No images here</p>"
        result = download_images(html, tmp_path, client=mock_client)
        assert result == html

    def test_extension_from_content_type(self, tmp_path: Path, mock_client) -> None:
        html = '<img src="https://example.com/good-image.jpg">'
        download_images(html, tmp_path, client=mock_client)

        files = list((tmp_path / "images").iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".jpg"


class TestEmbedImages:
    def test_rewrites_img_src_to_data_uri(self, mock_client) -> None:
        html = '<p><img src="https://example.com/good-image.png" alt="test"></p>'
        result = embed_images(html, client=mock_client)

        assert 'src="data:image/png;base64,' in result
        assert "example.com" not in result

    def test_correct_mime_type_jpeg(self, mock_client) -> None:
        html = '<img src="https://example.com/good-image.jpg">'
        result = embed_images(html, client=mock_client)

        assert 'src="data:image/jpeg;base64,' in result

    def test_handles_multiple_images(self, mock_client) -> None:
        html = (
            '<img src="https://example.com/good-image.png">'
            '<img src="https://example.com/good-image.jpg">'
        )
        result = embed_images(html, client=mock_client)

        assert result.count("src=\"data:image/") == 2
        assert "data:image/png;base64," in result
        assert "data:image/jpeg;base64," in result

    def test_deduplicates_same_url(self, mock_client) -> None:
        html = (
            '<img src="https://example.com/good-image.png">'
            '<img src="https://example.com/good-image.png">'
        )
        result = embed_images(html, client=mock_client)

        assert result.count("src=\"data:image/png;base64,") == 2
        # Both should have identical data URIs
        import re
        uris = re.findall(r'src="(data:image/png;base64,[^"]+)"', result)
        assert len(uris) == 2
        assert uris[0] == uris[1]

    def test_skips_existing_data_uris(self, mock_client) -> None:
        html = '<img src="data:image/png;base64,abc123">'
        result = embed_images(html, client=mock_client)
        assert 'src="data:image/png;base64,abc123"' in result

    def test_keeps_original_on_download_failure(self, mock_client) -> None:
        html = '<img src="https://example.com/missing.png">'
        result = embed_images(html, client=mock_client)
        assert "example.com/missing.png" in result

    def test_no_images_returns_unchanged(self, mock_client) -> None:
        html = "<p>No images here</p>"
        result = embed_images(html, client=mock_client)
        assert result == html

    def test_base64_is_valid(self, mock_client) -> None:
        html = '<img src="https://example.com/good-image.png">'
        result = embed_images(html, client=mock_client)

        import base64
        import re
        match = re.search(r'data:image/png;base64,([^"]+)', result)
        assert match is not None
        decoded = base64.b64decode(match.group(1))
        assert decoded == b"\x89PNG\r\n\x1a\nfake"

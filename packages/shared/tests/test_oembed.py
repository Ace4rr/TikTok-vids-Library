import httpx
import pytest
from toklib_core.enums import ErrorKind
from toklib_core.oembed import MetadataError, OEmbedData, author_username_from_url, fetch_oembed

OK_BODY = {
    "title": "рецепт пирога #recipes",
    "author_name": "Пекарь",
    "author_url": "https://www.tiktok.com/@baker",
    "thumbnail_url": "https://cdn.example/cover.jpg",
    "embed_html": "<blockquote></blockquote>",
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["url"] == "https://www.tiktok.com/@a/video/123"
        return httpx.Response(200, json=OK_BODY)

    data = fetch_oembed("https://www.tiktok.com/@a/video/123", client=_client(handler))
    assert isinstance(data, OEmbedData)
    assert data.author_name == "Пекарь"
    assert data.thumbnail_url == "https://cdn.example/cover.jpg"


def test_fetch_404_is_terminal():
    def handler(request):
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(MetadataError) as exc:
        fetch_oembed("https://www.tiktok.com/@a/video/404", client=_client(handler))
    assert exc.value.kind == ErrorKind.TERMINAL


def test_fetch_500_is_transient():
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(MetadataError) as exc:
        fetch_oembed("https://www.tiktok.com/@a/video/1", client=_client(handler))
    assert exc.value.kind == ErrorKind.TRANSIENT


def test_fetch_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("dns fail")

    with pytest.raises(MetadataError) as exc:
        fetch_oembed("https://www.tiktok.com/@a/video/1", client=_client(handler))
    assert exc.value.kind == ErrorKind.TRANSIENT


def test_fetch_bad_json_is_transient():
    def handler(request):
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(MetadataError) as exc:
        fetch_oembed("https://www.tiktok.com/@a/video/1", client=_client(handler))
    assert exc.value.kind == ErrorKind.TRANSIENT


def test_author_username_from_url():
    assert author_username_from_url("https://www.tiktok.com/@baker") == "baker"
    assert author_username_from_url("https://www.tiktok.com/@baker/video/1") == "baker"
    assert author_username_from_url(None) is None
    assert author_username_from_url("https://example.com/x") is None

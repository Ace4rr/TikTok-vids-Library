"""Аутентификация single-token (docs/05 §5.11) + Range-раздача файла."""


import pytest
from conftest import make_settings
from fastapi.testclient import TestClient
from toklib_core.db import Video


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    from conftest import _patch_resolve
    from toklib_api.main import create_app

    _patch_resolve(monkeypatch)
    application = create_app(make_settings(tmp_path, token="secret-token"))
    with TestClient(application) as test_client:
        yield test_client


def test_health_open_without_token(auth_client):
    assert auth_client.get("/api/health").status_code == 200


def test_api_requires_token(auth_client):
    assert auth_client.get("/api/videos").status_code == 401
    assert auth_client.get("/api/videos").status_code == 401


def test_bearer_token_accepted(auth_client):
    headers = {"Authorization": "Bearer secret-token"}
    assert auth_client.get("/api/videos", headers=headers).status_code == 200


def test_media_allows_query_token(auth_client, tmp_path):
    session = auth_client.app.state.ctx.session()
    file_path = tmp_path / "mediafile.mp4"
    file_path.write_bytes(b"0123456789abcdef")
    video = Video(url="u", tiktok_id="7305555555555555555", file_path=str(file_path), mime="video/mp4")
    session.add(video)
    session.commit()
    video_id = video.id
    session.close()

    base = f"/api/media/{video_id}/file"
    assert auth_client.get(base).status_code == 401
    assert auth_client.get(f"{base}?token=secret-token").status_code == 200

    partial = auth_client.get(f"{base}?token=secret-token", headers={"Range": "bytes=0-4"})
    assert partial.status_code == 206
    assert partial.content == b"01234"
    assert partial.headers["content-range"] == "bytes 0-4/16"

    tail = auth_client.get(f"{base}?token=secret-token", headers={"Range": "bytes=10-"})
    assert tail.status_code == 206
    assert tail.content == b"abcdef"

    bad = auth_client.get(f"{base}?token=secret-token", headers={"Range": "bytes=100-200"})
    assert bad.status_code == 416


def test_wrong_token_rejected(auth_client):
    headers = {"Authorization": "Bearer wrong"}
    assert auth_client.get("/api/videos", headers=headers).status_code == 401

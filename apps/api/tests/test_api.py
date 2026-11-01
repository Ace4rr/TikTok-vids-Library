"""Интеграционные тесты MVP-критериев (docs/03 §7): инбокс → карточка → поиск."""

import json

from conftest import add_video, run_stage
from toklib_core.enums import JobType


def test_health_open_without_auth(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_inbox_creates_card(client):
    resp = add_video(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["result"] == "created"
    assert body["video"]["status"] == "inbox"

    # дедуп FR-1.4: повторная ссылка → exists + счётчик
    resp2 = add_video(client)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["result"] == "exists"
    assert body2["video"]["save_count"] == 2


def test_inbox_rejects_non_tiktok(client):
    resp = client.post("/api/inbox", json={"text": "https://youtube.com/watch?v=1"})
    assert resp.status_code == 400


def test_full_pipeline_embed_mode(client, fake_llm):
    """Ссылка → карточка:Tier-0 после резолва, embed-fallback → ready (MVP-критерий 1,2)."""
    body = add_video(client).json()
    video_id = body["video_id"]

    assert run_stage(client.app, JobType.RESOLVE, video_id) == "resolved"
    assert run_stage(client.app, JobType.DOWNLOAD, video_id) == "downloaded"  # embed provider

    detail = client.get(f"/api/videos/{video_id}").json()
    assert detail["status"] == "ready"
    assert detail["media_mode"] == "embed"
    # Tier-0 словарные теги по подписи (FR-6.1)
    tag_names = [t["name"] for t in detail["tags"]]
    assert "рецепт" in tag_names
    assert detail["author_username"] == "user"
    assert detail["caption"].startswith("рецепт")


def test_pipeline_with_llm_tags_and_search(client, fake_llm):
    body = add_video(client).json()
    video_id = body["video_id"]
    run_stage(client.app, JobType.RESOLVE, video_id)
    run_stage(client.app, JobType.DOWNLOAD, video_id)

    # reanalyze (FR-6.9): LLM-теги поверх Tier-0
    resp = client.post(f"/api/videos/{video_id}/reanalyze", json={"force": True})
    assert resp.status_code == 202
    run_stage(client.app, JobType.ANALYZE, video_id, force=True)

    detail = client.get(f"/api/videos/{video_id}").json()
    tag_names = [t["name"] for t in detail["tags"]]
    assert "фарш" in tag_names
    assert detail["analysis"]["summary"].startswith("Готовим")
    assert detail["analysis"]["key_idea"]

    # поиск FR-7.1: по подписи
    found = client.get("/api/search", params={"q": "фарш"}).json()
    assert found["total"] >= 1
    assert any(hit["video"]["id"] == video_id for hit in found["items"])
    # оператор author: (FR-7.3)
    found_author = client.get("/api/search", params={"q": "author:user"}).json()
    assert found_author["total"] >= 1


def test_notes_saved_and_searchable(client, fake_llm):
    video_id = add_video(client).json()["video_id"]
    run_stage(client.app, JobType.RESOLVE, video_id)
    run_stage(client.app, JobType.DOWNLOAD, video_id)

    resp = client.post(f"/api/videos/{video_id}/notes", json={"text": "попробовать в субботу"})
    assert resp.status_code == 201

    found = client.get("/api/search", params={"q": "субботу"}).json()  # заметка ищется (MVP-критерий 4)
    assert found["total"] == 1


def test_folders_tags_crud_and_assign(client):
    folder = client.post("/api/folders", json={"name": "Кулинария"}).json()
    tag = client.post("/api/tags", json={"name": "ужин"}).json()

    video_id = add_video(client).json()["video_id"]
    run_stage(client.app, JobType.RESOLVE, video_id)
    run_stage(client.app, JobType.DOWNLOAD, video_id)

    resp = client.patch(f"/api/videos/{video_id}", json={"folder_ids": [folder["id"]], "tag_ids": [tag["id"]], "favorite": True})
    assert resp.status_code == 200
    detail = resp.json()
    assert [f["id"] for f in detail["folders"]] == [folder["id"]]
    assert any(t["name"] == "ужин" for t in detail["tags"])
    assert detail["favorite"] is True

    # фильтр ленты по папке (FR-3.4)
    feed = client.get("/api/videos", params={"folder_id": folder["id"]}).json()
    assert feed["total"] == 1

    # переименование папки не двигает файлы (Р5) и работает
    renamed = client.patch(f"/api/folders/{folder['id']}", json={"name": "Еда"}).json()
    assert renamed["name"] == "Еда"

    # слияние тегов (FR-3.3): связи from переводятся на to (или дубли удаляются)
    other_tag = client.post("/api/tags", json={"name": "ужин2"}).json()
    client.patch(f"/api/videos/{video_id}", json={"tag_ids": [tag["id"], other_tag["id"]]})
    merged = client.post("/api/tags/merge", json={"from_id": other_tag["id"], "to_id": tag["id"]})
    assert merged.status_code == 200
    assert merged.json()["merged"] in (0, 1)
    names_after = [t["name"] for t in client.get(f"/api/videos/{video_id}").json()["tags"]]
    assert "ужин" in names_after and "ужин2" not in names_after


def test_dead_link_best_effort(client, monkeypatch):
    from toklib_core.enums import ErrorKind
    from toklib_core.oembed import MetadataError
    from toklib_ingest import resolver as resolver_mod

    def dead(**kw):
        raise MetadataError(ErrorKind.TERMINAL, "oEmbed 404")

    monkeypatch.setattr(resolver_mod, "resolve_metadata", dead)
    video_id = add_video(client, text="https://www.tiktok.com/@user/video/7309999999999999999").json()["video_id"]
    outcome = run_stage(client.app, JobType.RESOLVE, video_id)
    assert outcome == "dead"

    detail = client.get(f"/api/videos/{video_id}").json()
    assert detail["health"] == "dead"
    assert detail["status"] == "ready"  # best-effort, не error (docs/09 §9.8)


def test_media_range_requests(client):
    video_id = add_video(client).json()["video_id"]
    resp = client.get(f"/api/media/{video_id}/file")
    assert resp.status_code == 404  # embed-режим: файла нет


def test_transcript_edit_and_reembed(client, fake_llm):
    video_id = add_video(client).json()["video_id"]
    run_stage(client.app, JobType.RESOLVE, video_id)
    run_stage(client.app, JobType.DOWNLOAD, video_id)

    resp = client.patch(
        f"/api/videos/{video_id}/transcript",
        json={"segments": [{"start": 0.0, "end": 2.0, "text": "исправленный текст"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "user_edit"  # FR-6.8

    found = client.get("/api/search", params={"q": "исправленный"}).json()
    assert found["total"] == 1


def test_export_json(client):
    id1 = add_video(client, text="https://www.tiktok.com/@user/video/7301234567890123456").json()["video_id"]
    id2 = add_video(client, text="https://www.tiktok.com/@user/video/7301234567890999999").json()["video_id"]
    run_stage(client.app, JobType.RESOLVE, id1)
    run_stage(client.app, JobType.RESOLVE, id2)
    resp = client.get("/api/system/export")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["videos"]) == 2
    assert all("tiktok_id" in v for v in body["videos"])


def test_import_data_export(client):
    """Массовый импорт архива TikTok: ссылки → ленивая очередь (FR-1.5/9.3)."""
    archive = json.dumps(
        {
            "ActivityList": [
                {"Link": "https://www.tiktok.com/@x/video/7301234567890123999", "Date": "2025-06-01 12:00:00"},
                {"Link": "https://www.tiktok.com/@x/video/7301234567890123888", "Date": "2025-06-02 12:00:00"},
            ]
        }
    ).encode("utf-8")
    resp = client.post(
        "/api/system/import/data-export",
        files={"file": ("export.json", archive, "application/json")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 2
    assert body["links_found"] == 2

    # карточки появились в ленте (ещё в инбоксе — backlog-обработка ночью)
    feed = client.get("/api/videos").json()
    assert feed["total"] == 2

    # повторный импорт — дубликаты
    resp2 = client.post("/api/system/import/data-export", files={"file": ("export.json", archive, "application/json")})
    assert resp2.json()["duplicates"] == 2

    empty = client.post("/api/system/import/data-export", files={"file": ("e.json", b"{}", "application/json")})
    assert empty.status_code == 422


def test_system_ai_status(client):
    body = client.get("/api/system/ai").json()
    assert body["mode"] == "local_only"  # NFR-9: по умолчанию
    assert body["providers"]["stt"]["provider"] == "faster_whisper"
    assert body["providers"]["llm"]["base_url"] == "http://ollama:11434"
    assert "queue_depths" in body

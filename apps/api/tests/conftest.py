"""Общие фикстуры API-тестов: приложение на tmp SQLite, фейковые провайдеры."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from toklib_core.config import Settings
from toklib_core.enums import JobType


def make_settings(tmp_path, token: str | None = None) -> Settings:
    settings = Settings()
    settings.server.data_dir = tmp_path / "data"
    settings.server.run_workers = False  # воркеры запускаются вручную по стадиям
    settings.server.token = token
    settings.download.provider = "none"  # embed-only: без сети
    return settings


@pytest.fixture
def app(tmp_path, monkeypatch):
    from toklib_api.main import create_app

    _patch_resolve(monkeypatch)
    application = create_app(make_settings(tmp_path))
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _patch_resolve(monkeypatch):
    from toklib_ingest import resolver as resolver_mod

    # реальный диск машины вне теста: watermark FR-2.7 не должен ломать тесты
    monkeypatch.setattr("toklib_ingest.workers.disk_usage_pct", lambda path: 10.0)

    def fake_resolve(**kwargs):
        tiktok_id = kwargs.get("tiktok_id") or "7301234567890123456"
        return {
            "tiktok_id": tiktok_id,
            "url": f"https://www.tiktok.com/@user/video/{tiktok_id}",
            "caption": "рецепт холодного фарша #recipes",
            "author_display": "Пекарь",
            "author_username": "user",
            "cover_url": None,
            "duration_sec": 45.0,
            "published_at": None,
            "hashtags": ["recipes"],
            "health_set": "alive",
        }

    monkeypatch.setattr(resolver_mod, "resolve_metadata", fake_resolve)
    monkeypatch.setattr(resolver_mod, "download_cover", lambda *a, **k: True)


def run_stage(app, job_type: JobType, video_id: int, priority: int = 1, force: bool = False):
    """Прогнать одну стадию конвейера вручную (воркеры в тестах отключены)."""
    from toklib_api.registry import dispatch

    namespace = SimpleNamespace(
        id=0,
        type=job_type,
        payload={"video_id": video_id, "priority": priority, "force": force},
        priority=priority,
    )
    session = app.state.ctx.session()
    try:
        return dispatch(app.state.ctx, session, namespace)
    finally:
        session.close()


@pytest.fixture
def fake_llm(app, monkeypatch):
    """Подменить LLM-анализ на предсказуемый результат."""
    from toklib_ai.interfaces import AnalyzeOutput
    from toklib_ai.router import AnalysisResult

    def fake_analyze(inp):
        return AnalysisResult(
            output=AnalyzeOutput(
                tags=["фарш", "рецепты"],
                summary="Готовим холодный фарш из подручных продуктов.",
                key_idea="Главное — охладить перед подачей.",
                category="рецепт",
                content_language="ru",
            ),
            model="fake-model",
            provider="fake",
            attempts=1,
        )

    monkeypatch.setattr(app.state.ctx.ai_ctx.router, "analyze", fake_analyze)
    return app


def add_video(client, text="https://www.tiktok.com/@user/video/7301234567890123456", **kwargs):
    return client.post("/api/inbox", json={"text": text, "source": "web", **kwargs})

"""Контекст приложения: настройки, движок, воркеры-конфиги, хаб событий."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, sessionmaker
from toklib_ai import tier0
from toklib_ai.providers import build_analyzer, build_embedder, build_transcriber
from toklib_ai.router import AiRouter
from toklib_ai.worker import AiContext
from toklib_core.config import Settings
from toklib_core.db import create_schema, make_engine
from toklib_core.search import enable_vector_search, ensure_fts
from toklib_ingest.workers import IngestContext

from toklib_api.events import EventHub

log = logging.getLogger(__name__)


def build_ai_router(settings: Settings, env: dict[str, str] | None = None) -> AiRouter:
    """Собрать AI-роутер из конфига (используется приложением и CLI: spike/benchmark)."""
    analyzers = build_analyzer(
        settings.ai.llm,
        settings.ai.llm_fallback if settings.ai.mode == "local_first" else None,
        env,
    )
    return AiRouter(
        mode=str(settings.ai.mode.value),
        transcriber=build_transcriber(settings.ai.stt, env),
        analyzers=analyzers,
        embedder=build_embedder(settings.ai.embeddings, env),
    )


@dataclass
class AppContext:
    settings: Settings
    engine: Any
    session_factory: sessionmaker
    hub: EventHub
    ingest_ctx: IngestContext
    ai_ctx: AiContext
    vector_enabled: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    _worker_threads: list[threading.Thread] = field(default_factory=list)

    @classmethod
    def build(cls, settings: Settings, env: dict[str, str] | None = None) -> AppContext:
        engine = make_engine(settings.server.db_url())
        vector_enabled = enable_vector_search(engine)

        dictionary_path = tier0.ensure_dictionary(settings.server.data_dir)
        dictionary = tier0.load_dictionary(dictionary_path)
        dictionary["_path"] = str(dictionary_path)

        hub = EventHub()

        router = build_ai_router(settings, env)

        ingest_ctx = IngestContext(settings=settings, publish=hub.publish_threadsafe)
        ai_ctx = AiContext(settings=settings, router=router, dictionary=dictionary, publish=hub.publish_threadsafe)

        ctx = cls(
            settings=settings,
            engine=engine,
            session_factory=sessionmaker(bind=engine, expire_on_commit=False, future=True),
            hub=hub,
            ingest_ctx=ingest_ctx,
            ai_ctx=ai_ctx,
            vector_enabled=vector_enabled,
        )
        # Hooks вне зависимости от run_workers: Tier-0 и FTS-индексация — часть стадии resolve/analyze
        ingest_ctx.post_resolve = ctx.post_resolve_tier0
        ai_ctx.index_hook = ctx.index_video
        return ctx

    def session(self) -> Session:
        return self.session_factory()

    def setup_database(self) -> None:
        """Миграции при старте (docs/05 §5.12): alembic upgrade head.

        БД, созданные до Alembic (create_all), детектируются и штампуются головой —
        чтобы история миграций началась с их текущего состояния.
        alembic.ini не найден (нестандартная установка) — fallback на create_all.
        """
        ini = Path("alembic.ini")
        if not ini.is_file():
            log.warning("alembic.ini не найден — создаю схему через create_all (без истории миграций)")
            create_schema(self.engine)
        else:
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(ini))
            cfg.set_main_option("sqlalchemy.url", self.settings.server.db_url())
            inspector = sa_inspect(self.engine)
            has_schema = inspector.has_table("video")
            has_alembic = inspector.has_table("alembic_version")
            if has_schema and not has_alembic:
                log.info("схема без alembic_version — штампую как head (инсталляция до Alembic)")
                command.stamp(cfg, "head")
            else:
                command.upgrade(cfg, "head")
        with self.engine.connect() as conn:
            ensure_fts(conn)

    def publish(self, event: dict[str, Any]) -> None:
        self.hub.publish_threadsafe(event)

    def post_resolve_tier0(self, session: Session, video) -> None:
        """Hook: Tier-0 теги сразу после резолва (FR-6.1); вызывается из ingest-воркера."""
        tier0.apply_tier0(session, video, self.ai_ctx.dictionary)
        self.index_video(video)

    def index_video(self, video) -> None:
        from toklib_core import search as search_mod

        search_mod.index_video_orm(self.engine, video)

    def start_workers(self) -> None:
        from toklib_core.enums import JobType

        from .runner import WorkerThread

        ai_types = [JobType.TRANSCRIBE, JobType.ANALYZE, JobType.EMBED]
        ingest_types = [JobType.RESOLVE, JobType.DOWNLOAD]

        specs: list[tuple[str, list[JobType]]] = []
        if self.settings.server.ai_only:
            specs.append(("ai-worker", ai_types))
        else:
            specs.append(("ingest-worker", ingest_types))
            specs.append(("ai-worker", ai_types))

        for name, types in specs:
            thread = WorkerThread(self, name=name, types=types, stop_event=self.stop_event)
            thread.daemon = True
            thread.start()
            self._worker_threads.append(thread)

    def stop_workers(self) -> None:
        self.stop_event.set()
        for thread in self._worker_threads:
            thread.join(timeout=10)

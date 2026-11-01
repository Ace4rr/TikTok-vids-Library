"""Воркеры стадий ingest: resolve → download (docs/04 §4.2, docs/05 §5.9).

Каждая стадия идемпотентна; сбой транслируется в retry-механику очереди.
Оркестрация и dead-letter-обработка — в toklib_api (workers_registry).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from toklib_core import queue as job_queue
from toklib_core.config import Settings
from toklib_core.db import InboxItem, Video, utcnow
from toklib_core.enums import (
    ErrorKind,
    Health,
    InboxStatus,
    JobType,
    MediaMode,
    VideoStatus,
)
from toklib_core.normalize import parse_link
from toklib_core.oembed import MetadataError
from toklib_core.paths import (
    canonical_cover_path,
    canonical_media_path,
    disk_usage_pct,
    ensure_dirs,
    tmp_dir,
)
from toklib_core.state import mark_dead_link, transition

from . import resolver as resolver_mod
from .providers import DownloadProvider, ProviderError, build_provider

log = logging.getLogger(__name__)

PublishFn = Callable[[dict], None]
PostResolveFn = Callable[[Session, Video], None]

MIME_BY_EXT = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}


@dataclass
class IngestContext:
    settings: Settings
    publish: PublishFn | None = None
    post_resolve: PostResolveFn | None = None
    _provider: DownloadProvider | None = field(default=None, repr=False)

    def provider(self) -> DownloadProvider:
        if self._provider is None:
            self._provider = build_provider(self.settings.download)
        return self._provider


def _publish(ctx: IngestContext, video: Video) -> None:
    if ctx.publish:
        ctx.publish(
            {
                "kind": "video",
                "video_id": video.id,
                "status": str(video.status.value if hasattr(video.status, "value") else video.status),
                "media_mode": str(video.media_mode.value if hasattr(video.media_mode, "value") else video.media_mode),
                "health": str(video.health.value if hasattr(video.health, "value") else video.health),
            }
        )


def _get_video(session: Session, job_payload: dict) -> Video | None:
    video_id = (job_payload or {}).get("video_id")
    if video_id is None:
        return None
    return session.get(Video, int(video_id))


def _merge_duplicate(session: Session, video: Video, existing: Video) -> None:
    """Повторная ссылка на то же видео: счётчик +1, дубль удаляется (FR-1.4)."""
    existing.save_count += 1
    for item in session.query(InboxItem).filter(InboxItem.video_id == video.id).all():
        item.status = InboxStatus.DUPLICATE
        item.video_id = existing.id
        item.updated_at = utcnow()
    session.delete(video)
    session.commit()


def _resolve_short_links(session: Session, video: Video) -> tuple[str | None, Video | None]:
    """Короткая ссылка → tiktok_id. Дедуп по id до любых тяжёлых операций.

    Возвращает (tiktok_id, duplicate_of): duplicate_of не None → дубль слит.
    """
    parsed = parse_link(video.url)
    if parsed.tiktok_id and not parsed.is_short:
        return parsed.tiktok_id, None
    final_url = resolver_mod.follow_short_link(video.url)
    tiktok_id = resolver_mod.resolve_canonical_id(final_url)
    if tiktok_id:
        existing = session.query(Video).filter(Video.tiktok_id == tiktok_id, Video.id != video.id).one_or_none()
        if existing:
            _merge_duplicate(session, video, existing)
            return tiktok_id, existing
        video.tiktok_id = tiktok_id
        video.url = f"https://www.tiktok.com/@-/video/{tiktok_id}"
        session.commit()
    return tiktok_id, None


def run_resolve_job(ctx: IngestContext, session: Session, payload: dict) -> str:
    video = _get_video(session, payload)
    if video is None or video.deleted_at is not None:
        return "gone"
    transition(video, VideoStatus.RESOLVING)
    session.commit()

    tiktok_id, duplicate_of = _resolve_short_links(session, video)
    if duplicate_of is not None:
        return "duplicate"
    if tiktok_id is None:
        raise MetadataError(
            ErrorKind.TRANSIENT,
            "не удалось раскрыть короткую ссылку в id видео",
        )
    existing = session.query(Video).filter(Video.tiktok_id == tiktok_id, Video.id != video.id).one_or_none()
    if existing:
        _merge_duplicate(session, video, existing)
        return "duplicate"

    try:
        fields = resolver_mod.resolve_metadata(
            url=video.url,
            tiktok_id=tiktok_id,
            settings=ctx.settings,
        )
    except MetadataError as exc:
        if exc.kind.value == "terminal":
            mark_dead_link(video)
            video.last_error = str(exc)
            _mark_items(session, video, InboxStatus.ERROR, str(exc))
            session.commit()
            _publish(ctx, video)
            return "dead"
        raise

    for key in ("caption", "author_display", "author_username", "cover_url", "duration_sec", "published_at", "hashtags", "url"):
        if fields.get(key) is not None:
            setattr(video, key, fields[key])
    video.health = Health.ALIVE
    session.commit()

    if fields.get("cover_url") and tiktok_id:
        resolver_mod.download_cover(fields["cover_url"], canonical_cover_path(ctx.settings.server.data_dir, tiktok_id))

    if ctx.post_resolve:
        try:
            ctx.post_resolve(session, video)  # Tier-0 теги сразу после резолва (FR-6.1)
        except Exception:
            log.exception("post_resolve (tier-0) упал, продолжаем без словарных тегов")

    job_queue.enqueue(
        session,
        JobType.DOWNLOAD,
        video_id=video.id,
        priority=priority_from(payload),
        idempotency_key=f"{JobType.DOWNLOAD.value}:{video.id}",
    )
    _publish(ctx, video)
    return "resolved"


def _mark_items(session: Session, video: Video, status: InboxStatus, error: str | None = None) -> None:
    for item in session.query(InboxItem).filter(InboxItem.video_id == video.id).all():
        item.status = status
        item.error = error
        item.updated_at = utcnow()


def priority_from(payload: dict) -> int:
    return int((payload or {}).get("priority", 1))


def run_download_job(ctx: IngestContext, session: Session, payload: dict) -> str:
    video = _get_video(session, payload)
    if video is None or video.deleted_at is not None:
        return "gone"
    transition(video, VideoStatus.DOWNLOADING)
    session.commit()

    data_dir = ctx.settings.server.data_dir
    ensure_dirs(data_dir)
    usage = disk_usage_pct(data_dir)
    if usage >= ctx.settings.storage.disk_pause_pct:
        # FR-2.7: новые загрузки останавливаются, карточки живут в embed
        raise ProviderError(
            ErrorKind.TRANSIENT,
            f"диск заполнен на {usage:.0f}% (порог {ctx.settings.storage.disk_pause_pct}%) — загрузка приостановлена",
        )

    dest = tmp_dir(data_dir) / f"dl-{video.id}-{uuid.uuid4().hex[:8]}"
    result = ctx.provider().download(video.url, dest)

    if result.file_path is not None:
        if not video.tiktok_id:
            raise ProviderError(
                ErrorKind.TERMINAL,
                "файл скачан, но tiktok_id не резолвнут — некуда канонизировать путь",
            )
        target = canonical_media_path(data_dir, video.tiktok_id, ext=result.file_path.suffix or ".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(result.file_path), str(target))
        video.file_path = str(target)
        video.file_size = target.stat().st_size
        video.mime = MIME_BY_EXT.get(target.suffix.lower(), "application/octet-stream")
        video.media_mode = MediaMode.FILE
    else:
        if video.media_mode == MediaMode.NONE:
            video.media_mode = MediaMode.EMBED

    shutil.rmtree(dest, ignore_errors=True)

    if video.media_mode == MediaMode.FILE:
        transition(video, VideoStatus.TRANSCRIBING)
        session.commit()
        job_queue.enqueue(
            session,
            JobType.TRANSCRIBE,
            video_id=video.id,
            priority=priority_from(payload),
            idempotency_key=f"{JobType.TRANSCRIBE.value}:{video.id}",
        )
    else:
        # Embed-режим: AI отложен, карточка живёт на Tier-0 (docs/07 §7.3)
        transition(video, VideoStatus.READY)
        _mark_items(session, video, InboxStatus.RESOLVED)
        session.commit()
    _publish(ctx, video)
    return "downloaded"

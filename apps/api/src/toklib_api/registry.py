"""Маршрутизация задач по воркерам + dead-letter политика (docs/09 §9.4, §9.8)."""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy.orm import Session
from toklib_ai import worker as ai_worker
from toklib_core.db import Video
from toklib_core.enums import JobType, MediaMode, VideoStatus
from toklib_ingest import workers as ingest_workers

from .context import AppContext

log = logging.getLogger(__name__)


class WorkerJob(Protocol):
    id: int
    type: JobType
    payload: dict


def dispatch(ctx: AppContext, session: Session, job: WorkerJob) -> str:
    """Выполнить одну задачу; возвращается исход (для логов и метрик)."""
    job_type = JobType(job.type.value if hasattr(job.type, "value") else job.type)
    if job_type == JobType.RESOLVE:
        return ingest_workers.run_resolve_job(ctx.ingest_ctx, session, job.payload)
    if job_type == JobType.DOWNLOAD:
        return ingest_workers.run_download_job(ctx.ingest_ctx, session, job.payload)
    if job_type == JobType.TRANSCRIBE:
        return ai_worker.run_transcribe_job(ctx.ai_ctx, session, job)
    if job_type == JobType.ANALYZE:
        return ai_worker.run_analyze_job(ctx.ai_ctx, session, job)
    if job_type == JobType.EMBED:
        return ai_worker.run_embed_job(ctx.ai_ctx, session, job)
    log.warning("неизвестный тип задачи %r — завершаю как done", job_type)
    return "unknown"


def _get_video(session: Session, payload: dict) -> Video | None:
    video_id = (payload or {}).get("video_id")
    if video_id is None:
        return None
    return session.get(Video, int(video_id))


def handle_dead_letter(ctx: AppContext, session: Session, job_type: JobType, payload: dict, error: str) -> None:
    """Исчерпание попыток: политика по стадиям — деградация, а не блокировка (NFR-2).

    - resolve → status=error (не удалось получить даже метаданные);
    - download → embed-fallback: карточка живёт без файла;
    - transcribe/analyze → ready + partial_ai (Tier-0 теги сохраняются);
    - embed → ready (карточка без вектора, доиндексация позже).
    """
    video = _get_video(session, payload)
    if video is None:
        return
    error = (error or "")[:1000]
    video.last_error = error
    try:
        if job_type == JobType.RESOLVE:
            video.status = VideoStatus.ERROR
        elif job_type == JobType.DOWNLOAD:
            if video.media_mode == MediaMode.NONE:
                video.media_mode = MediaMode.EMBED
            video.status = VideoStatus.READY
        elif job_type in (JobType.TRANSCRIBE, JobType.ANALYZE):
            video.partial_ai = True
            video.status = VideoStatus.READY
        else:
            if video.status != VideoStatus.READY:
                video.status = VideoStatus.READY
    except Exception:
        log.exception("dead-letter: не удалось обновить статус video %s", video.id)
        video.status = VideoStatus.ERROR
    session.commit()
    ctx.publish(
        {"kind": "video", "video_id": video.id, "status": str(video.status), "media_mode": str(video.media_mode), "health": str(video.health)}
    )


def reap_and_maintain(ctx: AppContext, session: Session, lease_seconds: int) -> None:
    from toklib_core import queue as job_queue

    job_queue.reap_expired_leases(session, lease_seconds)

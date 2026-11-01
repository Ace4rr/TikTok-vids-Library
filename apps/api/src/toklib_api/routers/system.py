"""Системные эндпоинты: статус AI, retry, reindex, экспорт/импорт (docs/05 §5.5)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from toklib_core import queue as job_queue
from toklib_core import search as search_mod
from toklib_core.db import Analysis, Job, Video, utcnow
from toklib_core.enums import JobPriority, JobType, VideoStatus
from toklib_core.schemas import AiStatusResponse, ExportResponse
from toklib_ingest.importer import import_links, parse_data_export

from ..deps import CtxDep, SessionDep, require_auth
from ..jellyfin import export_jellyfin

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_auth)])


@router.get("/ai", response_model=AiStatusResponse)
def ai_status(session: SessionDep, ctx: CtxDep) -> AiStatusResponse:
    """Модели, режимы, бюджеты, глубина очереди (NFR: честный статус в UI)."""
    ai = ctx.settings.ai
    day_start = dt.datetime.combine(utcnow().date(), dt.time.min)
    used_today = int(session.query(Analysis).filter(Analysis.created_at >= day_start).count())
    providers = {
        "mode": str(ai.mode.value),
        "stt": {"provider": ai.stt.provider, "model": ai.stt.model, "configured": ctx.ai_ctx.router.transcriber is not None},
        "llm": {
            "provider": ai.llm.provider,
            "base_url": ai.llm.base_url,
            "model": ai.llm.model,
            "configured": ctx.ai_ctx.router.primary_analyzer is not None,
            "fallback_configured": ai.llm_fallback.configured if ai.mode == "local_first" else False,
        },
        "embeddings": {
            "provider": ai.embeddings.provider,
            "model": ai.embeddings.model,
            "configured": ctx.ai_ctx.router.embedder is not None,
        },
        "night_window": {"start": ai.night_window.start, "end": ai.night_window.end},
    }
    budgets = {
        "analysis_per_day": ai.budgets.analysis_per_day,
        "used_today": used_today,
        "nightly_backlog": ai.budgets.nightly_backlog,
        "lazy_backlog": ai.budgets.lazy_backlog,
    }
    return AiStatusResponse(
        mode=str(ai.mode.value),
        providers=providers,
        budgets=budgets,
        queue_depths=job_queue.queue_depths(session),
        dead_letters=job_queue.dead_letter_count(session),
        download_success_24h=job_queue.download_success_ratio(session),
        tier0_dictionary=str(ctx.ai_ctx.dictionary.get("_path") or "") or None,
    )


@router.post("/retry/{video_id}", status_code=202)
def retry(video_id: int, session: SessionDep, ctx: CtxDep) -> dict:
    """Кнопка повтора в UI (5.9): перезапуск упавшей стадии."""
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "видео не найдено")

    if video.status == VideoStatus.READY and not video.partial_ai:
        raise HTTPException(422, "видео уже обработано; повторный AI — через reanalyze")

    if video.status in (VideoStatus.RESOLVING, VideoStatus.DOWNLOADING, VideoStatus.ERROR, VideoStatus.INBOX):
        job_type = JobType.RESOLVE
    elif video.status == VideoStatus.TRANSCRIBING:
        job_type = JobType.TRANSCRIBE
    else:  # ANALYZING или READY+partial_ai
        job_type = JobType.ANALYZE

    # старые задачи этого типа деактивируем, чтобы idempotency-key пропустил новую
    for job in session.query(Job).filter(Job.type == job_type, Job.status.in_(["pending", "running", "failed"])).all():
        if (job.payload or {}).get("video_id") == video.id:
            job.status = "done"
    session.commit()

    job_queue.enqueue(
        session,
        job_type,
        video_id=video.id,
        payload={"priority": int(JobPriority.HIGH), "force": True},
        priority=int(JobPriority.HIGH),
        idempotency_key=f"{job_type.value}:{video.id}:retry-{int(utcnow().timestamp())}",
    )
    ctx.publish({"kind": "video", "video_id": video.id, "status": str(video.status), "media_mode": str(video.media_mode), "health": str(video.health)})
    return {"queued": True, "stage": str(job_type.value)}


@router.post("/reindex", status_code=202)
def reindex(session: SessionDep, ctx: CtxDep) -> dict:
    """Переиндексация эмбеддингов — Фаза 2 (смена модели, FR-6.9); пока — FTS."""
    count = search_mod.reindex_all(ctx.engine)
    videos = session.query(Video).filter(Video.deleted_at.is_(None), Video.status == VideoStatus.READY).all()
    for video in videos:
        job_queue.enqueue(
            session,
            JobType.EMBED,
            video_id=video.id,
            payload={"priority": int(JobPriority.BACKLOG)},
            priority=int(JobPriority.BACKLOG),
            idempotency_key=f"{JobType.EMBED.value}:{video.id}",
        )
    return {"fts_indexed": count, "embed_queued": len(videos)}


@router.get("/export", response_model=ExportResponse)
def export_all(session: SessionDep) -> ExportResponse:
    """Экспорт библиотеки JSON (FR-9.1); файлы архивом — следующим шагом."""
    videos: list[dict] = []
    for video in session.query(Video).filter(Video.deleted_at.is_(None)).all():
        videos.append(
            {
                "tiktok_id": video.tiktok_id,
                "url": video.url,
                "save_count": video.save_count,
                "author_username": video.author_username,
                "author_display": video.author_display,
                "caption": video.caption,
                "hashtags": video.hashtags,
                "duration_sec": video.duration_sec,
                "published_at": video.published_at.isoformat() if video.published_at else None,
                "saved_at": video.saved_at.isoformat(),
                "status": str(video.status.value),
                "media_mode": str(video.media_mode.value),
                "health": str(video.health.value),
                "viewed": video.viewed,
                "favorite": video.favorite,
                "tags": [link.tag.name for link in video.tag_rows if link.tag],
                "notes": [{"kind": str(note.kind.value), "text": note.text, "timestamp_sec": note.timestamp_sec} for note in video.notes],
                "transcript": (
                    {"language": video.transcript.language, "segments": video.transcript.segments}
                    if video.transcript
                    else None
                ),
                "analysis": (
                    {
                        "model": video.analyses[-1].model,
                        "summary": video.analyses[-1].summary,
                        "key_idea": video.analyses[-1].key_idea,
                        "category": video.analyses[-1].category,
                    }
                    if video.analyses
                    else None
                ),
            }
        )
    return ExportResponse(exported_at=utcnow(), videos=videos)


@router.post("/import/data-export")
async def import_data_export(
    session: SessionDep,
    ctx: CtxDep,
    file: UploadFile,
) -> dict:
    """Массовый импорт архива TikTok «Скачать мои данные» (FR-1.5/9.3).

    Ленивая обработка (docs/07 №7): backlog-приоритет — Tier-0 после резолва,
    полный AI ночью; прогресс виден в очереди (/api/system/ai) и SSE.
    """
    data = await file.read()
    links = parse_data_export(data)
    if not links:
        raise HTTPException(422, "в файле не найдено ни одной ссылки TikTok")
    summary = import_links(session, links)
    ctx.publish({"kind": "import", **summary})
    return summary


@router.post("/export/jellyfin")
def export_jellyfin_tree(session: SessionDep, ctx: CtxDep) -> dict:
    """Пересобрать Jellyfin-дерево (FR-8.2): hardlink + movie.nfo + poster."""
    summary = export_jellyfin(session, ctx.settings.server.data_dir)
    return summary

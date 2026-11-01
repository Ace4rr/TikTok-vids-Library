"""AI-воркер: стадии transcribe → analyze → embed (docs/07 §7.3).

Каскад с деградацией: сбой STT → анализ по подписи; невалид JSON ×2 → Tier-0
+ partial_ai. Бюджеты и ленивый бэклог — docs/07 §7.6.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import subprocess
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session
from toklib_core import queue as job_queue
from toklib_core.config import Settings
from toklib_core.db import Analysis, Embedding, Job, Tag, Transcript, Video, VideoTag, utcnow
from toklib_core.enums import JobType, MediaMode, TranscriptSource, VideoStatus
from toklib_core.state import transition

from . import tier0
from .interfaces import AnalysisFailed, AnalyzeInput, NoProviderError
from .prompts import PROMPT_VERSION
from .router import AiRouter

log = logging.getLogger(__name__)

PublishFn = Callable[[dict], None]


@dataclass
class AiContext:
    settings: Settings
    router: AiRouter
    dictionary: dict[str, Any]
    publish: PublishFn | None = None
    index_hook: Callable[[Video], None] | None = None  # FTS-синхронизация (провайдер — api)

    @property
    def data_dir(self) -> Path:
        return self.settings.server.data_dir


def _index(ctx: AiContext, video: Video) -> None:
    if ctx.index_hook:
        try:
            ctx.index_hook(video)
        except Exception:
            log.exception("FTS-индексация video %s не удалась", video.id)


def _publish(ctx: AiContext, video: Video) -> None:
    if ctx.publish:
        ctx.publish(
            {
                "kind": "video",
                "video_id": video.id,
                "status": str(video.status),
                "media_mode": str(video.media_mode),
                "health": str(video.health),
            }
        )


def _get_video(session: Session, payload: dict) -> Video | None:
    video_id = (payload or {}).get("video_id")
    if video_id is None:
        return None
    return session.get(Video, int(video_id))


def _input_hash(inp: AnalyzeInput) -> str:
    """Кэш-ключ анализа (docs/07 §7.4 №6): только контент — словарь тегов (tags_vocab)
    меняется от прогонов и в ключ не входит, иначе кэш никогда не попадает."""
    body = json.dumps(
        {
            "caption": inp.caption,
            "hashtags": inp.hashtags,
            "transcript": (inp.transcript_text or "")[:100000],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def extract_audio_ffmpeg(video_file: str, dest: Path) -> Path:
    """Аудио 16 кГц mono во временный файл (docs/07 §7.3, ~0.2 МБ/мин)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", video_file, "-vn", "-ac", "1", "-ar", "16000", str(dest)],
            capture_output=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg не найден в PATH — транскрипция недоступна") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg завершился с ошибкой: {proc.stderr[-300:].decode(errors='ignore')}")
    return dest


def build_analyze_input(session: Session, video: Video, ctx: AiContext) -> AnalyzeInput:
    """Словарь тегов (топ-40 по использованию) и категории — docs/07 §7.4 №2."""
    vocab_rows = (
        session.query(Tag.name)
        .join(VideoTag, VideoTag.tag_id == Tag.id)
        .group_by(Tag.id, Tag.name)
        .order_by(func.count(VideoTag.video_id).desc())
        .limit(40)
        .all()
    )
    categories = list(ctx.dictionary.get("categories", {}).keys())
    transcript_text = ""
    if video.transcript and video.transcript.segments:
        transcript_text = " ".join(
            seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in video.transcript.segments
        ).strip()
    return AnalyzeInput(
        caption=video.caption,
        hashtags=list(video.hashtags or []),
        tags_vocab=[row[0] for row in vocab_rows],
        categories=categories,
        transcript_text=transcript_text or None,
        language_hint=(video.transcript.language if video.transcript else None),
    )


def _defer_to_night(session: Session, job: Job, ctx: AiContext) -> str:
    """Бюджет исчерпан: отложить до начала ночного окна (деградация, не затык)."""
    window = ctx.settings.ai.night_window
    now_local = dt.datetime.now().astimezone()
    start_h, start_m = (int(x) for x in window.start.split(":"))
    candidate = now_local.replace(hour=start_h, minute=start_m + 5, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += dt.timedelta(days=1)
    job.status = "pending"
    job.locked_by = None
    job.locked_at = None
    job.run_after = candidate.astimezone(dt.UTC).replace(tzinfo=None)
    session.commit()
    return "rescheduled"


def _budget_used_today(session: Session) -> int:
    day_start = dt.datetime.combine(utcnow().date(), dt.time.min)
    return int(session.query(Analysis).filter(Analysis.created_at >= day_start).count())


def enqueue_analyze(session: Session, video: Video, priority: int, *, force: bool = False) -> None:
    job_queue.enqueue(
        session,
        JobType.ANALYZE,
        video_id=video.id,
        payload={"priority": priority, "force": force},
        priority=priority,
        idempotency_key=f"{JobType.ANALYZE.value}:{video.id}",
    )


def enqueue_embed(session: Session, video: Video, priority: int) -> None:
    job_queue.enqueue(
        session,
        JobType.EMBED,
        video_id=video.id,
        payload={"priority": priority},
        priority=priority,
        idempotency_key=f"{JobType.EMBED.value}:{video.id}",
    )


def run_transcribe_job(ctx: AiContext, session: Session, job: Job) -> str:
    video = _get_video(session, job.payload)
    if video is None or video.deleted_at is not None:
        return "gone"
    if video.media_mode not in (MediaMode.FILE, MediaMode.AUDIO) or not video.file_path:
        return "no-media"  # embed-режим: AI отложен (docs/07 §7.3)

    from toklib_core.state import PipelineError

    try:
        transition(video, VideoStatus.TRANSCRIBING)
    except PipelineError:
        pass  # повторный прогон: статус уже транскрибируется/готов
    session.commit()

    language = ctx.settings.ai.stt.language
    language = None if language in (None, "", "auto") else language

    if ctx.router.transcriber is None:
        log.info("STT не сконфигурирован — анализ пойдёт по подписи (Tier-0)")
    else:
        audio_path = ctx.data_dir / "tmp" / f"audio-{video.id}.wav"
        try:
            extract_audio_ffmpeg(video.file_path, audio_path)
            result = ctx.router.transcribe(audio_path, language)
        except Exception as exc:
            audio_path.unlink(missing_ok=True)
            raise RuntimeError(f"STT: {exc}") from exc
        audio_path.unlink(missing_ok=True)
        video.transcript = Transcript(
            video_id=video.id,
            language=result.language,
            model=result.model,
            source=TranscriptSource.AI,
            segments=[{"start": s.start, "end": s.end, "text": s.text} for s in result.segments],
        )
        session.commit()

    try:
        transition(video, VideoStatus.ANALYZING)
    except PipelineError:
        pass
    session.commit()
    enqueue_analyze(session, video, int(job.priority))
    _publish(ctx, video)
    return "transcribed"


def run_analyze_job(ctx: AiContext, session: Session, job: Job) -> str:
    video = _get_video(session, job.payload)
    if video is None or video.deleted_at is not None:
        return "gone"

    from toklib_core.state import PipelineError

    try:
        transition(video, VideoStatus.ANALYZING)
    except PipelineError:
        pass
    session.commit()

    limit = ctx.settings.ai.budgets.analysis_per_day
    if limit > 0 and _budget_used_today(session) >= limit:
        return _defer_to_night(session, job, ctx)  # деградация, не затык (docs/07 §7.6)

    inp = build_analyze_input(session, video, ctx)
    model_id = ctx.router.primary_analyzer.model_id if ctx.router.primary_analyzer else "tier0"
    input_hash = _input_hash(inp)
    force = bool((job.payload or {}).get("force"))

    if not force:
        cached = (
            session.query(Analysis)
            .filter(
                Analysis.model == model_id,
                Analysis.prompt_version == PROMPT_VERSION,
                Analysis.input_hash == input_hash,
            )
            .one_or_none()
        )
        if cached is not None:
            # retry и перезапуски не анализируют повторно (docs/07 §7.4 №6)
            transition(video, VideoStatus.READY)
            session.commit()
            _index(ctx, video)
            enqueue_embed(session, video, int(job.priority))
            _publish(ctx, video)
            return "cached"

    try:
        result = ctx.router.analyze(inp)
    except AnalysisFailed:
        # Деградация: теги Tier-0 остаются, саммари отложено (docs/07 §7.3)
        video.partial_ai = True
        transition(video, VideoStatus.READY)
        session.commit()
        _index(ctx, video)
        enqueue_embed(session, video, int(job.priority))
        _publish(ctx, video)
        return "degraded"

    video.partial_ai = False
    session.add(
        Analysis(
            video_id=video.id,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            input_hash=input_hash,
            summary=result.output.summary or None,
            key_idea=result.output.key_idea or None,
            category=result.output.category,
            content_language=result.output.content_language,
        )
    )
    tier0.clear_ai_tags(session, video)
    tier0.upsert_ai_tags(session, video, result.output.tags)
    transition(video, VideoStatus.READY)
    session.commit()
    _index(ctx, video)
    enqueue_embed(session, video, int(job.priority))
    _publish(ctx, video)
    return "analyzed"


def composite_document(video: Video) -> str:
    """Составной документ для эмбеддинга: транскрипт + подпись + заметки (docs/06)."""
    parts: list[str] = []
    if video.transcript and video.transcript.segments:
        parts.extend(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in video.transcript.segments)
    if video.caption:
        parts.append(video.caption)
    parts.extend(note.text for note in video.notes)
    return "\n".join(p for p in parts if p.strip())[:50000]


def array_float32(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def run_embed_job(ctx: AiContext, session: Session, job: Job) -> str:
    video = _get_video(session, job.payload)
    if video is None or video.deleted_at is not None:
        return "gone"
    doc = composite_document(video)
    if not doc.strip():
        return "skipped"
    try:
        vectors, model_id = ctx.router.embed([doc])
    except NoProviderError:
        return "skipped"  # карточка без вектора, доиндексация ночным батчем (docs/07 §7.3)
    except Exception as exc:
        raise RuntimeError(f"embed: {exc}") from exc

    vec = vectors[0]
    content_hash = hashlib.sha256(doc.encode("utf-8")).hexdigest()
    existing = (
        session.query(Embedding)
        .filter(Embedding.entity_type == "video", Embedding.entity_id == video.id, Embedding.model_id == model_id)
        .one_or_none()
    )
    if existing:
        existing.content_hash = content_hash
        existing.dim = len(vec)
        existing.vector = array_float32(vec)
    else:
        session.add(
            Embedding(
                entity_type="video",
                entity_id=video.id,
                model_id=model_id,
                dim=len(vec),
                content_hash=content_hash,
                vector=array_float32(vec),
            )
        )
    session.commit()
    return "embedded"

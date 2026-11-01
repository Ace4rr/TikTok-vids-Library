"""Машина статусов конвейера — docs/05 §5.9 → код (docs/09 §9.8)."""

from __future__ import annotations

from .db import Video
from .enums import Health, MediaMode, VideoStatus


class PipelineError(RuntimeError):
    pass


# Разрешённые переходы. READY → стадии повторов (reanalyze/retry); ERROR → retry.
_TRANSITIONS: dict[VideoStatus, set[VideoStatus]] = {
    VideoStatus.INBOX: {VideoStatus.RESOLVING},
    # dead-link завершает карточку best-effort → READY (docs/09 §9.8)
    VideoStatus.RESOLVING: {VideoStatus.DOWNLOADING, VideoStatus.READY},
    # download fail терминально → embed-fallback → READY
    VideoStatus.DOWNLOADING: {VideoStatus.TRANSCRIBING, VideoStatus.READY},
    VideoStatus.TRANSCRIBING: {VideoStatus.ANALYZING},
    VideoStatus.ANALYZING: {VideoStatus.READY},
    # повторные прогоны (reanalyze, retry после ошибки, повторная загрузка)
    VideoStatus.READY: {
        VideoStatus.RESOLVING,
        VideoStatus.DOWNLOADING,
        VideoStatus.TRANSCRIBING,
        VideoStatus.ANALYZING,
    },
    VideoStatus.ERROR: {
        VideoStatus.RESOLVING,
        VideoStatus.DOWNLOADING,
        VideoStatus.TRANSCRIBING,
        VideoStatus.ANALYZING,
    },
}


def can_transition(current: VideoStatus, new: VideoStatus) -> bool:
    return new in _TRANSITIONS.get(current, set())


def transition(video: Video, new: VideoStatus) -> None:
    if not can_transition(video.status, new):
        raise PipelineError(f"недопустимый переход статуса {video.status} → {new} (video {video.id})")
    video.status = new


def mark_dead_link(video: Video) -> None:
    """Терминальная ошибка метаданных: карточка живёт best-effort без файла."""
    video.health = Health.DEAD
    if video.media_mode == MediaMode.NONE:
        video.media_mode = MediaMode.EMBED
    transition(video, VideoStatus.READY)

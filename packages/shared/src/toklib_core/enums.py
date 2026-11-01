"""Статусные перечисления — docs/05 §5.4, §5.9."""

from enum import IntEnum, StrEnum


class VideoStatus(StrEnum):
    """Стадия конвейера (не свойство носителя — см. MediaMode)."""

    INBOX = "inbox"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    READY = "ready"
    ERROR = "error"


class MediaMode(StrEnum):
    """Что физически сохранено (docs/05 §5.9)."""

    NONE = "none"
    EMBED = "embed"
    AUDIO = "audio"  # V1.1
    FILE = "file"


class Health(StrEnum):
    """Живость ссылки на TikTok (FR-2.5)."""

    UNKNOWN = "unknown"
    ALIVE = "alive"
    DEAD = "dead"


class TagKind(StrEnum):
    AI = "ai"
    USER = "user"
    SYSTEM = "system"


class TagSource(StrEnum):
    AI = "ai"
    USER = "user"


class NoteKind(StrEnum):
    USER = "user"
    AI_SUMMARY = "ai_summary"
    KEY_IDEA = "key_idea"


class TranscriptSource(StrEnum):
    AI = "ai"
    USER_EDIT = "user_edit"


class InboxSource(StrEnum):
    SHARE = "share"
    WEB = "web"
    TG = "tg"
    IMPORT = "import"
    EXT = "ext"
    SHORTCUT = "shortcut"


class InboxStatus(StrEnum):
    NEW = "new"
    RESOLVED = "resolved"
    DUPLICATE = "duplicate"
    ERROR = "error"


class JobType(StrEnum):
    RESOLVE = "resolve"
    DOWNLOAD = "download"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    EMBED = "embed"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"  # dead-letter после исчерпания попыток


class JobPriority(IntEnum):
    """0 — вперёд очереди: открытая карточка / ручной повтор; 2 — ночной бэклог."""

    HIGH = 0
    NORMAL = 1
    BACKLOG = 2


class AiMode(StrEnum):
    """Политика маршрутизации AI (docs/08 §8.1); default local_only — FR-6.5."""

    LOCAL_ONLY = "local_only"
    LOCAL_FIRST = "local_first"
    CLOUD_ONLY = "cloud_only"


class DownloadProviderKind(StrEnum):
    YT_DLP = "yt_dlp"
    COBALT = "cobalt"
    NONE = "none"  # embed-only: карточки без файлов


class ErrorKind(StrEnum):
    """Повторяемая сеть/таймаут vs терминальная (видео удалено/приватное) — 5.9."""

    TRANSIENT = "transient"
    TERMINAL = "terminal"

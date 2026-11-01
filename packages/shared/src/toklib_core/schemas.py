"""Pydantic-схемы API — единый контракт backend ↔ web ↔ mobile ↔ бот (packages/shared README)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import Health, InboxSource, InboxStatus, MediaMode, NoteKind, VideoStatus

ORM = ConfigDict(from_attributes=True)


class TagOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    kind: str
    source: str | None = None
    video_count: int | None = None


class FolderOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    parent_id: int | None = None
    slug: str
    icon: str | None = None


class NoteOut(BaseModel):
    model_config = ORM
    id: int
    video_id: int
    kind: NoteKind
    text: str
    timestamp_sec: float | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class TranscriptSegmentModel(BaseModel):
    start: float
    end: float
    text: str


class TranscriptOut(BaseModel):
    model_config = ORM
    video_id: int
    language: str | None = None
    model: str | None = None
    source: str
    segments: list[TranscriptSegmentModel] = Field(default_factory=list)


class AnalysisOut(BaseModel):
    model_config = ORM
    id: int
    model: str
    prompt_version: str
    summary: str | None = None
    key_idea: str | None = None
    category: str | None = None
    content_language: str | None = None
    confidence: float | None = None
    created_at: dt.datetime


class VideoOut(BaseModel):
    model_config = ORM
    id: int
    tiktok_id: str | None = None
    url: str
    save_count: int = 1
    author_username: str | None = None
    author_display: str | None = None
    author_avatar_url: str | None = None
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    duration_sec: float | None = None
    cover_url: str | None = None
    published_at: dt.datetime | None = None
    saved_at: dt.datetime
    status: VideoStatus
    media_mode: MediaMode
    health: Health
    viewed: bool = False
    favorite: bool = False
    partial_ai: bool = False
    file_size: int | None = None
    last_error: str | None = None


class VideoDetail(VideoOut):
    tags: list[TagOut] = Field(default_factory=list)
    folders: list[FolderOut] = Field(default_factory=list)
    notes: list[NoteOut] = Field(default_factory=list)
    transcript: TranscriptOut | None = None
    analysis: AnalysisOut | None = None


class VideoPage(BaseModel):
    items: list[VideoOut]
    total: int


class InboxCreateRequest(BaseModel):
    text: str
    source: InboxSource = InboxSource.WEB


class InboxResolveResponse(BaseModel):
    """Дедуп FR-1.4: повторная ссылка возвращает существующую карточку."""

    result: str  # "created" | "exists" | "pending"
    video: VideoOut | None = None
    video_id: int | None = None
    inbox_item_id: int | None = None


class InboxItemOut(BaseModel):
    model_config = ORM
    id: int
    raw_text: str
    url: str | None = None
    source: InboxSource
    status: InboxStatus
    error: str | None = None
    video_id: int | None = None
    created_at: dt.datetime


class VideoPatchRequest(BaseModel):
    viewed: bool | None = None
    favorite: bool | None = None
    folder_ids: list[int] | None = None
    tag_ids: list[int] | None = None


class FolderCreateRequest(BaseModel):
    name: str
    parent_id: int | None = None
    icon: str | None = None


class FolderPatchRequest(BaseModel):
    name: str | None = None
    icon: str | None = None
    parent_id: int | None = None


class TagCreateRequest(BaseModel):
    name: str
    kind: str = "user"


class TagMergeRequest(BaseModel):
    from_id: int
    to_id: int


class NoteCreateRequest(BaseModel):
    text: str
    kind: NoteKind = NoteKind.USER
    timestamp_sec: float | None = None


class TranscriptPatchRequest(BaseModel):
    """Правка транскрипта пользователем — FR-6.8."""

    segments: list[TranscriptSegmentModel]


class ReanalyzeRequest(BaseModel):
    force: bool = False  # True — игнорировать кэш (model, prompt_version, input_hash)


class SearchHit(BaseModel):
    video: VideoOut
    rank: float | None = None


class SearchResponse(BaseModel):
    total: int
    items: list[SearchHit]


class AiStatusResponse(BaseModel):
    mode: str
    providers: dict[str, Any]
    budgets: dict[str, Any]
    queue_depths: dict[str, int]
    dead_letters: int
    download_success_24h: float | None = None
    tier0_dictionary: str | None = None


class ExportResponse(BaseModel):
    exported_at: dt.datetime
    videos: list[dict[str, Any]]

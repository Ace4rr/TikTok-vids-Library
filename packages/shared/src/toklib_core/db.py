"""Модель данных — docs/05 §5.4 (+ дополнения, зафиксированные в docs/09 §9.8).

Дополнения к схеме 5.4: video.partial_ai, video.deleted_at (корзина FR-9.2),
analysis.content_language, inbox_item.video_id.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.pool import NullPool

from .enums import (
    Health,
    InboxSource,
    InboxStatus,
    JobPriority,
    JobStatus,
    JobType,
    MediaMode,
    NoteKind,
    TagKind,
    TagSource,
    TranscriptSource,
    VideoStatus,
)


def utcnow() -> dt.datetime:
    """Наивный UTC — единый формат хранения во всех СУБД."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _enum(enum_cls, **kw):
    return Enum(
        enum_cls,
        native_enum=False,
        length=24,
        values_callable=lambda e: [m.value for m in e],
        **kw,
    )


class Base(DeclarativeBase):
    pass


class Video(Base):
    __tablename__ = "video"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tiktok_id: Mapped[str | None] = mapped_column(String(32), unique=True)  # NULL у коротких ссылок до резолва
    url: Mapped[str] = mapped_column(String(512))
    save_count: Mapped[int] = mapped_column(Integer, default=1)  # дедуп: +1 при повторе (FR-1.4)

    author_username: Mapped[str | None] = mapped_column(String(128))
    author_display: Mapped[str | None] = mapped_column(String(256))
    author_avatar_url: Mapped[str | None] = mapped_column(String(512))

    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    saved_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    status: Mapped[VideoStatus] = mapped_column(_enum(VideoStatus), default=VideoStatus.INBOX)
    media_mode: Mapped[MediaMode] = mapped_column(_enum(MediaMode), default=MediaMode.NONE)
    health: Mapped[Health] = mapped_column(_enum(Health), default=Health.UNKNOWN)

    file_path: Mapped[str | None] = mapped_column(String(1024))
    file_size: Mapped[int | None] = mapped_column(Integer)
    mime: Mapped[str | None] = mapped_column(String(64))

    viewed: Mapped[bool] = mapped_column(Boolean, default=False)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)  # корзина FR-9.2

    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    notes: Mapped[list[Note]] = relationship(back_populates="video", cascade="all, delete-orphan")
    transcript: Mapped[Transcript | None] = relationship(back_populates="video", cascade="all, delete-orphan")
    analyses: Mapped[list[Analysis]] = relationship(back_populates="video", cascade="all, delete-orphan")
    tag_rows: Mapped[list[VideoTag]] = relationship(back_populates="video", cascade="all, delete-orphan")
    folder_rows: Mapped[list[VideoFolder]] = relationship(back_populates="video", cascade="all, delete-orphan")


class Folder(Base):
    __tablename__ = "folder"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("folder.id"))  # вложенность ≤ 2 (FR-3.2)
    slug: Mapped[str] = mapped_column(String(256), default="")
    icon: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    children: Mapped[list[Folder]] = relationship(back_populates="parent")
    parent: Mapped[Folder | None] = relationship(back_populates="children", remote_side=[id])
    video_rows: Mapped[list[VideoFolder]] = relationship(back_populates="folder", cascade="all, delete-orphan")


class VideoFolder(Base):
    __tablename__ = "video_folder"

    video_id: Mapped[int] = mapped_column(ForeignKey("video.id", ondelete="CASCADE"), primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folder.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    video: Mapped[Video] = relationship(back_populates="folder_rows")
    folder: Mapped[Folder] = relationship(back_populates="video_rows")


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[TagKind] = mapped_column(_enum(TagKind), default=TagKind.USER)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    video_rows: Mapped[list[VideoTag]] = relationship(back_populates="tag", cascade="all, delete-orphan")


class VideoTag(Base):
    __tablename__ = "video_tag"
    __table_args__ = (UniqueConstraint("video_id", "tag_id", name="uq_video_tag"),)

    video_id: Mapped[int] = mapped_column(ForeignKey("video.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[TagSource] = mapped_column(_enum(TagSource), default=TagSource.USER)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    video: Mapped[Video] = relationship(back_populates="tag_rows")
    tag: Mapped[Tag] = relationship(back_populates="video_rows")


class Note(Base):
    __tablename__ = "note"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video.id", ondelete="CASCADE"))
    kind: Mapped[NoteKind] = mapped_column(_enum(NoteKind), default=NoteKind.USER)
    text: Mapped[str] = mapped_column(Text)
    timestamp_sec: Mapped[float | None] = mapped_column(Float)  # FR-5.2 (V1.1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    video: Mapped[Video] = relationship(back_populates="notes")


class Transcript(Base):
    __tablename__ = "transcript"

    video_id: Mapped[int] = mapped_column(ForeignKey("video.id", ondelete="CASCADE"), primary_key=True)
    language: Mapped[str | None] = mapped_column(String(8))
    model: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[TranscriptSource] = mapped_column(_enum(TranscriptSource), default=TranscriptSource.AI)
    segments: Mapped[list] = mapped_column(JSON, default=list)  # [{start,end,text}]
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    video: Mapped[Video] = relationship(back_populates="transcript")


class Analysis(Base):
    __tablename__ = "analysis"
    __table_args__ = (Index("ix_analysis_cache", "model", "prompt_version", "input_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(16), default="v1")
    input_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    key_idea: Mapped[str | None] = mapped_column(String(256))
    category: Mapped[str | None] = mapped_column(String(64))
    content_language: Mapped[str | None] = mapped_column(String(8))
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    video: Mapped[Video] = relationship(back_populates="analyses")


class Embedding(Base):
    __tablename__ = "embedding"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "model_id", name="uq_embedding"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), default="video")
    entity_id: Mapped[int] = mapped_column(Integer)
    model_id: Mapped[str] = mapped_column(String(128))
    dim: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class InboxItem(Base):
    __tablename__ = "inbox_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[InboxSource] = mapped_column(_enum(InboxSource), default=InboxSource.WEB)
    status: Mapped[InboxStatus] = mapped_column(_enum(InboxStatus), default=InboxStatus.NEW)
    error: Mapped[str | None] = mapped_column(Text)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("video.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_pick", "status", "priority", "run_after"),
        Index("ix_job_type_status", "type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[JobType] = mapped_column(_enum(JobType))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.PENDING)
    priority: Mapped[int] = mapped_column(Integer, default=int(JobPriority.NORMAL))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


def make_engine(url: str) -> Engine:
    """Движок с прагмами SQLite (WAL + busy_timeout — один писатель, docs/06 Р1)."""
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
            future=True,
        )

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, future=True)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)

"""Лента и карточки видео — модули М3–М6 (docs/03), docs/05 §5.5."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import joinedload
from toklib_core import queue as job_queue
from toklib_core import search as search_mod
from toklib_core.db import (
    Analysis,
    Folder,
    InboxItem,
    Note,
    Tag,
    Transcript,
    Video,
    VideoFolder,
    VideoTag,
    utcnow,
)
from toklib_core.enums import (
    Health,
    JobPriority,
    JobType,
    MediaMode,
    TagSource,
    TranscriptSource,
    VideoStatus,
)
from toklib_core.paths import canonical_cover_path
from toklib_core.schemas import (
    AnalysisOut,
    FolderOut,
    InboxItemOut,
    NoteCreateRequest,
    NoteOut,
    ReanalyzeRequest,
    TagOut,
    TranscriptOut,
    TranscriptPatchRequest,
    VideoDetail,
    VideoOut,
    VideoPage,
    VideoPatchRequest,
)

from ..deps import CtxDep, SessionDep, require_auth

router = APIRouter(prefix="/api/videos", tags=["videos"], dependencies=[Depends(require_auth)])


def _tags_out(session: SessionDep, video_id: int) -> list[TagOut]:
    rows = (
        session.query(VideoTag, Tag)
        .join(Tag, Tag.id == VideoTag.tag_id)
        .filter(VideoTag.video_id == video_id)
        .all()
    )
    return [
        TagOut(id=tag.id, name=tag.name, kind=str(tag.kind.value), source=str(link.source.value))
        for link, tag in rows
    ]


def _folders_out(session: SessionDep, video_id: int) -> list[FolderOut]:
    rows = (
        session.query(VideoFolder, Folder)
        .join(Folder, Folder.id == VideoFolder.folder_id)
        .filter(VideoFolder.video_id == video_id)
        .all()
    )
    return [FolderOut.model_validate(folder) for _, folder in rows]


def video_detail(session: SessionDep, video: Video) -> VideoDetail:
    data = VideoOut.model_validate(video).model_dump()
    transcript = TranscriptOut.model_validate(video.transcript) if video.transcript else None
    row = (
        session.query(Analysis)
        .filter(Analysis.video_id == video.id)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        .first()
    )
    analysis = AnalysisOut.model_validate(row) if row else None
    return VideoDetail(
        **data,
        tags=_tags_out(session, video.id),
        folders=_folders_out(session, video.id),
        notes=[NoteOut.model_validate(n) for n in video.notes],
        transcript=transcript,
        analysis=analysis,
    )


_SORTS = {
    "saved_at": Video.saved_at,
    "published_at": Video.published_at,
    "duration": Video.duration_sec,
    "author": Video.author_username,
    "save_count": Video.save_count,
}


@router.get("", response_model=VideoPage)
def list_videos(
    session: SessionDep,
    folder_id: int | None = None,
    tag: str | None = None,
    author: str | None = None,
    status: VideoStatus | None = None,
    media_mode: MediaMode | None = None,
    health: Health | None = None,
    viewed: bool | None = None,
    favorite: bool | None = None,
    partial_ai: bool | None = None,
    q: str | None = None,
    sort: str = "saved_at",
    order: str = "desc",
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> VideoPage:
    """Лента с фильтрами (FR-3.4) и сортировками (FR-3.5)."""
    query = session.query(Video).filter(Video.deleted_at.is_(None))
    if folder_id is not None:
        query = query.join(VideoFolder, VideoFolder.video_id == Video.id).filter(VideoFolder.folder_id == folder_id)
    if tag:
        query = (
            query.join(VideoTag, VideoTag.video_id == Video.id)
            .join(Tag, Tag.id == VideoTag.tag_id)
            .filter(sa_func.lower(Tag.name) == tag.lower().lstrip("#"))
        )
    if author:
        query = query.filter(sa_func.lower(Video.author_username) == author.lower().lstrip("@"))
    if status is not None:
        query = query.filter(Video.status == status)
    if media_mode is not None:
        query = query.filter(Video.media_mode == media_mode)
    if health is not None:
        query = query.filter(Video.health == health)
    if viewed is not None:
        query = query.filter(Video.viewed == viewed)
    if favorite is not None:
        query = query.filter(Video.favorite == favorite)
    if partial_ai is not None:
        query = query.filter(Video.partial_ai == partial_ai)
    if q:
        query = query.filter(Video.caption.ilike(f"%{q}%"))

    total = int(query.with_entities(sa_func.count(Video.id)).scalar() or 0)

    column = _SORTS.get(sort, Video.saved_at)
    column = column.desc() if order == "desc" else column.asc()
    rows = (
        query.options(joinedload(Video.transcript))
        .order_by(column, Video.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return VideoPage(items=[VideoOut.model_validate(v) for v in rows], total=total)


@router.get("/{video_id}", response_model=VideoDetail)
def get_video(video_id: int, session: SessionDep) -> VideoDetail:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise HTTPException(404, "видео не найдено")
    return video_detail(session, video)


@router.patch("/{video_id}", response_model=VideoDetail)
def patch_video(video_id: int, body: VideoPatchRequest, session: SessionDep, ctx: CtxDep) -> VideoDetail:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise HTTPException(404, "видео не найдено")

    if body.viewed is not None:
        video.viewed = body.viewed
    if body.favorite is not None:
        video.favorite = body.favorite

    if body.folder_ids is not None:
        session.query(VideoFolder).filter(VideoFolder.video_id == video.id).delete()
        for fid in dict.fromkeys(body.folder_ids):
            if session.get(Folder, fid) is None:
                raise HTTPException(404, f"папка {fid} не найдена")
            session.add(VideoFolder(video_id=video.id, folder_id=fid))

    if body.tag_ids is not None:
        # пользовательская замена набора тегов: ai-теги восстанавливаем как были
        ai_links = [link.tag_id for link in video.tag_rows if link.source == TagSource.AI]
        session.query(VideoTag).filter(VideoTag.video_id == video.id).delete()
        for tid in dict.fromkeys(body.tag_ids):
            if session.get(Tag, tid) is None:
                raise HTTPException(404, f"тег {tid} не найден")
            source = TagSource.AI if tid in ai_links else TagSource.USER
            session.add(VideoTag(video_id=video.id, tag_id=tid, source=source))

    session.commit()
    search_mod.index_video_orm(ctx.engine, session.get(Video, video_id))
    ctx.publish({"kind": "video", "video_id": video.id, "status": str(video.status), "media_mode": str(video.media_mode), "health": str(video.health)})
    return video_detail(session, video)


@router.delete("/{video_id}")
def delete_video(video_id: int, session: SessionDep, ctx: CtxDep, hard: bool = False) -> dict:
    """Корзина FR-9.2: мягкое удаление с восстановлением; hard — с файлами."""
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "видео не найдено")
    file_path = video.file_path
    tiktok_id = video.tiktok_id
    if hard:
        session.delete(video)
        session.commit()
        search_mod.remove_video(ctx.engine, video_id)
        if file_path:
            Path(file_path).unlink(missing_ok=True)
        if tiktok_id:
            cover = canonical_cover_path(ctx.settings.server.data_dir, tiktok_id)
            cover.unlink(missing_ok=True)
        return {"deleted": True, "hard": True}
    video.deleted_at = utcnow()
    session.commit()
    return {"deleted": True, "hard": False}


@router.post("/{video_id}/restore", response_model=VideoDetail)
def restore_video(video_id: int, session: SessionDep) -> VideoDetail:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "видео не найдено")
    video.deleted_at = None
    session.commit()
    return video_detail(session, video)


@router.post("/{video_id}/notes", response_model=NoteOut, status_code=201)
def add_note(video_id: int, body: NoteCreateRequest, session: SessionDep, ctx: CtxDep) -> NoteOut:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise HTTPException(404, "видео не найдено")
    if not body.text.strip():
        raise HTTPException(422, "пустая заметка")
    note = Note(video_id=video.id, kind=body.kind, text=body.text.strip(), timestamp_sec=body.timestamp_sec)
    session.add(note)
    session.commit()
    search_mod.index_video_orm(ctx.engine, session.get(Video, video_id))
    return NoteOut.model_validate(note)


@router.patch("/{video_id}/transcript", response_model=TranscriptOut)
def patch_transcript(video_id: int, body: TranscriptPatchRequest, session: SessionDep, ctx: CtxDep) -> TranscriptOut:
    """Правка транскрипта пользователем — FR-6.8: чинит STT, улучшает поиск."""
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise HTTPException(404, "видео не найдено")
    segments = [seg.model_dump() for seg in body.segments]
    if video.transcript is None:
        video.transcript = Transcript(
            video_id=video.id,
            source=TranscriptSource.USER_EDIT,
            segments=segments,
            model=None,
        )
    else:
        video.transcript.segments = segments
        video.transcript.source = TranscriptSource.USER_EDIT
    session.commit()
    search_mod.index_video_orm(ctx.engine, session.get(Video, video_id))
    job_queue.enqueue(
        session,
        JobType.EMBED,
        video_id=video.id,
        priority=int(JobPriority.NORMAL),
        idempotency_key=f"{JobType.EMBED.value}:{video.id}",
    )
    return TranscriptOut.model_validate(video.transcript)


@router.post("/{video_id}/reanalyze", response_model=dict, status_code=202)
def reanalyze(video_id: int, body: ReanalyzeRequest, session: SessionDep, ctx: CtxDep) -> dict:
    """Повторный AI-анализ — FR-6.9; приоритет high: открытая карточка вперёд очереди."""
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise HTTPException(404, "видео не найдено")
    job_queue.enqueue(
        session,
        JobType.ANALYZE,
        video_id=video.id,
        payload={"priority": int(JobPriority.HIGH), "force": body.force},
        priority=int(JobPriority.HIGH),
        idempotency_key=f"{JobType.ANALYZE.value}:{video.id}",
    )
    ctx.publish({"kind": "video", "video_id": video.id, "status": str(video.status), "media_mode": str(video.media_mode), "health": str(video.health)})
    return {"queued": True, "force": body.force}


@router.get("/{video_id}/inbox", response_model=list[InboxItemOut])
def video_inbox_items(video_id: int, session: SessionDep) -> list[InboxItemOut]:
    rows = session.query(InboxItem).filter(InboxItem.video_id == video_id).order_by(InboxItem.created_at).all()
    return [InboxItemOut.model_validate(i) for i in rows]

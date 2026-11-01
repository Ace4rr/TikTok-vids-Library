"""Инбокс — приём ссылок с дедупликацией (FR-1.1–1.4, docs/05 §5.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from toklib_core import queue as job_queue
from toklib_core.db import InboxItem, Video
from toklib_core.enums import InboxStatus, JobPriority, JobType, VideoStatus
from toklib_core.normalize import LinkError, parse_link
from toklib_core.schemas import InboxCreateRequest, InboxItemOut, InboxResolveResponse, VideoOut

from ..deps import CtxDep, SessionDep, require_auth

router = APIRouter(prefix="/api/inbox", tags=["inbox"], dependencies=[Depends(require_auth)])


def _find_existing(session: SessionDep, tiktok_id: str | None, url: str) -> Video | None:
    query = session.query(Video).filter(Video.deleted_at.is_(None))
    if tiktok_id:
        return query.filter(Video.tiktok_id == tiktok_id).one_or_none()
    return query.filter(Video.url == url).one_or_none()


@router.post("", response_model=InboxResolveResponse, status_code=201)
def add_to_inbox(body: InboxCreateRequest, session: SessionDep, ctx: CtxDep, response: Response) -> InboxResolveResponse:
    if not body.text or not body.text.strip():
        raise HTTPException(422, "пустой текст")
    try:
        parsed = parse_link(body.text, tuple(ctx.settings.ingest.allow_extra_hosts))
    except LinkError as exc:
        raise HTTPException(400, str(exc)) from exc

    existing = _find_existing(session, parsed.tiktok_id, parsed.url)
    item = InboxItem(
        raw_text=body.text.strip(),
        url=parsed.url,
        source=body.source,
        video_id=existing.id if existing else None,
    )
    if existing is not None:
        # FR-1.4: повторная ссылка → счётчик +1, существующая карточка
        existing.save_count += 1
        item.status = InboxStatus.DUPLICATE
        session.add(item)
        session.commit()
        response.status_code = 200  # docs/05 §5.5: dedup → 200 существующее, 201 новое
        return InboxResolveResponse(
            result="exists", video=VideoOut.model_validate(existing), video_id=existing.id, inbox_item_id=item.id
        )

    video = Video(url=parsed.url, tiktok_id=parsed.tiktok_id, status=VideoStatus.INBOX)
    session.add(video)
    session.flush()
    item.video_id = video.id
    item.status = InboxStatus.NEW
    session.add(item)
    session.flush()
    job_queue.enqueue(
        session,
        JobType.RESOLVE,
        video_id=video.id,
        payload={"priority": int(JobPriority.NORMAL)},
        priority=int(JobPriority.NORMAL),
        idempotency_key=f"{JobType.RESOLVE.value}:{video.id}",
    )
    session.commit()
    ctx.publish({"kind": "inbox", "inbox_id": item.id, "status": str(InboxStatus.NEW)})
    return InboxResolveResponse(
        result="created", video=VideoOut.model_validate(video), video_id=video.id, inbox_item_id=item.id
    )


@router.get("", response_model=list[InboxItemOut])
def list_inbox(
    session: SessionDep,
    status: InboxStatus | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
) -> list[InboxItemOut]:
    query = session.query(InboxItem).order_by(InboxItem.created_at.desc())
    if status is not None:
        query = query.filter(InboxItem.status == status)
    items = query.offset(offset).limit(limit).all()
    return [InboxItemOut.model_validate(i) for i in items]

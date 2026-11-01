"""Поиск — FR-7.1/7.3: FTS по подписи/автору/тегам/заметкам + операторы author:/tag:/folder:.

Гибрид с векторами (FR-7.2) добавляется к этому же контракту в Фазе 2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from toklib_core import search as search_mod
from toklib_core.db import Video
from toklib_core.schemas import SearchHit, SearchResponse, VideoOut

from ..deps import CtxDep, SessionDep, require_auth

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])


@router.get("", response_model=SearchResponse)
def search(
    session: SessionDep,
    ctx: CtxDep,
    q: str = Query("", max_length=500),
    limit: int = Query(50, le=100),
    offset: int = 0,
) -> SearchResponse:
    result = search_mod.search(ctx.engine, q, limit=limit, offset=offset)
    ids = result["ids"]
    videos: list[Video] = []
    if ids:
        rows = session.query(Video).filter(Video.id.in_(ids), Video.deleted_at.is_(None)).all()
        by_id = {v.id: v for v in rows}
        videos = [by_id[i] for i in ids if i in by_id]
    return SearchResponse(
        total=result["total"],
        items=[SearchHit(video=VideoOut.model_validate(v)) for v in videos],
    )

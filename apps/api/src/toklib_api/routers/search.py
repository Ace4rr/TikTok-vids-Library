"""Поиск — FR-7.1/7.3: FTS + операторы author:/tag:/folder:; FR-7.2 — гибрид с
векторами (RRF), когда сконфигурирован эмбеддер и установлен sqlite-vec."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from toklib_core import search as search_mod
from toklib_core.db import Video
from toklib_core.schemas import SearchHit, SearchResponse, VideoOut

from ..deps import CtxDep, SessionDep, require_auth

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_auth)])


def _vector_provider(ctx: CtxDep) -> search_mod.VectorProvider | None:
    """(text) → (вектор, model_id) | None; None — эмбеддер не сконфигурирован.
    Дистанция считается через sqlite-vec, если тот загрузился, иначе в Python."""
    if ctx.ai_ctx.router.embedder is None:
        return None

    def provider(text: str) -> tuple[list[float], str] | None:
        return ctx.ai_ctx.router.embed_query(text)

    return provider


@router.get("", response_model=SearchResponse)
def search(
    session: SessionDep,
    ctx: CtxDep,
    q: str = Query("", max_length=500),
    limit: int = Query(50, le=100),
    offset: int = 0,
) -> SearchResponse:
    result = search_mod.search(ctx.engine, q, limit=limit, offset=offset, vector_provider=_vector_provider(ctx))
    ids = result["ids"]
    videos: list[Video] = []
    if ids:
        rows = session.query(Video).filter(Video.id.in_(ids), Video.deleted_at.is_(None)).all()
        by_id = {v.id: v for v in rows}
        videos = [by_id[i] for i in ids if i in by_id]
    return SearchResponse(
        total=result["total"],
        mode=result.get("mode", "fts"),
        items=[SearchHit(video=VideoOut.model_validate(v)) for v in videos],
    )

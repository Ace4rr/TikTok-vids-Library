"""Раздача медиа: файл с Range (перемотка), обложка, embed-редирект (docs/05 §5.11).

Пути берутся только из БД по id — никаких пользовательских путей.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from toklib_core.db import Video
from toklib_core.paths import canonical_cover_path

from ..deps import CtxDep, SessionDep, require_auth

router = APIRouter(prefix="/api/media", tags=["media"], dependencies=[Depends(require_auth)])

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _video_or_404(session: SessionDep, video_id: int) -> Video:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise HTTPException(404, "видео не найдено")
    return video


@router.get("/{video_id}/file")
def media_file(video_id: int, request: Request, session: SessionDep) -> Response:
    video = _video_or_404(session, video_id)
    if not video.file_path:
        raise HTTPException(404, "локальной копии нет (embed-режим)")
    path = Path(video.file_path)
    if not path.is_file():
        raise HTTPException(404, "файл отсутствует на диске")
    size = path.stat().st_size
    mime = video.mime or "video/mp4"

    range_header = request.headers.get("range")
    if range_header:
        match = _RANGE_RE.match(range_header.strip())
        if match:
            start = int(match.group(1)) if match.group(1) else 0
            end = int(match.group(2)) if match.group(2) else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            with path.open("rb") as fh:
                fh.seek(start)
                data = fh.read(end - start + 1)
            return Response(
                content=data,
                status_code=206,
                media_type=mime,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(data)),
                },
            )
    return FileResponse(path, media_type=mime, headers={"Accept-Ranges": "bytes"})


@router.get("/{video_id}/cover")
def media_cover(video_id: int, session: SessionDep, ctx: CtxDep) -> Response:
    video = _video_or_404(session, video_id)
    if video.tiktok_id:
        local = canonical_cover_path(ctx.settings.server.data_dir, video.tiktok_id)
        if local.is_file():
            return FileResponse(local, media_type="image/jpeg")
    if video.cover_url:
        return RedirectResponse(video.cover_url, status_code=302)
    raise HTTPException(404, "обложки нет")


@router.get("/{video_id}/embed")
def media_embed(video_id: int, session: SessionDep) -> RedirectResponse:
    """Редирект на официальный TikTok-плеер — embed-режим (FR-4.1 fallback)."""
    video = _video_or_404(session, video_id)
    if not video.tiktok_id:
        raise HTTPException(404, "tiktok_id ещё не резолвнут")
    if video.author_username:
        url = f"https://www.tiktok.com/@{video.author_username}/video/{video.tiktok_id}"
    else:
        url = f"https://www.tiktok.com/@-/video/{video.tiktok_id}"
    return RedirectResponse(url, status_code=302)

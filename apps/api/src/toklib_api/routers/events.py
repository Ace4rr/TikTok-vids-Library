"""SSE-эндпоинт: поток статусов обработки (docs/05 §5.5, docs/09 §9.9)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..deps import CtxDep, require_auth
from ..events import sse_stream

router = APIRouter(tags=["events"], dependencies=[Depends(require_auth)])


@router.get("/api/events")
def events(ctx: CtxDep) -> StreamingResponse:
    return StreamingResponse(
        sse_stream(ctx.hub),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )

"""FastAPI-зависимости: контекст, сессия БД, single-token аутентификация (docs/05 §5.11)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


CtxDep = Annotated[AppContext, Depends(get_ctx)]


def get_session(ctx: CtxDep) -> Session:
    session = ctx.session()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def require_auth(request: Request, ctx: CtxDep) -> None:
    """Статический токен single-user. Без токена в настройках — открытый режим
    (локальная разработка); в compose токен обязателен."""
    token = ctx.settings.token
    if not token:
        return

    media_path = request.url.path.startswith("/api/media")
    query_token = request.query_params.get("token")
    if media_path and query_token == token:
        return

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and header[7:].strip() == token:
        return
    if header.strip() == token:  # простой X-подобный формат без схемы
        return

    raise HTTPException(status_code=401, detail="нужен токен доступа (Authorization: Bearer <TOKLIB_TOKEN>)")

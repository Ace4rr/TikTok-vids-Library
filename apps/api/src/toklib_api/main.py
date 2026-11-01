"""Точка входа: create_app + lifespan (воркеры в threads) + `python -m toklib_api.main`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from toklib_core.config import Settings, load_settings
from toklib_core.db import utcnow
from toklib_core.paths import ensure_dirs

from .context import AppContext
from .routers import all_routers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_app(settings: Settings | None = None, env: dict | None = None) -> FastAPI:
    settings = settings or load_settings(env)
    ensure_dirs(settings.server.data_dir)

    ctx = AppContext.build(settings, env)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        ctx.hub.bind_loop(asyncio.get_running_loop())
        if settings.server.run_workers:
            ctx.start_workers()
            logging.getLogger("toklib").info("воркеры запущены (run_workers=true)")
        yield
        ctx.stop_workers()

    app = FastAPI(title="TokLib", version="0.1.0", lifespan=lifespan)
    app.state.ctx = ctx

    if settings.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"ok": True, "version": "0.1.0", "time": utcnow().isoformat()}

    for router in all_routers:
        app.include_router(router)

    web_dist = settings.server.web_dist
    if web_dist and Path(web_dist).is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


def main() -> None:
    """Запуск одного контейнера MVP: API + воркеры (docs/05 §5.8)."""
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "toklib_api.main:create_app",
        factory=True,
        host=settings.server.host,
        port=settings.server.port,
    )


if __name__ == "__main__":
    main()

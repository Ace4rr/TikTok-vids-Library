"""SSE-хаб: события статусов обработки для UI (docs/05 §5.5).

Воркеры живут в threads и публикуют через loop.call_soon_threadsafe;
WebSocket сознательно не используется (docs/06: SSE проще через reverse-proxy).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 200


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        if self._loop is None or self._loop.is_closed():
            return

        def _put() -> None:
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("подписчик SSE переполнен — событие пропущено")

        try:
            self._loop.call_soon_threadsafe(_put)
        except RuntimeError:  # loop закрывается при остановке
            pass

    @staticmethod
    def format_sse(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def sse_stream(hub: EventHub, heartbeat_seconds: float = 15.0) -> AsyncIterator[str]:
    """Поток событий с heartbeat; ping кладёт в ту же очередь фоновая задача —
    никаких отмен get() и потерь событий."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)

    async def _heartbeat() -> None:
        while queue in hub._subscribers:
            await asyncio.sleep(heartbeat_seconds)
            try:
                queue.put_nowait({"kind": "ping"})
            except asyncio.QueueFull:
                pass

    hub._subscribers.add(queue)
    heartbeat = asyncio.create_task(_heartbeat())
    try:
        yield hub.format_sse({"kind": "hello"})
        while True:
            event = await queue.get()
            yield hub.format_sse(event)
    finally:
        hub._subscribers.discard(queue)
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

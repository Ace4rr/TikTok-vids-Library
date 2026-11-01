"""Потоки-воркеры: опрос очереди, lease, dispatch, retry-механика (NFR-3: UI не блокируется)."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import traceback

from toklib_core import queue as job_queue
from toklib_core.enums import JobType

from .registry import dispatch, handle_dead_letter

log = logging.getLogger(__name__)


class WorkerThread(threading.Thread):
    def __init__(self, ctx, name: str, types: list[JobType], stop_event: threading.Event) -> None:
        super().__init__(name=f"toklib-{name}", daemon=True)
        self.ctx = ctx
        self.types = types
        self.stop_event = stop_event
        self.poll_seconds = ctx.settings.server.workers_poll_seconds
        self.lease_seconds = ctx.settings.server.lease_seconds

    def run(self) -> None:
        log.info("воркер %s запущен: типы=%s", self.name, [t.value for t in self.types])
        # Lease мог истечь, пока процесс был выключен
        session = self.ctx.session()
        try:
            job_queue.reap_expired_leases(session, self.lease_seconds)
        finally:
            session.close()

        while not self.stop_event.is_set():
            session = self.ctx.session()
            try:
                # lazy_backlog (docs/07 §7.6): backlog-задачи — только в ночном окне
                budgets = self.ctx.settings.ai.budgets
                allow_backlog = (not budgets.lazy_backlog) or self.ctx.settings.ai.night_window.in_window(
                    dt.datetime.now().astimezone()
                )
                claim = job_queue.claim_next(
                    session, self.name, self.types, self.lease_seconds, allow_backlog=allow_backlog
                )
                if claim is None:
                    session.close()
                    self.stop_event.wait(self.poll_seconds)
                    continue

                outcome: str
                try:
                    outcome = dispatch(self.ctx, session, claim)
                except Exception as exc:  # noqa: BLE001 — любая ошибка стадии → retry-механика
                    log.error("задача %s#%s упала: %s\n%s", claim.type, claim.id, exc, traceback.format_exc(limit=3))
                    status = job_queue.fail(session, claim.id, str(exc))
                    if status == "dead":
                        handle_dead_letter(self.ctx, session, claim.type, claim.payload, str(exc))
                    session.close()
                    continue

                if outcome == "rescheduled":
                    # воркер сам перенёс задачу (бюджет) — не трогаем
                    pass
                else:
                    job_queue.complete(session, claim.id)
                session.close()
            except Exception:
                log.exception("ошибка цикла воркера %s", self.name)
                try:
                    session.rollback()
                    session.close()
                except Exception as close_exc:  # noqa: BLE001
                    log.warning("сессия воркера %s не закрылась: %s", self.name, close_exc)
                self.stop_event.wait(1.0)
        log.info("воркер %s остановлен", self.name)

"""Очередь задач на таблице job — lease-семантика и backoff (docs/05 §5.4, docs/09 §9.4).

Захват задачи — один атомарный UPDATE…RETURNING: безопасен и для SQLite
(блокировка всей БД на запись), и для PostgreSQL.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .db import Job, utcnow
from .enums import JobPriority, JobStatus, JobType

BACKOFF_MINUTES = (1, 5, 30)  # docs/05 §5.9
DEFAULT_MAX_ATTEMPTS = 5

_CLAIM_SQL = text(
    """
    UPDATE job SET
        status = 'running', locked_by = :worker, locked_at = :now, attempts = attempts + 1,
        updated_at = :now
    WHERE id = (
        SELECT id FROM job
        WHERE status = 'pending' AND run_after <= :now
          AND (priority < :backlog_priority OR :allow_backlog)
          AND type IN :types
        ORDER BY priority ASC, run_after ASC, id ASC
        LIMIT 1
    )
    RETURNING id, type, payload, attempts, priority, max_attempts, idempotency_key
    """
).bindparams(bindparam("types", expanding=True))


@dataclass
class JobClaim:
    id: int
    type: JobType
    payload: dict[str, Any]
    attempts: int
    priority: int
    max_attempts: int
    idempotency_key: str | None


def enqueue(
    session: Session,
    job_type: JobType,
    *,
    video_id: int | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = int(JobPriority.NORMAL),
    idempotency_key: str | None = None,
    run_after: dt.datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Job:
    """Поставить задачу. Повтор ключа idempotency в pending/running — вернёт существующую."""
    body = dict(payload or {})
    if video_id is not None:
        body.setdefault("video_id", video_id)

    if idempotency_key:
        existing = (
            session.query(Job)
            .filter(
                Job.idempotency_key == idempotency_key,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
            .one_or_none()
        )
        if existing:
            return existing

    job = Job(
        type=job_type,
        payload=body,
        priority=priority,
        run_after=run_after or utcnow(),
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.commit()
    return job


def claim_next(
    session: Session,
    worker: str,
    types: Sequence[JobType],
    lease_seconds: int = 600,
    allow_backlog: bool = True,
) -> JobClaim | None:
    """Захват задачи. allow_backlog=False (день при lazy_backlog) — backlog-задачи
    (priority ≥ JobPriority.BACKLOG) не берутся до ночного окна (docs/07 §7.6)."""
    now = utcnow()
    row = session.execute(
        _CLAIM_SQL,
        {
            "worker": worker,
            "now": now,
            "types": [t.value for t in types],
            "lease": lease_seconds,
            "backlog_priority": int(JobPriority.BACKLOG),
            "allow_backlog": 1 if allow_backlog else 0,
        },
    ).first()
    if row is None:
        session.rollback()
        return None
    session.commit()
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload or "{}")
    return JobClaim(
        id=row.id,
        type=JobType(row.type if isinstance(row.type, str) else row.type.value),
        payload=payload,
        attempts=row.attempts,
        priority=row.priority,
        max_attempts=row.max_attempts,
        idempotency_key=row.idempotency_key,
    )


def complete(session: Session, job_id: int) -> None:
    session.query(Job).filter(Job.id == job_id).update(
        {"status": JobStatus.DONE, "locked_by": None, "last_error": None}
    )
    session.commit()


def fail(
    session: Session,
    job_id: int,
    error: str,
    *,
    backoff_minutes: Sequence[int] = BACKOFF_MINUTES,
) -> str:
    """Повторяемая ошибка → pending с backoff; исчерпание → dead-letter (failed).

    Возвращает "retry" | "dead". Обновление карточки при dead-letter делает воркер.
    """
    job = session.get(Job, job_id)
    if job is None:
        return "dead"
    job.last_error = error[:2000]
    job.locked_by = None
    if job.attempts < job.max_attempts:
        delay = backoff_minutes[min(job.attempts, len(backoff_minutes)) - 1]
        job.status = JobStatus.PENDING
        job.run_after = utcnow() + dt.timedelta(minutes=delay)
        session.commit()
        return "retry"
    job.status = JobStatus.FAILED
    session.commit()
    return "dead"


def reap_expired_leases(session: Session, lease_seconds: int) -> int:
    """Возврат задач с истёкшим lease в очередь (после падения воркера)."""
    cutoff = utcnow() - dt.timedelta(seconds=lease_seconds)
    count = (
        session.query(Job)
        .filter(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)
        .update({"status": JobStatus.PENDING, "locked_by": None, "locked_at": None})
    )
    session.commit()
    return count


def queue_depths(session: Session) -> dict[str, int]:
    rows = session.execute(
        text("SELECT type, count(*) FROM job WHERE status IN ('pending','running') GROUP BY type")
    ).all()
    return {str(t): int(c) for t, c in rows}


def dead_letter_count(session: Session) -> int:
    return int(session.query(Job).filter(Job.status == JobStatus.FAILED).count())


def download_success_ratio(session: Session, hours: float = 24.0) -> float | None:
    """Телеметрия деградации (docs/05 §5.7): доля успешных download за окно."""
    cutoff = utcnow() - dt.timedelta(hours=hours)
    rows = session.execute(
        text("SELECT status, count(*) FROM job WHERE type='download' AND created_at >= :c GROUP BY status"),
        {"c": cutoff},
    ).all()
    by_status = {str(s): int(c) for s, c in rows}
    total = sum(by_status.values())
    if total == 0:
        return None
    return by_status.get("done", 0) / total

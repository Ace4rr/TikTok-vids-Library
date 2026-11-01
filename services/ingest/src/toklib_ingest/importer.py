"""Импорт TikTok data export (FR-1.5/9.3, Фаза 3) — «Скачать мои данные» (JSON).

Формат архива волатилен (разделы Activity/Favorite Videos и т.п.), поэтому парсер
эвристический: рекурсивно собираем из JSON все строки с tiktok-ссылками; дату
берём из соседнего поля Date, если она есть. Массовый импорт идёт лениво:
backlog-приоритет — Tier-0 сразу после резолва, полный AI ночью (docs/07 §7.6).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session
from toklib_core import queue as job_queue
from toklib_core.db import InboxItem, Video, utcnow
from toklib_core.enums import InboxStatus, JobPriority, JobType, VideoStatus
from toklib_core.normalize import extract_url, parse_link

log = logging.getLogger(__name__)

_TIKTOK_URL_RE = re.compile(r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/[^\s\"'<>\\]+")
_DATE_KEY_RE = re.compile(r"date|time", re.IGNORECASE)


def extract_tiktok_links(node: Any, *, _near_date: str | None = None) -> list[tuple[str, str | None]]:
    """(url, date_str|None) для каждой tiktok-ссылки в JSON-структуре любой формы."""
    found: list[tuple[str, str | None]] = []
    if isinstance(node, dict):
        date_hint = _near_date
        for key, value in node.items():
            if isinstance(value, str) and _DATE_KEY_RE.search(str(key)) and value:
                date_hint = str(value)
        for value in node.values():
            found.extend(extract_tiktok_links(value, _near_date=date_hint))
    elif isinstance(node, list):
        for item in node:
            found.extend(extract_tiktok_links(item, _near_date=_near_date))
    elif isinstance(node, str):
        for match in _TIKTOK_URL_RE.finditer(node):
            url = extract_url(match.group(0)) or match.group(0)
            found.append((url, _near_date))
    # дедуп с сохранением порядка
    seen: set[str] = set()
    unique: list[tuple[str, str | None]] = []
    for url, date in found:
        if url not in seen:
            seen.add(url)
            unique.append((url, date))
    return unique


def parse_data_export(data: bytes | str) -> list[tuple[str, str | None]]:
    """JSON-архив → список ссылок. Нечувствителен к структуре разделов."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    try:
        payload = json.loads(data)
    except ValueError:
        # иногда архив прикладывают как .txt с ссылками построчно
        links = []
        for line in data.splitlines():
            match = _TIKTOK_URL_RE.search(line)
            if match:
                links.append((match.group(0).rstrip(".,)"), None))
        return links
    return extract_tiktok_links(payload)


def _parse_export_date(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def import_links(
    session: Session,
    links: list[tuple[str, str | None]],
    *,
    source: str = "import",
) -> dict:
    """Дедуп + карточки + очередь. Возвращает сводку для UI/CLI.

    Импорт ленивый (docs/07 №7): приоритет backlog — полный AI только ночью,
    Tier-0 теги карточки получают сразу после резолва.
    """
    from toklib_core.enums import InboxSource

    added = duplicated = skipped = 0
    for url, date_raw in links:
        try:
            parse_link(url)
        except Exception:  # noqa: BLE001 — нераспознанная ссылка не должна ронять импорт
            log.warning("импорт: пропущена нераспознанная ссылка %r", url[:200])
            skipped += 1
            continue
        parsed_date = _parse_export_date(date_raw)
        tiktok_id = _id_from_url(url)
        query = session.query(Video).filter(Video.deleted_at.is_(None))
        if tiktok_id:
            existing = query.filter(Video.tiktok_id == tiktok_id).one_or_none()
        else:
            existing = query.filter(Video.url == url).one_or_none()
        if existing is not None:
            existing.save_count += 1
            duplicated += 1
            continue
        video = Video(url=url, tiktok_id=tiktok_id, status=VideoStatus.INBOX, saved_at=parsed_date or utcnow())
        session.add(video)
        session.flush()
        session.add(
            InboxItem(
                raw_text=url,
                url=url,
                source=InboxSource(source) if source in InboxSource.__members__ else InboxSource.IMPORT,
                status=InboxStatus.NEW,
                video_id=video.id,
            )
        )
        session.flush()
        job_queue.enqueue(
            session,
            JobType.RESOLVE,
            video_id=video.id,
            payload={"priority": int(JobPriority.BACKLOG)},
            priority=int(JobPriority.BACKLOG),
            idempotency_key=f"{JobType.RESOLVE.value}:{video.id}",
        )
        added += 1
    session.commit()
    return {"added": added, "duplicates": duplicated, "skipped": skipped, "links_found": len(links)}


def _id_from_url(url: str) -> str | None:
    from toklib_core.normalize import parse_link

    try:
        return parse_link(url).tiktok_id
    except Exception:  # noqa: BLE001 — мусорная строка не должна ронять импорт
        return None

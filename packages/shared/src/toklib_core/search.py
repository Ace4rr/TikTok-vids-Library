"""Полнотекстовый поиск FTS5 + операторы author:/tag:/folder: — docs/09 §9.7.

Внешнее содержимое: строки синхронизируются кодом (без триггеров) после
resolve/analyze/правок — контролируемо и переносимо на PostgreSQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS video_fts USING fts5(
    video_id UNINDEXED, caption, author, tags, notes, transcript, summary
)
"""


def ensure_fts(conn: Connection) -> None:
    conn.exec_driver_sql(_FTS_DDL)
    conn.commit()


def index_video(
    conn: Connection,
    video_id: int,
    *,
    caption: str = "",
    author: str = "",
    tags: str = "",
    notes: str = "",
    transcript: str = "",
    summary: str = "",
) -> None:
    conn.execute(text("DELETE FROM video_fts WHERE video_id = :id"), {"id": video_id})
    conn.execute(
        text(
            "INSERT INTO video_fts(video_id, caption, author, tags, notes, transcript, summary) "
            "VALUES (:id, :caption, :author, :tags, :notes, :transcript, :summary)"
        ),
        {
            "id": video_id,
            "caption": caption,
            "author": author,
            "tags": tags,
            "notes": notes,
            "transcript": transcript,
            "summary": summary,
        },
    )
    conn.commit()


def remove_video(conn: Connection, video_id: int) -> None:
    conn.execute(text("DELETE FROM video_fts WHERE video_id = :id"), {"id": video_id})
    conn.commit()


def reindex_all(engine: Engine) -> int:
    """Полная пересборка индекса из БД (используется при правках схемы поиска)."""
    from sqlalchemy.orm import sessionmaker

    from .db import Video

    indexed = 0
    with engine.connect() as conn:
        ensure_fts(conn)
    conn = engine.connect()
    try:
        conn.execute(text("DELETE FROM video_fts"))
        conn.commit()
    finally:
        conn.close()

    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        for video in session.query(Video).filter(Video.deleted_at.is_(None)).all():
            index_video_orm(engine, video)
            indexed += 1
    finally:
        session.close()
    return indexed


def index_video_orm(engine: Engine, video) -> None:
    """Индексация карточки по ORM-объекту (после resolve/analyze/правок заметок)."""
    tag_names = [link.tag.name for link in video.tag_rows if link.tag is not None]
    note_texts = [note.text for note in video.notes]
    transcript_text = ""
    if video.transcript and video.transcript.segments:
        transcript_text = " ".join(
            seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in video.transcript.segments
        )
    summary = ""
    analyses = sorted(video.analyses, key=lambda a: a.created_at, reverse=True) if video.analyses else []
    if analyses and analyses[0].summary:
        summary = analyses[0].summary
    with engine.connect() as conn:
        index_video(
            conn,
            video.id,
            caption=video.caption or "",
            author=" ".join(filter(None, [video.author_username, video.author_display])),
            tags=" ".join(tag_names),
            notes=" ".join(note_texts),
            transcript=transcript_text,
            summary=summary,
        )


@dataclass
class ParsedQuery:
    authors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    text: str = ""


_OP_RE = re.compile(r"(author|tag|folder):(\S+)")


def parse_query(q: str) -> ParsedQuery:
    parsed = ParsedQuery()
    rest = q or ""
    for op, value in _OP_RE.findall(rest):
        {"author": parsed.authors, "tag": parsed.tags, "folder": parsed.folders}[op].append(value.strip('"'))
    parsed.text = _OP_RE.sub(" ", rest).strip()
    return parsed


def _fts_expression(text_query: str) -> str | None:
    """Безопасное FTS-выражение: префиксный поиск по токенам, OR между ними."""
    tokens = re.findall(r"[\wа-яё]+", text_query.lower(), re.UNICODE)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"*' for t in tokens)


def search(
    engine: Engine,
    q: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Гибридная заготовка: пока FTS (BM25); векторный ранг добавляется к этому же контракту."""
    parsed = parse_query(q)
    where: list[str] = ["v.deleted_at IS NULL"]
    params: dict[str, Any] = {}
    join_fts = ""
    order = "v.saved_at DESC"

    if parsed.authors:
        where.append("(" + " OR ".join(f"v.author_username = :a{i}" for i in range(len(parsed.authors))) + ")")
        for i, author in enumerate(parsed.authors):
            params[f"a{i}"] = author.lstrip("@")
    if parsed.tags:
        for i, tag in enumerate(parsed.tags):
            where.append(
                f"EXISTS (SELECT 1 FROM video_tag vt JOIN tag t ON t.id = vt.tag_id "
                f"WHERE vt.video_id = v.id AND lower(t.name) = :tag{i})"
            )
            params[f"tag{i}"] = tag.lower().lstrip("#")
    if parsed.folders:
        for i, folder in enumerate(parsed.folders):
            where.append(
                f"EXISTS (SELECT 1 FROM video_folder vf JOIN folder f ON f.id = vf.folder_id "
                f"WHERE vf.video_id = v.id AND (lower(f.name) = :folder{i} OR lower(f.slug) = :folder{i}))"
            )
            params[f"folder{i}"] = folder.lower()

    expr = _fts_expression(parsed.text)
    if expr:
        join_fts = "JOIN video_fts f ON f.video_id = v.id"
        where.append("video_fts MATCH :expr")
        params["expr"] = expr
        order = "rank"

    where_sql = " AND ".join(where)
    params["limit"], params["offset"] = limit, offset

    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT count(*) FROM video v {join_fts} WHERE {where_sql}"), params
        ).scalar_one()
        rows = conn.execute(
            text(f"SELECT v.id FROM video v {join_fts} WHERE {where_sql} ORDER BY {order} LIMIT :limit OFFSET :offset"),
            params,
        ).all()

    return {"total": int(total), "ids": [int(r[0]) for r in rows]}

"""Поиск — FTS5 (BM25) + гибрид с векторами через sqlite-vec, слияние RRF.

docs/09 §9.7: строки FTS синхронизируются кодом (без триггеров); векторная
ветка — необязательная (sqlite-vec не установлен → честная деградация до FTS,
NFR-10). Операторы author:/tag:/folder: применяются в обеих ветках одинаково.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine

log = logging.getLogger(__name__)

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS video_fts USING fts5(
    video_id UNINDEXED, caption, author, tags, notes, transcript, summary
)
"""

# константа RRF (reciprocal rank fusion)
RRF_K = 60
VECTOR_TOP_K = 50


def ensure_fts(conn: Connection) -> None:
    conn.exec_driver_sql(_FTS_DDL)
    conn.commit()


def enable_vector_search(engine: Engine) -> bool:
    """Подключить sqlite-vec ко всем соединениям движка (ускорение векторной ветки).

    False — не фатально: _vector_branch умеет считать косинусную дистанцию в Python
    (на персональных масштабах это тысячи векторов — незаметно). Часть сборок
    CPython (например, uv-managed) запрещает загрузку sqlite-расширений.
    """
    try:
        import sqlite_vec
    except ImportError:
        log.info("sqlite-vec не установлен — векторная ветка считает дистанцию в Python")
        return False

    @event.listens_for(engine, "connect")
    def _load_vec(dbapi_conn, _record):
        sqlite_vec.load(dbapi_conn)

    # проверка на живом соединении: функция должна появиться
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT vec_f32('[1.0,2.0]')"))
        return True
    except Exception as exc:  # noqa: BLE001 — любая ошибка → питонный fallback
        log.info("sqlite-vec не загрузился (%s) — векторная ветка считает дистанцию в Python", exc)
        try:
            event.remove(engine, "connect", _load_vec)
        except Exception as remove_exc:  # noqa: BLE001
            log.debug("listener sqlite-vec не снялся: %s", remove_exc)
        return False


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


def _operator_where(parsed: ParsedQuery, params: dict) -> str:
    """Общий SQL-фрагмент фильтров-операторов для FTS- и векторной веток."""
    where: list[str] = []
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
    return (" AND " + " AND ".join(where)) if where else ""


def _fts_branch(
    engine: Engine,
    parsed: ParsedQuery,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    where = ["v.deleted_at IS NULL"]
    params: dict[str, Any] = {}
    join_fts = ""
    order = "v.saved_at DESC"
    expr = _fts_expression(parsed.text)
    if expr:
        join_fts = "JOIN video_fts f ON f.video_id = v.id"
        where.append("video_fts MATCH :expr")
        params["expr"] = expr
        order = "rank"
    ops = _operator_where(parsed, params)
    where_sql = " AND ".join(where) + ops
    params["limit"], params["offset"] = limit, offset

    with engine.connect() as conn:
        total = int(conn.execute(text(f"SELECT count(*) FROM video v {join_fts} WHERE {where_sql}"), params).scalar_one())
        rows = conn.execute(
            text(f"SELECT v.id FROM video v {join_fts} WHERE {where_sql} ORDER BY {order} LIMIT :limit OFFSET :offset"),
            params,
        ).all()
    return {"total": total, "ids": [int(r[0]) for r in rows], "mode": "fts" if expr else "filters"}


def _cosine_rank_python(rows: list, query: list[float], top_k: int) -> list[int]:
    """Fallback без sqlite-vec: косинусная дистанция в Python по float32 BLOB-ам
    (масштаб персональной библиотеки — тысячи векторов)."""
    import math
    from array import array

    q = array("f", query)
    q_norm = math.sqrt(sum(x * x for x in q)) or 1.0
    scored: list[tuple[float, int]] = []
    for entity_id, blob in rows:
        vec = array("f")
        vec.frombytes(bytes(blob))
        if len(vec) != len(q):
            continue  # векторы других размерностей не смешиваются (8.4)
        v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        dot = sum(a * b for a, b in zip(q, vec, strict=True))
        scored.append((1.0 - dot / (q_norm * v_norm), int(entity_id)))
    scored.sort()
    return [vid for _, vid in scored[:top_k]]


def _vector_branch(
    engine: Engine,
    parsed: ParsedQuery,
    vector: list[float],
    model_id: str,
    top_k: int = VECTOR_TOP_K,
) -> list[int]:
    """K ближайших карточек по косинусной дистанции; операторы применяются так же, как в FTS.

    sqlite-vec (SQL-функция vec_distance_cosine) — если доступен, иначе Python-fallback.
    """
    where = ["v.deleted_at IS NULL", "e.entity_type = 'video'", "e.model_id = :model"]
    params: dict[str, Any] = {"model": model_id, "qv": json.dumps([float(x) for x in vector])}
    ops = _operator_where(parsed, params)
    where_sql = " AND ".join(where) + ops

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT e.entity_id FROM embedding e JOIN video v ON v.id = e.entity_id "
                    f"WHERE {where_sql} "
                    f"ORDER BY vec_distance_cosine(vec_f32(:qv), e.vector) LIMIT :k"
                ),
                {**params, "k": top_k},
            ).all()
        return [int(r[0]) for r in rows]
    except Exception as exc:  # noqa: BLE001 — расширения нет/не загрузилось → питонный путь
        log.debug("векторный SQL-путь недоступен (%s) — считаем в Python", exc)
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT e.entity_id, e.vector FROM embedding e JOIN video v ON v.id = e.entity_id WHERE {where_sql}"),
                params,
            ).all()
        return _cosine_rank_python(rows, vector, top_k)


def _rrf_merge(*ranked_lists: list[int]) -> list[int]:
    """Reciprocal rank fusion: score = Σ 1/(k + rank), ранжирование по убыванию."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for position, video_id in enumerate(ranked, start=1):
            scores[video_id] = scores.get(video_id, 0.0) + 1.0 / (RRF_K + position)
    return [vid for vid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


VectorProvider = Callable[[str], "tuple[list[float], str] | None"]


def search(
    engine: Engine,
    q: str,
    *,
    limit: int = 50,
    offset: int = 0,
    vector_provider: VectorProvider | None = None,
) -> dict[str, Any]:
    """Гибридный поиск. vector_provider(text) → (вектор, model_id) | None —
    подключается API-слоем (эмбеддер может быть недоступен)."""
    parsed = parse_query(q)
    base = _fts_branch(engine, parsed, limit, offset)

    vector_used = False
    if vector_provider and parsed.text:
        try:
            provided = vector_provider(parsed.text)
        except Exception as exc:  # noqa: BLE001 — эмбеддер не ответствал → FTS-only
            log.warning("векторная ветка не удалась (%s) — только FTS", exc)
            provided = None
        if provided is not None:
            vector, model_id = provided
            try:
                vector_ids = _vector_branch(engine, parsed, vector, model_id)
            except Exception as exc:  # noqa: BLE001 — расширения нет / schema рассинхронена
                log.warning("векторный запрос не удался (%s) — только FTS", exc)
            else:
                if vector_ids:
                    fused = _rrf_merge(base["ids"], vector_ids)
                    # пагинация по слитому списку; fts_total остаётся честным нижним пределом
                    total = max(base["total"], len(vector_ids))
                    return {
                        "total": total,
                        "ids": fused[offset : offset + limit],
                        "mode": "hybrid",
                    }
                vector_used = True  # вектор есть, но совпадений нет — отдаём FTS как есть

    if vector_used:
        base["mode"] = "hybrid"
    return base

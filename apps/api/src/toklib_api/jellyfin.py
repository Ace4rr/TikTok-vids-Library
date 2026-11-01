"""Jellyfin-совместимый экспорт (FR-8.2) — генерируемое представление поверх неизменяемых
данных (Р5, docs/05 §5.10): export/jellyfin/<Папка>/<tiktok-id>/{video.mp4, poster.jpg, movie.nfo}.

Hardlink требует одной ФС с media/; иначе копия. Jellyfin сканирует каталог как
библиотеку «Домашние видео» — плагин не нужен.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session
from toklib_core.db import Video
from toklib_core.paths import canonical_cover_path

log = logging.getLogger(__name__)

_UNSAFE_RE = re.compile(r"[\\/:*?\"<>|]+")


def _safe_dir_name(name: str) -> str:
    cleaned = _UNSAFE_RE.sub("_", name).strip().rstrip(".")
    return cleaned[:128] or "Unsorted"


def _hardlink_or_copy(src: Path, dest: Path) -> None:
    try:
        if dest.exists() and dest.samefile(src):
            return  # уже слинковано — идемпотентность
    except OSError:
        pass
    try:
        if dest.exists():
            dest.unlink()
        dest.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dest)  # разные ФС — копия


def build_nfo(video: Video) -> str:
    """movie.nfo: заголовок = подпись, описание = саммари + ключевая идея (FR-8.2)."""
    title = (video.caption or f"TikTok {video.tiktok_id}").strip()[:300]
    analysis = max(video.analyses, key=lambda a: a.created_at) if video.analyses else None
    plot_parts = [analysis.summary] if analysis and analysis.summary else []
    if analysis and analysis.key_idea:
        plot_parts.append(f"Ключевая идея: {analysis.key_idea}")
    plot = "\n\n".join(plot_parts) or ""
    tags = [link.tag.name for link in video.tag_rows if link.tag is not None]
    if analysis and analysis.category:
        tags.append(analysis.category)

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        "<movie>",
        f"  <title>{escape(title)}</title>",
        f"  <plot>{escape(plot)}</plot>",
        f"  <uniqueid type=\"tiktok\" default=\"true\">{escape(str(video.tiktok_id or ''))}</uniqueid>",
    ]
    if video.author_username:
        lines.append(f"  <director>{escape('@' + video.author_username)}</director>")
    if video.published_at:
        lines.append(f"  <year>{video.published_at.year}</year>")
        lines.append(f"  <aired>{video.published_at.date().isoformat()}</aired>")
    for tag in tags[:20]:
        lines.append(f"  <tag>{escape(tag)}</tag>")
    if video.url:
        lines.append(f"  <trailer>{escape(video.url)}</trailer>")
    lines.append("</movie>")
    return "\n".join(lines)


def export_jellyfin(session: Session, data_dir: Path) -> dict:
    """Собрать/пересобрать дерево экспорта. Идемпотентно: перезаписывает NFO, файлы — hardlink."""
    root = data_dir / "export" / "jellyfin"
    root.mkdir(parents=True, exist_ok=True)
    exported = 0
    skipped = 0

    for video in session.query(Video).filter(Video.deleted_at.is_(None)).all():
        src = Path(video.file_path) if video.file_path else None
        if not src or not src.is_file() or not video.tiktok_id:
            skipped += 1
            continue
        folder_names = sorted({link.folder.name for link in video.folder_rows if link.folder}) or ["Unsorted"]
        nfo = build_nfo(video)
        for folder_name in folder_names:
            dest_dir = root / _safe_dir_name(folder_name) / str(video.tiktok_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            _hardlink_or_copy(src, dest_dir / f"video{src.suffix or '.mp4'}")
            cover = canonical_cover_path(data_dir, video.tiktok_id)
            if cover.is_file():
                _hardlink_or_copy(cover, dest_dir / "poster.jpg")
            (dest_dir / "movie.nfo").write_text(nfo, encoding="utf-8")
            exported += 1

    return {"exported": exported, "skipped_no_file": skipped, "root": str(root)}

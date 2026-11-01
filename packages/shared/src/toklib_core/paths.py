"""Канонические пути и диск — docs/05 §5.10 (Р5: путь зависит только от tiktok_id)."""

from __future__ import annotations

import shutil
from pathlib import Path


def media_dir(data_dir: Path) -> Path:
    return data_dir / "media"


def canonical_media_path(data_dir: Path, tiktok_id: str, ext: str = ".mp4") -> Path:
    """media/<id[0:2]>/<id[2:4]>/<tiktok_id>.mp4 — неизменяем, папки его не двигают."""
    tiktok_id = tiktok_id.zfill(4)
    return media_dir(data_dir) / tiktok_id[:2] / tiktok_id[2:4] / f"{tiktok_id}{ext}"


def canonical_cover_path(data_dir: Path, tiktok_id: str) -> Path:
    return canonical_media_path(data_dir, tiktok_id, ext=".jpg")


def tmp_dir(data_dir: Path) -> Path:
    """Временные аудио для STT (16 кГц mono), удаляются после транскрипции."""
    return data_dir / "tmp"


def ensure_dirs(data_dir: Path) -> None:
    for path in (data_dir, media_dir(data_dir), tmp_dir(data_dir)):
        path.mkdir(parents=True, exist_ok=True)


def disk_usage_pct(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.used / usage.total * 100.0

"""Tier-0 — словарные автотеги без моделей (FR-6.1, docs/07 §7.2).

Работают всегда и сразу после резолва метаданных: миллисекунды CPU, ноль расходов.
Словарь пользователя лежит в <data_dir>/tier0-dictionary.yaml (копируется при
первом запуске из пакета и правится пользователем).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session
from toklib_core.db import Tag, Video, VideoTag, utcnow
from toklib_core.enums import TagKind, TagSource


def default_dictionary_path() -> Path:
    from importlib.resources import files

    return files("toklib_ai").joinpath("data/tier0-dictionary.yaml")  # type: ignore[return-value]


def ensure_dictionary(data_dir: Path) -> Path:
    """Скопировать дефолтный словарь в data_dir, если его там ещё нет."""
    target = data_dir / "tier0-dictionary.yaml"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(default_dictionary_path()), target)
    return target


def load_dictionary(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("categories", {})
    data.setdefault("tag_aliases", {})
    data.setdefault("max_tags", 5)
    return data


def detect_language(caption: str | None) -> str | None:
    """Детект языка по алфавиту без моделей: кириллица/латиница (docs/07 §7.2 Tier-0)."""
    if not caption:
        return None
    cyr = sum(1 for ch in caption if "\u0400" <= ch <= "\u04FF")
    lat = sum(1 for ch in caption if ch.isascii() and ch.isalpha())
    letters = cyr + lat
    if letters == 0:
        return None
    if cyr / letters >= 0.3:
        return "ru"
    if lat / letters >= 0.5:
        return "en"
    return None


def tier0_tags(
    caption: str | None,
    hashtags: list[str],
    dictionary: dict[str, Any],
) -> list[str]:
    """Теги по словарю: ключевые слова подписи + прямые соответствия хэштегов."""
    caption_lower = (caption or "").lower()
    categories: dict[str, list[str]] = dictionary["categories"]
    aliases: dict[str, str] = dictionary["tag_aliases"]
    max_tags = int(dictionary.get("max_tags", 5))

    found: list[str] = []
    for tag_name, keywords in categories.items():
        for keyword in keywords:
            if keyword.lower() in caption_lower:
                if tag_name not in found:
                    found.append(tag_name)
                break

    for hashtag in hashtags or []:
        alias = aliases.get(f"#{hashtag.lower().lstrip('#')}")
        if alias and alias not in found:
            found.append(alias)

    return found[:max_tags]


def upsert_ai_tags(session: Session, video: Video, names: list[str], *, kind: TagKind = TagKind.AI) -> None:
    """Создать/привязать теги источника ai; повторный прогон не создаёт дублей."""
    for name in names:
        name = name.strip()
        if not name:
            continue
        tag = session.query(Tag).filter(Tag.name == name).one_or_none()
        if tag is None:
            tag = Tag(name=name, kind=kind)
            session.add(tag)
            session.flush()
        link = (
            session.query(VideoTag)
            .filter(VideoTag.video_id == video.id, VideoTag.tag_id == tag.id)
            .one_or_none()
        )
        if link is None:
            session.add(VideoTag(video_id=video.id, tag_id=tag.id, source=TagSource.AI, created_at=utcnow()))


def clear_ai_tags(session: Session, video: Video) -> None:
    """Удалить только AI-связи — пользовательские теги не трогаем."""
    for link in list(video.tag_rows):
        if link.source == TagSource.AI:
            session.delete(link)


def apply_tier0(session: Session, video: Video, dictionary: dict[str, Any]) -> list[str]:
    """Полный Tier-0 прогон карточки: очистить старые ai-теги, применить словарь."""
    tags = tier0_tags(video.caption, list(video.hashtags or []), dictionary)
    clear_ai_tags(session, video)
    upsert_ai_tags(session, video, tags)
    video.updated_at = utcnow()
    session.commit()
    return tags

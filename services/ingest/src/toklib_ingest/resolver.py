"""Резолв метаданных: oEmbed (слой 1) → yt-dlp probe (слой 2, дополнение) — docs/06 Р2."""

from __future__ import annotations

import datetime as dt
import logging
import re

import httpx
from toklib_core import oembed as oembed_mod
from toklib_core.config import Settings
from toklib_core.enums import ErrorKind
from toklib_core.normalize import parse_link

from toklib_ingest.providers import ProviderError, YtDlpProvider

log = logging.getLogger(__name__)

_HASHTAG_RE = re.compile(r"#([\wа-яё-]+)", re.UNICODE)


def parse_hashtags(caption: str | None) -> list[str]:
    if not caption:
        return []
    seen: list[str] = []
    for tag in _HASHTAG_RE.findall(caption):
        tag = tag.lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def follow_short_link(url: str, timeout: float = 10.0) -> str:
    """vm/vt.tiktok.com → финальный URL с /video/<id> (прозрачно для oEmbed)."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as http:
            resp = http.get(url)
        return str(resp.url)
    except httpx.HTTPError as exc:
        raise oembed_mod.MetadataError(ErrorKind.TRANSIENT, f"короткая ссылка не раскрылась: {exc}") from exc


def resolve_canonical_id(final_url: str) -> str | None:
    try:
        return parse_link(final_url).tiktok_id
    except Exception:  # noqa: BLE001 — любой не-TikTok финал означает, что id не извлечь
        return None


def resolve_metadata(
    *,
    url: str,
    tiktok_id: str | None,
    settings: Settings,
    yt_dlp_fallback: bool = True,
) -> dict:
    """Собрать поля карточки. Бросает MetadataError(terminal|transient).

    oEmbed — первичный источник; yt-dlp probe дополняет (duration, дата, caption).
    """
    effective_url = url
    if tiktok_id is None:
        final = follow_short_link(url)
        effective_url = final
        found_id = resolve_canonical_id(final)
        if found_id:
            tiktok_id = found_id

    canonical = f"https://www.tiktok.com/@-/video/{tiktok_id}" if tiktok_id else effective_url

    fields: dict = {
        "tiktok_id": tiktok_id,
        "url": canonical,
        "hashtags": [],
        "health_set": "alive",
    }

    data = oembed_mod.fetch_oembed(canonical)
    fields["caption"] = data.title
    fields["author_display"] = data.author_name
    fields["author_username"] = oembed_mod.author_username_from_url(data.author_url)
    fields["cover_url"] = data.thumbnail_url

    if yt_dlp_fallback and tiktok_id:
        try:
            meta = YtDlpProvider(settings.download).probe(effective_url) or {}
        except ProviderError as exc:
            log.warning("yt-dlp probe не удался (%s) — карточка живёт на oEmbed", exc)
            meta = {}
        if meta.get("caption") and not fields["caption"]:
            fields["caption"] = meta["caption"]
        if meta.get("duration"):
            fields["duration_sec"] = meta["duration"]
        if meta.get("timestamp"):
            fields["published_at"] = dt.datetime.fromtimestamp(meta["timestamp"], dt.UTC).replace(tzinfo=None)
        if meta.get("thumbnail") and not fields["cover_url"]:
            fields["cover_url"] = meta["thumbnail"]
        if meta.get("uploader_id") and not fields["author_username"]:
            fields["author_username"] = meta["uploader_id"]

    fields["hashtags"] = parse_hashtags(fields["caption"])
    return fields


def download_cover(cover_url: str, dest) -> bool:
    """Обложка локально (best-effort) — карточка выживает при пропаже CDN-ссылки."""
    from pathlib import Path

    dest = Path(dest)
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as http:
            resp = http.get(cover_url)
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
        return True
    except httpx.HTTPError as exc:
        log.warning("обложка не скачалась (%s): %s", cover_url, exc)
        return False

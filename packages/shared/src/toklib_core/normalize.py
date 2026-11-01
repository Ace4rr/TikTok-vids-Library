"""Нормализация ссылок TikTok — FR-1.3, allowlist против SSRF (docs/05 §5.11).

Правила зафиксированы в docs/09 §9.5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_TIKTOK_HOST = "tiktok.com"
_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCT = ".,)];}>'\""

# Канонические пути с id видео
_ID_RES = [re.compile(p) for p in (
    r"/video/(\d+)",
    r"/photo/(\d+)",
    r"/v/(\d+)",
    r"/embed/v2/(\d+)",
)]
_SHORT_PATH_RE = re.compile(r"^/t/([\w.-]+)")


class LinkError(ValueError):
    """Ссылка не распознана или хост не в allowlist."""


@dataclass(frozen=True)
class ParsedLink:
    url: str
    host: str
    tiktok_id: str | None
    is_short: bool


def extract_url(text: str) -> str | None:
    """Первый URL из share-текста («смотри что нашёл https://... — огонь»)."""
    match = _URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(0)
    while url and url[-1] in _TRAILING_PUNCT:
        url = url[:-1]
    return url


def _host_allowed(host: str, extra: tuple[str, ...]) -> bool:
    host = host.lower().removeprefix("www.")
    if host == _TIKTOK_HOST or host.endswith("." + _TIKTOK_HOST):
        return True
    return any(host == e.lower().removeprefix("www.") or host.endswith("." + e.lower()) for e in extra)


def parse_link(text: str, extra_hosts: tuple[str, ...] = ()) -> ParsedLink:
    url = extract_url(text)
    if not url:
        raise LinkError("в тексте нет ссылки")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise LinkError(f"не удалось разобрать URL: {url}")
    if not _host_allowed(host, extra_hosts):
        raise LinkError(f"хост {host} не в allowlist (только tiktok.com)")

    tiktok_id: str | None = None
    for pattern in _ID_RES:
        found = pattern.search(parsed.path)
        if found:
            tiktok_id = found.group(1)
            break

    is_short = host in _SHORT_HOSTS or bool(_SHORT_PATH_RE.match(parsed.path or "/"))
    canonical_url = url
    if tiktok_id:
        canonical_url = f"https://www.tiktok.com/@-/video/{tiktok_id}"
    return ParsedLink(url=canonical_url, host=host, tiktok_id=tiktok_id, is_short=is_short)

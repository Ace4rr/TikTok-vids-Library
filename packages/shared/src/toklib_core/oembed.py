"""Клиент публичного oEmbed TikTok — слой 1 защиты платформенного риска (docs/06 Р2).

Не зависит от yt-dlp; поломка экстрактора не влияет на метаданные карточки.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from .enums import ErrorKind


class MetadataError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class OEmbedData(BaseModel):
    title: str | None = None
    author_name: str | None = None
    author_url: str | None = None
    thumbnail_url: str | None = None
    embed_html: str | None = None
    embed_product_id: str | None = None


_OEMBED_ENDPOINT = "https://www.tiktok.com/oembed"
_TIMEOUT_S = 10.0


def fetch_oembed(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = _TIMEOUT_S,
    base_url: str = _OEMBED_ENDPOINT,
) -> OEmbedData:
    """Резолв метаданных. 404/400 — терминальная ошибка, сеть/таймаут/429/5xx — повторяемая."""
    own_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = http.get(base_url, params={"url": url})
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise MetadataError(ErrorKind.TRANSIENT, f"oEmbed недоступен: {exc}") from exc
    finally:
        if own_client:
            http.close()

    if resp.status_code in (404, 400):
        raise MetadataError(ErrorKind.TERMINAL, f"oEmbed {resp.status_code}: видео недоступно ({url})")
    if resp.status_code in (429, 500, 502, 503, 504):
        raise MetadataError(ErrorKind.TRANSIENT, f"oEmbed {resp.status_code}, повтор позже")
    if resp.status_code != 200:
        raise MetadataError(ErrorKind.TRANSIENT, f"oEmbed неожиданный статус {resp.status_code}")

    try:
        body: dict[str, Any] = resp.json()
    except ValueError as exc:
        raise MetadataError(ErrorKind.TRANSIENT, "oEmbed вернул не-JSON") from exc
    return OEmbedData.model_validate(body)


def author_username_from_url(author_url: str | None) -> str | None:
    """https://www.tiktok.com/@user → user"""
    if not author_url or "/@" not in author_url:
        return None
    handle = author_url.rsplit("/@", 1)[1].split("/")[0].split("?")[0]
    return handle or None

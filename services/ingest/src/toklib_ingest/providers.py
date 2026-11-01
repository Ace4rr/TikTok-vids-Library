"""DownloadProvider — подключаемая стратегия скачивания (docs/05 принцип 2, FR-2.2/2.6).

yt-dlp изолирован здесь: поломка экстрактора TikTok не должна касаться остальной
системы. Ошибки делятся на терминальные (видео недоступно) и повторяемые (сеть).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from toklib_core.config import DownloadSettings
from toklib_core.enums import DownloadProviderKind, ErrorKind, MediaMode

log = logging.getLogger(__name__)

_TERMINAL_MARKERS = (
    "video has been removed",
    "private video",
    "not available",
    "unavailable",
    "removed by the uploader",
    "unable to extract",
    "does not exist",
)


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def classify_ytdlp_error(message: str) -> ErrorKind:
    lowered = (message or "").lower()
    if any(marker in lowered for marker in _TERMINAL_MARKERS):
        return ErrorKind.TERMINAL
    return ErrorKind.TRANSIENT


class DownloadResult:
    def __init__(
        self,
        file_path: Path | None = None,
        meta: dict | None = None,
        media_mode: MediaMode = MediaMode.FILE,
    ) -> None:
        self.file_path = file_path
        self.meta = meta or {}
        self.media_mode = media_mode


class DownloadProvider(ABC):
    name: str = "base"

    @abstractmethod
    def download(self, url: str, dest_dir: Path) -> DownloadResult:
        """Скачать файл во dest_dir (имя произвольное, канонизирует вызывающий)."""


class EmbedOnlyProvider(DownloadProvider):
    """Последний рубеж (Р2): карточка живёт без файла, просмотр через embed-плеер."""

    name = "embed_only"

    def download(self, url: str, dest_dir: Path) -> DownloadResult:
        return DownloadResult(media_mode=MediaMode.EMBED)


class YtDlpProvider(DownloadProvider):
    name = "yt_dlp"

    def __init__(self, settings: DownloadSettings) -> None:
        self.settings = settings

    def _opts(self, dest_dir: Path) -> dict:
        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "outtmpl": str(dest_dir / "video.%(ext)s"),
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "merge_output_format": "mp4",
            "socket_timeout": 30,
            "retries": 2,
            "noplaylist": True,
        }
        if self.settings.cookies_file:
            opts["cookiefile"] = self.settings.cookies_file
        if self.settings.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.settings.cookies_from_browser, None, None, None)
        if self.settings.proxy:
            opts["proxy"] = self.settings.proxy
        return opts

    def _import(self):
        try:
            import yt_dlp
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(ErrorKind.TRANSIENT, "yt-dlp не установлен") from exc
        return yt_dlp

    def download(self, url: str, dest_dir: Path) -> DownloadResult:
        yt_dlp = self._import()
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            with yt_dlp.YoutubeDL(self._opts(dest_dir)) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:  # yt_dlp.utils.DownloadError и прочие
            raise ProviderError(classify_ytdlp_error(str(exc)), f"yt-dlp: {exc}") from exc
        info = info or {}
        files = sorted(dest_dir.glob("video.*"))
        file_path = next((f for f in files if f.suffix != ".part"), None)
        if file_path is None:
            raise ProviderError(ErrorKind.TRANSIENT, "yt-dlp не создал файл")
        return DownloadResult(file_path=file_path, meta=self._meta(info))

    def probe(self, url: str) -> dict | None:
        """Метаданные без скачивания — fallback резолва (слой 2, docs/06 Р2)."""
        yt_dlp = self._import()
        opts = self._opts(Path(".")) | {"skip_download": True, "outtmpl": "-"}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise ProviderError(classify_ytdlp_error(str(exc)), f"yt-dlp probe: {exc}") from exc
        return self._meta(info or {})

    @staticmethod
    def _meta(info: dict) -> dict:
        return {
            "caption": info.get("description"),
            "duration": info.get("duration"),
            "timestamp": info.get("timestamp"),
            "uploader": info.get("uploader"),
            "uploader_id": (info.get("uploader_id") or "").lstrip("@") or None,
            "thumbnail": info.get("thumbnail"),
            "raw": {k: info.get(k) for k in ("id", "ext", "width", "height") if info.get(k)},
        }


class CobaltProvider(DownloadProvider):
    """Альтернативный провайдер — самостоятельно развёрнутый cobalt (FR-2.6)."""

    name = "cobalt"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def download(self, url: str, dest_dir: Path) -> DownloadResult:
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as http:
                resp = http.post(self.base_url + "/", json={"url": url, "videoQuality": "720"})
                resp.raise_for_status()
                body = resp.json()
                status = body.get("status")
                if status in ("error", "picker"):
                    raise ProviderError(ErrorKind.TERMINAL, f"cobalt: {body.get('text', status)}")
                media_url = body.get("url")
                if not media_url:
                    raise ProviderError(ErrorKind.TRANSIENT, f"cobalt: неожиданный ответ {json.dumps(body)[:200]}")
                file_path = dest_dir / "video.mp4"
                with http.stream("GET", media_url) as stream:
                    stream.raise_for_status()
                    with file_path.open("wb") as fh:
                        for chunk in stream.iter_bytes():
                            fh.write(chunk)
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorKind.TRANSIENT, f"cobalt: {exc}") from exc
        return DownloadResult(file_path=file_path)


def build_provider(settings: DownloadSettings) -> DownloadProvider:
    if settings.provider == DownloadProviderKind.COBALT:
        if not settings.cobalt_base_url:
            raise ProviderError(ErrorKind.TERMINAL, "cobalt_base_url не задан")
        return CobaltProvider(settings.cobalt_base_url)
    if settings.provider == DownloadProviderKind.NONE:
        return EmbedOnlyProvider()
    return YtDlpProvider(settings)

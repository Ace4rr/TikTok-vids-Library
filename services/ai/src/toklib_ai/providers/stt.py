"""STT-провайдеры — docs/08 §8.3. Локальный faster-whisper по умолчанию."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from toklib_core.config import SttSettings, secret_from_env

from ..interfaces import Transcriber, TranscriptResult, TranscriptSegment

log = logging.getLogger(__name__)


class FasterWhisperTranscriber(Transcriber):
    """In-process STT; модель грузится лениво и остаётся резидентной (docs/07 §7.4 №12)."""

    name = "faster_whisper"

    def __init__(self, settings: SttSettings) -> None:
        self.settings = settings
        self.model_id = f"faster-whisper/{settings.model}"
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            device = "auto" if self.settings.compute == "float16" else "cpu"
            self._model = WhisperModel(
                self.settings.model,
                device=device,
                compute_type=self.settings.compute,
            )
        return self._model

    def transcribe(self, audio_path: Path, language: str | None = None) -> TranscriptResult:
        model = self._load()
        segments, info = model.transcribe(
            str(audio_path),
            language=language if language and language != "auto" else None,
            vad_filter=self.settings.vad,
            beam_size=self.settings.beam,
            condition_on_previous_text=False,
        )
        result = [
            TranscriptSegment(start=float(seg.start), end=float(seg.end), text=seg.text.strip())
            for seg in segments
        ]
        return TranscriptResult(language=info.language, segments=result, model=self.model_id)


class OpenAiCompatStt(Transcriber):
    """Облачный STT (opt-in): /audio/transcriptions любого OpenAI-совместимого endpoint."""

    name = "openai_compat_stt"

    def __init__(self, settings: SttSettings, env: dict[str, str] | None = None) -> None:
        if not settings.base_url:
            raise ValueError("STT base_url не задан")
        self.settings = settings
        self.model_id = settings.model_name or "whisper-1"
        self.api_key = secret_from_env(settings.api_key_env, env)

    def transcribe(self, audio_path: Path, language: str | None = None) -> TranscriptResult:
        with httpx.Client(timeout=180.0) as http:
            resp = http.post(
                self.settings.base_url.rstrip("/") + "/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key or ''}"},
                data={"model": self.model_id, "response_format": "verbose_json", "language": language or ""},
                files={"file": (audio_path.name, audio_path.read_bytes())},
            )
            resp.raise_for_status()
            body = resp.json()
        segments = [
            TranscriptSegment(start=float(s.get("start", 0.0)), end=float(s.get("end", 0.0)), text=s.get("text", ""))
            for s in body.get("segments", [])
        ]
        return TranscriptResult(language=body.get("language"), segments=segments, model=self.model_id)


def build_transcriber(settings: SttSettings, env: dict[str, str] | None = None) -> Transcriber | None:
    provider = settings.provider.lower()
    if provider in ("faster_whisper", "faster-whisper"):
        return FasterWhisperTranscriber(settings)
    if provider in ("openai_api", "groq_api", "openai_compat"):
        if not settings.base_url:
            log.warning("STT API-провайдер задан, но base_url пуст — STT выключен")
            return None
        return OpenAiCompatStt(settings, env)
    log.warning("неизвестный STT-провайдер %r — STT выключен", settings.provider)
    return None

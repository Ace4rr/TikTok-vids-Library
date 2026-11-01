"""AI-маршрутизатор — docs/08 §8.1: основной провайдер → fallback → circuit breaker.

Политики режима: local_only (облако физически исключено), local_first (облако
как fallback), cloud_only. Один вызов LLM на видео + одна попытка ремонта (docs/07 §7.5).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass

from .interfaces import (
    AnalysisFailed,
    AnalyzeInput,
    AnalyzeOutput,
    Embedder,
    NoProviderError,
    TextAnalyzer,
    Transcriber,
)
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class CircuitBreaker:
    """N неудач подряд = пауза M минут (docs/08 §8.1)."""

    def __init__(self, failures: int = 3, pause_min: int = 15) -> None:
        self.failures_threshold = failures
        self.pause = dt.timedelta(minutes=pause_min)
        self.consecutive_failures = 0
        self.opened_until: dt.datetime | None = None

    def allow(self, now: dt.datetime) -> bool:
        return self.opened_until is None or now >= self.opened_until

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = None

    def record_failure(self, now: dt.datetime) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failures_threshold:
            self.opened_until = now + self.pause


def extract_json(raw: str) -> dict:
    """Убрать markdown-обёртки и найти первый JSON-объект."""
    text = (raw or "").strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSON-объект не найден в ответе")
    return json.loads(text[start : end + 1])


@dataclass
class _Entry:
    provider: TextAnalyzer
    cloud: bool
    breaker: CircuitBreaker


@dataclass
class AnalysisResult:
    output: AnalyzeOutput
    model: str
    provider: str
    attempts: int
    degraded: bool = False


class AiRouter:
    def __init__(
        self,
        *,
        mode: str,
        transcriber: Transcriber | None = None,
        analyzers: list[tuple[TextAnalyzer, bool]] | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.mode = mode
        self.transcriber = transcriber
        self.embedder = embedder
        self.entries: list[_Entry] = []
        for provider, cloud in analyzers or []:
            if mode == "local_only" and cloud:
                continue  # NFR-9: физически нет облачных вызовов
            if mode == "cloud_only" and not cloud:
                continue
            self.entries.append(_Entry(provider=provider, cloud=cloud, breaker=CircuitBreaker()))

    @property
    def primary_analyzer(self) -> TextAnalyzer | None:
        return self.entries[0].provider if self.entries else None

    def transcribe(self, audio_path, language: str | None = None):
        if self.transcriber is None:
            raise NoProviderError("STT-провайдер не сконфигурирован")
        return self.transcriber.transcribe(audio_path, language)

    def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        if self.embedder is None:
            raise NoProviderError("провайдер эмбеддингов не сконфигурирован")
        return self.embedder.embed(texts), self.embedder.model_id

    def embed_query(self, text: str) -> tuple[list[float], str]:
        """Вектор поискового запроса (query-префикс для e5, docs/08 §8.4)."""
        if self.embedder is None:
            raise NoProviderError("провайдер эмбеддингов не сконфигурирован")
        return self.embedder.embed_query([text])[0], self.embedder.model_id

    def analyze(self, inp: AnalyzeInput) -> AnalysisResult:
        if not self.entries:
            raise AnalysisFailed("LLM-провайдеры не сконфигурированы")

        user_prompt = build_user_prompt(inp)
        last_error = "нет провайдеров"
        total_attempts = 0

        for entry in self.entries:
            now = dt.datetime.now(dt.UTC)
            if not entry.breaker.allow(now):
                log.info("circuit breaker открыт для %s — пропускаем", entry.provider.name)
                continue
            attempts = 0
            last_raw = ""
            for attempt in range(2):  # 1 вызов + 1 ремонт (docs/07 §7.5)
                attempts += 1
                total_attempts += 1
                try:
                    prompt = user_prompt if (attempt == 0 or not last_raw) else build_repair_prompt(last_raw, last_error)
                    raw = entry.provider.complete(SYSTEM_PROMPT, prompt)
                    last_raw = raw
                    data = extract_json(raw)
                    output = AnalyzeOutput.model_validate(data)
                    if not output.is_meaningful():
                        raise ValueError("пустой ответ (нет тегов и саммари)")
                except Exception as exc:  # noqa: BLE001 — ошибка конкретного провайдера
                    last_error = str(exc)[:300]
                    entry.breaker.record_failure(dt.datetime.now(dt.UTC))
                    log.warning("анализ %s попытка %d не удалась: %s", entry.provider.name, attempt + 1, exc)
                    continue
                entry.breaker.record_success()
                return AnalysisResult(
                    output=output,
                    model=entry.provider.model_id,
                    provider=entry.provider.name,
                    attempts=attempts,
                )

        raise AnalysisFailed(f"все LLM-провайдеры не прошли: {last_error}")

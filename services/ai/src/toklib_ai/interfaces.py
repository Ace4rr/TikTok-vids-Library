"""Интерфейсы AI-провайдеров — docs/08 §8.1: Transcriber / TextAnalyzer / Embedder.

Воркеры не знают о конкретных вендорах; любой провайдер подключается за интерфейсом.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class NoProviderError(RuntimeError):
    """Провайдер не сконфигурирован (например, режим без STT/эмбеддингов)."""


class AnalysisFailed(RuntimeError):
    """Все провайдеры анализа исчерпаны (невалид JSON/ошибки) → деградация Tier-0."""


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    language: str | None
    segments: list[TranscriptSegment]
    model: str

    def full_text(self) -> str:
        return " ".join(seg.text for seg in self.segments).strip()


class Transcriber(ABC):
    name: str = "base"
    model_id: str = "base"

    @abstractmethod
    def transcribe(self, audio_path: Path, language: str | None = None) -> TranscriptResult: ...


@dataclass
class AnalyzeInput:
    caption: str | None
    hashtags: list[str] = field(default_factory=list)
    tags_vocab: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    transcript_text: str | None = None
    language_hint: str | None = None


class AnalyzeOutput(BaseModel):
    """Схема выхода LLM — docs/07 §7.5. Валидация до сохранения."""

    tags: list[str] = Field(default_factory=list)
    summary: str = ""
    key_idea: str = ""
    category: str | None = None
    content_language: str | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        cleaned = []
        for tag in v[:7]:
            tag = " ".join(str(tag).split())[:60]
            if tag and tag.lower() not in [c.lower() for c in cleaned]:
                cleaned.append(tag)
        return cleaned

    @field_validator("summary", "key_idea")
    @classmethod
    def _trim(cls, v: str) -> str:
        return " ".join((v or "").split())

    @field_validator("key_idea")
    @classmethod
    def _idea_len(cls, v: str) -> str:
        return v[:200]

    def is_meaningful(self) -> bool:
        return bool(self.tags) or bool(self.summary)


class TextAnalyzer(ABC):
    name: str = "base"
    model_id: str = "base"
    cloud: bool = False

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Один вызов завершения; возвращает сырой текст ответа."""


class Embedder(ABC):
    name: str = "base"
    model_id: str = "base"
    dim: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        """Вектор запроса поиска. По умолчанию — как документ; модели с
        асимметричными префиксами (e5) переопределяют (docs/08 §8.4)."""
        return self.embed(texts)

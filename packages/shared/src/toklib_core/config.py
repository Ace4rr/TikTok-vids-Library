"""Конфигурация: yaml в data_dir + env-секреты (docs/08 §8.5, docs/09 §9.3).

Приоритет: env перекрывает yaml; секреты читаются ТОЛЬКО из env.
"""

from __future__ import annotations

import os
from datetime import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import AiMode, DownloadProviderKind


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("./data")
    token: str | None = None
    database_url: str | None = None  # None → SQLite в data_dir
    run_workers: bool = True  # False для web-only / выноса воркеров
    ai_only: bool = False  # только AI-воркер (GPU-профиль XL, docs/08 §8.6)
    workers_poll_seconds: float = 1.0
    lease_seconds: int = 600
    cors_origins: list[str] = Field(default_factory=list)
    web_dist: str | None = None  # каталог статики web (apps/web/out) — иначе не раздаётся

    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'toklib.db'}"


class DownloadSettings(BaseModel):
    provider: DownloadProviderKind = DownloadProviderKind.YT_DLP
    cookies_file: str | None = None
    cookies_from_browser: str | None = None  # chrome | firefox | edge | ...
    proxy: str | None = None
    cobalt_base_url: str | None = None


class IngestSettings(BaseModel):
    allow_extra_hosts: list[str] = Field(default_factory=list)


class SttSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    provider: str = "faster_whisper"  # faster_whisper | whisper_cpp | openai_api | groq_api
    model: str = "small"
    compute: str = "int8"  # int8 (CPU) | float16 (GPU)
    vad: bool = True
    beam: int = 1
    language: str = "auto"
    base_url: str | None = None  # для API-провайдеров
    model_name: str | None = None  # имя модели для API-провайдеров
    api_key_env: str = "TOKLIB_STT_KEY"


class CircuitBreakerSettings(BaseModel):
    failures: int = 3
    pause_min: int = 15


class LlmSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    provider: str = "ollama"  # ollama | llama_cpp | openai_compat
    base_url: str = "http://ollama:11434"
    model: str = "qwen3:4b-instruct-q4_K_M"
    keep_alive: str = "30m"
    temperature: float = 0.2
    max_tokens: int = 256
    api_key_env: str = "TOKLIB_LLM_KEY"


class LlmFallbackSettings(BaseModel):
    """Fallback-провайдер LLM; активен только при mode=local_first (docs/08 §8.5)."""

    provider: str = "openai_compat"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "TOKLIB_LLM_KEY"
    on_timeout_s: int = 120
    circuit_breaker: CircuitBreakerSettings = CircuitBreakerSettings()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


class EmbeddingsSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    provider: str = "sentence_transformers"  # sentence_transformers | ollama | openai_compat
    model: str = "multilingual-e5-small"
    dim: int = 384
    base_url: str | None = None
    api_key_env: str = "TOKLIB_EMBED_KEY"


class BudgetsSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    analysis_per_day: int = 0  # 0 = без лимита (self-hosted, docs/07 §7.6)
    nightly_backlog: int = 400
    lazy_backlog: bool = True
    stt_concurrency: int = 1


class NightWindow(BaseModel):
    start: str = "01:00"
    end: str = "07:00"

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2 or not all(p.isdigit() and 0 <= int(p) <= 59 for p in parts):
            raise ValueError(f"ожидается HH:MM, получено {v!r}")
        h, m = int(parts[0]), int(parts[1])
        if h > 23:
            raise ValueError(f"час > 23: {v!r}")
        return f"{h:02d}:{m:02d}"

    def in_window(self, moment: dt) -> bool:
        """Локальное время момента внутри ночного окна (учитывает переход через полночь)."""
        start_h, start_m = (int(x) for x in self.start.split(":"))
        end_h, end_m = (int(x) for x in self.end.split(":"))
        cur = moment.hour * 60 + moment.minute
        s, e = start_h * 60 + start_m, end_h * 60 + end_m
        if s <= e:
            return s <= cur < e
        return cur >= s or cur < e  # окно через полночь


class AiSettings(BaseModel):
    """validate_assignment: пресеты присваивают строки (например, mode) —
    pydantic приводит их к enum'ам на месте (docs/09 §9.15)."""

    model_config = ConfigDict(validate_assignment=True)

    mode: AiMode = AiMode.LOCAL_ONLY  # FR-6.5: local_only по умолчанию
    preset: str = "custom"  # custom | S | M | L | XL | cloud (docs/08 §8.6, docs/09 §9.15)
    night_window: NightWindow = NightWindow()
    budgets: BudgetsSettings = BudgetsSettings()
    stt: SttSettings = SttSettings()
    llm: LlmSettings = LlmSettings()
    llm_fallback: LlmFallbackSettings = LlmFallbackSettings()
    embeddings: EmbeddingsSettings = EmbeddingsSettings()


class StorageSettings(BaseModel):
    """Дисковые watermark — FR-2.7; пороги проверяются на реальном NAS (docs/06 §6.5.2)."""

    disk_pause_pct: float = 90.0
    disk_warn_pct: float = 95.0


class Settings(BaseModel):
    server: ServerSettings = ServerSettings()
    download: DownloadSettings = DownloadSettings()
    ingest: IngestSettings = IngestSettings()
    ai: AiSettings = AiSettings()
    storage: StorageSettings = StorageSettings()

    @property
    def token(self) -> str | None:
        return self.server.token


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Собрать конфиг: yaml ($TOKLIB_CONFIG или <data_dir>/config.yaml) + env-оверрайды."""
    env = dict(os.environ if env is None else env)
    data_dir = Path(env.get("TOKLIB_DATA_DIR", "./data")).expanduser()
    cfg_path = Path(env.get("TOKLIB_CONFIG", str(data_dir / "config.yaml"))).expanduser()

    raw: dict[str, Any] = {}
    if cfg_path.is_file():
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError(f"{cfg_path}: корень конфига должен быть mapping-ом")
            raw = loaded

    settings = Settings.model_validate(raw)
    _apply_preset(settings, raw)

    settings.server.data_dir = data_dir
    if env.get("TOKLIB_TOKEN"):
        settings.server.token = env["TOKLIB_TOKEN"]
    if env.get("TOKLIB_DATABASE_URL"):
        settings.server.database_url = env["TOKLIB_DATABASE_URL"]
    if env.get("TOKLIB_WEB_DIST"):
        settings.server.web_dist = env["TOKLIB_WEB_DIST"]
    return settings


# ---------------------------------------------------------------------------
# Пресеты железа (docs/08 §8.6, docs/09 §9.15): один ключ ai.preset подставляет
# значения ТОЛЬКО в те пути конфига, которые пользователь не задал явно.
PRESETS: dict[str, dict[str, Any]] = {
    # S «NAS/N100/RPi»: локальный STT, LLM — дешёвое облако (opt-in), e5-small
    "S": {
        "ai.mode": "local_first",
        "ai.stt.provider": "faster_whisper",
        "ai.stt.model": "small",
        "ai.stt.compute": "int8",
        "ai.llm.provider": "openai_compat",
        "ai.llm.base_url": "https://openrouter.ai/api/v1",
        "ai.llm.model": "deepseek/deepseek-chat",
        "ai.llm.api_key_env": "TOKLIB_LLM_KEY",
        "ai.embeddings.model": "multilingual-e5-small",
        "ai.budgets.nightly_backlog": 100,
    },
    # M «старый десктопный CPU» (i7-4790 класс): всё локально, 4B Q4 через Ollama
    "M": {
        "ai.mode": "local_only",
        "ai.stt.provider": "faster_whisper",
        "ai.stt.model": "small",
        "ai.stt.compute": "int8",
        "ai.llm.provider": "ollama",
        "ai.llm.base_url": "http://127.0.0.1:11434",
        "ai.llm.model": "qwen3:4b-instruct-q4_K_M",
        "ai.embeddings.model": "multilingual-e5-small",
        "ai.budgets.nightly_backlog": 200,
    },
    # L «GPU 8–12 ГБ»: large-v3-turbo float16 (если VRAM < 8 ГБ — замените на medium)
    "L": {
        "ai.mode": "local_only",
        "ai.stt.provider": "faster_whisper",
        "ai.stt.model": "large-v3-turbo",
        "ai.stt.compute": "float16",
        "ai.llm.provider": "ollama",
        "ai.llm.base_url": "http://127.0.0.1:11434",
        "ai.llm.model": "qwen3:8b-instruct-q4_K_M",
        "ai.embeddings.model": "multilingual-e5-small",
        "ai.budgets.nightly_backlog": 400,
    },
    # XL «бокс в LAN»: TokLib на NAS, инференс на GPU-машине (путь 3, docs/08 §8.1)
    "XL": {
        "ai.mode": "local_only",
        "ai.stt.provider": "faster_whisper",
        "ai.stt.model": "small",
        "ai.stt.compute": "int8",
        "ai.llm.provider": "ollama",
        "ai.llm.base_url": "http://gpu-box.lan:11434",
        "ai.llm.model": "qwen3:8b-instruct-q4_K_M",
        "ai.embeddings.model": "multilingual-e5-small",
        "ai.budgets.nightly_backlog": 400,
    },
    # cloud «без локального инференса»: Groq STT + дешёвый LLM; ключи — env
    "cloud": {
        "ai.mode": "local_first",
        "ai.stt.provider": "openai_compat",
        "ai.stt.base_url": "https://api.groq.com/openai/v1",
        "ai.stt.model_name": "whisper-large-v3-turbo",
        "ai.stt.api_key_env": "TOKLIB_STT_KEY",
        "ai.llm.provider": "openai_compat",
        "ai.llm.base_url": "https://openrouter.ai/api/v1",
        "ai.llm.model": "deepseek/deepseek-chat",
        "ai.llm.api_key_env": "TOKLIB_LLM_KEY",
        "ai.embeddings.model": "multilingual-e5-small",
        "ai.budgets.nightly_backlog": 1000,
    },
}

_PRESET_FIELDS = {
    "S",
    "M",
    "L",
    "XL",
    "cloud",
}


def _raw_has_path(raw: dict[str, Any], dotted: str) -> bool:
    node: Any = raw
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _apply_preset(settings: Settings, raw: dict[str, Any]) -> None:
    preset = settings.ai.preset
    if preset == "custom":
        return
    if preset not in _PRESET_FIELDS:
        raise ValueError(f"ai.preset: неизвестный пресет {preset!r} (доступны: custom, {', '.join(sorted(_PRESET_FIELDS))})")
    overrides = PRESETS[preset]
    applied: list[str] = []
    for dotted, value in overrides.items():
        if _raw_has_path(raw, dotted):
            continue  # явное значение пользователя сильнее пресета
        node: Any = settings
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = getattr(node, part)
        setattr(node, parts[-1], value)
        applied.append(dotted)
    if applied:
        import logging

        logging.getLogger("toklib.config").info(
            "пресет %s подставил: %s (явно заданные значения не тронуты)", preset, ", ".join(applied)
        )


def secret_from_env(name: str, env: dict[str, str] | None = None) -> str | None:
    env = dict(os.environ if env is None else env)
    value = env.get(name, "").strip()
    return value or None

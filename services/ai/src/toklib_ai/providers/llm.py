"""LLM-провайдеры — docs/08 §8.2. Один OpenAI-совместимый клиент покрывает
ollama / llama.cpp / LM Studio / vLLM / OpenRouter / облака."""

from __future__ import annotations

import logging

import httpx
from toklib_core.config import LlmFallbackSettings, LlmSettings, secret_from_env

from ..interfaces import TextAnalyzer

log = logging.getLogger(__name__)


def normalize_base_url(base_url: str) -> str:
    """http://ollama:11434 → http://ollama:11434/v1 (openai-compat у всех локальных раннеров)."""
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


class OpenAiCompatAnalyzer(TextAnalyzer):
    def __init__(
        self,
        *,
        name: str,
        model_id: str,
        base_url: str,
        api_key: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 256,
        cloud: bool = False,
        timeout_s: float = 120.0,
    ) -> None:
        self.name = name
        self.model_id = model_id
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.cloud = cloud
        self.timeout_s = timeout_s

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        with httpx.Client(timeout=self.timeout_s) as http:
            resp = http.post(self.base_url + "/chat/completions", json=payload, headers=headers)
            if resp.status_code == 400:
                # Часть эндпоинтов не знает про json-режим/параметры — повтор без лишнего
                payload.pop("temperature", None)
                resp = http.post(self.base_url + "/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM вернул пустой choices: {str(body)[:200]}")
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(message.get("reasoning_content"), str) and not content:
            # reasoning-модели тратят токены на рассуждения — docs/08 §8.2 это запрещает
            raise RuntimeError("модель вернула только reasoning_content — выберите instruct-модель")
        if not content:
            raise RuntimeError("LLM вернул пустой content")
        return str(content)


class OllamaAnalyzer(OpenAiCompatAnalyzer):
    name = "ollama"  # type: ignore[misc]

    def __init__(self, settings: LlmSettings) -> None:
        super().__init__(
            name="ollama",
            model_id=settings.model,
            base_url=settings.base_url,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
        )


# Схема выхода анализа (docs/07 §7.5) для structured outputs Ollama — жёсткий JSON
# ещё до валидатора: критично для 3–4B моделей, где свободная генерация ломается чаще.
ANALYZE_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "key_idea": {"type": "string"},
        "category": {"type": "string"},
        "content_language": {"type": "string"},
    },
    "required": ["tags", "summary", "key_idea"],
}


class OllamaNativeAnalyzer(TextAnalyzer):
    """Нативный /api/chat Ollama с format=json_schema (structured outputs).

    Стоит в цепочке перед OpenAI-совместимым клиентом: если нативный вызов
    не поддержан старым Ollama, complete() сам повторяет без schema, а следующая
    запись цепочки (openai-compat) остаётся запасным путём.
    """

    name = "ollama"

    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings
        self.model_id = settings.model
        self.base_url = settings.base_url.rstrip("/")
        self.temperature = settings.temperature
        self.max_tokens = settings.max_tokens
        self.cloud = False

    def complete(self, system: str, user: str) -> str:
        payload: dict = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.settings.keep_alive,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
            "format": ANALYZE_JSON_SCHEMA,
        }
        with httpx.Client(timeout=120.0) as http:
            resp = http.post(self.base_url + "/api/chat", json=payload)
            if resp.status_code == 400:
                # старый Ollama без structured outputs — повтор без format
                payload.pop("format", None)
                resp = http.post(self.base_url + "/api/chat", json=payload)
            resp.raise_for_status()
            body = resp.json()
        content = (body.get("message") or {}).get("content")
        if not content:
            raise RuntimeError(f"ollama вернул пустой content: {str(body)[:200]}")
        return str(content)


def build_analyzer(
    settings: LlmSettings,
    fallback: LlmFallbackSettings | None = None,
    env: dict[str, str] | None = None,
) -> list[tuple[TextAnalyzer, bool]]:
    """Цепочка (провайдер, cloud) для роутера: основной + fallback (docs/08 §8.5)."""
    chain: list[tuple[TextAnalyzer, bool]] = []

    provider = (settings.provider or "ollama").lower()
    base = settings.base_url
    api_key = secret_from_env(settings.api_key_env, env)
    is_cloud = not _is_local(base)
    if provider == "ollama":
        chain.append((OllamaNativeAnalyzer(settings), is_cloud))
        chain.append((OllamaAnalyzer(settings), is_cloud))  # запасной openai-compat путь
    elif provider in ("llama_cpp", "lm_studio", "vllm", "openai_compat"):
        chain.append(
            (
                OpenAiCompatAnalyzer(
                    name=provider,
                    model_id=settings.model,
                    base_url=base,
                    api_key=api_key,
                    temperature=settings.temperature,
                    max_tokens=settings.max_tokens,
                ),
                not _is_local(base),
            )
        )
    else:
        log.warning("неизвестный LLM-провайдер %r — пропущен", provider)

    if fallback and fallback.configured:
        chain.append(
            (
                OpenAiCompatAnalyzer(
                    name="llm_fallback",
                    model_id=fallback.model,
                    base_url=fallback.base_url,
                    api_key=secret_from_env(fallback.api_key_env, env),
                    temperature=0.2,
                    max_tokens=settings.max_tokens,
                    cloud=True,
                    timeout_s=float(fallback.on_timeout_s),
                ),
                True,
            )
        )
    return chain


def _is_local(base_url: str) -> bool:
    """Локальный ли endpoint: localhost/частные сети/docker-имена без точки (ollama, llama-server)."""
    if not base_url:
        return True
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return True
    if host in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "ollama"):
        return True
    if "." not in host:  # docker-compose service names: ollama, llama-server, gpu-box
        return True
    return host.endswith((".local", ".lan", ".home", ".internal")) or host.startswith(
        ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.")
    )

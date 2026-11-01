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
        chain.append((OllamaAnalyzer(settings), is_cloud))
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
    if not base_url:
        return True
    lowered = base_url.lower()
    return any(
        marker in lowered
        for marker in ("127.0.0.1", "localhost", "0.0.0.0", "::1", ".local", "192.168.", "10.", "172.")
    )

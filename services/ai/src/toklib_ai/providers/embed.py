"""Провайдеры эмбеддингов — docs/08 §8.4. Векторы разных моделей не смешиваются."""

from __future__ import annotations

import logging

import httpx
from toklib_core.config import EmbeddingsSettings, secret_from_env

from ..interfaces import Embedder

log = logging.getLogger(__name__)


class SentenceTransformerEmbedder(Embedder):
    """In-process, default: multilingual-e5-small — CPU любого профиля, доли секунды."""

    name = "sentence_transformers"

    def __init__(self, settings: EmbeddingsSettings) -> None:
        self.settings = settings
        self.model_id = settings.model
        self.dim = settings.dim
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.settings.model)
            self.dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        # e5 требует префиксов; для документов — "passage: "
        prefixed = [t if t.lower().startswith(("passage:", "query:")) else f"passage: {t}" for t in texts]
        vectors = model.encode(prefixed, batch_size=16, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        # асимметричный e5: запросы — с префиксом "query: " (docs/08 §8.4)
        prefixed = [t if t.lower().startswith(("query:", "passage:")) else f"query: {t}" for t in texts]
        vectors = model.encode(prefixed, batch_size=16, show_progress_bar=False)
        return [v.tolist() for v in vectors]


class OpenAiCompatEmbedder(Embedder):
    """Ollama / vLLM / OpenAI / OpenRouter — /v1/embeddings."""

    name = "openai_compat_embed"

    def __init__(self, settings: EmbeddingsSettings, env: dict[str, str] | None = None) -> None:
        base = settings.base_url or "http://ollama:11434"
        url = base.rstrip("/")
        if not url.endswith("/v1"):
            url += "/v1"
        self.url = url
        self.model_id = settings.model
        self.dim = settings.dim
        self.api_key = secret_from_env(settings.api_key_env, env)

    def embed(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=60.0) as http:
            resp = http.post(
                self.url + "/embeddings",
                json={"model": self.model_id, "input": texts},
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            )
            resp.raise_for_status()
            body = resp.json()
        data = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
        vectors = [d["embedding"] for d in data]
        if vectors:
            self.dim = len(vectors[0])
        return vectors


class OllamaEmbedder(OpenAiCompatEmbedder):
    name = "ollama_embed"


def build_embedder(settings: EmbeddingsSettings, env: dict[str, str] | None = None) -> Embedder | None:
    provider = (settings.provider or "").lower()
    if provider == "sentence_transformers":
        return SentenceTransformerEmbedder(settings)
    if provider in ("ollama", "openai_compat"):
        return OpenAiCompatEmbedder(settings, env)
    log.warning("неизвестный провайдер эмбеддингов %r — эмбеддинги выключены", settings.provider)
    return None

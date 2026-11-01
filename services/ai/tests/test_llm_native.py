"""Ollama native structured outputs (format=json_schema) — жёсткий JSON на 3–4B."""

import json

import httpx
from toklib_ai.providers.llm import ANALYZE_JSON_SCHEMA, OllamaNativeAnalyzer, build_analyzer
from toklib_core.config import LlmSettings


def _patch_http(monkeypatch, handler):
    original_client = httpx.Client

    def _client(**kw):
        return original_client(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr("toklib_ai.providers.llm.httpx.Client", _client)


def test_native_complete_sends_json_schema(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200, json={"message": {"content": '{"tags": ["рецепт"], "summary": "с", "key_idea": "и"}'}}
        )

    _patch_http(monkeypatch, handler)
    analyzer = OllamaNativeAnalyzer(LlmSettings())
    out = analyzer.complete("system", "user")

    assert '"рецепт"' in out
    assert captured["body"]["format"] == ANALYZE_JSON_SCHEMA  # жёсткий JSON ещё до валидатора
    assert captured["body"]["options"]["temperature"] == analyzer.temperature


def test_native_falls_back_without_format_on_400(monkeypatch):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append(body)
        if "format" in body:
            return httpx.Response(400, json={"error": "format not supported"})
        return httpx.Response(200, json={"message": {"content": '{"tags": ["x"], "summary": "s", "key_idea": "k"}'}})

    _patch_http(monkeypatch, handler)
    analyzer = OllamaNativeAnalyzer(LlmSettings())
    out = analyzer.complete("system", "user")

    assert '"tags"' in out
    assert len(calls) == 2
    assert "format" not in calls[1]  # повтор без schema (старый Ollama)


def test_build_analyzer_ollama_chain_has_native_and_compat():
    chain = build_analyzer(LlmSettings())
    names = [provider.name for provider, _cloud in chain]
    assert names.count("ollama") == 2  # native + openai-compat запасной
    assert all(not cloud for _provider, cloud in chain)  # localhost — не облако

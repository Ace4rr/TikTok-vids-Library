import pytest
from toklib_ai.interfaces import AnalysisFailed, AnalyzeInput, AnalyzeOutput
from toklib_ai.providers.llm import normalize_base_url
from toklib_ai.router import AiRouter, CircuitBreaker, extract_json


def test_extract_json_plain_and_fenced():
    assert extract_json('{"tags": ["a"]}') == {"tags": ["a"]}
    assert extract_json('Вот ответ:\n```json\n{"tags": ["a"]}\n```') == {"tags": ["a"]}
    with pytest.raises(ValueError):
        extract_json("вообще не json")


def test_analyze_output_validation_trims():
    out = AnalyzeOutput(tags=["А", "а", "b" * 100, "", "c"], summary="  ок  ", key_idea="x" * 500)
    assert out.tags == ["А", "b" * 60, "c"]
    assert out.summary == "ок"
    assert len(out.key_idea) == 200


class FakeAnalyzer:
    def __init__(self, name, responses, cloud=False, model="fake-model"):
        self.name = name
        self.responses = list(responses)
        self.cloud = cloud
        self.model_id = model
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise RuntimeError("нет заготовленных ответов")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


GOOD = '{"tags": ["рецепт"], "summary": "Про пирог", "key_idea": "Яблочный пирог", "category": "рецепт", "content_language": "ru"}'
BAD = "я не json"


def test_mode_local_only_drops_cloud():
    cloud = FakeAnalyzer("cloud", [GOOD], cloud=True)
    local = FakeAnalyzer("local", [GOOD])
    router = AiRouter(mode="local_only", analyzers=[(local, False), (cloud, True)])
    assert [e.provider.name for e in router.entries] == ["local"]


def test_mode_cloud_only_keeps_cloud():
    cloud = FakeAnalyzer("cloud", [GOOD], cloud=True)
    local = FakeAnalyzer("local", [GOOD])
    router = AiRouter(mode="cloud_only", analyzers=[(local, False), (cloud, True)])
    assert [e.provider.name for e in router.entries] == ["cloud"]


def test_fallback_to_second_provider():
    bad = FakeAnalyzer("bad", [BAD])
    good = FakeAnalyzer("good", [GOOD])
    router = AiRouter(mode="local_first", analyzers=[(bad, False), (good, False)])
    result = router.analyze(AnalyzeInput(caption="test"))
    assert result.output.tags == ["рецепт"]
    assert result.provider == "good"


def test_repair_attempt_with_error_in_prompt():
    flaky = FakeAnalyzer("flaky", [BAD, GOOD])
    router = AiRouter(mode="local_only", analyzers=[(flaky, False)])
    result = router.analyze(AnalyzeInput(caption="test"))
    assert result.attempts == 2
    # во второй попытке ошибка и сырой ответ попали в промпт (docs/07 §7.5)
    repair_prompt = flaky.calls[1][1]
    assert "не прошёл валидацию" in repair_prompt
    assert BAD in repair_prompt


def test_all_providers_fail_raises():
    bad1 = FakeAnalyzer("bad1", [BAD, BAD])
    bad2 = FakeAnalyzer("bad2", [RuntimeError("down"), RuntimeError("down")])
    router = AiRouter(mode="local_only", analyzers=[(bad1, False), (bad2, False)])
    with pytest.raises(AnalysisFailed):
        router.analyze(AnalyzeInput(caption="test"))


def test_circuit_breaker_opens():
    breaker = CircuitBreaker(failures=2, pause_min=15)
    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    assert breaker.allow(now)
    breaker.record_failure(now)
    assert breaker.allow(now)
    breaker.record_failure(now)
    assert not breaker.allow(now)
    assert breaker.allow(now + dt.timedelta(minutes=16))
    breaker.record_success()
    assert breaker.consecutive_failures == 0


def test_no_providers_raises_analysis_failed():
    from toklib_ai.interfaces import AnalysisFailed

    router = AiRouter(mode="local_only", analyzers=[])
    with pytest.raises(AnalysisFailed):
        router.analyze(AnalyzeInput(caption="test"))


def test_normalize_base_url():
    assert normalize_base_url("http://ollama:11434") == "http://ollama:11434/v1"
    assert normalize_base_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1"

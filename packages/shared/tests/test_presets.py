"""Пресеты железа (ai.preset) — подгонка конфига под слабое железо одним ключом."""

import pytest
from toklib_core.config import PRESETS, load_settings


def test_preset_m_applies_to_unset_paths(tmp_path):
    """Пресет M: faster-whisper small int8 + локальный Ollama 4B (i7-4790 класс)."""
    settings = load_settings({"TOKLIB_DATA_DIR": str(tmp_path)})
    settings.ai.preset = "M"
    from toklib_core.config import _apply_preset

    _apply_preset(settings, {})
    assert settings.ai.mode == "local_only"
    assert settings.ai.stt.model == "small"
    assert settings.ai.stt.compute == "int8"
    assert settings.ai.llm.model == "qwen3:4b-instruct-q4_K_M"
    assert settings.ai.budgets.nightly_backlog == 200


def test_preset_does_not_override_explicit_values(tmp_path):
    """Явно заданное в yaml значение сильнее пресета."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "ai:\n  preset: L\n  stt:\n    model: medium\n",
        encoding="utf-8",
    )
    settings = load_settings({"TOKLIB_DATA_DIR": str(tmp_path), "TOKLIB_CONFIG": str(config)})
    assert settings.ai.stt.model == "medium"  # явное — не перетёрто пресетом L
    assert settings.ai.stt.compute == "float16"  # не задано — от пресета
    assert settings.ai.llm.model == "qwen3:8b-instruct-q4_K_M"


def test_preset_cloud_uses_providers(tmp_path):
    """Пресет cloud: Groq STT + OpenRouter LLM, ключи через env."""
    settings = load_settings({"TOKLIB_DATA_DIR": str(tmp_path)})
    settings.ai.preset = "cloud"
    from toklib_core.config import _apply_preset

    _apply_preset(settings, {})
    assert settings.ai.stt.provider == "openai_compat"
    assert "groq.com" in settings.ai.stt.base_url
    assert settings.ai.stt.api_key_env == "TOKLIB_STT_KEY"
    assert "openrouter" in settings.ai.llm.base_url
    assert settings.ai.mode == "local_first"


def test_unknown_preset_raises(tmp_path):
    settings = load_settings({"TOKLIB_DATA_DIR": str(tmp_path)})
    settings.ai.preset = "ZXS"
    from toklib_core.config import _apply_preset

    with pytest.raises(ValueError, match="неизвестный пресет"):
        _apply_preset(settings, {})


def test_load_settings_applies_preset_from_yaml(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("ai:\n  preset: M\n", encoding="utf-8")
    settings = load_settings({"TOKLIB_DATA_DIR": str(tmp_path), "TOKLIB_CONFIG": str(config)})
    assert settings.ai.llm.model == "qwen3:4b-instruct-q4_K_M"
    assert settings.ai.stt.compute == "int8"


def test_all_presets_have_mode_and_stt():
    for name, overrides in PRESETS.items():
        assert "ai.mode" in overrides, name
        assert "ai.stt.provider" in overrides, name
        assert "ai.llm.model" in overrides, name

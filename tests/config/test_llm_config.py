import pytest

from src.config.llm_config import LlmConfig, build_chat_model, load_llm_config
from src.exceptions import ToolException


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("MOLT_SERVER_") and "_LLM_" in key:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MOLT_SERVER_ENVIRONMENT", "staging")


def test_falls_back_to_the_built_in_model_when_nothing_is_configured():
    assert load_llm_config("assess_security").model == "anthropic:claude-opus-5"


def test_applies_the_shared_default_to_every_tool(monkeypatch):
    monkeypatch.setenv("MOLT_SERVER_DEFAULT_LLM_MODEL", "openai:gpt-5.5")

    assert load_llm_config("assess_security").model == "openai:gpt-5.5"
    assert load_llm_config("assess_license").model == "openai:gpt-5.5"


def test_lets_one_tool_override_the_shared_default(monkeypatch):
    monkeypatch.setenv("MOLT_SERVER_DEFAULT_LLM_MODEL", "openai:gpt-5.5")
    monkeypatch.setenv("MOLT_SERVER_ASSESS_SECURITY_LLM_MODEL", "anthropic:claude-opus-5")

    assert load_llm_config("assess_security").model == "anthropic:claude-opus-5"
    assert load_llm_config("assess_license").model == "openai:gpt-5.5"


def test_keeps_shared_settings_a_tool_did_not_override(monkeypatch):
    monkeypatch.setenv("MOLT_SERVER_DEFAULT_LLM_MODEL", "openai:gpt-5.5")
    monkeypatch.setenv("MOLT_SERVER_DEFAULT_LLM_MAX_TOKENS", "8192")
    monkeypatch.setenv("MOLT_SERVER_ASSESS_SECURITY_LLM_TEMPERATURE", "0.7")

    config = load_llm_config("assess_security")

    assert (config.model, config.max_tokens, config.temperature) == ("openai:gpt-5.5", 8192, 0.7)


def test_configures_each_tool_independently(monkeypatch):
    monkeypatch.setenv("MOLT_SERVER_ASSESS_SECURITY_LLM_MODEL", "anthropic:claude-opus-5")
    monkeypatch.setenv("MOLT_SERVER_ASSESS_LICENSE_LLM_MODEL", "ollama:llama3.1")

    assert load_llm_config("assess_security").model == "anthropic:claude-opus-5"
    assert load_llm_config("assess_license").model == "ollama:llama3.1"


def test_leaves_temperature_unset_so_reasoning_models_do_not_reject_it():
    # anthropic:claude-opus-5 — the built-in default — 400s on any temperature at all.
    assert load_llm_config("assess_security").temperature is None


def test_omits_unconfigured_settings_instead_of_forwarding_them(monkeypatch):
    captured = {}

    def fake_init_chat_model(model, **kwargs):
        captured.update(model=model, **kwargs)
        return object()

    monkeypatch.setattr("src.config.llm_config.init_chat_model", fake_init_chat_model)
    build_chat_model(LlmConfig(model="anthropic:claude-opus-5"))

    assert "temperature" not in captured
    assert "max_tokens" not in captured
    assert captured["timeout"] == 120.0


def test_forwards_settings_that_were_configured(monkeypatch):
    captured = {}

    def fake_init_chat_model(model, **kwargs):
        captured.update(model=model, **kwargs)
        return object()

    monkeypatch.setattr("src.config.llm_config.init_chat_model", fake_init_chat_model)
    build_chat_model(LlmConfig(model="openai:gpt-5.5", temperature=0.7, max_tokens=8192))

    assert (captured["temperature"], captured["max_tokens"]) == (0.7, 8192)


@pytest.mark.parametrize(
    "model, provider",
    [
        ("anthropic:claude-opus-5", "anthropic"),
        ("openai:gpt-5.5", "openai"),
        ("ollama:llama3.1", "ollama"),
        ("claude-opus-5", "unknown"),
    ],
)
def test_reads_the_provider_off_the_model_string(model, provider):
    assert LlmConfig(model=model).provider == provider


def test_reports_an_unknown_provider_clearly():
    with pytest.raises(ToolException, match="not-a-provider"):
        build_chat_model(LlmConfig(model="not-a-provider:some-model"))

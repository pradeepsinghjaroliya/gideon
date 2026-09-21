import pytest
from pydantic_ai.models.openai import OpenAIChatModel

from agentic.providers.cerebras import MissingApiKeyError as CerebrasMissingApiKeyError
from agentic.providers.fireworks import MissingApiKeyError as FireworksMissingApiKeyError
from agentic.providers.openrouter import MissingApiKeyError
from agentic.providers.registry import PROVIDERS, UnknownProviderError, get_provider
from shared.config import LlmConfig


def test_ollama_registered_and_local():
    provider = get_provider("ollama")
    assert provider.id == "ollama"
    assert provider.is_local is True


def test_ollama_build_model_uses_v1_endpoint():
    provider = get_provider("ollama")
    model = provider.build_model(LlmConfig(model="qwen2.5:1.5b", base_url="http://localhost:11434"))
    assert isinstance(model, OpenAIChatModel)


def test_ollama_build_model_strips_trailing_slash_on_base_url():
    provider = get_provider("ollama")
    # Just needs to not raise / not double up the slash before "/v1" -
    # OpenAIChatModel doesn't expose base_url back out, so this only
    # guards against a crash on a trailing-slash config value.
    provider.build_model(LlmConfig(model="qwen2.5:1.5b", base_url="http://localhost:11434/"))


def test_unknown_provider_raises_clear_error():
    with pytest.raises(UnknownProviderError, match="unknown-provider"):
        get_provider("unknown-provider")


def test_registry_is_a_plain_dict_keyed_by_id():
    assert set(PROVIDERS) == {"ollama", "openrouter", "fireworks", "cerebras"}


def test_openrouter_registered_and_not_local():
    provider = get_provider("openrouter")
    assert provider.id == "openrouter"
    assert provider.is_local is False


def test_openrouter_build_model_raises_without_api_key_env(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_OPENROUTER_KEY", raising=False)
    provider = get_provider("openrouter")
    config = LlmConfig(backend="openrouter", model="nvidia/nemotron-3.5-lightning:free", api_key_env="")
    with pytest.raises(MissingApiKeyError, match="api_key_env"):
        provider.build_model(config)


def test_openrouter_build_model_raises_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_OPENROUTER_KEY", raising=False)
    provider = get_provider("openrouter")
    config = LlmConfig(
        backend="openrouter", model="nvidia/nemotron-3.5-lightning:free", api_key_env="SOME_UNSET_OPENROUTER_KEY"
    )
    with pytest.raises(MissingApiKeyError, match="SOME_UNSET_OPENROUTER_KEY"):
        provider.build_model(config)


def test_openrouter_build_model_succeeds_with_api_key_set(monkeypatch):
    monkeypatch.setenv("SOME_OPENROUTER_KEY", "sk-fake")
    provider = get_provider("openrouter")
    config = LlmConfig(
        backend="openrouter", model="nvidia/nemotron-3.5-lightning:free", api_key_env="SOME_OPENROUTER_KEY"
    )
    model = provider.build_model(config)
    assert isinstance(model, OpenAIChatModel)


def test_fireworks_registered_and_not_local():
    provider = get_provider("fireworks")
    assert provider.id == "fireworks"
    assert provider.is_local is False


def test_fireworks_build_model_raises_without_api_key_env(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_FIREWORKS_KEY", raising=False)
    provider = get_provider("fireworks")
    config = LlmConfig(backend="fireworks", model="accounts/fireworks/models/llama-v3p1-8b-instruct", api_key_env="")
    with pytest.raises(FireworksMissingApiKeyError, match="api_key_env"):
        provider.build_model(config)


def test_fireworks_build_model_raises_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_FIREWORKS_KEY", raising=False)
    provider = get_provider("fireworks")
    config = LlmConfig(
        backend="fireworks",
        model="accounts/fireworks/models/llama-v3p1-8b-instruct",
        api_key_env="SOME_UNSET_FIREWORKS_KEY",
    )
    with pytest.raises(FireworksMissingApiKeyError, match="SOME_UNSET_FIREWORKS_KEY"):
        provider.build_model(config)


def test_fireworks_build_model_succeeds_with_api_key_set(monkeypatch):
    monkeypatch.setenv("SOME_FIREWORKS_KEY", "fw-fake")
    provider = get_provider("fireworks")
    config = LlmConfig(
        backend="fireworks",
        model="accounts/fireworks/models/llama-v3p1-8b-instruct",
        api_key_env="SOME_FIREWORKS_KEY",
    )
    model = provider.build_model(config)
    assert isinstance(model, OpenAIChatModel)


def test_cerebras_registered_and_not_local():
    provider = get_provider("cerebras")
    assert provider.id == "cerebras"
    assert provider.is_local is False


def test_cerebras_build_model_raises_without_api_key_env(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_CEREBRAS_KEY", raising=False)
    provider = get_provider("cerebras")
    config = LlmConfig(backend="cerebras", model="llama3.1-8b", api_key_env="")
    with pytest.raises(CerebrasMissingApiKeyError, match="api_key_env"):
        provider.build_model(config)


def test_cerebras_build_model_raises_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_CEREBRAS_KEY", raising=False)
    provider = get_provider("cerebras")
    config = LlmConfig(
        backend="cerebras",
        model="llama3.1-8b",
        api_key_env="SOME_UNSET_CEREBRAS_KEY",
    )
    with pytest.raises(CerebrasMissingApiKeyError, match="SOME_UNSET_CEREBRAS_KEY"):
        provider.build_model(config)


def test_cerebras_build_model_succeeds_with_api_key_set(monkeypatch):
    monkeypatch.setenv("SOME_CEREBRAS_KEY", "csk-fake")
    provider = get_provider("cerebras")
    config = LlmConfig(
        backend="cerebras",
        model="llama3.1-8b",
        api_key_env="SOME_CEREBRAS_KEY",
    )
    model = provider.build_model(config)
    assert isinstance(model, OpenAIChatModel)

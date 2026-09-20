"""OpenRouter, via pydantic-ai's dedicated `OpenRouterProvider`.

Unlike Ollama (a plain OpenAI-compatible `base_url`, since it has no
pydantic-ai model class of its own), OpenRouter gets a real provider
class that applies per-upstream-model tool-calling profiles (qwen/
anthropic/meta-llama/etc. naming and format quirks) instead of treating
every OpenRouter model as generically OpenAI-shaped.
"""

from __future__ import annotations

import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from shared.config import LlmConfig

from .base import ProviderDefinition


class MissingApiKeyError(RuntimeError):
    """Raised when `config.llm.api_key_env` isn't set, or the named env
    var is empty - a clear message instead of pydantic-ai's own error
    surfacing deep inside the first LLM call."""


def build_model(config: LlmConfig) -> OpenAIChatModel:
    if not config.api_key_env:
        raise MissingApiKeyError(
            "llm.api_key_env must name an environment variable holding your "
            "OpenRouter API key - see .env.example"
        )
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise MissingApiKeyError(
            f"environment variable '{config.api_key_env}' (llm.api_key_env) is not set - "
            "put it in .env (see .env.example) or export it yourself"
        )
    return OpenAIChatModel(config.model, provider=OpenRouterProvider(api_key=api_key))


OPENROUTER = ProviderDefinition(id="openrouter", label="OpenRouter", is_local=False, build_model=build_model)

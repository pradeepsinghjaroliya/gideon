"""Fireworks AI, via pydantic-ai's dedicated `FireworksProvider`.

Same shape as `openrouter.py`: a real provider class (OpenAI-compatible
endpoint + per-model-family tool-calling profiles for llama/qwen/deepseek/
mistral/gemma) rather than treating Fireworks as generically OpenAI-shaped
the way `ollama.py` does.
"""

from __future__ import annotations

import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.fireworks import FireworksProvider

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
            "Fireworks API key - see .env.example"
        )
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise MissingApiKeyError(
            f"environment variable '{config.api_key_env}' (llm.api_key_env) is not set - "
            "put it in .env (see .env.example) or export it yourself"
        )
    return OpenAIChatModel(config.model, provider=FireworksProvider(api_key=api_key))


FIREWORKS = ProviderDefinition(id="fireworks", label="Fireworks AI", is_local=False, build_model=build_model)

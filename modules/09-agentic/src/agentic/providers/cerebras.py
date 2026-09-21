"""Cerebras Cloud, via pydantic-ai's dedicated `CerebrasProvider`.

Cerebras is remote like OpenRouter/Fireworks: the model name comes from
`config.llm.model`, and the API key is read from the env var named by
`config.llm.api_key_env` rather than stored in config.yaml.
"""

from __future__ import annotations

import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.cerebras import CerebrasProvider

from shared.config import LlmConfig

from .base import ProviderDefinition


class MissingApiKeyError(RuntimeError):
    """Raised when `config.llm.api_key_env` is empty or points at no value."""


def build_model(config: LlmConfig) -> OpenAIChatModel:
    if not config.api_key_env:
        raise MissingApiKeyError(
            "llm.api_key_env must name an environment variable holding your "
            "Cerebras API key - see .env.example"
        )
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise MissingApiKeyError(
            f"environment variable '{config.api_key_env}' (llm.api_key_env) is not set - "
            "put it in .env (see .env.example) or export it yourself"
        )
    return OpenAIChatModel(config.model, provider=CerebrasProvider(api_key=api_key))


CEREBRAS = ProviderDefinition(
    id="cerebras", label="Cerebras Cloud", is_local=False, build_model=build_model
)

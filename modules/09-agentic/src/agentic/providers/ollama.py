"""Ollama, reached through its OpenAI-compatible endpoint.

Ollama has no dedicated pydantic-ai model class; `/v1` is the endpoint
Ollama itself documents for any OpenAI-compatible client, and it's what
pydantic-ai's own docs recommend for local Ollama models too. `api_key`
is a required parameter for `OpenAIProvider` but unused by Ollama, which
has no auth - "ollama" is a placeholder, not read by anything.
"""

from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from shared.config import LlmConfig

from .base import ProviderDefinition


def build_model(config: LlmConfig) -> OpenAIChatModel:
    return OpenAIChatModel(
        config.model,
        provider=OpenAIProvider(base_url=f"{config.base_url.rstrip('/')}/v1", api_key="ollama"),
    )


OLLAMA = ProviderDefinition(id="ollama", label="Ollama (local)", is_local=True, build_model=build_model)

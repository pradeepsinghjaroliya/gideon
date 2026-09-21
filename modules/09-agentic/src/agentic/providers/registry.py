"""`config.llm.backend` -> `ProviderDefinition`.

To add a provider: write `providers/<id>.py` exporting a
`ProviderDefinition` (see `providers/ollama.py`), then add it here.
"""

from __future__ import annotations

from .base import ProviderDefinition
from .cerebras import CEREBRAS
from .fireworks import FIREWORKS
from .ollama import OLLAMA
from .openrouter import OPENROUTER

PROVIDERS: dict[str, ProviderDefinition] = {
    OLLAMA.id: OLLAMA,
    OPENROUTER.id: OPENROUTER,
    FIREWORKS.id: FIREWORKS,
    CEREBRAS.id: CEREBRAS,
}


class UnknownProviderError(ValueError):
    """Raised when `config.llm.backend` doesn't match any registered provider."""


def get_provider(provider_id: str) -> ProviderDefinition:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS)) or "(none registered)"
        raise UnknownProviderError(
            f"unknown llm.backend '{provider_id}' - known providers: {known}"
        ) from None

"""The one abstraction every LLM provider file implements.

Adding a provider = one new file here exporting a `ProviderDefinition`,
plus one entry in `registry.PROVIDERS`. Nothing else in the app needs to
change - `llm_client.agentic_client.AgenticClient` and
`07-orchestrator/main.py`'s tray wiring both go through the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from shared.config import LlmConfig

BuildModelFn = Callable[["LlmConfig"], "Model"]


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    label: str
    # True for a provider backed by a local server the tray can start/stop
    # itself (Ollama today, via `orchestrator.ollama_control.OllamaControl`).
    # False for a remote/cloud provider - those get the tray's "pause"
    # guard instead (see `main.py::_build_dashboard_controls`), since
    # there's no local process to start/stop and an accidental background
    # conversation would otherwise just spend real API credits.
    is_local: bool
    build_model: BuildModelFn

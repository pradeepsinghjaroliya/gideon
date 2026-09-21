"""Bridge from the `set_gideon_volume`/`get_gideon_volume` tools to the
live `Orchestrator`'s "assistant voice volume" gain
(`orchestrator.state_machine.Orchestrator.set_volume`/`get_volume`).

`agentic` has no reference to the running `Orchestrator` - it's built
first (`AgenticClient(config.llm)` in `orchestrator.main.main()`, before
`Orchestrator` exists) and the module dependency direction only ever goes
`orchestrator -> agentic`, never the reverse (see `09-agentic/plan.md`).
`orchestrator.main.main()` calls `bind()` once at startup, right after
constructing the (single, process-wide) `Orchestrator`, so the tools in
`tools/gideon_volume_tool.py` can reach the real gain state without
`agentic` importing `orchestrator`.
"""

from __future__ import annotations

from typing import Callable

GetVolumeFn = Callable[[], float]
SetVolumeFn = Callable[[float], None]

_get_volume: GetVolumeFn | None = None
_set_volume: SetVolumeFn | None = None


class VolumeBridgeError(RuntimeError):
    """Raised when a tool reaches the bridge before `bind()` has run."""


def bind(get_volume: GetVolumeFn, set_volume: SetVolumeFn) -> None:
    global _get_volume, _set_volume
    _get_volume = get_volume
    _set_volume = set_volume


def get_volume() -> float:
    if _get_volume is None:
        raise VolumeBridgeError("assistant voice volume is not available yet")
    return _get_volume()


def set_volume(value: float) -> None:
    if _set_volume is None:
        raise VolumeBridgeError("assistant voice volume is not available yet")
    _set_volume(value)

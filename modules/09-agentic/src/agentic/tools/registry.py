"""Every tool passed to the `pydantic_ai.Agent` - see `agentic_client.py`.

Add a tool: write a plain function with a docstring in its own file (see
`datetime_tool.py`), then append it here (wrapped in `_logged`, like
`get_current_datetime` below). An MCP server plugs in the same spot later
via a `pydantic_ai.mcp.MCPToolset` passed to `Agent(toolsets=[...])`
instead of `tools=` - not wired up yet since none is needed today.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable

from .brightness_tool import get_screen_brightness, set_screen_brightness
from .datetime_tool import get_current_datetime
from .gideon_volume_tool import get_gideon_volume, set_gideon_volume
from .system_volume_tool import get_system_volume, set_system_volume

# Every real tool call/result/error shows up on the "agentic.tools"
# logger (see `shared.logging_setup`'s "%(name)s" prefix) - pydantic-ai
# calls tools silently otherwise, which made it hard to tell whether a
# reply actually used a tool or the model just guessed (see
# docs/task.md's "Tool-calling reliability" note).
_log = logging.getLogger("agentic.tools")


def _logged(fn: Callable[..., object]) -> Callable[..., object]:
    """Wraps `fn` for logging only - `functools.wraps` keeps `__name__`,
    `__doc__` and (via `__wrapped__`, which `inspect.signature` follows
    by default) the original signature, so pydantic-ai still builds the
    exact same tool schema/description it would from `fn` directly."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        call_args = ", ".join([repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()])
        _log.info("tool call: %s(%s)", fn.__name__, call_args)
        start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except Exception:
            _log.exception("tool call failed: %s (%.3fs)", fn.__name__, time.monotonic() - start)
            raise
        _log.info("tool result: %s -> %r (%.3fs)", fn.__name__, result, time.monotonic() - start)
        return result

    return wrapper


TOOLS: list[Callable[..., object]] = [
    _logged(get_current_datetime),
    _logged(get_screen_brightness),
    _logged(set_screen_brightness),
    _logged(get_gideon_volume),
    _logged(set_gideon_volume),
    _logged(get_system_volume),
    _logged(set_system_volume),
]

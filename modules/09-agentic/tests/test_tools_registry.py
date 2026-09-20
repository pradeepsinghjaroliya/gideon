import logging

import pytest

from agentic.tools.brightness_tool import get_screen_brightness, set_screen_brightness
from agentic.tools.datetime_tool import get_current_datetime
from agentic.tools.gideon_volume_tool import get_gideon_volume, set_gideon_volume
from agentic.tools.registry import TOOLS, _logged
from agentic.tools.system_volume_tool import get_system_volume, set_system_volume


def test_registered_tools_are_the_logged_tools():
    assert len(TOOLS) == 7
    assert [t.__name__ for t in TOOLS] == [
        "get_current_datetime",
        "get_screen_brightness",
        "set_screen_brightness",
        "get_gideon_volume",
        "set_gideon_volume",
        "get_system_volume",
        "set_system_volume",
    ]


def test_logged_wrapper_preserves_name_and_docstring():
    def example(x: int) -> int:
        """Doubles x."""
        return x * 2

    wrapped = _logged(example)

    assert wrapped.__name__ == "example"
    assert wrapped.__doc__ == "Doubles x."


def test_logged_wrapper_preserves_signature_for_schema_generation():
    """pydantic-ai builds a tool's schema from `inspect.signature` - this
    only works through the wrapper because `functools.wraps` sets
    `__wrapped__`, which `inspect.signature` follows by default."""
    import inspect

    def example(x: int, y: str = "default") -> str:
        return f"{x}{y}"

    wrapped = _logged(example)

    assert inspect.signature(wrapped) == inspect.signature(example)


def test_logged_wrapper_calls_through_and_returns_result():
    calls = []

    def example(x: int) -> int:
        calls.append(x)
        return x + 1

    wrapped = _logged(example)

    assert wrapped(41) == 42
    assert calls == [41]


def test_logged_wrapper_logs_call_and_result(caplog):
    def example(x: int) -> int:
        return x + 1

    wrapped = _logged(example)

    with caplog.at_level(logging.INFO, logger="agentic.tools"):
        wrapped(41)

    messages = [r.message for r in caplog.records]
    assert any("tool call: example(41)" in m for m in messages)
    assert any("tool result: example -> 42" in m for m in messages)


def test_logged_wrapper_logs_and_reraises_on_error(caplog):
    def always_fails() -> None:
        raise ValueError("boom")

    wrapped = _logged(always_fails)

    with caplog.at_level(logging.INFO, logger="agentic.tools"):
        with pytest.raises(ValueError, match="boom"):
            wrapped()

    assert any("tool call failed: always_fails" in r.message for r in caplog.records)


def test_registered_tools_wrap_the_real_functions():
    """Registered tools are wrapped, not replaced - `functools.wraps`
    sets `__wrapped__` to the original function each one decorates."""
    assert TOOLS[0].__wrapped__ is get_current_datetime
    assert TOOLS[1].__wrapped__ is get_screen_brightness
    assert TOOLS[2].__wrapped__ is set_screen_brightness
    assert TOOLS[3].__wrapped__ is get_gideon_volume
    assert TOOLS[4].__wrapped__ is set_gideon_volume
    assert TOOLS[5].__wrapped__ is get_system_volume
    assert TOOLS[6].__wrapped__ is set_system_volume

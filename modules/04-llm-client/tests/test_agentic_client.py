"""Unit tests for `AgenticClient` - runs against `pydantic_ai`'s built-in
`TestModel` (canned output, no tool calls needed for these), never a real
Ollama server. Mirrors the old `test_ollama_client.py`'s coverage:
reply text, history/multi-turn ordering, streaming deltas, and the
clear-error path when the provider can't be reached.

Every `TestModel(...)` here passes `call_tools=[]`. `AgenticClient`
always builds its `Agent` with the real `agentic.tools.registry.TOOLS`
(no way to override it), and `TestModel`'s default `call_tools='all'`
calls *every* tool it's given real arguments before producing output -
harmless while the only tool was `get_current_datetime`, but `TOOLS` now
also has real hardware-mutating tools (screen brightness, system/assistant
volume - see `09-agentic/plan.md`). Confirmed the hard way: running this
file without `call_tools=[]` actually changed the machine's real screen
brightness and system volume (2026-09-20). `call_tools=[]` keeps these
tests to what they're actually testing - reply text/streaming/history/
logging - not tool-calling behavior.
"""

from __future__ import annotations

import logging

import pytest
from pydantic_ai import CancellationToken
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from llm_client.agentic_client import AgenticClient, AgenticClientError
from shared.config import LlmConfig

CONFIG = LlmConfig(backend="ollama", model="does-not-matter", system_prompt="You are terse.")


def test_generate_returns_stripped_reply_text():
    client = AgenticClient(CONFIG, model=TestModel(call_tools=[], custom_output_text="  hello there  "))
    assert client.generate("hi", []) == "hello there"


def test_generate_sees_prior_history():
    """The model under test echoes back whatever it was asked, including
    prior turns pydantic-ai handed it via `message_history` - proves
    `_history_to_messages` actually threads history through."""

    def echo_last_user_message(messages, info: AgentInfo):
        from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart

        last_user_text = next(
            part.content
            for message in reversed(messages)
            for part in reversed(message.parts)
            if isinstance(part, UserPromptPart)
        )
        return ModelResponse(parts=[TextPart(content=f"echo: {last_user_text}")])

    client = AgenticClient(CONFIG, model=FunctionModel(echo_last_user_message))
    history = [{"role": "user", "content": "my name is Alex"}, {"role": "assistant", "content": "hi Alex"}]
    reply = client.generate("what's my name?", history)
    assert reply == "echo: what's my name?"


def test_generate_stream_yields_deltas():
    client = AgenticClient(CONFIG, model=TestModel(call_tools=[], custom_output_text="one two three"))
    chunks = list(client.generate_stream("hi", []))
    assert "".join(chunks) == "one two three"
    assert len(chunks) >= 1


def test_generate_raises_clear_error_on_provider_failure():
    def always_fails(messages, info: AgentInfo):
        raise ModelHTTPError(status_code=500, model_name="test", body="boom")

    client = AgenticClient(CONFIG, model=FunctionModel(always_fails))
    with pytest.raises(AgenticClientError):
        client.generate("hi", [])


def test_cancel_with_nothing_in_flight_is_a_no_op():
    client = AgenticClient(CONFIG, model=TestModel(call_tools=[], custom_output_text="ok"))
    client.cancel()  # must not raise


def test_generate_logs_run_start_and_finish_on_the_agentic_logger(caplog):
    with caplog.at_level(logging.INFO, logger="agentic"):
        AgenticClient(CONFIG, model=TestModel(call_tools=[], custom_output_text="hello there")).generate("hi", [])

    messages = [r.message for r in caplog.records]
    assert any("agent ready: backend=ollama model=does-not-matter" in m for m in messages)
    assert any("agent run starting" in m for m in messages)
    assert any("agent run finished" in m for m in messages)


def test_generate_logs_prompt_and_reply_at_debug_level(caplog):
    with caplog.at_level(logging.DEBUG, logger="agentic"):
        AgenticClient(CONFIG, model=TestModel(call_tools=[], custom_output_text="hello there")).generate("hi there", [])

    messages = [r.message for r in caplog.records]
    assert any("agent run prompt: 'hi there'" in m for m in messages)
    assert any("agent run reply: 'hello there'" in m for m in messages)


def test_generate_stream_logs_run_start_and_finish_with_delta_count(caplog):
    client = AgenticClient(CONFIG, model=TestModel(call_tools=[], custom_output_text="one two three"))

    with caplog.at_level(logging.INFO, logger="agentic"):
        list(client.generate_stream("hi", []))

    messages = [r.message for r in caplog.records]
    assert any("agent stream starting" in m for m in messages)
    assert any("agent stream finished" in m and "deltas)" in m for m in messages)


def test_generate_logs_and_raises_clear_error_on_provider_failure(caplog):
    def always_fails(messages, info: AgentInfo):
        raise ModelHTTPError(status_code=500, model_name="test", body="boom")

    client = AgenticClient(CONFIG, model=FunctionModel(always_fails))

    with caplog.at_level(logging.INFO, logger="agentic"):
        with pytest.raises(AgenticClientError):
            client.generate("hi", [])

    assert any("agent run failed" in r.message for r in caplog.records)


def test_cancel_logs_when_a_run_is_actually_in_flight(caplog):
    client = AgenticClient(CONFIG, model=TestModel(call_tools=[], custom_output_text="ok"))
    client._token = CancellationToken()  # simulate a run in flight without a real one

    with caplog.at_level(logging.INFO, logger="agentic"):
        client.cancel()

    assert any("cancel requested for in-flight agent run" in r.message for r in caplog.records)

"""LLM client implementing `shared.interfaces.LLMClient` on top of
`09-agentic`'s `pydantic_ai.Agent` - the one path every provider (Ollama
today, more later) goes through, so every provider gets the same tool-use
loop instead of a single-shot chat completion.

Sync/async bridge: pydantic-ai's streaming API is async
(`async with agent.run_stream(...) as result: async for delta in
result.stream_text(delta=True): ...`), but `Orchestrator` calls
`generate_stream()` as a plain synchronous `Iterator[str]` from its own
background thread (see `orchestrator/state_machine.py::_think_and_speak`).
This client owns one dedicated thread running its own asyncio event loop
for the lifetime of an in-flight `generate_stream()` call; deltas cross
back to the caller's thread over a plain `queue.Queue`, the same way
`generate_stream()` never lets async leak into `Orchestrator`.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import TYPE_CHECKING, Iterator

from pydantic_ai import Agent, CancellationToken
from pydantic_ai.exceptions import AgentRunError, RunCancelled
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from agentic.providers.registry import get_provider
from agentic.tools.registry import TOOLS
from shared.config import LlmConfig

if TYPE_CHECKING:
    from pydantic_ai.models import Model

# Silences the ASCII-art banner pydantic-ai prints on first Agent use -
# Gideon's stdout goes to a log file/journald, not an interactive
# terminal, so this is pure noise there. Must be set before the first
# `Agent(...)` is constructed, not merely before it's run.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

# Sentinel telling the reading side of the queue "no more deltas coming" -
# distinct from any real (possibly falsy/empty) delta string.
_DONE = object()

# Every log line from this module is prefixed "agentic:" (see
# `shared.logging_setup`'s "%(name)s" format) - run/tool-call visibility
# was the whole point of the debugging done in docs/task.md's
# "Tool-calling reliability" note, so it's worth more than the bare
# "LLM token delta" logging `07-orchestrator` already does. Full
# prompt/reply text is DEBUG (verbose, mirrors the existing token-delta
# logging); run start/finish/cancel/error is INFO.
_log = logging.getLogger("agentic")


class AgenticClientError(RuntimeError):
    """Raised when the configured provider can't be reached, with a clear
    message instead of a raw `pydantic_ai` traceback - same rationale as
    `OllamaClient`'s `OllamaConnectionError` it replaces."""


def _history_to_messages(history: list[dict], system_prompt: str) -> list[ModelMessage]:
    """Converts Gideon's plain `{"role", "content"}` history (see
    `shared.interfaces.LLMClient`) into pydantic-ai's typed message list.
    The system prompt is injected once, at the front, matching how
    `Agent(system_prompt=...)` would apply it on a fresh conversation -
    `Agent` does not re-add it when `message_history` is passed in."""
    messages: list[ModelMessage] = []
    if system_prompt:
        messages.append(ModelRequest(parts=[SystemPromptPart(content=system_prompt)]))
    for turn in history:
        content = turn["content"]
        if turn["role"] == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=content)]))
    return messages


class AgenticClient:
    def __init__(self, config: LlmConfig, model: "Model | None" = None) -> None:
        """`model` is a test-only override (e.g. `pydantic_ai.models.test.TestModel`)
        to run against a canned model instead of `config.backend`'s real
        provider - the same role `OllamaClient`'s injectable `post_fn`/
        `stream_post_fn` played for testing without a real Ollama server."""
        if model is None:
            model = get_provider(config.backend).build_model(config)
        self._system_prompt = config.system_prompt
        self._agent = Agent(model, system_prompt=config.system_prompt, tools=TOOLS)
        self._token: CancellationToken | None = None
        self._lock = threading.Lock()
        _log.info("agent ready: backend=%s model=%s", config.backend, config.model)

    def generate(self, prompt: str, history: list[dict]) -> str:
        token = CancellationToken()
        with self._lock:
            self._token = token
        _log.info("agent run starting (%d history turns)", len(history))
        _log.debug("agent run prompt: %r", prompt)
        start = time.monotonic()
        try:
            result = self._agent.run_sync(
                prompt,
                message_history=_history_to_messages(history, self._system_prompt),
                cancellation_token=token,
            )
            reply = result.output.strip()
            _log.info("agent run finished in %.2fs", time.monotonic() - start)
            _log.debug("agent run reply: %r", reply)
            return reply
        except RunCancelled:
            _log.info("agent run cancelled after %.2fs", time.monotonic() - start)
            return ""  # a deliberate `cancel()` - not an error, just no reply
        except AgentRunError as exc:
            _log.error("agent run failed after %.2fs: %s", time.monotonic() - start, exc)
            raise AgenticClientError(f"agentic LLM call failed: {exc}") from exc
        finally:
            with self._lock:
                if self._token is token:
                    self._token = None

    def generate_stream(self, prompt: str, history: list[dict]) -> Iterator[str]:
        """See the module docstring for the thread/queue bridge this uses
        to expose pydantic-ai's async streaming as a plain iterator."""
        messages = _history_to_messages(history, self._system_prompt)
        token = CancellationToken()
        with self._lock:
            self._token = token

        chunks: queue.Queue[object] = queue.Queue()
        error: list[BaseException] = []

        _log.info("agent stream starting (%d history turns)", len(history))
        _log.debug("agent stream prompt: %r", prompt)
        start = time.monotonic()

        def run_loop() -> None:
            import asyncio

            async def stream() -> None:
                delta_count = 0
                try:
                    async with self._agent.run_stream(
                        prompt, message_history=messages, cancellation_token=token
                    ) as result:
                        async for delta in result.stream_text(delta=True):
                            delta_count += 1
                            chunks.put(delta)
                except RunCancelled:
                    _log.info(
                        "agent stream cancelled after %.2fs (%d deltas)", time.monotonic() - start, delta_count
                    )
                except AgentRunError as exc:
                    _log.error("agent stream failed after %.2fs: %s", time.monotonic() - start, exc)
                    error.append(AgenticClientError(f"agentic LLM call failed: {exc}"))
                else:
                    _log.info("agent stream finished in %.2fs (%d deltas)", time.monotonic() - start, delta_count)
                finally:
                    chunks.put(_DONE)

            asyncio.run(stream())

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        try:
            while True:
                item = chunks.get()
                if item is _DONE:
                    break
                yield item  # type: ignore[misc]
        finally:
            thread.join()
            with self._lock:
                if self._token is token:
                    self._token = None
        if error:
            raise error[0]

    def cancel(self) -> None:
        """Interrupts an in-flight `generate`/`generate_stream()` call from
        another thread (the dashboard's "Stop generating" control).
        `CancellationToken.cancel()` is itself thread-safe and idempotent -
        a no-op if nothing is in flight, or if the call already finished
        between this method grabbing the reference and cancelling it."""
        with self._lock:
            token = self._token
        if token is not None:
            _log.info("cancel requested for in-flight agent run")
            token.cancel()

"""LLM client implementing `shared.interfaces.LLMClient`.

Talks to a local Ollama server over its HTTP `/api/chat` endpoint using
plain `requests` - no SDK needed for this one endpoint.
"""

from __future__ import annotations

import json
import threading
from typing import Callable, Iterator, Protocol

# (url, json_payload) -> parsed JSON response body
PostFn = Callable[[str, dict], dict]


class StreamResponse(Protocol):
    """Whatever `stream_post_fn` returns - just needs to look like a
    `requests.Response` opened with `stream=True`: NDJSON lines one at a
    time via `iter_lines()`, and a `close()` that releases the underlying
    connection (also what `cancel()` calls from another thread)."""

    def iter_lines(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


StreamPostFn = Callable[[str, dict], StreamResponse]


class OllamaConnectionError(RuntimeError):
    """Raised when the Ollama server can't be reached, with a clear
    message instead of a raw `requests` connection-refused traceback."""


class OllamaClient:
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        system_prompt: str = "",
        post_fn: PostFn | None = None,
        stream_post_fn: StreamPostFn | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._system_prompt = system_prompt
        self._post_fn = post_fn or self._default_post
        self._stream_post_fn = stream_post_fn or self._default_stream_post
        self._response: StreamResponse | None = None
        self._lock = threading.Lock()

    def _default_post(self, url: str, payload: dict) -> dict:
        import requests

        try:
            response = requests.post(url, json=payload, timeout=120)
        except requests.exceptions.ConnectionError as exc:
            raise OllamaConnectionError(
                f"could not connect to Ollama at {self._base_url} - "
                "is 'ollama serve' running?"
            ) from exc
        response.raise_for_status()
        return response.json()

    def _default_stream_post(self, url: str, payload: dict) -> StreamResponse:
        import requests

        try:
            response = requests.post(url, json=payload, stream=True, timeout=120)
        except requests.exceptions.ConnectionError as exc:
            raise OllamaConnectionError(
                f"could not connect to Ollama at {self._base_url} - "
                "is 'ollama serve' running?"
            ) from exc
        response.raise_for_status()
        return response

    def _build_messages(self, prompt: str, history: list[dict]) -> list[dict]:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        return messages

    def generate(self, prompt: str, history: list[dict]) -> str:
        url = f"{self._base_url}/api/chat"
        payload = {"model": self._model, "messages": self._build_messages(prompt, history), "stream": False}
        data = self._post_fn(url, payload)
        return data["message"]["content"].strip()

    def generate_stream(self, prompt: str, history: list[dict]) -> Iterator[str]:
        """Yields incremental text deltas from Ollama's streaming
        `/api/chat` (NDJSON, one `{"message": {"content": "..."}, "done":
        bool}` object per line) as they arrive, instead of blocking for
        the whole reply - lets `07-orchestrator` start speaking the first
        sentence while later ones are still being generated.

        Tracks the in-flight response under `self._lock` the same way
        `01-audio-io`'s `SpeakerAudioSink` tracks its stream: this method
        is the sole owner of `response.close()` for the response it
        opens, from start to finish - `cancel()` (called from another
        thread) only ever closes a response it can prove is still this
        one's, never racing this method's own `finally`.
        """
        url = f"{self._base_url}/api/chat"
        payload = {"model": self._model, "messages": self._build_messages(prompt, history), "stream": True}
        response = self._stream_post_fn(url, payload)
        with self._lock:
            self._response = response
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content
                if data.get("done"):
                    break
        finally:
            response.close()
            with self._lock:
                if self._response is response:
                    self._response = None

    def cancel(self) -> None:
        """Interrupts an in-flight `generate_stream()` call from another
        thread (the dashboard's "Stop generating" control) by closing the
        response it's reading - `requests` surfaces that as an exception
        from the blocked read, unblocking the caller's `for line in
        response.iter_lines()`. A no-op if nothing is in flight, or if
        the response already finished and closed itself between this
        method grabbing the reference and calling `close()` on it."""
        with self._lock:
            response = self._response
        if response is None:
            return
        try:
            response.close()
        except Exception:
            pass

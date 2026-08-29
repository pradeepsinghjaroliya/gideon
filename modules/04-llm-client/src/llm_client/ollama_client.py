"""LLM client implementing `shared.interfaces.LLMClient`.

Talks to a local Ollama server over its HTTP `/api/chat` endpoint using
plain `requests` - no SDK needed for this one endpoint.
"""

from __future__ import annotations

from typing import Callable

# (url, json_payload) -> parsed JSON response body
PostFn = Callable[[str, dict], dict]


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
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._system_prompt = system_prompt
        self._post_fn = post_fn or self._default_post

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

    def generate(self, prompt: str, history: list[dict]) -> str:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        url = f"{self._base_url}/api/chat"
        payload = {"model": self._model, "messages": messages, "stream": False}
        data = self._post_fn(url, payload)
        return data["message"]["content"].strip()

"""Standalone multi-turn chat REPL demo.

Proves conversation history/context works before anything voice-related
touches this module. Run: `python -m llm_client.chat_demo`.
"""

from __future__ import annotations

import time

from shared.config import load_config

from llm_client.ollama_client import OllamaClient, OllamaConnectionError


def main() -> None:
    config = load_config()
    client = OllamaClient(
        model=config.llm.model,
        base_url=config.llm.base_url,
        system_prompt=config.llm.system_prompt,
    )

    print(f"Chatting with {config.llm.model} at {config.llm.base_url} (Ctrl+C to quit)")
    history: list[dict] = []

    while True:
        try:
            prompt = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not prompt:
            continue

        start = time.monotonic()
        try:
            reply = client.generate(prompt, history)
        except OllamaConnectionError as exc:
            print(f"error: {exc}")
            continue
        elapsed = time.monotonic() - start

        print(f"{reply}\n[{elapsed:.2f}s]")
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()

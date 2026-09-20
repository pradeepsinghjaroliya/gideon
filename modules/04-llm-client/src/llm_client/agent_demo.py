"""Standalone REPL for exercising just the agentic LLM client - no mic,
wake word, STT, or TTS involved. Useful for quickly trying a new
model/provider or watching tool-call behavior in isolation, e.g. when
chasing a tool-calling reliability question like the one in
docs/task.md's "Tool-calling reliability" note. Run:
`python -m llm_client.agent_demo`.

Turns on INFO logging for the "agentic" and "agentic.tools" loggers (see
`agentic_client.py` / `agentic/tools/registry.py`) so every agent run and
tool call shows up live while you type - pass `--debug` for full
prompt/reply text too.
"""

from __future__ import annotations

import logging
import sys
import time

from shared.config import load_config
from shared.logging_setup import setup_logging

from llm_client.agentic_client import AgenticClient, AgenticClientError


def main() -> None:
    level = logging.DEBUG if "--debug" in sys.argv[1:] else logging.INFO
    setup_logging("agentic", level=level)
    setup_logging("agentic.tools", level=level)

    config = load_config()
    client = AgenticClient(config.llm)

    print(f"Chatting with {config.llm.backend}:{config.llm.model} (Ctrl+C to quit)")
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
        except AgenticClientError as exc:
            print(f"error: {exc}")
            continue
        elapsed = time.monotonic() - start

        print(f"{reply}\n[{elapsed:.2f}s]")
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()

"""A scripted fake conversation, for developing the overlay without the
rest of the assistant.

Restyling the card, retuning the fade, or checking the layout at a
different width otherwise means saying "hey gideon" into a microphone and
waiting on whisper, Ollama and Piper for every single iteration. This
replays a realistic event stream - mic levels, live partials mutating into
a final transcript, token-by-token reply deltas, the return to idle and the
auto-hide - at realistic timings instead.

Two ways in:

    python -m transcript_ui --demo      # overlay + script in one process
    python -m transcript_ui.demo        # script only, into a running overlay
"""

from __future__ import annotations

import math
import random
import sys
import threading
import time
from typing import Callable

from shared.transcript import (
    ASSISTANT_DELTA,
    ASSISTANT_FINAL,
    LEVEL,
    RESET,
    STATE,
    STATE_IDLE,
    STATE_LISTENING,
    STATE_PROCESSING,
    STATE_SPEAKING,
    USER_FINAL,
    USER_PARTIAL,
    TranscriptEvent,
)

Emit = Callable[[TranscriptEvent], None]

# Each entry is the growing partial transcript of one spoken question, the
# way `StreamingTranscriber` actually reports it: whole-utterance rewrites
# that get longer and occasionally *correct* an earlier guess (here
# "whether" -> "weather"), since that is the behaviour the overlay has to
# look good handling.
_SCRIPT = [
    {
        "partials": [
            "what's the",
            "what's the whether",
            "what's the weather like",
            "what's the weather like in",
        ],
        "final": "What's the weather like in Kathmandu today?",
        "reply": (
            "Right now Kathmandu is about 18 degrees and mostly clear. "
            "There's a light breeze from the south. "
            "It should stay dry through the evening, so you won't need an umbrella."
        ),
    },
    {
        "partials": ["and", "and what about", "and what about tomorrow"],
        "final": "And what about tomorrow?",
        "reply": "Tomorrow looks cooler, around 14 degrees, with a chance of rain in the afternoon.",
    },
]


def run_demo(emit: Emit, loop: bool = True) -> None:
    """Play the script. Blocks; run it on a thread (see
    `start_demo_thread`) if the caller also needs to run a main loop."""
    while True:
        emit(TranscriptEvent(kind=RESET))
        for turn in _SCRIPT:
            _play_turn(emit, turn)
        emit(TranscriptEvent(kind=STATE, state=STATE_IDLE))
        if not loop:
            return
        # Long enough to watch the auto-hide actually happen, which is half
        # of what there is to tune here.
        time.sleep(9.0)


def _play_turn(emit: Emit, turn: dict) -> None:
    emit(TranscriptEvent(kind=STATE, state=STATE_LISTENING))

    # Speaking: mic levels throughout, with partials landing among them.
    for partial in turn["partials"]:
        _emit_levels(emit, seconds=0.75)
        emit(TranscriptEvent(kind=USER_PARTIAL, text=partial))
    _emit_levels(emit, seconds=0.5)

    emit(TranscriptEvent(kind=STATE, state=STATE_PROCESSING))
    emit(TranscriptEvent(kind=LEVEL, level=0.0))
    time.sleep(0.9)  # stands in for whisper on the full utterance
    emit(TranscriptEvent(kind=USER_FINAL, text=turn["final"]))

    time.sleep(0.7)  # stands in for the LLM's time-to-first-token
    emit(TranscriptEvent(kind=STATE, state=STATE_SPEAKING))
    for delta in _fake_deltas(turn["reply"]):
        emit(TranscriptEvent(kind=ASSISTANT_DELTA, text=delta))
        # Bursty on purpose: a steady drip would not exercise the
        # typewriter smoothing that exists specifically to hide this.
        time.sleep(random.uniform(0.01, 0.12))
    emit(TranscriptEvent(kind=ASSISTANT_FINAL, text=turn["reply"]))
    time.sleep(1.6)


def _fake_deltas(text: str) -> list[str]:
    """Chop a reply into token-sized pieces the way Ollama streams it -
    a few characters at a time, splitting mid-word as real tokenizers do."""
    deltas = []
    index = 0
    while index < len(text):
        size = random.randint(2, 7)
        deltas.append(text[index : index + size])
        index += size
    return deltas


def _emit_levels(emit: Emit, seconds: float) -> None:
    """Plausible speech envelope: a slow syllable rhythm plus noise, rather
    than a flat value, so the meter animates the way it will in real use."""
    steps = max(1, int(seconds / 0.05))
    for step in range(steps):
        phase = step / 6.0
        level = 0.45 + 0.35 * math.sin(phase) + random.uniform(-0.12, 0.12)
        emit(TranscriptEvent(kind=LEVEL, level=max(0.05, min(1.0, level))))
        time.sleep(0.05)


def start_demo_thread(emit: Emit) -> threading.Thread:
    thread = threading.Thread(target=run_demo, args=(emit,), name="transcript-demo", daemon=True)
    thread.start()
    return thread



def main(argv: list[str] | None = None) -> int:
    """Standalone mode: feed a *running* overlay over the real socket, so
    this also serves as an end-to-end check of the wire format."""
    from shared.logging_setup import setup_logging
    from transcript_ui.client import TranscriptClient

    log = setup_logging("transcript_ui.demo")
    client = TranscriptClient(logger=log)
    if not client.socket_path.exists():
        log.error(
            "no overlay is listening on %s - start one with `python -m transcript_ui`, "
            "or run `python -m transcript_ui --demo` to do both at once",
            client.socket_path,
        )
        return 1
    client.start()
    log.info("replaying a scripted conversation into %s (Ctrl-C to stop)", client.socket_path)
    try:
        run_demo(client.emit)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The conversation-transcript event contract, shared between the producer
(`07-orchestrator`, which watches the pipeline) and the consumer
(`08-transcript-ui`, which draws the on-screen overlay).

Lives here, next to `interfaces.py`, for the same reason every other
cross-module contract does (see docs/ARCHITECTURE.md): the orchestrator must be
able to emit these without importing any UI code, and the UI must be able
to render them without importing any pipeline code. Neither side depends
on the other - both depend on this.

Events are deliberately flat and JSON-serializable rather than a class
hierarchy, because they cross a process boundary (a unix socket - see
`transcript_ui/protocol.py`). `kind` is the discriminator; only the fields
relevant to that kind carry meaning, and the rest keep their defaults.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

# --- event kinds -----------------------------------------------------------
#
# STATE mirrors the symbolic state strings `07-orchestrator` already reports
# via its `on_state` callback (see `state_machine.Orchestrator._set_status`),
# so the overlay and the tray icon are driven by exactly the same vocabulary
# rather than two parallel sets of names that could drift apart.
STATE = "state"

# The user's own speech, transcribed live while they are still talking
# (`03-stt`'s `StreamingTranscriber`). Successive partials for one utterance
# each supersede the previous one - they are a replacement, not an append.
USER_PARTIAL = "user_partial"

# The user's finished utterance, from the main (higher-quality) STT model.
# Replaces whatever partial was last shown for that utterance.
USER_FINAL = "user_final"

# One incremental chunk of the assistant's reply, appended to whatever has
# been shown so far - straight from `LLMClient.generate_stream()`.
ASSISTANT_DELTA = "assistant_delta"

# The assistant's complete reply, marking the end of its turn.
ASSISTANT_FINAL = "assistant_final"

# Mic input loudness, 0.0-1.0, emitted while listening so the overlay can
# show that it is hearing something even before any words are decoded.
LEVEL = "level"

# Clears the transcript - a brand new conversation rather than a follow-up.
RESET = "reset"

KINDS = frozenset(
    {STATE, USER_PARTIAL, USER_FINAL, ASSISTANT_DELTA, ASSISTANT_FINAL, LEVEL, RESET}
)

# Symbolic assistant states, matching `on_state`'s existing vocabulary.
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_PROCESSING = "processing"
STATE_SPEAKING = "speaking"
STATE_ERROR = "error"


@dataclass(frozen=True)
class TranscriptEvent:
    """One thing that just happened in the conversation.

    Frozen because these fan out to more than one consumer (the overlay
    process today, potentially a log/history file later) and a shared
    mutable event would let one consumer corrupt another's view.

    `seq` is a per-producer monotonic counter. The transport is ordered
    (a stream socket), so it is not needed for reordering - it exists so a
    consumer can cheaply detect that it dropped events (e.g. it was started
    mid-conversation, or reconnected after the socket went away) instead of
    silently rendering a transcript with a hole in it.
    """

    kind: str
    text: str = ""
    state: str = ""
    level: float = 0.0
    seq: int = 0

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown transcript event kind: {self.kind!r}")

    def to_json(self) -> str:
        """Single-line JSON - newline-delimited JSON is the wire format
        (see `transcript_ui/protocol.py`), so the payload itself must never
        contain a raw newline. `json.dumps` escapes them inside strings, and
        passing no `indent` keeps the output on one line."""
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "TranscriptEvent":
        """Raises `ValueError` on anything unusable (malformed JSON, not an
        object, unknown/missing `kind`, wrong field types) so callers have a
        single exception type to guard against - the overlay's reader treats
        a bad line as one to skip, never as a reason to die."""
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed transcript event JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"transcript event must be a JSON object, got {type(raw).__name__}")

        unknown = set(raw) - {f for f in ("kind", "text", "state", "level", "seq")}
        if unknown:
            # Forward-compatibility: a newer producer may add fields this
            # consumer doesn't know yet. Dropping them is strictly better
            # than refusing the whole event, which would blank the overlay.
            raw = {key: value for key, value in raw.items() if key not in unknown}

        try:
            return cls(
                kind=str(raw["kind"]),
                text=str(raw.get("text", "")),
                state=str(raw.get("state", "")),
                level=float(raw.get("level", 0.0)),
                seq=int(raw.get("seq", 0)),
            )
        except KeyError as exc:
            raise ValueError(f"transcript event missing required field: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid transcript event field: {exc}") from exc


@runtime_checkable
class TranscriptSink(Protocol):
    """Whatever the orchestrator emits transcript events into.

    Implementations MUST NOT raise and MUST NOT block for any meaningful
    length of time: `emit` is called from the pipeline's own hot paths -
    once per mic frame while listening, once per LLM token while replying -
    so a slow or throwing sink would stutter or break the actual
    conversation. `transcript_ui.client.TranscriptClient` is the real
    implementation and swallows every transport error accordingly, the same
    way `text_input.tray.TrayApp.set_status` already refuses to let a
    failed tooltip update take down status reporting.
    """

    def emit(self, event: TranscriptEvent) -> None: ...

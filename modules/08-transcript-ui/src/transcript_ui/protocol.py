"""Wire format between the orchestrator and the overlay process:
newline-delimited JSON over a unix domain socket.

Why a socket at all rather than an in-process callback: see this module's
`plan.md`. Why NDJSON specifically - it is self-framing (one event per
line, no length prefixes to get wrong), trivially debuggable
(`socat - UNIX-CONNECT:...` shows the live conversation as plain text), and
`shared.transcript.TranscriptEvent` already serializes to exactly one line.

Everything here is toolkit-agnostic and import-safe: **no GTK import**, so
the orchestrator side (`client.py`) can use it without PyGObject being
present or a display being available.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO, Iterator

from shared.transcript import TranscriptEvent

# Lets a caller point both ends at a different socket - used by the tests
# (so a test run never collides with a real overlay the user has open) and
# handy for running two instances side by side while developing.
SOCKET_ENV_VAR = "GIDEON_TRANSCRIPT_SOCKET"

_SOCKET_NAME = "gideon-transcript.sock"


def default_socket_path() -> Path:
    """Where the overlay listens unless told otherwise.

    `$XDG_RUNTIME_DIR` is the correct home for a per-user, per-session
    socket and is what systemd sets up on every mainstream distro. It is
    not *guaranteed* to exist though (a bare `su`, a minimal container, a
    non-systemd init), so fall back to a uid-qualified name under the
    system temp dir - qualified by uid because `/tmp` is shared, and an
    unqualified name there would collide between users on a multi-user box.
    """
    override = os.environ.get(SOCKET_ENV_VAR)
    if override:
        return Path(override)

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and Path(runtime_dir).is_dir():
        return Path(runtime_dir) / _SOCKET_NAME

    import tempfile

    return Path(tempfile.gettempdir()) / f"gideon-transcript-{os.getuid()}.sock"


def encode(event: TranscriptEvent) -> bytes:
    """One event, one line, UTF-8."""
    return (event.to_json() + "\n").encode("utf-8")


def iter_events(stream: IO[bytes]) -> Iterator[TranscriptEvent]:
    """Decode a byte stream of NDJSON into events, skipping anything
    unusable.

    Deliberately forgiving: a truncated final line (the producer was killed
    mid-write), a blank line, or a line this version does not understand
    must cost exactly that one event, never the connection. The overlay
    showing a transcript with one gap in it is a far better outcome than the
    overlay dying in the middle of a conversation.

    Iteration ends when the stream does (the producer disconnected).
    """
    for raw in stream:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            yield TranscriptEvent.from_json(line)
        except ValueError:
            continue

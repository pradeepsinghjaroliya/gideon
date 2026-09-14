"""Orchestrator-side end of the transcript link: implements
`shared.transcript.TranscriptSink`.

This class exists to make one guarantee, and every design choice below
follows from it: **nothing about the transcript overlay may ever slow
down, break, or crash the voice pipeline.** `emit()` is called from the
orchestrator's hot paths - once per 30ms mic frame while listening, once
per LLM token while replying - so it must be O(1), must never block on I/O,
and must never raise. The overlay is a nicety; the assistant working is not.

So `emit()` only drops the event into a bounded in-memory queue and
returns. A background thread owns the socket and does all the connecting,
reconnecting and writing. If the overlay is not running, is wedged, or dies
mid-conversation, the worst case is that events are silently discarded.

No GTK import here (only `protocol`/`shared.transcript`), so the
orchestrator can use this on a machine with no PyGObject and no display.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from shared.transcript import HIDE, TranscriptEvent

from transcript_ui.protocol import default_socket_path, encode

# Generous relative to the real event rate (a few dozen per second at the
# very peak), so this only fills if the consumer has genuinely stopped
# reading - at which point discarding is the correct response, not buffering
# an ever-growing backlog of transcript nobody is looking at.
_QUEUE_SIZE = 512

# Reconnect backoff. Starts quick (the common case is the overlay being a
# second slower to start than the orchestrator) and caps low enough that
# starting the overlay mid-session picks up within a few seconds.
_RECONNECT_MIN_SECONDS = 0.5
_RECONNECT_MAX_SECONDS = 4.0

# Bounds how long a single write may block. The overlay's reader is a tight
# loop, so the socket buffer filling means it has wedged; failing the write
# (and so dropping the connection) recovers, where blocking would pin the
# sender thread indefinitely.
_SEND_TIMEOUT_SECONDS = 1.0

_SHUTDOWN = object()


class TranscriptClient:
    def __init__(
        self,
        socket_path: Path | str | None = None,
        enabled: bool = True,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._socket_path = Path(socket_path) if socket_path is not None else default_socket_path()
        self._enabled = enabled
        self._log = logger or logging.getLogger("transcript_ui.client")
        self._clock = clock

        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._dropped = 0
        self._connected = False

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def is_connected(self) -> bool:
        """Best-effort, for diagnostics/tests only - the orchestrator never
        branches on this, it just emits and lets events fall on the floor if
        nobody is listening."""
        return self._connected

    def is_enabled(self) -> bool:
        """For the tray's "Transcript: On/Off" toggle to read current
        state (`set_enabled`'s counterpart)."""
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Runtime on/off toggle for the tray's "Transcript" control -
        unlike the constructor's `enabled` (a static "don't even wire this
        up" switch checked once at startup), this can flip at any time.
        Turning off hides whatever is on screen right now (rather than
        leaving it to the overlay's own auto-hide timer) before it stops
        forwarding events; turning on resumes forwarding and starts the
        sender thread if `start()` was never called or `enabled=False` at
        construction skipped it. Never raises - same contract as `emit()`,
        since this runs from a tray click, not the pipeline itself, but
        nothing here should be allowed to break the tray either."""
        if value == self._enabled:
            return
        if not value:
            self.hide_now()
        self._enabled = value
        if value:
            self.start()

    def hide_now(self) -> None:
        """Dismiss the overlay immediately - the tray's "Hide transcript
        now" action, for early dismissal instead of waiting out
        `hide_after_seconds`. A no-op while disabled, same as `emit()`."""
        self.emit(TranscriptEvent(kind=HIDE))

    def start(self) -> None:
        if not self._enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="transcript-client", daemon=True)
        self._thread.start()

    def close(self, timeout: float = 1.0) -> None:
        self._stopping.set()
        try:
            self._queue.put_nowait(_SHUTDOWN)
        except queue.Full:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        if self._dropped:
            self._log.debug("dropped %d transcript events over this session", self._dropped)

    # --- TranscriptSink ----------------------------------------------------

    def emit(self, event: TranscriptEvent) -> None:
        """Never raises, never blocks. See the module docstring."""
        if not self._enabled:
            return
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        try:
            # `replace`-by-construction rather than mutating: the event is
            # frozen, and stamping the sequence number here (not at each of
            # the orchestrator's ~6 call sites) keeps the counter in one place.
            stamped = TranscriptEvent(
                kind=event.kind, text=event.text, state=event.state, level=event.level, seq=seq
            )
            self._queue.put_nowait(stamped)
        except queue.Full:
            self._dropped += 1
        except Exception:
            # Truly last-ditch. Reaching here means something pathological
            # (a bad `kind`), and even that must not propagate into the
            # pipeline - log once at debug and carry on.
            self._dropped += 1
            self._log.debug("failed to enqueue transcript event", exc_info=True)

    # --- sender thread -----------------------------------------------------

    def _run(self) -> None:
        sock: socket.socket | None = None
        backoff = _RECONNECT_MIN_SECONDS
        next_attempt = 0.0

        try:
            while not self._stopping.is_set():
                try:
                    item = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is _SHUTDOWN:
                    return

                if sock is None:
                    if self._clock() < next_attempt:
                        # Still backing off - discard rather than hold the
                        # event, so a long-absent overlay cannot cause the
                        # queue to fill with stale transcript.
                        self._dropped += 1
                        continue
                    sock = self._connect()
                    if sock is None:
                        next_attempt = self._clock() + backoff
                        backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)
                        self._dropped += 1
                        continue
                    backoff = _RECONNECT_MIN_SECONDS

                try:
                    sock.sendall(encode(item))
                except (OSError, socket.timeout):
                    # Overlay went away (or wedged). Close, and let the next
                    # event trigger a reconnect after the backoff.
                    self._close_socket(sock)
                    sock = None
                    self._connected = False
                    next_attempt = self._clock() + backoff
                    self._dropped += 1
        finally:
            if sock is not None:
                self._close_socket(sock)
            self._connected = False

    def _connect(self) -> "socket.socket | None":
        """Returns a connected socket, or `None` if the overlay is not
        listening. Not an error case - not running the overlay is a
        perfectly normal way to run the assistant."""
        if not self._socket_path.exists():
            return None
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(_SEND_TIMEOUT_SECONDS)
            sock.connect(str(self._socket_path))
        except OSError:
            self._close_socket(sock)
            return None
        self._connected = True
        self._log.info("transcript overlay connected at %s", self._socket_path)
        return sock

    @staticmethod
    def _close_socket(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass


class NullTranscriptClient:
    """Drop-in `TranscriptSink` that discards everything - what `main.py`
    uses when the overlay is switched off in config, so the orchestrator's
    emit call sites need no `if enabled` guard at all."""

    def emit(self, event: TranscriptEvent) -> None:
        return None

    def start(self) -> None:
        return None

    def close(self, timeout: float = 1.0) -> None:
        return None

    def is_connected(self) -> bool:
        return False

    def is_enabled(self) -> bool:
        return False

    def set_enabled(self, value: bool) -> None:
        return None

    def hide_now(self) -> None:
        return None

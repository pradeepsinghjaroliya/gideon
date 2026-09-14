import socket
import threading
import time

import pytest

from shared.transcript import (
    ASSISTANT_DELTA,
    HIDE,
    STATE,
    USER_FINAL,
    TranscriptEvent,
    TranscriptSink,
)
from transcript_ui.client import NullTranscriptClient, TranscriptClient
from transcript_ui.protocol import iter_events


class Listener:
    """A minimal stand-in for the overlay: accepts one connection and
    collects whatever events arrive."""

    def __init__(self, path):
        self.path = str(path)
        self.events = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.path)
        self._sock.listen(1)
        self._sock.settimeout(5.0)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn, conn.makefile("rb") as stream:
            for event in iter_events(stream):
                self.events.append(event)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_client_satisfies_the_sink_protocol():
    assert isinstance(TranscriptClient(), TranscriptSink)
    assert isinstance(NullTranscriptClient(), TranscriptSink)


def test_events_reach_a_listening_overlay(tmp_path):
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path)
    client.start()
    try:
        client.emit(TranscriptEvent(kind=USER_FINAL, text="hello"))
        assert _wait_for(lambda: len(listener.events) == 1), listener.events
        assert listener.events[0].text == "hello"
    finally:
        client.close()
        listener.close()


def test_emit_is_a_silent_no_op_when_no_overlay_is_running(tmp_path):
    """Not running the overlay is a perfectly normal way to run the
    assistant - it must not be an error, and must not raise."""
    client = TranscriptClient(socket_path=tmp_path / "absent.sock")
    client.start()
    try:
        for _ in range(20):
            client.emit(TranscriptEvent(kind=ASSISTANT_DELTA, text="x"))
        time.sleep(0.1)
        assert client.is_connected() is False
    finally:
        client.close()


def test_emit_before_start_does_not_raise(tmp_path):
    client = TranscriptClient(socket_path=tmp_path / "t.sock")

    client.emit(TranscriptEvent(kind=STATE, state="idle"))


def test_emit_stamps_a_monotonic_sequence_number(tmp_path):
    """So a consumer can cheaply detect that it dropped events rather than
    silently rendering a transcript with a hole in it."""
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path)
    client.start()
    try:
        for index in range(5):
            client.emit(TranscriptEvent(kind=ASSISTANT_DELTA, text=str(index)))
        assert _wait_for(lambda: len(listener.events) == 5), listener.events
        assert [event.seq for event in listener.events] == [1, 2, 3, 4, 5]
    finally:
        client.close()
        listener.close()


def test_emit_never_blocks_even_with_no_consumer(tmp_path):
    """`emit` runs on the pipeline's hot paths - once per 30ms mic frame,
    once per LLM token - so it must be O(1) and must never wait on I/O."""
    client = TranscriptClient(socket_path=tmp_path / "absent.sock")
    client.start()
    try:
        start = time.monotonic()
        for _ in range(5000):
            client.emit(TranscriptEvent(kind=ASSISTANT_DELTA, text="token"))
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"5000 emits took {elapsed:.2f}s"
    finally:
        client.close()


def test_a_full_queue_drops_events_instead_of_blocking(tmp_path):
    """Discarding is the right response to a consumer that has stopped
    reading; buffering an ever-growing backlog of transcript nobody is
    looking at is not."""
    client = TranscriptClient(socket_path=tmp_path / "absent.sock")
    # Deliberately not started: nothing drains the queue.
    for _ in range(5000):
        client.emit(TranscriptEvent(kind=ASSISTANT_DELTA, text="x"))

    assert client._dropped > 0


def test_the_client_reconnects_when_the_overlay_starts_later(tmp_path):
    """The overlay is routinely a second slower to come up than the
    orchestrator, and the user may start it by hand mid-session."""
    path = tmp_path / "late.sock"
    client = TranscriptClient(socket_path=path)
    client.start()
    try:
        client.emit(TranscriptEvent(kind=ASSISTANT_DELTA, text="dropped"))
        time.sleep(0.1)

        listener = Listener(path)
        try:
            assert _wait_for(
                lambda: (
                    client.emit(TranscriptEvent(kind=USER_FINAL, text="landed")),
                    len(listener.events) > 0,
                )[1],
                timeout=10.0,
            ), "client never reconnected"
            assert any(event.text == "landed" for event in listener.events)
        finally:
            listener.close()
    finally:
        client.close()


def test_the_client_survives_the_overlay_dying_mid_conversation(tmp_path):
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path)
    client.start()
    try:
        client.emit(TranscriptEvent(kind=USER_FINAL, text="before"))
        assert _wait_for(lambda: len(listener.events) == 1)

        listener.close()
        path.unlink(missing_ok=True)

        for _ in range(10):
            client.emit(TranscriptEvent(kind=ASSISTANT_DELTA, text="after"))
        time.sleep(0.2)
    finally:
        client.close()


def test_a_disabled_client_emits_nothing_and_starts_no_thread(tmp_path):
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path, enabled=False)
    client.start()
    try:
        client.emit(TranscriptEvent(kind=USER_FINAL, text="ignored"))
        time.sleep(0.15)
        assert listener.events == []
        assert client._thread is None
    finally:
        client.close()
        listener.close()


def test_start_is_idempotent(tmp_path):
    client = TranscriptClient(socket_path=tmp_path / "t.sock")
    client.start()
    first = client._thread
    client.start()
    try:
        assert client._thread is first
    finally:
        client.close()


def test_close_is_safe_without_start(tmp_path):
    TranscriptClient(socket_path=tmp_path / "t.sock").close()


def test_null_client_accepts_the_full_lifecycle():
    """It exists so `main.py` needs no `if enabled` guard at any call
    site."""
    client = NullTranscriptClient()
    client.start()
    client.emit(TranscriptEvent(kind=USER_FINAL, text="x"))
    assert client.is_connected() is False
    client.close()
    assert client.is_enabled() is False
    client.set_enabled(True)
    client.hide_now()


def test_hide_now_sends_a_hide_event(tmp_path):
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path)
    client.start()
    try:
        client.hide_now()
        assert _wait_for(lambda: len(listener.events) == 1), listener.events
        assert listener.events[0].kind == HIDE
    finally:
        client.close()
        listener.close()


def test_set_enabled_false_hides_first_then_stops_forwarding(tmp_path):
    """Toggling off from the tray must still get the "hide now" event out
    before it starts discarding everything - reversing that order would
    leave a stale transcript on screen with no way to clear it."""
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path)
    client.start()
    try:
        client.emit(TranscriptEvent(kind=USER_FINAL, text="hi"))
        assert _wait_for(lambda: len(listener.events) == 1)

        client.set_enabled(False)
        assert _wait_for(lambda: len(listener.events) == 2), listener.events
        assert listener.events[1].kind == HIDE

        client.emit(TranscriptEvent(kind=USER_FINAL, text="should be dropped"))
        time.sleep(0.1)
        assert len(listener.events) == 2
    finally:
        client.close()
        listener.close()


def test_set_enabled_true_resumes_forwarding_and_starts_the_thread_if_needed(tmp_path):
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path, enabled=False)
    client.start()  # no-op: disabled at construction
    try:
        client.set_enabled(True)
        client.emit(TranscriptEvent(kind=USER_FINAL, text="now visible"))
        assert _wait_for(lambda: len(listener.events) == 1), listener.events
        assert listener.events[0].text == "now visible"
    finally:
        client.close()
        listener.close()


def test_set_enabled_to_the_same_value_is_a_no_op(tmp_path):
    path = tmp_path / "t.sock"
    listener = Listener(path)
    client = TranscriptClient(socket_path=path)
    client.start()
    try:
        client.set_enabled(True)  # already enabled - must not emit a HIDE
        time.sleep(0.1)
        assert listener.events == []
        assert client.is_enabled() is True
    finally:
        client.close()
        listener.close()

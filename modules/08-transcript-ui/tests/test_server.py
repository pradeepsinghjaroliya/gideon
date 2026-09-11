import socket
import threading
import time

import pytest

from shared.transcript import USER_FINAL, TranscriptEvent
from transcript_ui.client import TranscriptClient
from transcript_ui.protocol import encode
from transcript_ui.server import AlreadyRunningError, TranscriptServer


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _send_raw(path, payload: bytes) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    sock.connect(str(path))
    with sock:
        sock.sendall(payload)


def test_events_from_a_client_reach_the_handler(tmp_path):
    got = []
    server = TranscriptServer(got.append, socket_path=tmp_path / "t.sock")
    server.start()
    try:
        _send_raw(server.socket_path, encode(TranscriptEvent(kind=USER_FINAL, text="hi")))
        assert _wait_for(lambda: len(got) == 1), got
        assert got[0].text == "hi"
    finally:
        server.stop()


def test_the_socket_file_is_removed_on_stop(tmp_path):
    path = tmp_path / "t.sock"
    server = TranscriptServer(lambda event: None, socket_path=path)
    server.start()
    assert path.exists()

    server.stop()

    assert not path.exists()


def test_a_stale_socket_file_is_reclaimed(tmp_path):
    """A unix socket file outlives the process that made it, so a crashed
    or SIGKILLed overlay would otherwise make `bind()` fail with
    EADDRINUSE forever after."""
    path = tmp_path / "t.sock"
    path.write_text("")  # a leftover file with nothing listening behind it

    server = TranscriptServer(lambda event: None, socket_path=path)
    server.start()
    try:
        assert path.exists()
        _send_raw(path, encode(TranscriptEvent(kind=USER_FINAL, text="works")))
    finally:
        server.stop()


def test_a_live_socket_is_not_stolen_from_a_running_overlay(tmp_path):
    """Distinguishing "stale file" from "another overlay is genuinely
    running" is done the only reliable way: by trying to connect."""
    path = tmp_path / "t.sock"
    first = TranscriptServer(lambda event: None, socket_path=path)
    first.start()
    try:
        second = TranscriptServer(lambda event: None, socket_path=path)
        with pytest.raises(AlreadyRunningError):
            second.bind()
    finally:
        first.stop()


def test_bind_can_be_called_before_start(tmp_path):
    """`app.py` binds first so a duplicate instance exits before building a
    GTK window, instead of flashing a card on screen."""
    path = tmp_path / "t.sock"
    server = TranscriptServer(lambda event: None, socket_path=path)
    server.bind()
    try:
        assert path.exists()
        server.start()  # must not re-bind or raise
    finally:
        server.stop()


def test_bind_is_idempotent(tmp_path):
    server = TranscriptServer(lambda event: None, socket_path=tmp_path / "t.sock")
    server.bind()
    try:
        server.bind()
    finally:
        server.stop()


def test_a_reconnecting_producer_is_served(tmp_path):
    """Connections are handled sequentially, so an orchestrator restart is
    picked up automatically by the next `accept()`."""
    got = []
    server = TranscriptServer(got.append, socket_path=tmp_path / "t.sock")
    server.start()
    try:
        _send_raw(server.socket_path, encode(TranscriptEvent(kind=USER_FINAL, text="first")))
        assert _wait_for(lambda: len(got) == 1)

        _send_raw(server.socket_path, encode(TranscriptEvent(kind=USER_FINAL, text="second")))
        assert _wait_for(lambda: len(got) == 2), got
        assert [event.text for event in got] == ["first", "second"]
    finally:
        server.stop()


def test_a_raising_handler_does_not_drop_the_connection(tmp_path):
    """A rendering bug must not freeze the transcript for the rest of the
    session."""
    seen = []

    def handler(event):
        seen.append(event)
        if len(seen) == 1:
            raise RuntimeError("render failed")

    server = TranscriptServer(handler, socket_path=tmp_path / "t.sock")
    server.start()
    try:
        payload = encode(TranscriptEvent(kind=USER_FINAL, text="a")) + encode(
            TranscriptEvent(kind=USER_FINAL, text="b")
        )
        _send_raw(server.socket_path, payload)
        assert _wait_for(lambda: len(seen) == 2), seen
    finally:
        server.stop()


def test_garbage_on_the_wire_is_skipped(tmp_path):
    got = []
    server = TranscriptServer(got.append, socket_path=tmp_path / "t.sock")
    server.start()
    try:
        payload = b"garbage\n" + encode(TranscriptEvent(kind=USER_FINAL, text="good"))
        _send_raw(server.socket_path, payload)
        assert _wait_for(lambda: len(got) == 1), got
        assert got[0].text == "good"
    finally:
        server.stop()


def test_client_and_server_interoperate_end_to_end(tmp_path):
    """The two halves of the link were written against the same protocol
    module; this is the test that proves they actually agree."""
    got = []
    server = TranscriptServer(got.append, socket_path=tmp_path / "t.sock")
    server.start()
    client = TranscriptClient(socket_path=server.socket_path)
    client.start()
    try:
        client.emit(TranscriptEvent(kind=USER_FINAL, text="round trip"))
        assert _wait_for(lambda: len(got) == 1), got
        assert got[0].text == "round trip"
        assert got[0].seq == 1
    finally:
        client.close()
        server.stop()


def test_stop_is_safe_without_start(tmp_path):
    TranscriptServer(lambda event: None, socket_path=tmp_path / "t.sock").stop()


def test_a_missing_parent_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "dirs" / "t.sock"
    server = TranscriptServer(lambda event: None, socket_path=path)
    server.start()
    try:
        assert path.exists()
    finally:
        server.stop()

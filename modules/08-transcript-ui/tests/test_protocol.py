import io
import os
from pathlib import Path

from shared.transcript import LEVEL, USER_PARTIAL, TranscriptEvent
from transcript_ui.protocol import (
    SOCKET_ENV_VAR,
    default_socket_path,
    encode,
    iter_events,
)


def _stream(*lines: str) -> io.BytesIO:
    return io.BytesIO("".join(lines).encode("utf-8"))


def test_encode_terminates_each_event_with_exactly_one_newline():
    """NDJSON is self-framing only if this holds."""
    data = encode(TranscriptEvent(kind=USER_PARTIAL, text="hi"))

    assert data.endswith(b"\n")
    assert data.count(b"\n") == 1


def test_round_trip_through_a_stream():
    events = [
        TranscriptEvent(kind=USER_PARTIAL, text="hello", seq=1),
        TranscriptEvent(kind=LEVEL, level=0.5, seq=2),
    ]
    stream = io.BytesIO(b"".join(encode(event) for event in events))

    assert list(iter_events(stream)) == events


def test_malformed_lines_cost_one_event_not_the_connection():
    """The overlay showing a transcript with one gap in it is a far better
    outcome than the overlay dying in the middle of a conversation."""
    good = encode(TranscriptEvent(kind=USER_PARTIAL, text="kept")).decode()
    stream = _stream("not json\n", '{"kind":"bogus"}\n', good, "[1,2]\n")

    events = list(iter_events(stream))

    assert [event.text for event in events] == ["kept"]


def test_blank_lines_are_skipped():
    good = encode(TranscriptEvent(kind=USER_PARTIAL, text="hi")).decode()
    stream = _stream("\n", "   \n", good, "\n")

    assert len(list(iter_events(stream))) == 1


def test_a_truncated_final_line_is_dropped_without_raising():
    """The producer was killed mid-write."""
    good = encode(TranscriptEvent(kind=USER_PARTIAL, text="complete")).decode()
    stream = _stream(good, '{"kind":"user_par')

    events = list(iter_events(stream))

    assert [event.text for event in events] == ["complete"]


def test_invalid_utf8_does_not_raise():
    stream = io.BytesIO(b"\xff\xfe bad bytes\n")

    assert list(iter_events(stream)) == []


def test_iteration_ends_when_the_producer_disconnects():
    assert list(iter_events(io.BytesIO(b""))) == []


def test_unicode_survives_the_round_trip():
    event = TranscriptEvent(kind=USER_PARTIAL, text="काठमाडौं में मौसम")

    assert list(iter_events(io.BytesIO(encode(event))))[0].text == event.text


def test_the_env_var_overrides_the_default_path(monkeypatch):
    monkeypatch.setenv(SOCKET_ENV_VAR, "/tmp/custom-gideon.sock")

    assert default_socket_path() == Path("/tmp/custom-gideon.sock")


def test_the_runtime_dir_is_preferred_when_it_exists(monkeypatch, tmp_path):
    monkeypatch.delenv(SOCKET_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert default_socket_path() == tmp_path / "gideon-transcript.sock"


def test_the_fallback_path_is_uid_qualified(monkeypatch):
    """`XDG_RUNTIME_DIR` is not guaranteed (a bare `su`, a minimal
    container, non-systemd init). `/tmp` is shared, so an unqualified name
    there would collide between users on a multi-user box."""
    monkeypatch.delenv(SOCKET_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    path = default_socket_path()

    assert str(os.getuid()) in path.name


def test_a_nonexistent_runtime_dir_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv(SOCKET_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "does-not-exist"))

    assert str(os.getuid()) in default_socket_path().name

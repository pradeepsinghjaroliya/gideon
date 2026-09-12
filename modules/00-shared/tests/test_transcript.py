import json

import pytest

from shared.transcript import (
    ASSISTANT_DELTA,
    KINDS,
    LEVEL,
    STATE,
    USER_PARTIAL,
    TranscriptEvent,
    TranscriptSink,
)


def test_json_round_trip_preserves_every_field():
    event = TranscriptEvent(kind=LEVEL, text="t", state="listening", level=0.25, seq=7)

    assert TranscriptEvent.from_json(event.to_json()) == event


def test_to_json_is_a_single_line_even_with_newlines_in_the_text():
    """The wire format is newline-delimited, so a literal newline in a
    transcript (an LLM emitting one mid-reply) must not split one event
    into two unparseable halves."""
    event = TranscriptEvent(kind=ASSISTANT_DELTA, text="line one\nline two")

    line = event.to_json()

    assert "\n" not in line
    assert TranscriptEvent.from_json(line).text == "line one\nline two"


def test_unknown_kind_is_rejected_at_construction():
    with pytest.raises(ValueError, match="unknown transcript event kind"):
        TranscriptEvent(kind="teleport")


@pytest.mark.parametrize(
    "line",
    [
        "not json at all",
        "[1, 2, 3]",          # valid JSON, wrong shape
        '"a string"',
        "{}",                  # no kind
        '{"kind": "nope"}',   # unknown kind
        '{"kind": "level", "level": "loud"}',  # unparseable field
    ],
)
def test_from_json_raises_value_error_on_anything_unusable(line):
    """One exception type for every kind of bad input, so the overlay's
    reader has exactly one thing to guard against when skipping a line."""
    with pytest.raises(ValueError):
        TranscriptEvent.from_json(line)


def test_unknown_fields_are_dropped_rather_than_rejected():
    """Forward compatibility: a newer orchestrator may add a field this
    consumer has never heard of. Dropping it keeps the event usable, where
    rejecting it would blank the overlay mid-conversation."""
    line = json.dumps({"kind": STATE, "state": "speaking", "emotion": "cheerful"})

    event = TranscriptEvent.from_json(line)

    assert event.kind == STATE
    assert event.state == "speaking"


def test_missing_optional_fields_fall_back_to_defaults():
    event = TranscriptEvent.from_json('{"kind": "user_partial", "text": "hi"}')

    assert (event.text, event.state, event.level, event.seq) == ("hi", "", 0.0, 0)


def test_events_are_frozen():
    """They fan out to more than one consumer, so a shared mutable event
    would let one corrupt another's view."""
    event = TranscriptEvent(kind=USER_PARTIAL, text="hi")

    with pytest.raises(Exception):
        event.text = "changed"


def test_kinds_set_matches_the_declared_constants():
    assert STATE in KINDS and LEVEL in KINDS and USER_PARTIAL in KINDS


def test_sink_protocol_is_satisfied_by_anything_with_emit():
    class Sink:
        def emit(self, event):
            pass

    assert isinstance(Sink(), TranscriptSink)

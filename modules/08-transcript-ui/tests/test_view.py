"""Tests for the GTK layer.

The interesting logic deliberately lives in `model.py` (tested exhaustively
in `test_model.py` with no display at all); what is left here is "state ->
widgets", so these tests build a real window and drive `handle_event` /
`render` directly rather than simulating clicks or spinning `Gtk.main()` -
the same "drive the callback, not the event loop" style `06-text-input`'s
`test_dashboard.py` already uses for its canvas pills.

Skipped wholesale where there is no display or no PyGObject, so the suite
still passes on a headless box.
"""

import os

import pytest

_HAS_DISPLAY = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
pytestmark = pytest.mark.skipif(not _HAS_DISPLAY, reason="no graphical display")

try:
    from transcript_ui.surface import load_gtk

    load_gtk()
except Exception:  # pragma: no cover - exercised only on a box without GTK
    pytestmark = pytest.mark.skip(reason="PyGObject/GTK3 unavailable")

from shared.transcript import (
    ASSISTANT_DELTA,
    ASSISTANT_FINAL,
    LEVEL,
    STATE,
    STATE_IDLE,
    STATE_LISTENING,
    STATE_SPEAKING,
    USER_FINAL,
    USER_PARTIAL,
    TranscriptEvent,
)
from transcript_ui.model import TranscriptModel
from transcript_ui.view import _hex_to_rgb, TranscriptView


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def view():
    clock = FakeClock()
    model = TranscriptModel(clock=clock)
    instance = TranscriptView(model, width=720, bottom_margin=48)
    instance.model = model
    instance.clock = clock
    yield instance
    instance.stop()
    instance._window.destroy()


def _send(view, **kwargs):
    view.handle_event(TranscriptEvent(**kwargs))


def _row_texts(view):
    return [label.get_text() for _role, label, _box in view._turn_rows]


def _role_texts(view):
    return [label.get_text() for label, _text, _box in view._turn_rows]


def test_a_row_is_created_for_each_visible_turn(view):
    _send(view, kind=USER_PARTIAL, text="what's the weather")

    assert len(view._turn_rows) == 1
    assert "what's the weather" in _row_texts(view)[0]


def test_rows_are_reused_across_repaints_not_rebuilt(view):
    """The typewriter repaints at 60fps; destroying and recreating labels
    that fast would thrash GTK's layout and make the text flicker."""
    _send(view, kind=USER_PARTIAL, text="one")
    first = view._turn_rows[0]

    _send(view, kind=USER_PARTIAL, text="one two")

    assert view._turn_rows[0] is first


def test_role_labels_are_human_readable(view):
    _send(view, kind=USER_FINAL, text="hi")
    _send(view, kind=ASSISTANT_DELTA, text="Hello")
    view.model.tick()
    view.clock.advance(1.0)
    view.model.tick()
    view.render()

    assert _role_texts(view) == ["YOU", "GIDEON"]


def test_a_streaming_turn_shows_a_caret(view):
    _send(view, kind=USER_FINAL, text="hi")
    _send(view, kind=ASSISTANT_DELTA, text="Hello there")
    view.model.tick()
    view.clock.advance(0.05)
    view.model.tick()
    view.render()

    assert _row_texts(view)[-1].endswith("▌")


def test_a_finished_turn_shows_no_caret(view):
    """Leaving it on a finished line would be a lie - it is the cue that
    more text is coming."""
    _send(view, kind=USER_FINAL, text="hi")
    _send(view, kind=ASSISTANT_DELTA, text="Hello.")
    _send(view, kind=ASSISTANT_FINAL, text="Hello.")
    view.model.tick()
    view.clock.advance(3.0)
    view.model.tick()
    view.render()

    assert not _row_texts(view)[-1].endswith("▌")
    assert _row_texts(view)[-1] == "Hello."


def test_removed_turns_have_their_rows_destroyed(view):
    view.model.max_turns = 2
    for index in range(4):
        _send(view, kind=USER_FINAL, text=f"q{index}")
    view.render()

    assert len(view._turn_rows) == 2


def test_the_state_label_tracks_the_pipeline(view):
    _send(view, kind=STATE, state=STATE_LISTENING)
    assert view._state_label.get_text() == "LISTENING"

    _send(view, kind=STATE, state=STATE_SPEAKING)
    assert view._state_label.get_text() == "SPEAKING"


def test_an_unrecognized_state_falls_back_to_idle_rather_than_raising(view):
    """Matches how `tray.py` handles an unknown state - a stale state string
    must not crash the UI."""
    view.model.state = "teleporting"
    view.render()

    assert view._state_label.get_text() == "IDLE"


def test_the_level_meter_is_only_shown_while_listening(view):
    view.start()
    _send(view, kind=STATE, state=STATE_LISTENING)
    assert view._meter.get_visible() is True

    _send(view, kind=STATE, state=STATE_SPEAKING)
    assert view._meter.get_visible() is False


def test_the_meter_draw_handler_runs_at_every_level(view):
    """It is cairo code on a hot path, so exercise it rather than trusting
    it - including the degenerate level=0 case."""
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 76, 14)
    context = cairo.Context(surface)
    view._meter.set_size_request(76, 14)

    for level in (0.0, 0.01, 0.5, 1.0):
        view.model.level = level
        view._draw_meter(view._meter, context)


def test_the_state_dot_draw_handler_runs_for_every_state(view):
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
    context = cairo.Context(surface)

    for state in (STATE_IDLE, STATE_LISTENING, STATE_SPEAKING, "unknown"):
        view.model.state = state
        view._draw_state_dot(view._state_dot, context)


def test_the_card_is_no_wider_than_configured(view):
    """A GTK size request is a *minimum*, so a long unwrapped line used to
    push the card from 720px to over 1200px. The scroller is what clamps
    it."""
    long_line = "word " * 200
    _send(view, kind=USER_FINAL, text=long_line)
    view.render()
    view._window.show_all()
    while view.Gtk.events_pending():
        view.Gtk.main_iteration_do(False)

    width = view._window.get_size().width
    assert width <= 760, f"card grew to {width}px against a configured 720px"


def test_the_card_grows_taller_as_turns_are_added(view):
    """A resizable window keeps its first allocation, which for a layer
    surface is the height of an *empty* card - so rows got squashed into a
    1x1 allocation and nothing but the status line was visible."""
    view._window.show_all()

    def settle():
        while view.Gtk.events_pending():
            view.Gtk.main_iteration_do(False)

    settle()
    empty = view._card.get_preferred_height().natural_height

    _send(view, kind=USER_FINAL, text="a question")
    _send(view, kind=ASSISTANT_DELTA, text="an answer")
    view.model.tick()
    view.clock.advance(2.0)
    view.model.tick()
    view.render()
    settle()

    assert view._card.get_preferred_height().natural_height > empty
    assert view._window.get_resizable() is False


def test_the_wrap_width_is_measured_from_the_real_font(view):
    """Hardcoding a character count would be a guess about the user's font
    and scaling factor."""
    chars = view._chars_per_line

    assert 40 < chars < 200, chars
    assert view._chars_per_line is chars or view._chars_per_line == chars  # cached


def test_tick_fades_the_card_in_and_out(view):
    view.start()
    _send(view, kind=STATE, state=STATE_LISTENING)
    _send(view, kind=USER_FINAL, text="hi")

    for _ in range(60):
        view.clock.advance(1 / 60)
        view._tick()
    assert view._opacity_of_window() > 0.9

    _send(view, kind=STATE, state=STATE_IDLE)
    view.clock.advance(10.0)
    for _ in range(60):
        view.clock.advance(1 / 60)
        view._tick()

    assert view._opacity_of_window() < 0.1


def test_tick_keeps_the_timer_alive(view):
    assert view._tick() is True


def test_start_and_stop_are_safe_to_repeat(view):
    view.start()
    view.start()
    view.stop()
    view.stop()


def test_hex_to_rgb():
    assert _hex_to_rgb("#ffffff") == (1.0, 1.0, 1.0)
    assert _hex_to_rgb("000000") == (0.0, 0.0, 0.0)
    red, green, blue = _hex_to_rgb("#7c5cff")
    assert round(red, 2) == 0.49 and round(blue, 2) == 1.0

from shared.transcript import (
    ASSISTANT_DELTA,
    ASSISTANT_FINAL,
    LEVEL,
    RESET,
    STATE,
    STATE_IDLE,
    STATE_LISTENING,
    STATE_SPEAKING,
    USER_FINAL,
    USER_PARTIAL,
    TranscriptEvent,
)
from transcript_ui.model import ROLE_ASSISTANT, ROLE_USER, TranscriptModel


class FakeClock:
    """Explicit time, so the typewriter and auto-hide timings are exact
    rather than dependent on how fast the test machine happens to be."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _make(**kwargs):
    clock = FakeClock()
    kwargs.setdefault("clock", clock)
    model = TranscriptModel(**kwargs)
    return model, clock


def _send(model, **kwargs):
    return model.handle(TranscriptEvent(**kwargs))


# --- turn bookkeeping ------------------------------------------------------


def test_a_user_partial_starts_a_user_turn():
    model, _ = _make()

    assert _send(model, kind=USER_PARTIAL, text="what's the") is True
    assert [(t.role, t.text) for t in model.turns] == [(ROLE_USER, "what's the")]


def test_successive_partials_replace_rather_than_append():
    """A live partial is a whole-utterance rewrite, not an increment - the
    transcriber re-transcribes the growing buffer each time."""
    model, _ = _make()

    _send(model, kind=USER_PARTIAL, text="what's the")
    _send(model, kind=USER_PARTIAL, text="what's the weather")

    assert [t.text for t in model.turns] == ["what's the weather"]


def test_a_partial_correcting_an_earlier_guess_replaces_it_cleanly():
    model, _ = _make()

    _send(model, kind=USER_PARTIAL, text="what's the whether")
    _send(model, kind=USER_PARTIAL, text="what's the weather like")

    assert [t.text for t in model.turns] == ["what's the weather like"]


def test_the_final_transcript_supersedes_the_last_partial():
    model, _ = _make()

    _send(model, kind=USER_PARTIAL, text="whats the wether")
    _send(model, kind=USER_FINAL, text="What's the weather like?")

    assert [t.text for t in model.turns] == ["What's the weather like?"]
    assert model.turns[0].final is True


def test_an_empty_final_removes_the_user_row_entirely():
    """STT heard nothing usable - better to drop the row than leave an
    empty "You" bubble stranded on screen."""
    model, _ = _make()

    _send(model, kind=USER_PARTIAL, text="mmh")
    _send(model, kind=USER_FINAL, text="   ")

    assert model.turns == []


def test_an_unchanged_partial_reports_no_repaint_needed():
    model, _ = _make()
    _send(model, kind=USER_PARTIAL, text="same")

    assert _send(model, kind=USER_PARTIAL, text="same") is False


def test_an_assistant_delta_after_a_final_user_turn_opens_a_new_turn():
    model, _ = _make()
    _send(model, kind=USER_FINAL, text="hi")

    _send(model, kind=ASSISTANT_DELTA, text="Hello")

    assert [t.role for t in model.turns] == [ROLE_USER, ROLE_ASSISTANT]


def test_max_turns_drops_the_oldest_rows():
    model, _ = _make(max_turns=2)

    for index in range(4):
        _send(model, kind=USER_FINAL, text=f"q{index}")

    assert [t.text for t in model.turns] == ["q2", "q3"]


def test_reset_clears_the_transcript():
    model, _ = _make()
    _send(model, kind=USER_FINAL, text="hi")

    assert _send(model, kind=RESET) is True
    assert model.turns == []


# --- typewriter ------------------------------------------------------------


def test_a_delta_is_buffered_rather_than_shown_immediately():
    """Painting each delta the instant it lands reproduces the LLM's token
    jitter on screen; `tick()` owns when characters become visible."""
    model, _ = _make()

    assert _send(model, kind=ASSISTANT_DELTA, text="Hello there") is False
    assert model.turns[0].text == ""
    assert model.turns[0].pending == "Hello there"


def test_tick_reveals_buffered_text_at_the_configured_rate():
    model, clock = _make(typewriter_cps=10.0)
    _send(model, kind=ASSISTANT_DELTA, text="abcdefghijklmnop")

    model.tick()          # first tick establishes the time base
    clock.advance(0.5)    # 0.5s at 10 cps == 5 characters
    assert model.tick() is True

    assert model.turns[0].text == "abcde"
    assert model.turns[0].pending == "fghijklmnop"


def test_fractional_characters_accumulate_across_frames():
    """At 60fps and 55cps a frame is worth ~0.9 of a character, so rounding
    per frame would either stall completely or run at 60cps."""
    model, clock = _make(typewriter_cps=55.0)
    _send(model, kind=ASSISTANT_DELTA, text="x" * 60)

    model.tick()
    for _ in range(60):          # 60 frames of 1/60s == 1 second
        clock.advance(1 / 60)
        model.tick()

    revealed = len(model.turns[0].text)
    assert 50 <= revealed <= 60, revealed


def test_a_completed_reply_flushes_its_backlog_promptly():
    """Once the reply is done there is no jitter left to smooth, so a fast
    model on a long answer must not leave text crawling for many seconds."""
    model, clock = _make(typewriter_cps=10.0)
    _send(model, kind=ASSISTANT_DELTA, text="x" * 500)
    _send(model, kind=ASSISTANT_FINAL, text="x" * 500)

    model.tick()
    clock.advance(1.3)
    model.tick()

    assert model.turns[0].pending == "", "backlog should be flushed, not trickled at 10cps"


def test_the_flush_never_slows_the_typewriter_below_the_normal_rate():
    model, clock = _make(typewriter_cps=100.0)
    _send(model, kind=ASSISTANT_DELTA, text="x" * 20)
    _send(model, kind=ASSISTANT_FINAL, text="x" * 20)

    model.tick()
    clock.advance(0.2)   # 0.2s at 100cps == 20 characters
    model.tick()

    assert model.turns[0].pending == ""


def test_assistant_final_without_any_deltas_falls_back_to_its_own_text():
    """Covers the overlay connecting mid-reply, or dropped events - the
    final event carries the whole text, so the row must not stay empty."""
    model, clock = _make()

    _send(model, kind=ASSISTANT_FINAL, text="A complete answer.")
    model.tick()
    clock.advance(2.0)
    model.tick()

    assert model.turns[0].text == "A complete answer."


def test_tick_with_nothing_pending_reports_no_change():
    model, clock = _make()
    model.tick()
    clock.advance(1.0)

    assert model.tick() is False


# --- visibility / auto-hide ------------------------------------------------


def test_a_non_idle_state_makes_the_overlay_visible():
    model, _ = _make()

    _send(model, kind=STATE, state=STATE_LISTENING)

    assert model.visible is True


def test_the_overlay_hides_after_the_idle_grace_period():
    model, clock = _make(hide_after_seconds=4.0)
    _send(model, kind=STATE, state=STATE_LISTENING)
    _send(model, kind=USER_FINAL, text="hi")
    _send(model, kind=STATE, state=STATE_IDLE)

    model.tick()
    clock.advance(3.9)
    model.tick()
    assert model.visible is True, "should still be up inside the grace period"

    clock.advance(0.2)
    model.tick()
    assert model.visible is False


def test_repeated_idle_events_do_not_restart_the_hide_timer():
    """The orchestrator reports IDLE both entering and leaving its
    follow-up window; restarting the timer each time would mean the overlay
    never actually hides."""
    model, clock = _make(hide_after_seconds=4.0)
    _send(model, kind=STATE, state=STATE_LISTENING)
    _send(model, kind=USER_FINAL, text="hi")
    _send(model, kind=STATE, state=STATE_IDLE)
    model.tick()

    for _ in range(5):
        clock.advance(1.0)
        _send(model, kind=STATE, state=STATE_IDLE)
        model.tick()

    assert model.visible is False


def test_the_overlay_does_not_hide_while_text_is_still_being_revealed():
    """Hiding mid-sentence would lose the tail of the reply."""
    model, clock = _make(hide_after_seconds=1.0, typewriter_cps=5.0)
    _send(model, kind=STATE, state=STATE_SPEAKING)
    _send(model, kind=USER_FINAL, text="hi")
    _send(model, kind=ASSISTANT_DELTA, text="a very long answer indeed")
    _send(model, kind=STATE, state=STATE_IDLE)

    model.tick()
    clock.advance(1.5)
    model.tick()

    assert model.turns[-1].pending != ""
    assert model.visible is True


def test_a_new_conversation_clears_a_transcript_that_was_already_hidden():
    """Once the card has faded out, its contents are stale context the user
    has stopped looking at."""
    model, clock = _make(hide_after_seconds=1.0)
    _send(model, kind=STATE, state=STATE_LISTENING)
    _send(model, kind=USER_FINAL, text="old question")
    _send(model, kind=STATE, state=STATE_IDLE)
    model.tick()
    clock.advance(2.0)
    model.tick()
    assert model.visible is False

    _send(model, kind=STATE, state=STATE_LISTENING)

    assert model.turns == []
    assert model.visible is True


def test_a_visible_transcript_survives_a_follow_up_in_the_same_conversation():
    model, _ = _make()
    _send(model, kind=STATE, state=STATE_LISTENING)
    _send(model, kind=USER_FINAL, text="first question")
    _send(model, kind=STATE, state=STATE_IDLE)
    _send(model, kind=STATE, state=STATE_LISTENING)

    assert [t.text for t in model.turns] == ["first question"]


# --- level meter -----------------------------------------------------------


def test_the_level_is_smoothed_rather_than_tracked_raw():
    """Raw per-frame RMS is spiky enough to make the meter look like a
    strobe."""
    model, _ = _make()

    _send(model, kind=LEVEL, level=1.0)

    assert 0.0 < model.level < 1.0


def test_an_imperceptible_level_change_reports_no_repaint_needed():
    model, _ = _make()
    model.level = 0.5

    assert _send(model, kind=LEVEL, level=0.5) is False


def test_the_level_is_clamped_to_the_unit_range():
    model, _ = _make()

    _send(model, kind=LEVEL, level=50.0)
    for _ in range(50):
        _send(model, kind=LEVEL, level=50.0)

    assert model.level <= 1.0


# --- view helpers ----------------------------------------------------------


def test_visible_turns_excludes_a_turn_with_nothing_revealed_yet():
    """Otherwise an assistant row flashes into existence as an empty bubble
    the instant the first token arrives."""
    model, _ = _make()
    _send(model, kind=USER_FINAL, text="hi")
    _send(model, kind=ASSISTANT_DELTA, text="Hello")

    assert len(model.turns) == 2
    assert [t.text for t in model.visible_turns()] == ["hi"]


def test_is_empty_tracks_visible_turns():
    model, _ = _make()
    assert model.is_empty() is True

    _send(model, kind=USER_PARTIAL, text="hi")

    assert model.is_empty() is False


def test_an_empty_state_string_is_ignored():
    model, _ = _make()

    assert _send(model, kind=STATE, state="") is False
    assert model.state == STATE_IDLE


# --- a finalised user turn is closed ---------------------------------------


def test_a_duplicate_final_does_not_create_a_second_row():
    """The overlay-side backstop for the duplicate `user_final` a bug in
    `07-orchestrator`'s follow-up branch used to emit, which drew the
    question as two identical "You" rows (reported by the user from a
    screenshot)."""
    model, _ = _make()

    _send(model, kind=USER_FINAL, text="What's the weather like?")
    assert _send(model, kind=USER_FINAL, text="What's the weather like?") is False

    assert [t.text for t in model.turns] == ["What's the weather like?"]


def test_a_partial_arriving_after_the_final_is_ignored():
    """A partial can only ever precede its own final. One that arrives
    afterwards was still being computed when the utterance ended, and must
    not open a second row."""
    model, _ = _make()
    _send(model, kind=USER_PARTIAL, text="what's the weather")
    _send(model, kind=USER_FINAL, text="What's the weather like?")

    assert _send(model, kind=USER_PARTIAL, text="what's the weather like") is False

    assert [t.text for t in model.turns] == ["What's the weather like?"]


def test_a_genuinely_new_question_still_opens_a_new_row():
    """The guard above must not swallow a real second turn."""
    model, _ = _make()
    _send(model, kind=USER_FINAL, text="first question")

    _send(model, kind=USER_FINAL, text="second question")

    assert [t.text for t in model.turns] == ["first question", "second question"]


def test_a_full_two_turn_voice_conversation_has_one_row_per_utterance():
    """End-to-end over the exact event sequence `step()` produces for a
    spoken question plus a spoken follow-up."""
    model, clock = _make()

    for turn_text, reply in (
        ("What's the weather like?", "It is sunny."),
        ("And tomorrow?", "Cooler."),
    ):
        _send(model, kind=STATE, state=STATE_LISTENING)
        _send(model, kind=LEVEL, level=0.5)
        _send(model, kind=USER_PARTIAL, text=turn_text[:10].lower())
        _send(model, kind=USER_FINAL, text=turn_text)
        _send(model, kind=STATE, state=STATE_SPEAKING)
        _send(model, kind=ASSISTANT_DELTA, text=reply)
        _send(model, kind=ASSISTANT_FINAL, text=reply)
        model.tick()
        clock.advance(2.0)
        model.tick()

    assert [(t.role, t.text) for t in model.turns] == [
        (ROLE_USER, "What's the weather like?"),
        (ROLE_ASSISTANT, "It is sunny."),
        (ROLE_USER, "And tomorrow?"),
        (ROLE_ASSISTANT, "Cooler."),
    ]

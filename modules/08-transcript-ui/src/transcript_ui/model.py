"""What the overlay should currently be showing - as plain Python, with no
GTK anywhere in it.

Splitting this out from `view.py` is what makes the interesting behaviour
testable: the turn bookkeeping, the partial-to-final swap, the typewriter
pacing and the auto-hide timer are all pure functions of events plus a
clock, so they can be driven directly in a unit test with a fake clock and
no display, no compositor and no event loop. `view.py` is then only
"translate this state into widgets", which is the part that genuinely needs
a running GTK.

The typewriter deserves a word, since it is the main reason streamed text
*reads* smoothly. LLM tokens do not arrive at a steady rate - Ollama
delivers them in bursts, several characters at a time with irregular gaps.
Painting each delta the instant it lands reproduces that jitter on screen
and looks broken. So deltas accumulate in `Turn.pending` and are revealed
at a constant characters-per-second in `tick()`. The text then flows at a
readable, even pace regardless of how lumpy the underlying stream was.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from shared.transcript import (
    ASSISTANT_DELTA,
    ASSISTANT_FINAL,
    HIDE,
    LEVEL,
    RESET,
    STATE,
    STATE_IDLE,
    USER_FINAL,
    USER_PARTIAL,
    TranscriptEvent,
)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# Reveal rate for streamed assistant text. Comfortably faster than reading
# speed (~25 cps for most people) so it never feels like it is holding the
# reply back, but slow enough to smooth out token bursts.
_DEFAULT_TYPEWRITER_CPS = 55.0

# Once a reply is complete there is no more jitter left to smooth, so any
# remaining backlog is flushed within this long rather than trickling out
# at the normal rate - otherwise a fast model on a long answer would leave
# text still crawling across the screen well after Gideon stopped speaking.
_FINAL_FLUSH_SECONDS = 1.2

# Exponential smoothing for the mic level meter. Raw per-frame RMS is very
# spiky; this is enough to look like a level meter rather than a strobe.
_LEVEL_SMOOTHING = 0.35


@dataclass
class Turn:
    """One line in the transcript.

    `text` is what is on screen now; `pending` is text that has arrived but
    not yet been revealed by the typewriter. For user turns `pending` is
    always empty - a live partial is a *replacement* for the previous
    partial and is shown immediately, because typewriting the user's own
    speech would put the preview permanently behind what they are actually
    saying, defeating the entire point of having it.
    """

    role: str
    text: str = ""
    pending: str = ""
    final: bool = False

    @property
    def full_text(self) -> str:
        return self.text + self.pending

    @property
    def is_settled(self) -> bool:
        """Finished arriving *and* finished being revealed."""
        return self.final and not self.pending


@dataclass
class TranscriptModel:
    max_turns: int = 4
    typewriter_cps: float = _DEFAULT_TYPEWRITER_CPS
    hide_after_seconds: float = 4.0
    clock: Callable[[], float] = time.monotonic

    state: str = STATE_IDLE
    level: float = 0.0
    visible: bool = False
    turns: list[Turn] = field(default_factory=list)

    _idle_since: float | None = field(default=None, repr=False)
    _last_tick: float | None = field(default=None, repr=False)
    _char_credit: float = field(default=0.0, repr=False)

    # --- event intake ------------------------------------------------------

    def handle(self, event: TranscriptEvent) -> bool:
        """Fold one event into the state. Returns whether the view needs to
        repaint, so the caller can skip work for events that changed
        nothing visible."""
        if event.kind == STATE:
            return self._handle_state(event.state)
        if event.kind == LEVEL:
            return self._handle_level(event.level)
        if event.kind in (USER_PARTIAL, USER_FINAL):
            return self._handle_user(event.text, final=event.kind == USER_FINAL)
        if event.kind == ASSISTANT_DELTA:
            return self._handle_assistant_delta(event.text)
        if event.kind == ASSISTANT_FINAL:
            return self._handle_assistant_final(event.text)
        if event.kind == RESET:
            self.turns.clear()
            self.level = 0.0
            return True
        if event.kind == HIDE:
            return self._handle_hide()
        return False

    def _handle_hide(self) -> bool:
        """Dismiss immediately - the tray's "Hide transcript now", rather
        than waiting out the normal idle + `hide_after_seconds` countdown.
        Clears the transcript too, same as a genuinely new conversation, so
        a stale reply is not still sitting there next time it reappears."""
        changed = self.visible or bool(self.turns)
        self.visible = False
        self.level = 0.0
        self.turns.clear()
        self._idle_since = None
        return changed

    def _handle_state(self, state: str) -> bool:
        if not state:
            return False
        changed = state != self.state
        self.state = state
        if state == STATE_IDLE:
            # Start the auto-hide countdown, but only once - re-reporting
            # IDLE (which the orchestrator does on its way in and out of the
            # follow-up window) must not keep restarting the timer, or the
            # overlay would never actually hide.
            if self._idle_since is None:
                self._idle_since = self.clock()
        else:
            self._idle_since = None
            # Read *before* flipping `visible` below: staleness is defined
            # by the card having been hidden when this event arrived, so
            # setting visible first would make the check permanently false.
            was_hidden = not self.visible
            if was_hidden:
                self.visible = True
                changed = True
            # Anything other than idle means a turn is under way. If the
            # previous conversation had already been hidden, its transcript
            # is stale context the user has stopped looking at - clear it so
            # a new conversation starts from a clean card. (The orchestrator
            # also sends an explicit RESET on a new conversation; this is the
            # backstop for an overlay started or reconnected mid-session.)
            if was_hidden and self.turns:
                self.turns.clear()
                changed = True
        return changed

    def _handle_level(self, level: float) -> bool:
        target = max(0.0, min(1.0, level))
        smoothed = self.level + (target - self.level) * _LEVEL_SMOOTHING
        # Only repaint when the change is actually perceptible - the meter
        # is a few pixels tall, so sub-1% moves are invisible and would just
        # burn a redraw per mic frame.
        changed = abs(smoothed - self.level) > 0.01
        self.level = smoothed
        return changed

    def _handle_user(self, text: str, final: bool) -> bool:
        text = text.strip()

        # A finalised user turn is closed, so nothing more can belong to
        # that utterance. Guard before `_open_turn`, which appends a row as
        # a side effect - without this, anything arriving late for a
        # finished utterance silently became a *second* "You" row showing
        # near-identical text. Two ways that happens:
        #
        # - a partial that was still being computed when the final landed
        #   (`StreamingTranscriber` invalidates these by generation, but the
        #   callback fires outside that check, so the ordering is not
        #   guaranteed end to end);
        # - a duplicate `user_final`, which is exactly what a bug in
        #   `07-orchestrator`'s follow-up branch used to produce.
        #
        # A genuinely new question still opens a new row, since its text
        # differs from the finished one.
        last = self.turns[-1] if self.turns else None
        if last is not None and last.role == ROLE_USER and last.final:
            if not final:
                return False  # a partial can only precede its own final
            if last.text == text:
                return False  # the same final twice

        turn = self._open_turn(ROLE_USER)
        if final and not text:
            # STT heard nothing usable. Drop the whole row rather than
            # leaving an empty "You" bubble stranded on screen.
            if turn in self.turns:
                self.turns.remove(turn)
            return True
        if turn.text == text and turn.final == final:
            return False
        turn.text = text
        turn.pending = ""
        turn.final = final
        return True

    def _handle_assistant_delta(self, text: str) -> bool:
        if not text:
            return False
        turn = self._open_turn(ROLE_ASSISTANT)
        turn.pending += text
        # No repaint yet: `tick()` owns when these characters become
        # visible. Returning True here would paint the delta immediately and
        # reintroduce exactly the jitter the typewriter exists to remove.
        return False

    def _handle_assistant_final(self, text: str) -> bool:
        turn = self._open_turn(ROLE_ASSISTANT)
        turn.final = True
        if not turn.full_text.strip() and text.strip():
            # Fallback for the case where the deltas never arrived (the
            # overlay connected mid-reply, or events were dropped) - the
            # final event carries the whole text, so use it rather than
            # showing an empty assistant row.
            turn.pending = text.strip()
        return True

    def _open_turn(self, role: str) -> Turn:
        """The turn new text for `role` belongs to: the last one if it is
        still open, otherwise a fresh one."""
        if self.turns:
            last = self.turns[-1]
            if last.role == role and not last.final:
                return last
        turn = Turn(role=role)
        self.turns.append(turn)
        del self.turns[: max(0, len(self.turns) - self.max_turns)]
        return turn

    # --- per-frame advance -------------------------------------------------

    def tick(self) -> bool:
        """Advance the typewriter and the auto-hide timer. Called by the
        view on every animation frame; returns whether anything visible
        changed."""
        now = self.clock()
        elapsed = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now

        changed = self._advance_typewriter(elapsed)

        if self._idle_since is not None and self.visible:
            # Do not hide while text is still being revealed, even if the
            # pipeline has already gone idle - the user would lose the tail
            # of the reply mid-sentence.
            if self._all_settled() and now - self._idle_since >= self.hide_after_seconds:
                self.visible = False
                self.level = 0.0
                changed = True
        return changed

    def _all_settled(self) -> bool:
        return all(turn.is_settled for turn in self.turns)

    def _advance_typewriter(self, elapsed: float) -> bool:
        pending_turns = [turn for turn in self.turns if turn.pending]
        if not pending_turns:
            self._char_credit = 0.0
            return False

        backlog = sum(len(turn.pending) for turn in pending_turns)
        rate = self.typewriter_cps
        if all(turn.final for turn in pending_turns):
            # Reply is complete - clear the backlog promptly (see
            # `_FINAL_FLUSH_SECONDS`). `max` so this only ever speeds the
            # typewriter up, never slows it below the normal rate.
            rate = max(rate, backlog / _FINAL_FLUSH_SECONDS)

        # Accumulate fractional characters across frames rather than
        # rounding each frame: at 60fps and 55cps a frame is worth ~0.9 of
        # a character, so rounding per frame would either stall completely
        # (floor) or run at 60cps (ceil).
        self._char_credit += rate * elapsed
        reveal = int(self._char_credit)
        if reveal <= 0:
            return False
        self._char_credit -= reveal

        for turn in pending_turns:
            if reveal <= 0:
                break
            take = min(reveal, len(turn.pending))
            turn.text += turn.pending[:take]
            turn.pending = turn.pending[take:]
            reveal -= take
        return True

    # --- view helpers ------------------------------------------------------

    def visible_turns(self) -> list[Turn]:
        """Turns worth drawing - excludes a turn that exists but has no text
        revealed yet, so an assistant row does not flash into existence as
        an empty bubble the instant the first token arrives."""
        return [turn for turn in self.turns if turn.text]

    def is_empty(self) -> bool:
        return not self.visible_turns()

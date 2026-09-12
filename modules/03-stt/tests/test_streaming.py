import threading
import time

import numpy as np

from stt.streaming import StreamingTranscriber


class FakeEngine:
    """Records every buffer it was asked to transcribe, and can be made to
    block so a test can control exactly when one inference finishes."""

    def __init__(self, texts=None, gate: threading.Event | None = None):
        self.seen = []
        self._texts = list(texts or [])
        self._gate = gate
        self.calls = 0

    def transcribe(self, audio, sample_rate):
        if self._gate is not None:
            self._gate.wait(timeout=5.0)
        self.seen.append(np.array(audio, copy=True))
        self.calls += 1
        if self._texts:
            return self._texts.pop(0)
        return f"text-{self.calls}"


def _audio(seconds: float, sample_rate: int = 16000, value: int = 1000) -> np.ndarray:
    return np.full(int(seconds * sample_rate), value, dtype=np.int16)


def _make(engine, on_partial=None, **kwargs):
    kwargs.setdefault("min_interval_seconds", 0.0)
    return StreamingTranscriber(engine=engine, on_partial=on_partial, **kwargs)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_submit_produces_a_partial():
    got = []
    transcriber = _make(FakeEngine(["hello there"]), on_partial=got.append)
    transcriber.start()
    try:
        transcriber.submit(_audio(1.0))
        assert _wait_for(lambda: got == ["hello there"]), got
    finally:
        transcriber.stop()


def test_submit_below_the_minimum_audio_length_is_ignored():
    """Whisper pads short input and reliably hallucinates filler ("Thank
    you.") below roughly half a second, which looks worse on screen than
    showing nothing."""
    engine = FakeEngine()
    transcriber = _make(engine, min_audio_seconds=0.6)
    transcriber.start()
    try:
        transcriber.submit(_audio(0.2))
        time.sleep(0.1)
        assert engine.calls == 0
    finally:
        transcriber.stop()


def test_stale_submissions_are_dropped_rather_than_queued():
    """The core guarantee: the mailbox holds one snapshot, not a queue. A
    queue would build a backlog the worker could never catch up on and
    every partial drawn would fall further behind what the user is actually
    saying."""
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    transcriber = _make(engine)
    transcriber.start()
    try:
        # First submission is picked up and blocks inside the fake engine.
        transcriber.submit(_audio(1.0, value=1))
        assert _wait_for(lambda: transcriber._pending is None)

        # Three more arrive while the worker is busy; only the newest may
        # survive.
        for value in (2, 3, 4):
            transcriber.submit(_audio(1.0, value=value))

        gate.set()
        assert _wait_for(lambda: engine.calls == 2), engine.calls
        time.sleep(0.1)

        assert engine.calls == 2, "intermediate snapshots should have been discarded"
        assert engine.seen[0][0] == 1
        assert engine.seen[1][0] == 4, "the newest snapshot should win"
    finally:
        gate.set()
        transcriber.stop()


def test_submit_does_not_block_on_a_busy_worker():
    """`submit()` is called from the orchestrator's per-mic-frame loop, so
    blocking would stall audio capture."""
    gate = threading.Event()
    transcriber = _make(FakeEngine(gate=gate))
    transcriber.start()
    try:
        transcriber.submit(_audio(1.0))
        assert _wait_for(lambda: transcriber._pending is None)

        start = time.monotonic()
        for _ in range(50):
            transcriber.submit(_audio(1.0))
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, f"50 submits took {elapsed:.2f}s while the worker was busy"
    finally:
        gate.set()
        transcriber.stop()


def test_submitted_buffer_is_copied():
    """The orchestrator keeps appending to its own frame list, so holding a
    reference would mutate the array out from under the worker."""
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    transcriber = _make(engine)
    transcriber.start()
    try:
        buffer = _audio(1.0, value=7)
        transcriber.submit(buffer)
        buffer[:] = 999
        gate.set()
        assert _wait_for(lambda: engine.calls == 1)
        assert engine.seen[0][0] == 7
    finally:
        gate.set()
        transcriber.stop()


def test_identical_text_is_not_republished():
    """Repainting identical text would restart the overlay's typewriter
    animation for no reason."""
    got = []
    transcriber = _make(FakeEngine(["same", "same", "different"]), on_partial=got.append)
    transcriber.start()
    try:
        for _ in range(3):
            transcriber.submit(_audio(1.0))
            time.sleep(0.05)
        assert _wait_for(lambda: got == ["same", "different"]), got
    finally:
        transcriber.stop()


def test_reset_discards_a_partial_computed_for_the_previous_utterance():
    """A partial still mid-inference when the turn ends must not overwrite
    the final transcript, nor leak into the next turn."""
    gate = threading.Event()
    got = []
    transcriber = _make(FakeEngine(["late partial"], gate=gate), on_partial=got.append)
    transcriber.start()
    try:
        transcriber.submit(_audio(1.0))
        assert _wait_for(lambda: transcriber._pending is None)

        transcriber.reset()  # the utterance ended while inference was running
        gate.set()
        time.sleep(0.2)

        assert got == []
    finally:
        gate.set()
        transcriber.stop()


def test_reset_clears_a_queued_submission():
    engine = FakeEngine()
    transcriber = _make(engine)
    transcriber.start()
    try:
        transcriber.reset()
        assert transcriber._pending is None
    finally:
        transcriber.stop()


def test_empty_text_is_not_published():
    got = []
    transcriber = _make(FakeEngine(["   ", "words"]), on_partial=got.append)
    transcriber.start()
    try:
        for _ in range(2):
            transcriber.submit(_audio(1.0))
            time.sleep(0.05)
        assert _wait_for(lambda: got == ["words"]), got
    finally:
        transcriber.stop()


class ExplodingEngine:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, sample_rate):
        self.calls += 1
        raise RuntimeError("model weights missing")


def test_an_engine_failure_disables_partials_permanently_and_silently():
    """The realistic causes (missing weights, no disk space, unsupported
    compute type) are all persistent, so retrying would mean a stack trace
    per mic frame for the rest of the session. Live previews are a nicety;
    losing them must be quiet and must not affect the final transcript."""
    got = []
    engine = ExplodingEngine()
    transcriber = _make(engine, on_partial=got.append)
    transcriber.start()
    try:
        transcriber.submit(_audio(1.0))
        assert _wait_for(lambda: engine.calls == 1)

        for _ in range(5):
            transcriber.submit(_audio(1.0))
        time.sleep(0.1)

        assert engine.calls == 1, "should not keep retrying a permanent failure"
        assert got == []
    finally:
        transcriber.stop()


def test_a_raising_callback_does_not_kill_the_worker():
    calls = []

    def bad_callback(text):
        calls.append(text)
        raise RuntimeError("render failed")

    transcriber = _make(FakeEngine(["one", "two"]), on_partial=bad_callback)
    transcriber.start()
    try:
        transcriber.submit(_audio(1.0))
        assert _wait_for(lambda: calls == ["one"])
        transcriber.submit(_audio(1.5))
        assert _wait_for(lambda: calls == ["one", "two"]), calls
    finally:
        transcriber.stop()


def test_start_is_idempotent():
    transcriber = _make(FakeEngine())
    transcriber.start()
    first = transcriber._thread
    transcriber.start()
    try:
        assert transcriber._thread is first
    finally:
        transcriber.stop()


def test_stop_is_safe_without_start():
    _make(FakeEngine()).stop()


def test_min_interval_throttles_consecutive_inferences():
    """Once the model is faster than a human can read, spending more CPU to
    redraw a partial buys nothing."""
    clock = [0.0]
    engine = FakeEngine()
    transcriber = StreamingTranscriber(
        engine=engine,
        min_interval_seconds=10.0,
        clock=lambda: clock[0],
    )
    transcriber.start()
    try:
        transcriber.submit(_audio(1.0))
        assert _wait_for(lambda: engine.calls == 1)

        transcriber.submit(_audio(1.5))
        time.sleep(0.15)
        assert engine.calls == 1, "second inference should still be inside the throttle window"
    finally:
        transcriber.stop()

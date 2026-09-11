"""Live, partial transcription of speech that is still in progress.

`engine.py`'s `FasterWhisperEngine` is a one-shot: hand it a finished
utterance, get back the text. That is what the pipeline needs for the
*authoritative* transcript, but it means nothing can be shown on screen
until the user has stopped talking and the model has finished running -
a second or two of dead air where the transcript overlay
(`08-transcript-ui`) would otherwise have nothing to draw.

`StreamingTranscriber` fills that gap. It owns a **second, deliberately
small** model (`tiny` by default) and re-transcribes the growing audio
buffer on a background thread, publishing each result as a partial. The
main `small` model still produces the final text, which supersedes the
last partial - so accuracy is unchanged and only the *preview* is cheap.

Two properties matter more than anything else here, because the caller is
the orchestrator's per-mic-frame loop:

1. **`submit()` never blocks.** It drops a snapshot into a single-slot
   mailbox and returns. Whisper inference is orders of magnitude slower
   than the 30ms frame cadence, so anything else would stall audio capture.
2. **Latest wins; stale snapshots are discarded.** The mailbox holds one
   snapshot, not a queue. A queue would build a backlog the worker could
   never catch up on, and every partial drawn would be further and further
   behind what the user is actually saying. Dropping intermediate snapshots
   is exactly right: nobody wants to see a stale partial, they want the
   newest one. It also makes the whole thing self-pacing - partials arrive
   as fast as the model can produce them and no faster, with no need to
   guess a good polling interval.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from stt.engine import FasterWhisperEngine

# Whisper is trained on 30s windows and pads short input; below roughly
# half a second it reliably returns either nothing or a hallucinated
# filler word ("Thank you.", "..."), which looks worse on screen than
# showing nothing at all. So the first partial of an utterance waits until
# there is enough audio to be meaningful.
_MIN_AUDIO_SECONDS = 0.6

# Floor between two inference runs. The latest-wins mailbox already paces
# the worker naturally (it can only go as fast as the model), so this only
# bites when the model is *faster* than we need - at which point spending
# more CPU to redraw a partial 20x/sec buys nothing a human can read.
_MIN_INTERVAL_SECONDS = 0.45


class StreamingTranscriber:
    def __init__(
        self,
        engine: "FasterWhisperEngine | None" = None,
        model_size: str = "tiny",
        device: str = "cpu",
        sample_rate: int = 16000,
        on_partial: Callable[[str], None] | None = None,
        min_audio_seconds: float = _MIN_AUDIO_SECONDS,
        min_interval_seconds: float = _MIN_INTERVAL_SECONDS,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`engine` is the usual dependency-injection seam this project uses
        throughout (`model_fn`/`synth_fn`/`post_fn` elsewhere) - tests pass a
        fake so none of this needs a real model. Left `None` in production,
        in which case a `FasterWhisperEngine(model_size, device)` is built
        **lazily, on the worker thread, on first use**: loading whisper
        weights takes seconds, and doing it eagerly would delay orchestrator
        startup (and make a missing/corrupt model file stop the assistant
        from starting at all, rather than just costing it live previews).
        """
        self._engine = engine
        self._model_size = model_size
        self._device = device
        self._sample_rate = sample_rate
        self._on_partial = on_partial
        self._min_audio_samples = int(min_audio_seconds * sample_rate)
        self._min_interval = min_interval_seconds
        self._log = logger or logging.getLogger("stt.streaming")
        self._clock = clock

        # The single-slot mailbox and everything guarding it.
        self._lock = threading.Lock()
        self._pending: np.ndarray | None = None
        self._work = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

        # Bumped by `reset()`. A partial produced from audio belonging to an
        # already-finished utterance must not be published into the next one
        # (the worker may still have been mid-inference when the turn ended),
        # so every snapshot carries the generation it was captured in and is
        # discarded on the way out if that no longer matches.
        self._generation = 0
        self._last_text = ""
        self._disabled = False

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Idempotent, so the orchestrator can call it without tracking
        whether it already did."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="stt-partials", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signals the worker and waits briefly for it. A `timeout` rather
        than an unbounded join because the worker may be inside a
        `transcribe()` call that cannot be interrupted - the thread is a
        daemon, so a straggler cannot keep the process alive either way."""
        self._stopping.set()
        self._work.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def reset(self) -> None:
        """Marks the end of an utterance: drops any queued snapshot and
        invalidates any partial still being computed, so nothing from the
        turn that just ended can leak into the next one."""
        with self._lock:
            self._generation += 1
            self._pending = None
            self._last_text = ""

    # --- producer side (called from the orchestrator's mic loop) -----------

    def submit(self, audio: np.ndarray) -> None:
        """Offer the utterance-so-far for transcription. Non-blocking;
        replaces any snapshot not yet picked up.

        A no-op below `min_audio_seconds` of audio, and after a permanent
        engine failure (see `_transcribe`) - in both cases the caller does
        not need to know or care, it just keeps submitting.
        """
        if self._disabled or len(audio) < self._min_audio_samples:
            return
        with self._lock:
            # A copy, not a reference: the orchestrator keeps appending to
            # its own frame list and would otherwise mutate the array out
            # from under the worker mid-inference.
            self._pending = np.array(audio, copy=True)
        self._work.set()

    # --- consumer side (the worker thread) --------------------------------

    def _run(self) -> None:
        next_allowed = 0.0
        while not self._stopping.is_set():
            self._work.wait()
            if self._stopping.is_set():
                return
            self._work.clear()

            wait = next_allowed - self._clock()
            if wait > 0:
                # `Event.wait` rather than `sleep` so a `stop()` during the
                # throttle window is noticed immediately.
                if self._stopping.wait(wait):
                    return

            with self._lock:
                audio = self._pending
                generation = self._generation
                self._pending = None
            if audio is None:
                continue

            text = self._transcribe(audio)
            next_allowed = self._clock() + self._min_interval
            if text is None:
                continue
            self._publish(text, generation)

    def _transcribe(self, audio: np.ndarray) -> str | None:
        """Returns the partial text, or `None` if this run produced nothing
        usable. A failure here disables the transcriber permanently rather
        than retrying every snapshot: the realistic causes (missing model
        weights, no disk space for the download, an unsupported compute
        type) are all persistent, and retrying would mean a stack trace per
        mic frame for the rest of the session. Live previews are a nicety -
        losing them must never be louder than it is important."""
        try:
            if self._engine is None:
                self._log.info("loading partial-transcript model %r", self._model_size)
                self._engine = FasterWhisperEngine(model_size=self._model_size, device=self._device)
            return self._engine.transcribe(audio, self._sample_rate)
        except Exception:
            self._disabled = True
            self._log.warning(
                "live partial transcription disabled after an error - the final "
                "transcript is unaffected",
                exc_info=True,
            )
            return None

    def _publish(self, text: str, generation: int) -> None:
        text = text.strip()
        with self._lock:
            if generation != self._generation:
                # `reset()` happened while this was being computed - this
                # partial belongs to an utterance that is already over.
                return
            if not text or text == self._last_text:
                # Nothing new to draw. Skipping the callback avoids making
                # the overlay repaint identical text, which at worst
                # restarts its typewriter animation for no reason.
                return
            self._last_text = text

        if self._on_partial is None:
            return
        try:
            self._on_partial(text)
        except Exception:
            self._log.warning("partial-transcript callback failed", exc_info=True)

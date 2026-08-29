"""The `IDLE -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE`
loop from `../../ARCHITECTURE.md`, wiring every other module's interface
together.

Each state is its own method so tests can drive/inspect them individually
with fake `shared.interfaces` implementations, without real audio/models -
same DI spirit used throughout every other module (`model_fn`/`synth_fn`/
`post_fn`).
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from typing import Callable, ContextManager, Protocol

import numpy as np

from shared.interfaces import (
    AudioSink,
    AudioSource,
    LLMClient,
    STTEngine,
    TTSEngine,
    VoiceActivityDetector,
    WakeWordDetector,
)


class TextQueue(Protocol):
    """Whatever `06-text-input`'s tray icon feeds submitted text into -
    just needs a non-blocking `get_nowait()`, per `queue.Queue`."""

    def get_nowait(self) -> str: ...


class Orchestrator:
    def __init__(
        self,
        audio_source: AudioSource,
        audio_sink: AudioSink,
        vad: VoiceActivityDetector,
        wake_word: WakeWordDetector,
        stt: STTEngine,
        llm: LLMClient,
        tts: TTSEngine,
        text_queue: TextQueue,
        history_turns: int = 6,
        max_listen_seconds: float = 15.0,
        followup_seconds: float = 10.0,
        sample_rate: int = 16000,
        logger: logging.Logger | None = None,
        on_status: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        drain_context: Callable[[], ContextManager] | None = None,
    ) -> None:
        self._audio_source = audio_source
        self._audio_sink = audio_sink
        self._vad = vad
        self._wake_word = wake_word
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._text_queue = text_queue
        self._history_turns = history_turns
        self._max_listen_seconds = max_listen_seconds
        self._followup_seconds = followup_seconds
        self._sample_rate = sample_rate
        self._log = logger or logging.getLogger("orchestrator")
        self._on_status = on_status
        self._clock = clock
        self._drain_context = drain_context or self._default_drain_context

        self.history: list[dict] = []
        self._running = False
        self._user_muted_mic = False
        self._auto_muted_for_speaking = False
        self._speaking = False
        self._stop_requested = False
        self._online_event = threading.Event()
        self._online_event.set()

    @contextlib.contextmanager
    def _default_drain_context(self):
        """Keeps consuming (and discarding) mic frames on a background
        thread for the duration of the `with` block.

        Without this, a CPU/network-bound step (STT, the LLM call, TTS
        synthesis+playback) leaves nothing reading `AudioSource`'s
        internal queue, so real mic frames captured during that time pile
        up un-drained - this was a real, confirmed bug: the next read
        (`_await_followup`, right after SPEAKING) then drained that whole
        backlog almost instantly, which both corrupted its wait timing and
        risked feeding stale/self-echo audio into the VAD as if it were a
        live follow-up. Overridable via the `drain_context` constructor
        param so tests can inject a no-op (e.g. `contextlib.nullcontext`)
        instead of a real thread.
        """
        stop = threading.Event()

        def drain() -> None:
            while not stop.is_set():
                self._audio_source.read_chunk()

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()

    def _set_status(self, message: str) -> None:
        """Logs `message` and, if `on_status` was given (the tray icon's
        `TrayApp.set_status`, in `main.py`), forwards it there too - see
        `06-text-input/src/text_input/tray.py`'s "Status / logs..." menu
        item, added so a non-technical user can see what state the
        assistant is in (idle/listening/thinking/speaking) without reading
        the terminal/journald log."""
        self._log.info(message)
        if self._on_status is not None:
            self._on_status(message)

    def stop(self) -> None:
        """Safe to call from another thread (e.g. a signal handler) - the
        main loop notices within one mic frame, since `read_chunk()` only
        blocks until the next frame, not indefinitely."""
        self._running = False
        self._online_event.set()  # don't let a stop() hang behind an offline wait

    def set_mic_muted(self, value: bool) -> None:
        """Manual mic on/off, from the tray's "Mute mic" toggle - stacks
        with (doesn't replace) the automatic mute during SPEAKING, so
        un-muting right as a reply finishes speaking can't accidentally
        turn the mic back on against the user's own choice."""
        self._user_muted_mic = value
        self._apply_mute()

    def is_mic_muted(self) -> bool:
        return self._user_muted_mic

    def set_online(self, value: bool) -> None:
        """Offline means fully inactive - not watching for the wake word
        or a typed question at all (see `_idle`) - from the tray's
        "Go offline" toggle."""
        if value:
            self._online_event.set()
        else:
            self._online_event.clear()

    def is_online(self) -> bool:
        return self._online_event.is_set()

    def is_speaking(self) -> bool:
        return self._speaking

    def stop_speaking(self) -> None:
        """Cuts a reply short mid-playback, from the tray's "Stop
        speaking" action - e.g. the LLM produced a long-winded answer and
        the user doesn't want to keep listening to it. Safe to call from
        another thread while `_speak()` blocks the main loop on
        `AudioSink.play()`, per `../../ARCHITECTURE.md`'s `AudioSink.stop()`
        contract. `_speak()` treats the resulting interruption (if any -
        depends on the sink's `stop()`/`play()` implementation) as a normal
        end to SPEAKING, not an error, so the conversation still proceeds
        to the follow-up window afterward instead of crashing."""
        if self._speaking:
            self._stop_requested = True
            self._audio_sink.stop()

    def run_forever(self) -> None:
        self._running = True
        while self._running:
            # Blocks here (not consuming mic frames, not busy-looping)
            # for as long as `set_online(False)` has been called - `step()`
            # itself doesn't wait, both so it stays synchronously testable
            # and so `_idle()`'s own per-frame offline check still covers
            # the case where offline is toggled mid-wait, not just at the
            # top of a turn.
            self._online_event.wait()
            if not self._running:
                break
            self.step()

    def step(self) -> None:
        """Runs one full trip through the state machine, starting in IDLE.
        Returns early (without THINKING/SPEAKING) if IDLE was stopped or
        taken offline, or if a turn produced no usable text. After
        SPEAKING, gives the user up to `followup_seconds` to continue the
        conversation without repeating the wake word (see
        `_await_followup`) before finally returning to true IDLE."""
        self._set_status("Idle - waiting for the wake word or a typed question")
        kind, text = self._idle()
        if kind in ("stopped", "offline"):
            return

        if kind == "voice":
            self._set_status("Listening - recording your question")
            audio = self._listen()
            self._set_status("Transcribing your question")
            text = self._transcribe_and_log(audio)
        else:
            self._set_status(f"Got a typed question: {text!r}")

        while True:
            if not text:
                self._set_status("Didn't catch anything - back to idle")
                return

            self._set_status("Thinking - waiting on the local LLM for a reply")
            start = time.monotonic()
            reply = self._think(text)
            self._log.info("got LLM reply in %.2fs: %r", time.monotonic() - start, reply)

            self._set_status(f"Speaking: {reply!r}")
            start = time.monotonic()
            self._speak(reply)
            self._log.info("finished speaking in %.2fs", time.monotonic() - start)

            self._set_status(
                f"Idle - listening for a follow-up ({self._followup_seconds:.0f}s, no wake word needed)..."
            )
            followup_kind, payload = self._await_followup()
            if followup_kind == "timeout":
                self._set_status("Idle - waiting for the wake word or a typed question")
                return

            if followup_kind == "voice":
                self._set_status("Transcribing your question")
                text = self._transcribe_and_log(payload)
            else:
                text = payload
                self._set_status(f"Got a typed question: {text!r}")

    def _transcribe_and_log(self, audio: np.ndarray) -> str:
        start = time.monotonic()
        with self._drain_context():
            text = self._transcribe(audio)
        self._log.info(
            "transcribed %.2fs of audio in %.2fs: %r",
            len(audio) / self._sample_rate, time.monotonic() - start, text,
        )
        return text

    def _idle(self) -> tuple[str, str | None]:
        while self._running:
            if not self._online_event.is_set():
                return "offline", None
            chunk = self._audio_source.read_chunk()
            if self._wake_word.process_chunk(chunk):
                self._wake_word.reset()
                return "voice", None
            try:
                text = self._text_queue.get_nowait()
            except queue.Empty:
                continue
            return "text", text
        return "stopped", None

    def _listen(self) -> np.ndarray:
        frames: list[np.ndarray] = []
        total_samples = 0
        heard_speech = False

        while True:
            chunk = self._audio_source.read_chunk()
            frames.append(chunk)
            total_samples += len(chunk)

            if self._vad.is_speech(chunk):
                heard_speech = True
            elif heard_speech:
                break
            if total_samples / self._sample_rate >= self._max_listen_seconds:
                break

        return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)

    def _await_followup(self) -> tuple[str, object]:
        """After SPEAKING, waits up to `followup_seconds` for either the
        user to start talking (detected the same way `_listen()` detects
        speech, via VAD - no wake word needed) or a tray "Ask..."
        submission. Unlike `_idle()`, a voice follow-up is recorded to
        completion here (silence-to-silence, same as `_listen()`) rather
        than just reporting that speech started, since there's no separate
        wake-word-triggered LISTENING phase to hand off to.

        Returns `("timeout", None)`, `("voice", audio)`, or `("text", text)`.

        The wait itself is timed by wall clock (`clock`, defaulting to
        `time.monotonic`), not by counting samples read - sample-counting
        assumes each `read_chunk()` corresponds to a fresh real-time frame,
        which is only true if the mic queue was kept drained during
        whatever ran just before this (see `_default_drain_context`); wall
        clock is correct either way.
        """
        frames: list[np.ndarray] = []
        total_samples = 0
        heard_speech = False
        deadline = self._clock() + self._followup_seconds

        while True:
            chunk = self._audio_source.read_chunk()
            speaking = self._vad.is_speech(chunk)

            if heard_speech or speaking:
                frames.append(chunk)
                total_samples += len(chunk)
                if speaking:
                    heard_speech = True
                elif heard_speech:
                    return "voice", np.concatenate(frames)
                if total_samples / self._sample_rate >= self._max_listen_seconds:
                    return "voice", np.concatenate(frames)
                continue

            try:
                text = self._text_queue.get_nowait()
            except queue.Empty:
                text = None
            if text is not None:
                return "text", text

            if self._clock() >= deadline:
                return "timeout", None

    def _transcribe(self, audio: np.ndarray) -> str:
        return self._stt.transcribe(audio, self._sample_rate)

    def _think(self, prompt: str) -> str:
        with self._drain_context():
            reply = self._llm.generate(prompt, list(self.history))

        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": reply})
        max_messages = self._history_turns * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

        return reply

    def _speak(self, text: str) -> None:
        with self._drain_context():
            audio, sample_rate = self._tts.synthesize(text)
            self._auto_muted_for_speaking = True
            self._apply_mute()
            # Reset _stop_requested before _speaking goes True, so
            # stop_speaking() (gated on `self._speaking`) can never fire
            # against a stale flag left over from a previous turn.
            self._stop_requested = False
            self._speaking = True
            try:
                self._audio_sink.play(audio, sample_rate)
            except Exception:
                if not self._stop_requested:
                    raise
                self._log.info("playback stopped early by request")
            finally:
                self._speaking = False
                self._auto_muted_for_speaking = False
                self._apply_mute()

    def _apply_mute(self) -> None:
        """The mic is muted while either SPEAKING (so the assistant
        doesn't hear itself) or the user has manually muted it - the two
        reasons stack, so un-muting after SPEAKING can't override a
        manual mute the user set during that time."""
        if hasattr(self._audio_source, "muted"):
            self._audio_source.muted = self._auto_muted_for_speaking or self._user_muted_mic

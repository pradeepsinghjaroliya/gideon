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
import re
import threading
import time
from typing import Callable, ContextManager, Iterable, Iterator, Protocol

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


# Matches sentence-ending punctuation followed by whitespace (not at the
# very end of the buffer, where more text may still be coming). Simple,
# not NLP-grade - doesn't special-case abbreviations ("Mr.") or decimals
# ("3.14") - which is fine here since it only controls where `_speak()`
# chunk boundaries fall for streaming playback, not the reply text itself.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?](?=[ \n\t])")


def _stream_sentences(deltas: Iterable[str]) -> Iterator[str]:
    """Buffers streamed LLM text deltas (`LLMClient.generate_stream()`)
    and yields each complete sentence as soon as it's seen, instead of
    waiting for the whole reply - lets `Orchestrator._think_and_speak()`
    start synthesizing/speaking sentence 1 while the LLM is still
    generating sentence 2. Any leftover text once `deltas` is exhausted
    (a final sentence with no trailing punctuation, or a short reply with
    none at all) is flushed as one last "sentence" so nothing is lost."""
    buffer = ""
    for delta in deltas:
        buffer += delta
        match = _SENTENCE_BOUNDARY_RE.search(buffer)
        while match:
            yield buffer[: match.end()].strip()
            buffer = buffer[match.end() :]
            match = _SENTENCE_BOUNDARY_RE.search(buffer)
    remainder = buffer.strip()
    if remainder:
        yield remainder


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
        on_state: Callable[[str], None] | None = None,
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
        self._on_state = on_state
        self._clock = clock
        self._drain_context = drain_context or self._default_drain_context

        self.history: list[dict] = []
        self._running = False
        self._user_muted_mic = False
        self._auto_muted_for_speaking = False
        self._speaking = False
        self._responding = False
        self._stop_requested = False
        self._volume = 1.0
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

    def _set_status(self, message: str, state: str | None = None) -> None:
        """Logs `message` and, if `on_status` was given (the tray icon's
        `TrayApp.set_status`, in `main.py`), forwards it there too - see
        `06-text-input/src/text_input/tray.py`'s dashboard log section,
        added so a non-technical user can see what state the assistant is
        in (idle/listening/thinking/speaking) without reading the
        terminal/journald log.

        `state` is a separate, symbolic ("idle"/"listening"/"processing"/
        "speaking") counterpart to `message`'s free-text description -
        forwarded to `on_state` (`TrayApp.set_icon_state`) so the tray icon
        can recolor itself without needing to pattern-match `message`,
        which is meant for humans reading the log, not machine parsing."""
        self._log.info(message)
        if self._on_status is not None:
            self._on_status(message)
        if state is not None and self._on_state is not None:
            self._on_state(state)

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

    def is_responding(self) -> bool:
        """True for the whole span of `_think_and_speak()` - both while
        waiting on the next LLM token and while a sentence is actually
        playing - not just the narrower `is_speaking()` window. Drives
        the dashboard's "Stop generating" control, which should stay
        clickable through either sub-phase."""
        return self._responding

    def set_volume(self, value: float) -> None:
        """Assistant voice volume, from the dashboard's slider - a
        multiplier applied to TTS output in `_speak()`, independent of the
        system/output-device volume. Clamped to `[0.0, 1.0]` since values
        outside that range would clip or invert the waveform.

        Logged at info level (temporary diagnostic - see
        `06-text-input/plan.md`'s "not audibly lowering output" note) so
        it's visible in the same terminal/journal as every other status
        line, to isolate whether the slider is even reaching this method
        versus reaching it but not affecting actual playback."""
        self._volume = max(0.0, min(1.0, value))
        self._log.info("assistant voice volume set to %d%%", round(self._volume * 100))

    def get_volume(self) -> float:
        return self._volume

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

    def stop_generating(self) -> None:
        """Interrupts a streaming reply (`_think_and_speak()`) from the
        dashboard's "Stop generating" control - covers both of its
        sub-phases in one call, unlike `stop_speaking()` above (which only
        ever covers the narrower "currently playing a sentence" window):
        an LLM stream still being read is cancelled via `LLMClient.cancel()`
        (the same "close what I still own, from another thread" pattern
        `SpeakerAudioSink.stop()` uses), and a sentence already mid-
        playback is aborted exactly like `stop_speaking()` always has.
        Either way `_think_and_speak()` still records whatever was
        generated so far to history before returning, so the next prompt
        is accepted normally afterward instead of the conversation
        getting stuck. A no-op if nothing is in flight."""
        if not self._responding:
            return
        self._stop_requested = True
        if self._speaking:
            self._audio_sink.stop()
        self._llm.cancel()

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
        self._set_status("Idle - waiting for the wake word or a typed question", state="idle")
        kind, text = self._idle()
        if kind in ("stopped", "offline"):
            return

        if kind == "voice":
            self._set_status("Listening - recording your question", state="listening")
            audio = self._listen()
            self._set_status("Transcribing your question", state="processing")
            text = self._transcribe_and_log(audio)
        else:
            self._set_status(f"Got a typed question: {text!r}", state="processing")

        while True:
            if not text:
                self._set_status("Didn't catch anything - back to idle", state="idle")
                return

            self._set_status("Thinking - waiting on the local LLM for a reply", state="processing")
            start = time.monotonic()
            self._think_and_speak(text)
            self._log.info("finished responding in %.2fs", time.monotonic() - start)

            self._set_status(
                f"Idle - listening for a follow-up ({self._followup_seconds:.0f}s, no wake word needed)...",
                state="listening",
            )
            followup_kind, payload = self._await_followup()
            if followup_kind == "timeout":
                self._set_status("Idle - waiting for the wake word or a typed question", state="idle")
                return

            if followup_kind == "voice":
                self._set_status("Transcribing your question", state="processing")
                text = self._transcribe_and_log(payload)
            else:
                text = payload
                self._set_status(f"Got a typed question: {text!r}", state="processing")

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
        """Non-streaming single-shot LLM call - still available/tested as
        a standalone primitive, but `step()` itself now calls
        `_think_and_speak()` instead (see its docstring)."""
        with self._drain_context():
            reply = self._llm.generate(prompt, list(self.history))
        self._record_reply(prompt, reply)
        return reply

    def _record_reply(self, prompt: str, reply: str) -> None:
        self.history.append({"role": "user", "content": prompt})
        self.history.append({"role": "assistant", "content": reply})
        max_messages = self._history_turns * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    def _think_and_speak(self, prompt: str) -> None:
        """Streams the reply sentence-by-sentence instead of the old
        generate-the-whole-reply-then-synthesize-the-whole-reply sequence
        (`_think()` + `_speak()`, kept above as standalone, still-tested
        primitives) - the user's ask: waiting for the *entire* reply to
        both finish generating and finish synthesizing before hearing
        anything left a dead gap that didn't feel like a natural
        conversation. Each sentence is synthesized and spoken (`_speak()`,
        unchanged) the moment it's complete, so the assistant starts
        talking as soon as the LLM has produced one full sentence rather
        than the whole answer.

        As a side effect, `_apply_volume()` (inside `_speak()`) now reads
        the current volume once per sentence instead of once per whole
        reply - a volume change made mid-reply is heard starting with the
        very next sentence instead of only the next full turn.

        `stop_generating()` can interrupt either sub-phase: the LLM
        stream (checked via `_stop_requested` between sentences, and
        interruptible mid-network-wait via `LLMClient.cancel()`) or an
        in-flight `_speak()` call (the existing "abort mid-play"
        mechanism). Either way, whatever was generated so far is still
        recorded to history in the `finally` below, so the conversation
        can continue normally on the next turn instead of getting stuck.
        """
        reply_parts: list[str] = []
        self._stop_requested = False
        self._responding = True
        try:
            with self._drain_context():
                deltas = self._llm.generate_stream(prompt, list(self.history))
                try:
                    for sentence in _stream_sentences(self._log_deltas(deltas)):
                        if self._stop_requested:
                            break
                        reply_parts.append(sentence)
                        self._set_status(f"Speaking: {sentence!r}", state="speaking")
                        self._speak(sentence)
                        if self._stop_requested:
                            break
                except Exception:
                    if not self._stop_requested:
                        raise
                    self._log.info("generation stopped early by request")
                finally:
                    deltas.close()
        finally:
            self._responding = False
            self._record_reply(prompt, " ".join(reply_parts).strip())

    def _log_deltas(self, deltas: Iterator[str]) -> Iterator[str]:
        """Debug-level visibility into the raw token-level stream from
        `LLMClient.generate_stream()` - Ollama really is streaming
        token-by-token under the hood here (the same NDJSON-per-token
        mechanism OpenAI-style APIs use), it's just not logged/spoken at
        that granularity: `_stream_sentences()` buffers deltas into whole
        sentences before anything is synthesized or shown at a "Speaking:
        ..." line, because Piper (the TTS engine) needs a full sentence
        to produce natural-sounding speech - synthesizing word-by-word
        would sound choppy and robotic. That's a deliberate TTS-quality
        choice in this module, not a model or streaming limitation. Set
        the orchestrator logger to DEBUG to see this."""
        for delta in deltas:
            self._log.debug("LLM token delta: %r", delta)
            yield delta

    def _speak(self, text: str) -> None:
        with self._drain_context():
            # Reset *before* synthesizing, not after: `_think_and_speak()`
            # calls `_speak()` once per streamed sentence, and a stop
            # request can legitimately arrive *during* this call's own
            # `synthesize()` (a real, confirmed bug - resetting after
            # synthesize silently swallowed exactly that request, letting
            # generation carry on through the rest of the reply
            # unimpeded). Resetting here is still safe for a standalone
            # `_speak()` call too: any caller looping over several
            # `_speak()` calls (only `_think_and_speak()` today) already
            # checks `_stop_requested` and stops looping before ever
            # reaching this point, so this can only be clearing a stale
            # `True` left over from an earlier, unrelated, already-
            # finished turn - never one meant for the call in progress.
            self._stop_requested = False
            audio, sample_rate = self._tts.synthesize(text)
            if self._stop_requested:
                self._log.info("skipping playback - stop requested during synthesis")
                return
            audio = self._apply_volume(audio)
            self._auto_muted_for_speaking = True
            self._apply_mute()
            self._speaking = True
            try:
                self._audio_sink.play(audio, sample_rate)
            except Exception:
                if not self._stop_requested:
                    raise
                self._log.info("playback stopped early by request")
            else:
                # `01-audio-io`'s `SpeakerAudioSink` no longer raises on
                # interruption - it just returns early once `stop()`'s
                # flag is noticed between write chunks (see its
                # docstring) - so a stop still needs logging here even
                # without an exception to catch.
                if self._stop_requested:
                    self._log.info("playback stopped early by request")
            finally:
                self._speaking = False
                self._auto_muted_for_speaking = False
                self._apply_mute()

    def _apply_volume(self, audio: np.ndarray) -> np.ndarray:
        """Scales TTS output by the dashboard volume slider before
        playback - done here rather than in `AudioSink` so this module
        stays the single owner of "assistant voice" volume state,
        independent of whichever sink backend is wired up. Multiplies in
        float64 and clips before casting back to the original (int16)
        dtype, since a naive in-place int16 multiply would wrap around
        instead of clipping at high-amplitude samples."""
        if self._volume == 1.0 or len(audio) == 0:
            return audio
        self._log.info(
            "scaling TTS output (%d samples, peak %d) by volume=%.2f",
            len(audio), int(np.abs(audio).max()), self._volume,
        )
        dtype = audio.dtype
        scaled = audio.astype(np.float64) * self._volume
        if np.issubdtype(dtype, np.integer):
            info = np.iinfo(dtype)
            scaled = np.clip(scaled, info.min, info.max)
        return scaled.astype(dtype)

    def _apply_mute(self) -> None:
        """The mic is muted while either SPEAKING (so the assistant
        doesn't hear itself) or the user has manually muted it - the two
        reasons stack, so un-muting after SPEAKING can't override a
        manual mute the user set during that time."""
        if hasattr(self._audio_source, "muted"):
            self._audio_source.muted = self._auto_muted_for_speaking or self._user_muted_mic

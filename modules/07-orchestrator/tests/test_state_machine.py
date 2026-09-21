import contextlib
import queue
import threading
import time

import numpy as np
import pytest

from shared.transcript import (
    ASSISTANT_DELTA,
    ASSISTANT_FINAL,
    LEVEL,
    RESET,
    STATE,
    USER_FINAL,
    TranscriptEvent,
)

from orchestrator.state_machine import Orchestrator


class FakeClock:
    """Deterministic stand-in for `time.monotonic` - each call returns the
    previous value then advances by `tick`, so tests can compute exactly
    how many waiting iterations `_await_followup` needs to time out
    instead of depending on real elapsed wall-clock time (slow and
    non-deterministic with fakes that never actually block)."""

    def __init__(self, tick: float = 1.0) -> None:
        self._value = 0.0
        self._tick = tick

    def __call__(self) -> float:
        value = self._value
        self._value += self._tick
        return value


class FakeAudioSource:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.muted = False
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def read_chunk(self):
        return next(self._chunks)

    def stop(self):
        self.stopped = True


class FakeAudioSink:
    def __init__(self):
        self.played = []

    def play(self, audio, sample_rate):
        self.played.append((audio, sample_rate))

    def stop(self):
        pass


class FakeVAD:
    def __init__(self, results=None):
        self._results = iter(results or [])
        self._default = False

    def is_speech(self, chunk):
        return next(self._results, self._default)


class FakeWakeWord:
    def __init__(self, results):
        self._results = iter(results)
        self.reset_calls = 0

    def process_chunk(self, chunk):
        return next(self._results, False)

    def reset(self):
        self.reset_calls += 1


class FakeSTT:
    def __init__(self, text=""):
        self.text = text
        self.calls = []

    def transcribe(self, audio, sample_rate):
        self.calls.append((audio, sample_rate))
        return self.text


class FakeLLM:
    def __init__(self, reply="a reply"):
        self.reply = reply
        self.calls = []
        self.cancel_calls = 0

    def generate(self, prompt, history):
        self.calls.append((prompt, list(history)))
        return self.reply

    def generate_stream(self, prompt, history):
        """Yields the whole configured reply as a single delta - matches
        every existing status/history test's expectations exactly, since
        `_stream_sentences` then flushes it as one "sentence" once this
        generator is exhausted. `StreamingFakeLLM` below yields multiple
        deltas for tests that specifically exercise multi-sentence
        streaming."""
        self.calls.append((prompt, list(history)))
        yield self.reply

    def cancel(self):
        self.cancel_calls += 1


class StreamingFakeLLM(FakeLLM):
    """Yields `deltas` one at a time instead of the whole reply in one
    shot, for tests that need multiple sentences to actually stream in
    separately (e.g. to prove each one is spoken as soon as it's
    complete, not only once the whole reply is done)."""

    def __init__(self, deltas):
        super().__init__(reply="".join(deltas))
        self.deltas = deltas

    def generate_stream(self, prompt, history):
        self.calls.append((prompt, list(history)))
        for delta in self.deltas:
            yield delta


class FakeTTS:
    def synthesize(self, text):
        return np.array([1, 2, 3], dtype=np.int16), 22050


def _chunk(n=1):
    return np.zeros(n, dtype=np.int16)


def _make_orchestrator(**overrides):
    defaults = dict(
        audio_source=FakeAudioSource([_chunk()] * 10),
        audio_sink=FakeAudioSink(),
        vad=FakeVAD(),
        wake_word=FakeWakeWord([False]),
        stt=FakeSTT(),
        llm=FakeLLM(),
        tts=FakeTTS(),
        text_queue=queue.Queue(),
        sample_rate=16000,
        drain_context=contextlib.nullcontext,
    )
    defaults.update(overrides)
    return Orchestrator(**defaults)


def test_idle_returns_voice_on_wake_word_detection():
    wake_word = FakeWakeWord([False, False, True])
    orch = _make_orchestrator(audio_source=FakeAudioSource([_chunk()] * 5), wake_word=wake_word)
    orch._running = True

    kind, text = orch._idle()

    assert kind == "voice"
    assert text is None
    assert wake_word.reset_calls == 1


def test_idle_returns_text_when_queue_has_item():
    text_queue = queue.Queue()
    text_queue.put("what's the weather")
    orch = _make_orchestrator(audio_source=FakeAudioSource([_chunk()] * 5), text_queue=text_queue)
    orch._running = True

    kind, text = orch._idle()

    assert (kind, text) == ("text", "what's the weather")


def test_idle_returns_stopped_when_not_running():
    orch = _make_orchestrator()
    orch._running = False

    assert orch._idle() == ("stopped", None)


def test_listen_stops_after_speech_then_silence():
    chunks = [_chunk(160) for _ in range(4)]
    orch = _make_orchestrator(
        audio_source=FakeAudioSource(chunks),
        vad=FakeVAD([False, True, True, False]),
        sample_rate=16000,
    )

    audio = orch._listen()

    assert len(audio) == 160 * 4


def test_listen_hits_max_duration_cutoff_even_if_still_speaking():
    chunks = [_chunk(16000) for _ in range(20)]
    orch = _make_orchestrator(
        audio_source=FakeAudioSource(chunks),
        vad=FakeVAD([True] * 20),
        sample_rate=16000,
        max_listen_seconds=3.0,
    )

    audio = orch._listen()

    assert len(audio) == 16000 * 3


def test_transcribe_delegates_to_stt():
    stt = FakeSTT(text="hello there")
    orch = _make_orchestrator(stt=stt, sample_rate=16000)
    audio = _chunk(100)

    text = orch._transcribe(audio)

    assert text == "hello there"
    assert len(stt.calls) == 1
    called_audio, called_rate = stt.calls[0]
    assert np.array_equal(called_audio, audio)
    assert called_rate == 16000


def test_think_appends_and_trims_history():
    llm = FakeLLM(reply="reply")
    orch = _make_orchestrator(llm=llm, history_turns=1)

    orch._think("first question")
    reply = orch._think("second question")

    assert reply == "reply"
    # history_turns=1 -> at most 2 messages (one user + one assistant) kept
    assert len(orch.history) == 2
    assert orch.history[0] == {"role": "user", "content": "second question"}
    # the second call's `generate` should have seen only the first turn's history
    assert llm.calls[1][1] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "reply"},
    ]


def test_speak_mutes_source_during_playback_and_unmutes_after():
    audio_source = FakeAudioSource([])
    sink = FakeAudioSink()
    orch = _make_orchestrator(audio_source=audio_source, audio_sink=sink)

    orch._speak("hello")

    assert len(sink.played) == 1
    played_audio, played_rate = sink.played[0]
    assert np.array_equal(played_audio, np.array([1, 2, 3], dtype=np.int16))
    assert played_rate == 22050
    assert audio_source.muted is False


def test_speak_unmutes_even_if_play_raises():
    class RaisingSink(FakeAudioSink):
        def play(self, audio, sample_rate):
            raise RuntimeError("boom")

    audio_source = FakeAudioSource([])
    orch = _make_orchestrator(audio_source=audio_source, audio_sink=RaisingSink())

    with pytest.raises(RuntimeError):
        orch._speak("hello")

    assert audio_source.muted is False


def test_manual_mic_mute_persists_after_speaking_ends():
    """Regression-shaped test for the tray's "Mute mic" toggle: the
    automatic un-mute at the end of `_speak()` must not override a mute
    the user asked for themselves."""
    audio_source = FakeAudioSource([])
    orch = _make_orchestrator(audio_source=audio_source)

    orch.set_mic_muted(True)
    orch._speak("hello")

    assert orch.is_mic_muted() is True
    assert audio_source.muted is True


def test_mic_auto_mutes_during_speaking_even_without_manual_mute():
    audio_source = FakeAudioSource([])

    class ObservingSink(FakeAudioSink):
        def __init__(self, audio_source):
            super().__init__()
            self.muted_during_play = None
            self._audio_source = audio_source

        def play(self, audio, sample_rate):
            self.muted_during_play = self._audio_source.muted
            super().play(audio, sample_rate)

    sink = ObservingSink(audio_source)
    orch = _make_orchestrator(audio_source=audio_source, audio_sink=sink)

    orch._speak("hello")

    assert sink.muted_during_play is True
    assert audio_source.muted is False


def test_unmuting_after_manual_mute_clears_it():
    audio_source = FakeAudioSource([])
    orch = _make_orchestrator(audio_source=audio_source)

    orch.set_mic_muted(True)
    orch.set_mic_muted(False)

    assert orch.is_mic_muted() is False
    assert audio_source.muted is False


def test_online_by_default_and_offline_toggle():
    orch = _make_orchestrator()

    assert orch.is_online() is True

    orch.set_online(False)
    assert orch.is_online() is False

    orch.set_online(True)
    assert orch.is_online() is True


def test_idle_returns_offline_when_taken_offline():
    orch = _make_orchestrator(audio_source=FakeAudioSource([_chunk()] * 5))
    orch._running = True
    orch.set_online(False)

    assert orch._idle() == ("offline", None)


def test_step_returns_immediately_when_offline():
    orch = _make_orchestrator()
    orch._running = True
    orch.set_online(False)

    orch.step()  # must not block forever or touch the mic

    assert orch.is_online() is False


def test_is_speaking_true_only_during_play():
    class ObservingSink(FakeAudioSink):
        def __init__(self, orch_box):
            super().__init__()
            self._orch_box = orch_box
            self.was_speaking_during_play = None

        def play(self, audio, sample_rate):
            self.was_speaking_during_play = self._orch_box[0].is_speaking()
            super().play(audio, sample_rate)

    orch_box = []
    sink = ObservingSink(orch_box)
    orch = _make_orchestrator(audio_sink=sink)
    orch_box.append(orch)

    assert orch.is_speaking() is False
    orch._speak("hello")
    assert sink.was_speaking_during_play is True
    assert orch.is_speaking() is False


def test_stop_speaking_does_nothing_when_not_speaking():
    sink = FakeAudioSink()
    orch = _make_orchestrator(audio_sink=sink)

    orch.stop_speaking()  # must not raise or call anything

    assert sink.played == []


def test_stop_speaking_interrupts_play_and_is_treated_as_a_normal_end():
    class InterruptibleSink(FakeAudioSink):
        def __init__(self, orch_box):
            super().__init__()
            self._orch_box = orch_box
            self.stop_called = False

        def play(self, audio, sample_rate):
            # simulate the tray's "Stop speaking" being clicked mid-playback
            self._orch_box[0].stop_speaking()
            raise RuntimeError("stream aborted")

        def stop(self):
            self.stop_called = True

    orch_box = []
    sink = InterruptibleSink(orch_box)
    orch = _make_orchestrator(audio_sink=sink)
    orch_box.append(orch)

    orch._speak("a very long reply")  # must not raise

    assert sink.stop_called is True
    assert orch.is_speaking() is False


def test_unrequested_play_exception_still_propagates_after_stop_speaking_used_previously():
    """A prior turn's stop-speaking flag must not leak into a later turn's
    unrelated, genuine playback error."""

    class RaisingSink(FakeAudioSink):
        def play(self, audio, sample_rate):
            raise RuntimeError("real bug, not a user-requested stop")

    orch = _make_orchestrator(audio_sink=FakeAudioSink())
    orch.stop_speaking()  # no-op, not speaking yet

    orch._audio_sink = RaisingSink()
    with pytest.raises(RuntimeError):
        orch._speak("hello")


def test_step_full_voice_flow():
    wake_word = FakeWakeWord([True])
    stt = FakeSTT(text="what time is it")
    llm = FakeLLM(reply="it's noon")
    sink = FakeAudioSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(160)] * 20),
        vad=FakeVAD([False, True, False]),
        wake_word=wake_word,
        stt=stt,
        llm=llm,
        tts=FakeTTS(),
        audio_sink=sink,
        followup_seconds=0,
    )
    orch._running = True

    orch.step()

    assert llm.calls[0][0] == "what time is it"
    assert len(sink.played) == 1
    assert orch.history[-1] == {"role": "assistant", "content": "it's noon"}


def test_step_text_flow_skips_listen_and_transcribe():
    text_queue = queue.Queue()
    text_queue.put("hello there")
    stt = FakeSTT(text="should not be used")
    llm = FakeLLM(reply="hi!")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 20),
        text_queue=text_queue,
        stt=stt,
        llm=llm,
        followup_seconds=0,
    )
    orch._running = True

    orch.step()

    assert stt.calls == []
    assert llm.calls[0][0] == "hello there"


def test_step_reports_status_at_each_stage_for_voice_turn():
    statuses: list[str] = []
    wake_word = FakeWakeWord([True])
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(160)] * 20),
        vad=FakeVAD([False, True, False]),
        wake_word=wake_word,
        stt=FakeSTT(text="what time is it"),
        llm=FakeLLM(reply="it's noon"),
        on_status=statuses.append,
        followup_seconds=0,
    )
    orch._running = True

    orch.step()

    assert statuses == [
        "Idle - waiting for the wake word or a typed question",
        "Listening - recording your question",
        "Transcribing your question",
        "Thinking - waiting on the local LLM for a reply",
        "Speaking: \"it's noon\"",
        "Idle - listening for a follow-up (0s, no wake word needed)...",
        "Idle - waiting for the wake word or a typed question",
    ]


def test_step_reports_status_for_text_turn():
    statuses: list[str] = []
    text_queue = queue.Queue()
    text_queue.put("hello there")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 20),
        text_queue=text_queue,
        llm=FakeLLM(reply="hi!"),
        on_status=statuses.append,
        followup_seconds=0,
    )
    orch._running = True

    orch.step()

    assert statuses == [
        "Idle - waiting for the wake word or a typed question",
        "Got a typed question: 'hello there'",
        "Thinking - waiting on the local LLM for a reply",
        "Speaking: 'hi!'",
        "Idle - listening for a follow-up (0s, no wake word needed)...",
        "Idle - waiting for the wake word or a typed question",
    ]


def test_step_reports_icon_state_at_each_stage_for_voice_turn():
    states: list[str] = []
    wake_word = FakeWakeWord([True])
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(160)] * 20),
        vad=FakeVAD([False, True, False]),
        wake_word=wake_word,
        stt=FakeSTT(text="what time is it"),
        llm=FakeLLM(reply="it's noon"),
        on_state=states.append,
        followup_seconds=0,
    )
    orch._running = True

    orch.step()

    assert states == ["idle", "listening", "processing", "processing", "speaking", "listening", "idle"]


def test_set_volume_clamps_to_zero_one_range():
    orch = _make_orchestrator()

    orch.set_volume(1.5)
    assert orch.get_volume() == 1.0

    orch.set_volume(-0.5)
    assert orch.get_volume() == 0.0

    orch.set_volume(0.3)
    assert orch.get_volume() == 0.3


def test_speak_scales_audio_by_volume_before_playback():
    sink = FakeAudioSink()
    orch = _make_orchestrator(audio_sink=sink, tts=FakeTTS())
    orch.set_volume(0.5)

    orch._speak("it's noon")

    played_audio, _ = sink.played[0]
    assert list(played_audio) == [0, 1, 1]  # [1, 2, 3] * 0.5, truncated back to int16
    assert played_audio.dtype == np.int16


def test_speak_at_full_volume_leaves_audio_unchanged():
    sink = FakeAudioSink()
    orch = _make_orchestrator(audio_sink=sink, tts=FakeTTS())

    orch._speak("it's noon")

    played_audio, _ = sink.played[0]
    assert list(played_audio) == [1, 2, 3]


def test_speak_clips_instead_of_wrapping_at_high_volume():
    class LoudTTS:
        def synthesize(self, text):
            return np.array([30000, -30000], dtype=np.int16), 22050

    sink = FakeAudioSink()
    orch = _make_orchestrator(audio_sink=sink, tts=LoudTTS())
    orch.set_volume(1.0)
    orch._volume = 2.0  # beyond what set_volume allows, to exercise the clip path directly

    orch._speak("loud")

    played_audio, _ = sink.played[0]
    assert list(played_audio) == [32767, -32768]


def test_think_and_speak_speaks_each_streamed_sentence_separately():
    class TrackingTTS:
        def __init__(self):
            self.calls = []

        def synthesize(self, text):
            self.calls.append(text)
            return np.array([1, 2, 3], dtype=np.int16), 22050

    tts = TrackingTTS()
    sink = FakeAudioSink()
    llm = StreamingFakeLLM(["Hi there. ", "How are you? ", "Bye."])
    orch = _make_orchestrator(llm=llm, tts=tts, audio_sink=sink)

    orch._think_and_speak("hello")

    assert tts.calls == ["Hi there.", "How are you?", "Bye."]
    assert len(sink.played) == 3


def test_think_and_speak_records_full_reply_to_history():
    llm = StreamingFakeLLM(["Hi there. ", "How are you?"])
    orch = _make_orchestrator(llm=llm, tts=FakeTTS())

    orch._think_and_speak("hello")

    assert orch.history[-2:] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi there. How are you?"},
    ]


def test_think_and_speak_reports_status_per_sentence():
    statuses = []
    llm = StreamingFakeLLM(["Hi there. ", "Bye."])
    orch = _make_orchestrator(llm=llm, tts=FakeTTS(), on_status=statuses.append)

    orch._think_and_speak("hello")

    assert statuses == ["Speaking: 'Hi there.'", "Speaking: 'Bye.'"]


def test_think_and_speak_skips_llm_call_while_paused():
    llm = StreamingFakeLLM(["should not be spoken"])
    tts = FakeTTS()
    sink = FakeAudioSink()
    orch = _make_orchestrator(llm=llm, tts=tts, audio_sink=sink)

    orch.set_paused(True)
    orch._think_and_speak("hello")

    assert llm.calls == []
    assert sink.played == []
    assert orch.history == []


def test_set_paused_defaults_to_false_and_round_trips():
    orch = _make_orchestrator()
    assert orch.is_paused() is False
    orch.set_paused(True)
    assert orch.is_paused() is True
    orch.set_paused(False)
    assert orch.is_paused() is False


def test_is_responding_true_only_during_think_and_speak():
    llm = StreamingFakeLLM(["Hi there. "])

    class SnoopingTTS:
        def __init__(self, orch_box):
            self.orch_box = orch_box
            self.was_responding_during_synthesize = None

        def synthesize(self, text):
            self.was_responding_during_synthesize = self.orch_box[0].is_responding()
            return np.array([1, 2, 3], dtype=np.int16), 22050

    orch_box = []
    tts = SnoopingTTS(orch_box)
    orch = _make_orchestrator(llm=llm, tts=tts)
    orch_box.append(orch)

    assert orch.is_responding() is False
    orch._think_and_speak("hello")
    assert tts.was_responding_during_synthesize is True
    assert orch.is_responding() is False


def test_stop_generating_does_nothing_when_not_responding():
    llm = FakeLLM()
    orch = _make_orchestrator(llm=llm)

    orch.stop_generating()  # must not raise or call anything

    assert llm.cancel_calls == 0


def test_stop_generating_cancels_llm_and_skips_remaining_sentences():
    llm = StreamingFakeLLM(["Hi there. ", "How are you? ", "Bye."])
    sink = FakeAudioSink()

    class StoppingTTS:
        """Stops generation as soon as the first sentence is being
        synthesized - simulates the dashboard click landing mid-turn."""

        def __init__(self, orch_box):
            self.orch_box = orch_box
            self.calls = []

        def synthesize(self, text):
            self.calls.append(text)
            if len(self.calls) == 1:
                self.orch_box[0].stop_generating()
            return np.array([1, 2, 3], dtype=np.int16), 22050

    orch_box = []
    tts = StoppingTTS(orch_box)
    orch = _make_orchestrator(llm=llm, tts=tts, audio_sink=sink)
    orch_box.append(orch)

    orch._think_and_speak("hello")

    assert tts.calls == ["Hi there."]  # never got to the remaining sentences
    # Regression coverage: a stop requested *during* synthesis (before
    # `_speaking` is even set True) used to get silently cleared by
    # `_speak()`'s own reset, so playback proceeded anyway and the loop
    # carried on to the next sentence unimpeded.
    assert sink.played == []
    assert llm.cancel_calls == 1
    assert orch.is_responding() is False
    # the sentence already spoken is still recorded, not dropped:
    assert orch.history[-1] == {"role": "assistant", "content": "Hi there."}


def test_stop_generating_interrupts_playback_of_the_current_sentence():
    class AbortingSink:
        def __init__(self, orch_box):
            self.orch_box = orch_box
            self.stopped = False

        def play(self, audio, sample_rate):
            self.orch_box[0].stop_generating()
            raise RuntimeError("interrupted")

        def stop(self):
            self.stopped = True

    orch_box = []
    sink = AbortingSink(orch_box)
    llm = StreamingFakeLLM(["Hi there. ", "How are you?"])
    orch = _make_orchestrator(llm=llm, tts=FakeTTS(), audio_sink=sink)
    orch_box.append(orch)

    orch._think_and_speak("hello")  # must not raise

    assert sink.stopped
    assert orch.is_speaking() is False
    assert orch.is_responding() is False


def test_stream_sentences_yields_complete_sentences_as_they_appear():
    from orchestrator.state_machine import _stream_sentences

    deltas = ["Hi ", "there. How ", "are you? ", "Good, thanks."]

    assert list(_stream_sentences(deltas)) == ["Hi there.", "How are you?", "Good, thanks."]


def test_stream_sentences_flushes_trailing_text_with_no_punctuation():
    from orchestrator.state_machine import _stream_sentences

    assert list(_stream_sentences(["just some text"])) == ["just some text"]


def test_stream_sentences_yields_nothing_for_empty_input():
    from orchestrator.state_machine import _stream_sentences

    assert list(_stream_sentences([])) == []


def test_step_does_nothing_further_on_empty_transcript():
    wake_word = FakeWakeWord([True])
    llm = FakeLLM()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(160)] * 3),
        vad=FakeVAD([True, False]),
        wake_word=wake_word,
        stt=FakeSTT(text=""),
        llm=llm,
    )
    orch._running = True

    orch.step()

    assert llm.calls == []


def test_await_followup_times_out_when_nothing_happens():
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 20),
        vad=FakeVAD(),
        followup_seconds=3.0,
        clock=FakeClock(tick=1.0),
    )

    kind, payload = orch._await_followup()

    assert (kind, payload) == ("timeout", None)


def test_await_followup_returns_text_when_submitted_during_wait():
    text_queue = queue.Queue()
    text_queue.put("what about tomorrow")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(1600)] * 20),
        vad=FakeVAD(),
        text_queue=text_queue,
        followup_seconds=5.0,
    )

    kind, payload = orch._await_followup()

    assert (kind, payload) == ("text", "what about tomorrow")


def test_await_followup_records_only_from_speech_onset_to_silence():
    # first two chunks are silence while waiting, then speech starts,
    # then silence again - the returned audio should exclude the leading
    # wait-time silence.
    chunks = [np.full(160, i, dtype=np.int16) for i in range(4)]
    orch = _make_orchestrator(
        audio_source=FakeAudioSource(chunks),
        vad=FakeVAD([False, False, True, False]),
        followup_seconds=5.0,
        sample_rate=16000,
    )

    kind, payload = orch._await_followup()

    assert kind == "voice"
    assert np.array_equal(payload, np.concatenate([chunks[2], chunks[3]]))


def test_step_continues_conversation_on_followup_voice_without_wake_word():
    wake_word = FakeWakeWord([True])
    llm = FakeLLM(reply="first reply")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(160)] * 40),
        vad=FakeVAD([False, True, False, False, True, False]),
        wake_word=wake_word,
        stt=FakeSTT(text="first question"),
        llm=llm,
        followup_seconds=5.0,
        clock=FakeClock(tick=1.0),
    )
    orch._running = True

    orch.step()

    # first turn plus a follow-up turn triggered purely by speech (the VAD
    # sequence's second True/False pair), with no second wake-word
    # detection - the follow-up's own timeout (VAD defaults to False once
    # its scripted results run out) is what finally ends the conversation.
    assert [call[0] for call in llm.calls] == ["first question", "first question"]
    assert wake_word.reset_calls == 1


def test_step_continues_conversation_on_followup_text_without_wake_word():
    text_queue = queue.Queue()
    text_queue.put("first question")
    text_queue.put("second question")
    llm = FakeLLM(reply="reply")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 40),
        text_queue=text_queue,
        llm=llm,
        followup_seconds=0,
    )
    orch._running = True

    orch.step()

    assert [call[0] for call in llm.calls] == ["first question", "second question"]


class InfiniteAudioSource:
    """Unlike `FakeAudioSource`, never raises `StopIteration` - needed for
    the drain tests below since the real background drain thread reads in
    a tight loop for however long the wrapped block takes, an
    indeterminate number of times."""

    def __init__(self):
        self.muted = False
        self.read_count = 0
        self._lock = threading.Lock()

    def read_chunk(self):
        with self._lock:
            self.read_count += 1
        return _chunk()


class SlowFakeSink(FakeAudioSink):
    def __init__(self, sleep_seconds):
        super().__init__()
        self._sleep_seconds = sleep_seconds

    def play(self, audio, sample_rate):
        time.sleep(self._sleep_seconds)
        super().play(audio, sample_rate)


class SlowFakeLLM(FakeLLM):
    def __init__(self, reply="reply", sleep_seconds=0.0):
        super().__init__(reply=reply)
        self._sleep_seconds = sleep_seconds

    def generate(self, prompt, history):
        time.sleep(self._sleep_seconds)
        return super().generate(prompt, history)


def test_speak_drains_mic_in_background_during_slow_playback():
    """Regression test for the real bug the user hit: nothing read the mic
    while `_speak()` blocked on TTS synthesis + playback, so real frames
    captured during that time piled up un-drained in the audio source's
    queue - the very next read (in `_await_followup`) then drained that
    whole backlog almost instantly, making the follow-up wait look like it
    didn't happen at all."""
    audio_source = InfiniteAudioSource()
    orch = _make_orchestrator(
        audio_source=audio_source,
        audio_sink=SlowFakeSink(sleep_seconds=0.2),
        drain_context=None,  # use the real threaded default, not the test no-op
    )

    orch._speak("hello")

    assert audio_source.read_count > 5
    assert audio_source.muted is False


def test_think_drains_mic_in_background_during_slow_llm_call():
    audio_source = InfiniteAudioSource()
    llm = SlowFakeLLM(reply="reply", sleep_seconds=0.2)
    orch = _make_orchestrator(
        audio_source=audio_source,
        llm=llm,
        drain_context=None,
    )

    orch._think("a question")

    assert audio_source.read_count > 5


# --- transcript events (08-transcript-ui) ----------------------------------


class RecordingSink:
    """Collects every `TranscriptEvent` the orchestrator publishes."""

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def kinds(self):
        return [event.kind for event in self.events]

    def of(self, kind):
        return [event for event in self.events if event.kind == kind]


class FakePartials:
    def __init__(self):
        self.submitted = []
        self.resets = 0

    def submit(self, audio):
        self.submitted.append(np.array(audio, copy=True))

    def reset(self):
        self.resets += 1


def test_no_transcript_sink_leaves_behaviour_unchanged():
    """Every existing test constructs an Orchestrator without a sink, so
    this is the property that keeps them all valid."""
    orch = _make_orchestrator()

    assert orch._transcript is None
    orch._emit(TranscriptEvent(kind=STATE, state="idle"))  # must be a no-op


def test_state_transitions_are_published_to_the_transcript():
    sink = RecordingSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 40),
        wake_word=FakeWakeWord([True]),
        vad=FakeVAD([True, False]),
        stt=FakeSTT("what's the weather"),
        llm=FakeLLM("It is sunny."),
        clock=FakeClock(tick=100.0),
        transcript=sink,
    )
    orch._running = True

    orch.step()

    states = [event.state for event in sink.of(STATE)]
    assert "listening" in states
    assert "processing" in states
    assert "speaking" in states


def test_the_final_user_transcript_is_published():
    sink = RecordingSink()
    orch = _make_orchestrator(stt=FakeSTT("what's the weather"), transcript=sink)

    orch._transcribe_and_log(_chunk(16000))

    assert [event.text for event in sink.of(USER_FINAL)] == ["what's the weather"]


def test_a_typed_question_is_published_as_the_user_turn():
    """The popup path skips STT entirely, so it needs its own emit - the
    overlay should show typed questions the same way it shows spoken ones."""
    sink = RecordingSink()
    text_queue = queue.Queue()
    text_queue.put("what's the weather")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 40),
        text_queue=text_queue,
        llm=FakeLLM("It is sunny."),
        clock=FakeClock(tick=100.0),
        transcript=sink,
    )
    orch._running = True

    orch.step()

    assert [event.text for event in sink.of(USER_FINAL)] == ["what's the weather"]


def test_assistant_deltas_are_published_at_token_granularity():
    """Unlike `_speak()`, which needs whole sentences for Piper to sound
    natural, text on screen has no such constraint - the overlay gets the
    real stream so the reply appears as it is generated."""
    sink = RecordingSink()
    llm = StreamingFakeLLM(["Hello", " there", ". How", " are you?"])
    orch = _make_orchestrator(llm=llm, transcript=sink)

    orch._think_and_speak("hi")

    assert [event.text for event in sink.of(ASSISTANT_DELTA)] == [
        "Hello",
        " there",
        ". How",
        " are you?",
    ]


def test_the_assistant_turn_is_closed_when_the_reply_ends():
    sink = RecordingSink()
    orch = _make_orchestrator(llm=FakeLLM("It is sunny."), transcript=sink)

    orch._think_and_speak("weather?")

    finals = sink.of(ASSISTANT_FINAL)
    assert len(finals) == 1
    assert finals[0].text == "It is sunny."


def test_an_interrupted_reply_still_closes_its_turn():
    """Otherwise the overlay is left showing a streaming caret forever and
    never fades out."""
    sink = RecordingSink()
    llm = StreamingFakeLLM(["First sentence. ", "Second sentence. "])
    orch = _make_orchestrator(llm=llm, transcript=sink)
    orch._responding = True
    orch._stop_requested = True

    orch._think_and_speak("hi")

    assert len(sink.of(ASSISTANT_FINAL)) == 1


def test_a_wake_word_starts_a_new_conversation_in_the_transcript():
    sink = RecordingSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 5),
        wake_word=FakeWakeWord([False, True]),
        transcript=sink,
    )
    orch._running = True

    orch._idle()

    assert len(sink.of(RESET)) == 1


def test_mic_level_is_published_while_listening():
    """Before any words are decoded, the meter is the only thing proving
    Gideon can hear the user at all."""
    sink = RecordingSink()
    loud = np.full(480, 8000, dtype=np.int16)
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([loud] * 10),
        vad=FakeVAD([True] * 6 + [False]),
        transcript=sink,
    )

    orch._listen()

    levels = sink.of(LEVEL)
    assert levels, "expected at least one level event"
    assert all(0.0 <= event.level <= 1.0 for event in levels)
    assert max(event.level for event in levels) > 0.0


def test_silence_produces_a_zero_level():
    sink = RecordingSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(480)] * 10),
        vad=FakeVAD([True, False]),
        transcript=sink,
    )

    orch._listen()

    assert all(event.level == 0.0 for event in sink.of(LEVEL))


def test_events_carry_no_sequence_number_from_the_orchestrator():
    """Stamping `seq` is the client's job, in one place, rather than at each
    of the orchestrator's emit call sites."""
    sink = RecordingSink()
    orch = _make_orchestrator(stt=FakeSTT("hi"), transcript=sink)

    orch._transcribe_and_log(_chunk(16000))

    assert all(event.seq == 0 for event in sink.events)


class ExplodingSink:
    def __init__(self):
        self.calls = 0

    def emit(self, event):
        self.calls += 1
        raise RuntimeError("overlay wedged")


def test_a_throwing_transcript_sink_cannot_break_a_conversation():
    """The overlay is a nicety; the assistant working is not."""
    sink = ExplodingSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 40),
        wake_word=FakeWakeWord([True]),
        vad=FakeVAD([True, False]),
        stt=FakeSTT("what's the weather"),
        llm=FakeLLM("It is sunny."),
        clock=FakeClock(tick=100.0),
        transcript=sink,
    )
    orch._running = True

    orch.step()

    assert sink.calls > 0
    assert orch.history == [
        {"role": "user", "content": "what's the weather"},
        {"role": "assistant", "content": "It is sunny."},
    ]


# --- live partial transcription --------------------------------------------


def test_the_utterance_so_far_is_handed_to_the_partial_transcriber():
    partials = FakePartials()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(480)] * 40),
        vad=FakeVAD([True] * 25 + [False]),
        partial_transcriber=partials,
    )

    orch._listen()

    assert partials.submitted, "expected at least one partial submission"
    # Each submission is the whole utterance so far, so they grow.
    lengths = [len(buffer) for buffer in partials.submitted]
    assert lengths == sorted(lengths)


def test_partials_are_submitted_less_often_than_once_per_frame():
    """Whisper inference is orders of magnitude slower than the 30ms frame
    cadence; submitting every frame would be pure waste."""
    partials = FakePartials()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(480)] * 40),
        vad=FakeVAD([True] * 30 + [False]),
        partial_transcriber=partials,
    )

    orch._listen()

    assert len(partials.submitted) < 30


def test_listening_starts_from_a_clean_partial_state():
    """So nothing left over from a previous utterance can surface as this
    one's first partial."""
    partials = FakePartials()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(480)] * 10),
        vad=FakeVAD([True, False]),
        partial_transcriber=partials,
    )

    orch._listen()

    assert partials.resets >= 1


def test_the_final_transcript_resets_the_partial_transcriber():
    """A partial still mid-inference must not overwrite the final text, nor
    leak into the next turn."""
    partials = FakePartials()
    orch = _make_orchestrator(stt=FakeSTT("hello"), partial_transcriber=partials)
    before = partials.resets

    orch._transcribe_and_log(_chunk(16000))

    assert partials.resets == before + 1


def test_a_follow_up_also_feeds_the_partial_transcriber():
    partials = FakePartials()
    sink = RecordingSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(480)] * 40),
        vad=FakeVAD([True] * 25 + [False]),
        clock=FakeClock(tick=0.001),
        partial_transcriber=partials,
        transcript=sink,
    )

    kind, _payload = orch._await_followup()

    assert kind == "voice"
    assert partials.submitted
    assert sink.of(LEVEL)


def test_no_partial_transcriber_is_fine():
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(480)] * 10),
        vad=FakeVAD([True, False]),
    )

    orch._listen()  # must not raise


def test_a_voice_followup_publishes_its_question_exactly_once():
    """Regression: `step()`'s follow-up branch called `_emit_user_text`
    *outside* its if/else, so a spoken follow-up published `user_final`
    twice - once from `_transcribe_and_log` and once from the stray call -
    and the overlay drew the question as two identical "You" rows. Found by
    the user on real hardware, from a screenshot of the overlay.

    Driven through `step()` rather than `_await_followup()` directly,
    because that is exactly the gap that let this through: the existing
    follow-up tests checked the LLM calls, and the existing transcript
    tests called `_transcribe_and_log` on its own, so nothing ever counted
    events across a whole two-turn conversation.
    """
    sink = RecordingSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk(160)] * 40),
        vad=FakeVAD([False, True, False, False, True, False]),
        wake_word=FakeWakeWord([True]),
        stt=FakeSTT(text="first question"),
        llm=FakeLLM(reply="a reply"),
        followup_seconds=5.0,
        clock=FakeClock(tick=1.0),
        transcript=sink,
    )
    orch._running = True

    orch.step()

    finals = sink.of(USER_FINAL)
    assert [event.text for event in finals] == ["first question", "first question"], (
        "expected exactly one user_final per turn across the two turns"
    )


def test_a_typed_followup_publishes_its_question_exactly_once():
    """The other half of the same branch - the typed path is the one that
    legitimately needs its own `_emit_user_text`, since nothing else
    publishes for it."""
    sink = RecordingSink()
    text_queue = queue.Queue()
    text_queue.put("first question")
    text_queue.put("second question")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 40),
        text_queue=text_queue,
        llm=FakeLLM(reply="a reply"),
        followup_seconds=0,
        transcript=sink,
    )
    orch._running = True

    orch.step()

    assert [event.text for event in sink.of(USER_FINAL)] == [
        "first question",
        "second question",
    ]


def test_a_first_voice_turn_publishes_its_question_exactly_once():
    sink = RecordingSink()
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 40),
        wake_word=FakeWakeWord([True]),
        vad=FakeVAD([True, False]),
        stt=FakeSTT("only question"),
        llm=FakeLLM("a reply"),
        clock=FakeClock(tick=100.0),
        transcript=sink,
    )
    orch._running = True

    orch.step()

    assert [event.text for event in sink.of(USER_FINAL)] == ["only question"]


def test_a_first_typed_turn_publishes_its_question_exactly_once():
    sink = RecordingSink()
    text_queue = queue.Queue()
    text_queue.put("only question")
    orch = _make_orchestrator(
        audio_source=FakeAudioSource([_chunk()] * 40),
        text_queue=text_queue,
        llm=FakeLLM("a reply"),
        clock=FakeClock(tick=100.0),
        transcript=sink,
    )
    orch._running = True

    orch.step()

    assert [event.text for event in sink.of(USER_FINAL)] == ["only question"]

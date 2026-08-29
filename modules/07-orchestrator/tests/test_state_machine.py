import contextlib
import queue
import threading
import time

import numpy as np
import pytest

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

    def generate(self, prompt, history):
        self.calls.append((prompt, list(history)))
        return self.reply


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

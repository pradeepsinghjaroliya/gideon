import numpy as np

from audio_io.sink import SpeakerAudioSink, resample


def test_resample_noop_when_rates_match():
    audio = np.array([1, 2, 3], dtype=np.int16)
    assert np.array_equal(resample(audio, 16000, 16000), audio)


def test_resample_upsamples_to_target_length():
    audio = np.linspace(-1000, 1000, num=100, dtype=np.int16)
    out = resample(audio, 22050, 44100)
    assert len(out) == 200
    assert out.dtype == audio.dtype


def test_resample_downsamples_to_target_length():
    audio = np.linspace(-1000, 1000, num=200, dtype=np.int16)
    out = resample(audio, 44100, 22050)
    assert len(out) == 100


class FakeOutputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes: list[np.ndarray] = []
        self.stopped = False
        self.closed = False

    def start(self):
        pass

    def write(self, audio):
        self.writes.append(np.array(audio))

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_play_resamples_to_device_native_rate_and_writes():
    created = {}

    def factory(**kwargs):
        stream = FakeOutputStream(**kwargs)
        created["stream"] = stream
        return stream

    sink = SpeakerAudioSink(stream_factory=factory)
    sink._device_native_rate = lambda: 48000

    audio = np.zeros(100, dtype=np.int16)
    sink.play(audio, sample_rate=16000)

    stream = created["stream"]
    assert stream.kwargs["samplerate"] == 48000
    assert sum(len(w) for w in stream.writes) == 300
    assert stream.stopped and stream.closed


def test_play_writes_in_small_chunks_instead_of_one_big_write():
    """A real, confirmed problem with one big blocking `write()`: aborting
    it mid-call (from another thread) triggered this project's ALSA
    backend's own internal xrun-recovery path, which then spent several
    seconds failing and retrying before the interrupted `write()` finally
    raised. Small chunks checked between writes give the same "stop
    within a fraction of a second" responsiveness without ever needing to
    interrupt an in-progress write."""
    created = []

    def factory(**kwargs):
        stream = FakeOutputStream(**kwargs)
        created.append(stream)
        return stream

    sink = SpeakerAudioSink(stream_factory=factory, write_chunk_seconds=0.01)
    sink._device_native_rate = lambda: 1000  # chunk_size = 10 samples

    sink.play(np.zeros(35, dtype=np.int16), sample_rate=1000)

    stream = created[0]
    assert [len(w) for w in stream.writes] == [10, 10, 10, 5]


def test_stop_between_chunks_stops_writing_further_chunks():
    calls = []

    class StoppingStream(FakeOutputStream):
        def write(self, audio):
            super().write(audio)
            calls.append(len(self.writes))
            if len(self.writes) == 1:
                sink.stop()  # simulate the dashboard click landing between chunks

    sink = SpeakerAudioSink(stream_factory=lambda **kwargs: StoppingStream(**kwargs), write_chunk_seconds=0.01)
    sink._device_native_rate = lambda: 1000  # chunk_size = 10 samples

    sink.play(np.zeros(50, dtype=np.int16), sample_rate=1000)  # would be 5 chunks uninterrupted

    assert calls == [1]  # stopped after the first chunk, never wrote a second


def test_play_clears_the_stop_event_so_a_later_play_call_is_not_immediately_stopped():
    created = []

    def factory(**kwargs):
        stream = FakeOutputStream(**kwargs)
        created.append(stream)
        return stream

    sink = SpeakerAudioSink(stream_factory=factory, write_chunk_seconds=0.01)
    sink._device_native_rate = lambda: 1000  # chunk_size = 10 samples
    sink.stop()  # simulate a stop left over from an earlier, unrelated turn

    sink.play(np.zeros(30, dtype=np.int16), sample_rate=1000)

    assert len(created[0].writes) == 3  # all chunks written, not stopped early


def test_stop_does_nothing_when_no_active_stream():
    sink = SpeakerAudioSink(stream_factory=lambda **kwargs: FakeOutputStream(**kwargs))

    sink.stop()  # must not raise


def test_play_clears_stream_reference_after_finishing():
    sink = SpeakerAudioSink(stream_factory=lambda **kwargs: FakeOutputStream(**kwargs))
    sink._device_native_rate = lambda: 16000

    sink.play(np.zeros(10, dtype=np.int16), sample_rate=16000)

    assert sink._stream is None


def test_play_does_not_clear_stream_reference_if_a_newer_stream_replaced_it():
    """If a second `play()` call somehow started before this one's
    `finally` runs, this one must not null out a stream it doesn't own
    anymore - only ever clear `self._stream` when it's still pointing at
    *this* method's own stream."""
    other_stream = FakeOutputStream()

    class SwappingStream(FakeOutputStream):
        def write(self, audio):
            super().write(audio)
            sink._stream = other_stream  # simulate a race swapping it mid-play

    sink = SpeakerAudioSink(stream_factory=lambda **kwargs: SwappingStream(**kwargs))
    sink._device_native_rate = lambda: 16000

    sink.play(np.zeros(10, dtype=np.int16), sample_rate=16000)

    assert sink._stream is other_stream

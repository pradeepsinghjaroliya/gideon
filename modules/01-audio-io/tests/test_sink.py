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
        self.written = None
        self.stopped = False
        self.closed = False
        self.aborted = False

    def start(self):
        pass

    def write(self, audio):
        self.written = audio

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True


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
    assert len(stream.written) == 300
    assert stream.stopped and stream.closed


def test_stop_aborts_active_stream():
    def factory(**kwargs):
        return FakeOutputStream(**kwargs)

    sink = SpeakerAudioSink(stream_factory=factory)
    sink._stream = factory()
    sink.stop()
    assert sink._stream is None

import numpy as np

from audio_io.source import MicAudioSource


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def make_source(**kwargs):
    created = {}

    def factory(**stream_kwargs):
        stream = FakeStream(**stream_kwargs)
        created["stream"] = stream
        return stream

    source = MicAudioSource(stream_factory=factory, **kwargs)
    source.start()
    return source, created["stream"]


def test_frame_size_matches_sample_rate_and_frame_ms():
    source, stream = make_source(sample_rate=16000, frame_ms=30)
    assert stream.kwargs["blocksize"] == 480
    assert stream.kwargs["samplerate"] == 16000


def test_read_chunk_returns_captured_frame():
    source, _ = make_source()
    frame = np.arange(480, dtype=np.int16)
    source._callback(frame.reshape(-1, 1), 480, None, None)

    assert np.array_equal(source.read_chunk(), frame)


def test_muted_returns_zeros_but_still_drains_queue():
    source, _ = make_source()
    frame = np.full(480, 123, dtype=np.int16)
    source._callback(frame.reshape(-1, 1), 480, None, None)

    source.muted = True
    chunk = source.read_chunk()

    assert chunk.shape == frame.shape
    assert np.all(chunk == 0)
    assert source._queue.empty()


def test_stop_closes_stream():
    source, stream = make_source()
    source.stop()
    assert stream.closed

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


def test_read_chunk_returns_silence_instead_of_blocking_forever_on_stalled_queue():
    """Regression test for a real, confirmed hang: `queue.Queue.get()` has
    no timeout by default, so a stalled input callback (observed after
    aborting playback mid-write) used to block `read_chunk()` - and
    everything waiting on it - forever, with no exception and no log
    line. A bounded timeout means the caller always gets a same-shaped
    silent frame back instead."""
    source, _ = make_source(read_timeout=0.05)

    chunk = source.read_chunk()

    assert chunk.shape == (source._frame_samples,)
    assert chunk.dtype == np.int16
    assert np.all(chunk == 0)


def test_read_chunk_logs_a_warning_on_timeout():
    warnings = []

    class FakeLog:
        def warning(self, *args, **kwargs):
            warnings.append(args)

    source, _ = make_source(read_timeout=0.05, logger=FakeLog())

    source.read_chunk()

    assert len(warnings) == 1


def test_read_chunk_does_not_time_out_when_a_real_frame_is_already_queued():
    source, _ = make_source(read_timeout=0.05)
    frame = np.full(480, 42, dtype=np.int16)
    source._callback(frame.reshape(-1, 1), 480, None, None)

    assert np.array_equal(source.read_chunk(), frame)

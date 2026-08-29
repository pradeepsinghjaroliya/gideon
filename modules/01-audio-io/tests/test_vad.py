import numpy as np

from audio_io.vad import SileroVAD

WINDOW = 512


def scripted_model(probs):
    probs = iter(probs)

    def predict(window, sample_rate):
        return next(probs)

    return predict


def windows(n):
    return np.zeros(WINDOW * n, dtype=np.int16)


def test_is_speech_true_once_above_threshold():
    vad = SileroVAD(model=scripted_model([0.9]), silence_duration_ms=800)
    assert vad.is_speech(windows(1)) is True


def test_is_speech_false_below_threshold():
    vad = SileroVAD(model=scripted_model([0.1]), silence_duration_ms=800)
    assert vad.is_speech(windows(1)) is False


def test_brief_dip_does_not_flip_to_silence():
    # 512-sample windows at 16kHz = 32ms each; hangover is 800ms (~25 windows).
    probs = [0.9, 0.1, 0.9]
    vad = SileroVAD(model=scripted_model(probs), silence_duration_ms=800)

    assert vad.is_speech(windows(1)) is True
    assert vad.is_speech(windows(1)) is True  # one silent window: still within hangover
    assert vad.is_speech(windows(1)) is True


def test_sustained_silence_flips_to_false():
    silence_windows_needed = int(16000 * 800 / 1000 / WINDOW) + 1
    probs = [0.9] + [0.1] * silence_windows_needed
    vad = SileroVAD(model=scripted_model(probs), silence_duration_ms=800)

    assert vad.is_speech(windows(1)) is True
    for _ in range(silence_windows_needed):
        result = vad.is_speech(windows(1))

    assert result is False


def test_partial_chunks_are_buffered_until_full_window():
    vad = SileroVAD(model=scripted_model([0.9]), silence_duration_ms=800)
    half = np.zeros(WINDOW // 2, dtype=np.int16)

    assert vad.is_speech(half) is False  # not enough samples yet, no model call
    assert vad.is_speech(half) is True  # completes the window now


def test_reset_clears_state():
    vad = SileroVAD(model=scripted_model([0.9, 0.1] * 5), silence_duration_ms=1)
    vad.is_speech(windows(1))
    vad.reset()
    assert vad._speaking is False
    assert vad._buffer.size == 0

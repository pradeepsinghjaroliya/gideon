import numpy as np

from tts.engine import PiperEngine


class ScriptedSynth:
    def __init__(self, audio: np.ndarray, sample_rate: int) -> None:
        self.audio = audio
        self.sample_rate = sample_rate
        self.calls: list[str] = []

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        self.calls.append(text)
        return self.audio, self.sample_rate


def test_synthesize_returns_audio_and_sample_rate_from_synth_fn():
    audio = np.array([1, 2, 3], dtype=np.int16)
    synth = ScriptedSynth(audio, 22050)
    engine = PiperEngine(synth_fn=synth)

    result_audio, result_rate = engine.synthesize("hello")

    assert np.array_equal(result_audio, audio)
    assert result_rate == 22050


def test_synthesize_passes_text_through_unchanged():
    synth = ScriptedSynth(np.zeros(1, dtype=np.int16), 22050)
    engine = PiperEngine(synth_fn=synth)

    engine.synthesize("hello world")

    assert synth.calls == ["hello world"]


def test_synthesize_returns_int16_dtype():
    audio = np.array([100, -100], dtype=np.int16)
    synth = ScriptedSynth(audio, 22050)
    engine = PiperEngine(synth_fn=synth)

    result_audio, _ = engine.synthesize("hello")

    assert result_audio.dtype == np.int16

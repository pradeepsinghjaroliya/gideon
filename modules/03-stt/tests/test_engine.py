import numpy as np
import pytest

from stt.engine import FasterWhisperEngine

CHUNK = np.zeros(16000, dtype=np.int16)


class ScriptedModel:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[np.ndarray, int]] = []

    def __call__(self, audio_float32: np.ndarray, sample_rate: int) -> str:
        self.calls.append((audio_float32, sample_rate))
        return self.text


def test_transcribe_returns_stripped_text():
    model = ScriptedModel("  hello world  ")
    engine = FasterWhisperEngine(model_fn=model)

    assert engine.transcribe(CHUNK, 16000) == "hello world"


def test_transcribe_passes_sample_rate_through():
    model = ScriptedModel("text")
    engine = FasterWhisperEngine(model_fn=model)

    engine.transcribe(CHUNK, 16000)

    assert model.calls[0][1] == 16000


def test_transcribe_converts_int16_to_float32_range():
    model = ScriptedModel("text")
    engine = FasterWhisperEngine(model_fn=model)
    chunk = np.array([32767, -32768, 0], dtype=np.int16)

    engine.transcribe(chunk, 16000)

    audio_float32 = model.calls[0][0]
    assert audio_float32.dtype == np.float32
    assert audio_float32.max() == pytest.approx(1.0, abs=1e-3)
    assert audio_float32.min() == pytest.approx(-1.0, abs=1e-3)


def test_transcribe_handles_empty_model_output():
    model = ScriptedModel("")
    engine = FasterWhisperEngine(model_fn=model)

    assert engine.transcribe(CHUNK, 16000) == ""

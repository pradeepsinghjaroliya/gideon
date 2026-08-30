import numpy as np

from shared.interfaces import (
    AudioSink,
    AudioSource,
    LLMClient,
    STTEngine,
    TextInputProvider,
    TTSEngine,
    VoiceActivityDetector,
    WakeWordDetector,
)


class FakeAudioSource:
    def start(self) -> None:
        pass

    def read_chunk(self) -> np.ndarray:
        return np.zeros(480, dtype=np.int16)

    def stop(self) -> None:
        pass


class FakeAudioSink:
    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        pass

    def stop(self) -> None:
        pass


class FakeVAD:
    def is_speech(self, chunk: np.ndarray) -> bool:
        return bool(chunk.any())


class FakeWakeWordDetector:
    def process_chunk(self, chunk: np.ndarray) -> bool:
        return False

    def reset(self) -> None:
        pass


class FakeSTTEngine:
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        return "fake transcript"


class FakeLLMClient:
    def generate(self, prompt: str, history: list[dict]) -> str:
        return "fake response"

    def generate_stream(self, prompt: str, history: list[dict]):
        yield "fake response"

    def cancel(self) -> None:
        pass


class FakeTTSEngine:
    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        return np.zeros(10, dtype=np.int16), 22050


class FakeTextInputProvider:
    def get_text(self) -> str | None:
        return "typed request"


def test_fakes_satisfy_protocols_structurally():
    # No inheritance anywhere above - isinstance works purely because the
    # method shapes match, proving the Protocols in interfaces.py are
    # usable as intended (duck typing, not required subclassing).
    assert isinstance(FakeAudioSource(), AudioSource)
    assert isinstance(FakeAudioSink(), AudioSink)
    assert isinstance(FakeVAD(), VoiceActivityDetector)
    assert isinstance(FakeWakeWordDetector(), WakeWordDetector)
    assert isinstance(FakeSTTEngine(), STTEngine)
    assert isinstance(FakeLLMClient(), LLMClient)
    assert isinstance(FakeTTSEngine(), TTSEngine)
    assert isinstance(FakeTextInputProvider(), TextInputProvider)


def test_fake_stt_engine_returns_text():
    engine = FakeSTTEngine()
    assert engine.transcribe(np.zeros(16000, dtype=np.int16), 16000) == "fake transcript"


def test_fake_llm_client_returns_text():
    client = FakeLLMClient()
    assert client.generate("hello", []) == "fake response"

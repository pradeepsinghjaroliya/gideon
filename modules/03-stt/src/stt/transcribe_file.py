"""Standalone transcription demo.

Loads a wav file (any sample rate/channel count - resampled/downmixed to
the shared mono 16kHz int16 convention) and prints the transcript. Doesn't
depend on `01-audio-io` or a mic. Run:
`python -m stt.transcribe_file path/to/clip.wav`.
"""

from __future__ import annotations

import argparse
import time
import wave

import numpy as np

from stt.engine import FasterWhisperEngine


def _load_wav_mono16k(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        raw = wav_file.readframes(wav_file.getnframes())

    audio = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)

    if sample_rate != 16000:
        duration = len(audio) / sample_rate
        target_len = int(duration * 16000)
        original_x = np.linspace(0, duration, num=len(audio))
        target_x = np.linspace(0, duration, num=target_len)
        audio = np.interp(target_x, original_x, audio).astype(np.int16)

    return audio


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Transcribe a wav file")
    parser.add_argument("wav_path")
    parser.add_argument("--model-size", default="small")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)

    audio = _load_wav_mono16k(args.wav_path)
    engine = FasterWhisperEngine(model_size=args.model_size, device=args.device)

    start = time.monotonic()
    text = engine.transcribe(audio, 16000)
    elapsed = time.monotonic() - start

    print(text)
    print(f"[{elapsed:.2f}s for {len(audio) / 16000:.1f}s of audio]")


if __name__ == "__main__":
    main()

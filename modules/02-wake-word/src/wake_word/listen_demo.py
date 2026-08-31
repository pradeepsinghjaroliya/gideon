"""Standalone wake-word demo.

Opens the mic directly with its own minimal capture (doesn't depend on
`01-audio-io`) and prints a message with a timestamp whenever the
configured wake word is detected. Run: `python -m wake_word.listen_demo`.
"""

from __future__ import annotations

import argparse
import queue
import time

import numpy as np

from wake_word.detector import OpenWakeWordDetector


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Wake word listen demo")
    parser.add_argument("--model", default="modules/02-wake-word/models/hey_gideon.onnx")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args(argv)

    import sounddevice as sd

    sample_rate = 16000
    frame_ms = 30
    frame_samples = int(sample_rate * frame_ms / 1000)

    chunks: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, frames, time_info, status) -> None:
        chunks.put(np.asarray(indata)[:, 0].copy())

    detector = OpenWakeWordDetector(model=args.model, threshold=args.threshold)

    print(f"Listening for wake word '{args.model}' (threshold={args.threshold}). Ctrl+C to stop.")
    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
        callback=callback,
    ):
        try:
            while True:
                chunk = chunks.get()
                if detector.process_chunk(chunk):
                    print(f"WAKE WORD DETECTED at {time.strftime('%H:%M:%S')}")
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()

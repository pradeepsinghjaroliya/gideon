"""Standalone CLI: synthesize text and play it back directly.

Quick-and-dirty playback just for this module's own test - `01-audio-io`
owns playback in the integrated system. Run:
`python -m tts.speak_demo "some text to speak"`, or add `--voice <name>`
to try a voice other than the one in `config.yaml` (e.g. to compare
candidates before picking one) without editing the config file.
"""

from __future__ import annotations

import argparse
import time

from shared.config import load_config

from tts.engine import PiperEngine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="+", help="text to speak")
    parser.add_argument(
        "--voice",
        default=None,
        help="voice name to use instead of config.tts.voice, e.g. en_US-amy-medium",
    )
    args = parser.parse_args()

    text = " ".join(args.text)
    voice = args.voice or load_config().tts.voice

    print(f"loading voice {voice}...")
    engine = PiperEngine(voice=voice)

    start = time.monotonic()
    audio, sample_rate = engine.synthesize(text)
    elapsed = time.monotonic() - start
    print(f"synthesized {len(audio) / sample_rate:.2f}s of audio in {elapsed:.2f}s")

    import sounddevice as sd

    sd.play(audio, sample_rate)
    sd.wait()


if __name__ == "__main__":
    main()

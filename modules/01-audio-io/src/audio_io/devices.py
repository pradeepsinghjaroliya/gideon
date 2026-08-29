"""Device listing/resolution helpers, plus a small CLI.

Kept import-light: `sounddevice` (and the PortAudio system library it
wraps) is only imported inside functions that actually touch audio
hardware, so importing this module never fails on a machine without
PortAudio installed.
"""

from __future__ import annotations

import argparse


def resolve_device(value: str | int | None) -> int | str | None:
    """Turn a config value into what `sounddevice` expects.

    `None`/`"default"` -> `None` (use the system default device).
    A numeric string -> `int` (device index).
    Anything else -> passed through as a device name for substring match.
    """
    if value is None or value == "default":
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except ValueError:
        return value


def list_devices() -> None:
    import sounddevice as sd

    print(sd.query_devices())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m audio_io.devices")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-devices", help="list input/output audio devices")

    args = parser.parse_args(argv)
    if args.command == "list-devices":
        list_devices()


if __name__ == "__main__":
    main()

"""Tool for reading/setting the system's overall output volume.

Uses `wpctl` (WirePlumber's CLI) - the sound-server control actually
installed and running on this machine (PipeWire + wireplumber; no
`pactl`/PulseAudio-utils installed here). `@DEFAULT_AUDIO_SINK@` is
wpctl's special id for whatever the default output device currently is,
so this never has to hardcode a sink id/name. No root/polkit prompt is
needed - unlike `brightness_tool.py`, `wpctl` works fine as the plain
logged-in user.

This is independent of `gideon_volume_tool.py`'s software gain on
Gideon's own speech - this tool changes the OS-wide output level for
everything.
"""

from __future__ import annotations

import re
import subprocess
from typing import Callable

RunFn = Callable[..., "subprocess.CompletedProcess[str]"]

DEFAULT_SINK = "@DEFAULT_AUDIO_SINK@"
_VOLUME_RE = re.compile(r"Volume:\s*([0-9.]+)")


class SystemVolumeError(RuntimeError):
    """Raised when reading or setting the system volume fails."""


def _run_wpctl(args: list[str], *, run: RunFn = subprocess.run) -> "subprocess.CompletedProcess[str]":
    try:
        return run(["wpctl", *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemVolumeError(f"wpctl {' '.join(args)} failed: {exc}") from exc


def get_system_volume() -> int:
    """Return the current system output volume as a percentage (0-100). Returns 0 if muted."""
    result = _run_wpctl(["get-volume", DEFAULT_SINK])
    if "MUTED" in result.stdout:
        return 0
    match = _VOLUME_RE.search(result.stdout)
    if not match:
        raise SystemVolumeError(f"could not parse wpctl output: {result.stdout!r}")
    return round(float(match.group(1)) * 100)


def set_system_volume(percent: int) -> str:
    """Set the system's overall output volume to the given percentage (0-100). Also unmutes if muted."""
    if not 0 <= percent <= 100:
        raise SystemVolumeError("volume percent must be between 0 and 100")
    _run_wpctl(["set-volume", DEFAULT_SINK, f"{percent}%"])
    _run_wpctl(["set-mute", DEFAULT_SINK, "0"])
    return f"System volume set to {percent}%."

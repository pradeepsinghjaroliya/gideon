"""Tool for reading and setting laptop screen brightness.

`/sys/class/backlight/*/brightness` is only root-writable on this system
(no `video`-group udev rule is installed), and this project's rules
forbid the assistant from ever running `sudo` (see
`orchestrator.ollama_control`'s docstring). `xrandr --output --brightness`
is a non-starter too: it's an X11 gamma trick, not real backlight
control, and this session is Wayland.

Instead this uses systemd-logind's `Session.SetBrightness` D-Bus method
(via `busctl`, part of systemd - no extra dependency), which grants
brightness control to the currently active local session without a
polkit prompt and without root. `loginctl` has no `set-brightness`
subcommand on this systemd version, so `busctl call` is used directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

RunFn = Callable[..., "subprocess.CompletedProcess[str]"]

BACKLIGHT_ROOT = Path("/sys/class/backlight")


class BrightnessError(RuntimeError):
    """Raised when reading or setting screen brightness fails."""


def _find_backlight_device(root: Path | None = None) -> Path:
    root = BACKLIGHT_ROOT if root is None else root
    try:
        devices = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as exc:
        raise BrightnessError(f"no backlight device found: {exc}") from exc
    if not devices:
        raise BrightnessError(f"no backlight device found under {root}")
    return devices[0]


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError) as exc:
        raise BrightnessError(f"failed to read {path}: {exc}") from exc


def _active_session_id(*, run: RunFn = subprocess.run) -> str:
    try:
        result = run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BrightnessError(f"failed to list login sessions: {exc}") from exc
    for line in result.stdout.splitlines():
        fields = line.split()
        if "active" in fields:
            return fields[0]
    raise BrightnessError("no active login session found")


def _set_via_logind(
    subsystem: str,
    device_name: str,
    value: int,
    *,
    run: RunFn = subprocess.run,
) -> None:
    session_id = _active_session_id(run=run)
    session_path = f"/org/freedesktop/login1/session/{session_id}"
    try:
        run(
            [
                "busctl",
                "call",
                "org.freedesktop.login1",
                session_path,
                "org.freedesktop.login1.Session",
                "SetBrightness",
                "ssu",
                subsystem,
                device_name,
                str(value),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BrightnessError(f"failed to set brightness via logind: {exc}") from exc


def get_screen_brightness() -> int:
    """Return the current screen brightness as a percentage (0-100)."""
    device = _find_backlight_device()
    current = _read_int(device / "brightness")
    maximum = _read_int(device / "max_brightness")
    return round(current / maximum * 100)


def set_screen_brightness(percent: int) -> str:
    """Set the screen brightness to the given percentage (0-100)."""
    if not 0 <= percent <= 100:
        raise BrightnessError("brightness percent must be between 0 and 100")
    device = _find_backlight_device()
    maximum = _read_int(device / "max_brightness")
    value = round(percent / 100 * maximum)
    _set_via_logind("backlight", device.name, value)
    return f"Screen brightness set to {percent}%."

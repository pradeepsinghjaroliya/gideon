"""Tool for reading/setting Gideon's own voice output volume.

This is the software gain already applied to synthesized speech before
playback (`orchestrator.state_machine.Orchestrator._apply_volume`,
exposed on the dashboard as the "Assistant voice volume" slider) -
independent of the system/output-device volume (see
`system_volume_tool.py` for that). Reached through `agentic.volume_bridge`,
which `orchestrator.main.main()` binds to the live `Orchestrator` at
startup, since this package has no reference to the running orchestrator
itself.
"""

from __future__ import annotations

from .. import volume_bridge


class GideonVolumeError(RuntimeError):
    """Raised when a Gideon-volume percentage is invalid."""


def get_gideon_volume() -> int:
    """Return Gideon's own voice output volume as a percentage (0-100), independent of the system volume."""
    return round(volume_bridge.get_volume() * 100)


def set_gideon_volume(percent: int) -> str:
    """Set Gideon's own voice output volume to the given percentage (0-100), independent of the system volume."""
    if not 0 <= percent <= 100:
        raise GideonVolumeError("volume percent must be between 0 and 100")
    volume_bridge.set_volume(percent / 100)
    return f"Assistant voice volume set to {percent}%."

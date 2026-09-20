"""One concrete example tool, proving the tool-call loop end-to-end.

Real device-control tools get added the same way later: a plain function
with a docstring (pydantic-ai turns both into the tool's schema/
description automatically) added to `TOOLS` in `registry.py`.
"""

from __future__ import annotations

from datetime import datetime


def get_current_datetime() -> str:
    """Return the current local date and time."""
    return datetime.now().strftime("%A, %B %d %Y %H:%M")

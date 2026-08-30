"""System tray icon exposing live quick-insight entries (LLM/mic status,
click to toggle), a "Dashboard..." item that opens the unified panel from
`dashboard.py`, and "Quit".

pystray and Tkinter each want to own an event loop, and Tkinter's must run
on the main thread - so the pystray icon runs in a background thread and
`run()` (called from the main thread) drives popup creation itself via a
request queue, instead of building the popup from the tray's own callback
thread.

No click-straight-to-a-panel shortcut is possible here: pystray's
AppIndicator backend (`pystray/_appindicator.py`) hardcodes
`HAS_DEFAULT_ACTION = False` with the comment "we expand the menu on
primary button click" - every click on the icon always opens the dropdown
menu, regardless of which item (if any) is marked `default=True`. This
mirrors the StatusNotifierItem/AppIndicator protocol itself, not just a
pystray choice - unlike the legacy X11 tray protocol (where a left click
could trigger a distinct default action from a separate right-click
context menu), GNOME's modern indicator protocol has no such distinction.
So the best available UX is putting the most-used item ("Dashboard...")
first in the menu, not skipping the menu entirely.

The "Ask..." popup and "Status / logs..." window that used to live here
were folded into `dashboard.py`'s unified 3-section panel (quick
buttons/log/ask box) - see that module's docstring - so this tray no
longer needs its own popup provider or standalone status window.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections import deque
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from text_input.dashboard import DashboardControl, DashboardSlider, build_dashboard_window

_DASHBOARD = object()
_QUIT = object()

_LOG_SIZE = 200

# grey/idle, green/listening, orange/processing, purple/speaking (matches
# the dashboard panel's own "active" pill color), red/error - covers the
# states `07-orchestrator/state_machine.py`'s `_set_status(..., state=...)`
# reports; an unrecognized state falls back to idle's grey rather than
# raising, since a stale/unknown state string shouldn't crash the tray.
_STATE_COLORS: dict[str, tuple[int, int, int, int]] = {
    "idle": (158, 158, 158, 255),
    "listening": (67, 176, 71, 255),
    "processing": (255, 152, 0, 255),
    "speaking": (124, 92, 255, 255),
    "error": (229, 57, 53, 255),
}
_DEFAULT_STATE = "idle"


def _make_icon_image(color: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Simple generated dot (colored by assistant state) so no icon asset
    needs to be bundled/shipped."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fill = color or _STATE_COLORS[_DEFAULT_STATE]
    ImageDraw.Draw(image).ellipse((4, 4, size - 4, size - 4), fill=fill)
    return image


def _quick_menu_item(control: DashboardControl) -> pystray.MenuItem:
    """One live-updating native menu entry mirroring a dashboard pill -
    pystray re-evaluates a callable `text`/`enabled` each time the menu is
    shown, so e.g. "LLM: Stopped" flips to "LLM: Running" without needing
    the menu to be rebuilt."""

    def click(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        control.on_click()

    return pystray.MenuItem(lambda item: control.get_label(), click, enabled=lambda item: control.is_enabled())


class TrayApp:
    def __init__(
        self,
        on_text: Callable[[str], None],
        icon: pystray.Icon | None = None,
        extra_menu_items: "list[pystray.MenuItem] | None" = None,
        dashboard_controls: list[DashboardControl] | None = None,
        quick_menu_controls: list[DashboardControl] | None = None,
        volume_control: DashboardSlider | None = None,
    ) -> None:
        """`quick_menu_controls` are rendered as native menu entries above
        "Dashboard..." (e.g. "LLM: Running", "Mic: On") - clicking one
        calls its `on_click`, same as the matching dashboard pill, so the
        most-checked state is visible/actionable without opening the full
        panel. `extra_menu_items` are inserted as plain native menu
        entries between those and "Dashboard...". `dashboard_controls`
        (plus `volume_control`) are handed to `dashboard.py`'s
        `build_dashboard_window` when "Dashboard..." is clicked - see its
        docstring for the panel's three sections. All three stay generic
        here rather than importing orchestrator specifics, so this module
        stays independently buildable/testable - `main.py` builds the
        `pystray.MenuItem`s/`DashboardControl`s/`DashboardSlider` and
        passes them in."""
        self._on_text = on_text
        self._dashboard_controls = dashboard_controls
        self._volume_control = volume_control
        self._requests: queue.Queue[object] = queue.Queue()
        self._log: deque[str] = deque(maxlen=_LOG_SIZE)

        quick_items = [_quick_menu_item(control) for control in (quick_menu_controls or [])]
        dashboard_item = (
            [pystray.MenuItem("Dashboard...", self._request_dashboard, default=True)] if dashboard_controls else []
        )
        self._icon = icon or pystray.Icon(
            "gideon",
            icon=_make_icon_image(),
            title="Gideon",
            menu=pystray.Menu(
                *quick_items,
                *dashboard_item,
                *(extra_menu_items or ()),
                pystray.MenuItem("Quit", self._request_quit),
            ),
        )

    def set_status(self, message: str) -> None:
        """Called by the orchestrator (from its own thread) on every state
        change, e.g. "Idle - waiting for the wake word", "Listening...".
        Appended to the log the dashboard's log section shows, and
        best-effort mirrored to the tray icon's tooltip - some tray
        backends (see `06-text-input/plan.md`'s AppIndicator notes) may not
        support a live tooltip, so a failure here must never take down the
        orchestrator's status reporting.
        """
        self._log.append(message)
        try:
            self._icon.title = f"Gideon - {message}"[:127]
        except Exception:
            pass

    def set_icon_state(self, state: str) -> None:
        """Recolors the tray dot per `_STATE_COLORS` - called by the
        orchestrator (from its own thread) alongside `set_status`, but
        with a symbolic state name instead of a free-text message, so this
        module never has to pattern-match human-readable status text.
        Wrapped in `try/except` for the same reason as `set_status`'s
        tooltip update - not every backend is guaranteed to support a live
        icon swap, and a failure here must never take down orchestrator
        status reporting."""
        try:
            self._icon.icon = _make_icon_image(_STATE_COLORS.get(state, _STATE_COLORS[_DEFAULT_STATE]))
        except Exception:
            pass

    def _request_dashboard(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._requests.put_nowait(_DASHBOARD)

    def _request_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._requests.put_nowait(_QUIT)

    def run(self) -> None:
        """Blocks the calling thread. Must be called from the main thread,
        since the popups it opens use Tkinter."""
        thread = threading.Thread(target=self._icon.run, daemon=True)
        thread.start()
        try:
            while True:
                request = self._requests.get()
                if request is _QUIT:
                    break
                elif request is _DASHBOARD:
                    self._show_dashboard_window()
        finally:
            self._icon.stop()

    def _show_dashboard_window(self) -> None:
        root = tk.Tk()
        build_dashboard_window(
            root,
            self._dashboard_controls or [],
            get_log_lines=lambda: list(self._log),
            on_ask=self._on_text,
            volume_control=self._volume_control,
        )
        root.mainloop()

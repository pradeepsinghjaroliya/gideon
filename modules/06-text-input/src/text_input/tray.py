"""System tray icon exposing "Ask..." (opens the text popup), "Status /
logs..." (shows recent orchestrator activity - see `set_status()`), and
"Quit".

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
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from text_input.dashboard import DashboardControl, build_dashboard_window
from text_input.popup import TkPopupProvider

_ASK = object()
_STATUS = object()
_DASHBOARD = object()
_QUIT = object()

_LOG_SIZE = 200


def _make_icon_image() -> Image.Image:
    """Simple generated dot so no icon asset needs to be bundled/shipped."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, size - 4, size - 4), fill=(30, 144, 255, 255))
    return image


@dataclass
class _StatusHandles:
    """Exposes the status window's widgets/callables directly, same
    testability pattern as `popup._PopupHandles`."""

    text_widget: tk.Text
    ask_entry: tk.Entry
    close: Callable[[], None]
    refresh: Callable[[], None]
    submit_ask: Callable[[], None]


def build_status_window(
    root: tk.Tk,
    get_log_lines: Callable[[], list[str]],
    on_ask: Callable[[str], None],
    refresh_ms: int = 500,
) -> _StatusHandles:
    """`get_log_lines` is called again on every refresh tick (not just once
    at open) - the window otherwise looked "stuck" on whatever state was
    current when it was opened, since a static snapshot never shows later
    state changes for as long as the window stays open.

    Also includes its own ask box (`on_ask`, the same callback the "Ask..."
    popup uses) - without it, this window and the "Ask..." popup were
    mutually exclusive, since `TrayApp.run()` only ever has one Tk window
    open at a time: leaving Status open blocked "Ask..." from ever being
    processed until Status was closed.
    """
    root.title("Gideon status")
    root.attributes("-topmost", True)

    text_widget = tk.Text(root, width=70, height=16)
    text_widget.pack(padx=10, pady=10)

    ask_frame = tk.Frame(root)
    ask_frame.pack(padx=10, pady=(0, 10), fill="x")
    ask_entry = tk.Entry(ask_frame, width=55)
    ask_entry.pack(side="left", fill="x", expand=True)

    def submit_ask(event: object = None) -> None:
        text = ask_entry.get().strip()
        if text:
            ask_entry.delete(0, "end")
            on_ask(text)

    ask_entry.bind("<Return>", submit_ask)
    tk.Button(ask_frame, text="Ask", command=submit_ask).pack(side="left", padx=(5, 0))

    pending_after_id: list[str | None] = [None]

    def render() -> None:
        lines = get_log_lines()
        content = "\n".join(lines) if lines else "(no activity yet)"
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", content)
        text_widget.see("end")
        text_widget.configure(state="disabled")

    def refresh() -> None:
        render()
        pending_after_id[0] = root.after(refresh_ms, refresh)

    def close() -> None:
        if pending_after_id[0] is not None:
            root.after_cancel(pending_after_id[0])
        root.destroy()

    render()
    pending_after_id[0] = root.after(refresh_ms, refresh)

    tk.Button(root, text="Close", command=close).pack(pady=(0, 10))
    root.protocol("WM_DELETE_WINDOW", close)

    return _StatusHandles(
        text_widget=text_widget, ask_entry=ask_entry, close=close, refresh=refresh, submit_ask=submit_ask
    )


class TrayApp:
    def __init__(
        self,
        on_text: Callable[[str], None],
        provider: TkPopupProvider | None = None,
        icon: pystray.Icon | None = None,
        extra_menu_items: "list[pystray.MenuItem] | None" = None,
        dashboard_controls: list[DashboardControl] | None = None,
    ) -> None:
        """`extra_menu_items` are inserted as plain native menu entries
        between "Ask..." and "Status / logs...". `dashboard_controls`
        instead adds a single "Dashboard..." entry - first in the menu,
        since it's the most-used entry point and every click on the tray
        icon opens this same dropdown regardless (see the class docstring
        below on why there's no way to skip straight to it) - that opens a
        custom panel of pill-shaped toggle/action buttons (see
        `dashboard.py`) for state a native OS menu can't render nicely
        (e.g. `07-orchestrator/main.py`'s mic mute / online-offline / LLM
        running / stop-speaking controls). Both are kept generic here
        rather than importing orchestrator specifics, so this module stays
        independently buildable/testable - `main.py` builds the
        `pystray.MenuItem`s/`DashboardControl`s and passes them in."""
        self._on_text = on_text
        self._provider = provider or TkPopupProvider()
        self._dashboard_controls = dashboard_controls
        self._requests: queue.Queue[object] = queue.Queue()
        self._log: deque[str] = deque(maxlen=_LOG_SIZE)
        dashboard_item = (
            [pystray.MenuItem("Dashboard...", self._request_dashboard)] if dashboard_controls else []
        )
        self._icon = icon or pystray.Icon(
            "gideon",
            icon=_make_icon_image(),
            title="Gideon",
            menu=pystray.Menu(
                *dashboard_item,
                pystray.MenuItem("Ask...", self._request_ask),
                *(extra_menu_items or ()),
                pystray.MenuItem("Status / logs...", self._request_status),
                pystray.MenuItem("Quit", self._request_quit),
            ),
        )

    def set_status(self, message: str) -> None:
        """Called by the orchestrator (from its own thread) on every state
        change, e.g. "Idle - waiting for the wake word", "Listening...".
        Appended to the log the "Status / logs..." window shows, and
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

    def _request_ask(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._requests.put_nowait(_ASK)

    def _request_status(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._requests.put_nowait(_STATUS)

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
                elif request is _STATUS:
                    self._show_status_window()
                elif request is _DASHBOARD:
                    self._show_dashboard_window()
                elif request is _ASK:
                    text = self._provider.get_text()
                    if text is not None:
                        self._on_text(text)
        finally:
            self._icon.stop()

    def _show_status_window(self) -> None:
        root = tk.Tk()
        build_status_window(root, lambda: list(self._log), on_ask=self._on_text)
        root.mainloop()

    def _show_dashboard_window(self) -> None:
        root = tk.Tk()
        build_dashboard_window(root, self._dashboard_controls or [])
        root.mainloop()

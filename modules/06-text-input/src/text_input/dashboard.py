"""Custom dark "quick settings"-style panel opened from the tray's
"Dashboard..." item - a grid of pill-shaped toggle/action buttons, since a
native tray menu can't render anything like this (the user's ask: a panel
similar to GNOME's quick-settings tray popup).

Kept orchestrator-agnostic like the rest of this module: callers hand in
plain `DashboardControl`s (a label/state/click callable each) rather than
this module knowing anything about mic/LLM/online state itself.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Callable

_BG = "#1e1e1e"
_PILL_OFF = "#3a3a3a"
_PILL_ON = "#7c5cff"
_PILL_DISABLED = "#2a2a2a"
_TEXT_ENABLED = "#ffffff"
_TEXT_DISABLED = "#777777"

_PILL_WIDTH = 190
_PILL_HEIGHT = 50
_PAD = 12
_COLUMNS = 2


@dataclass
class DashboardControl:
    """One pill button. `get_label`/`is_active`/`is_enabled` are called
    fresh on every render, so the panel reflects live state (e.g. "Stop
    speaking" greying out once playback ends) without needing to be closed
    and reopened."""

    get_label: Callable[[], str]
    on_click: Callable[[], None]
    is_active: Callable[[], bool] = lambda: False
    is_enabled: Callable[[], bool] = lambda: True


@dataclass
class _DashboardHandles:
    canvas: tk.Canvas
    close: Callable[[], None]
    refresh: Callable[[], None]


def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs):
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def build_dashboard_window(
    root: tk.Tk,
    controls: list[DashboardControl],
    refresh_ms: int = 500,
) -> _DashboardHandles:
    root.title("Gideon dashboard")
    root.attributes("-topmost", True)
    root.configure(bg=_BG)

    rows = (len(controls) + _COLUMNS - 1) // _COLUMNS
    width = _PAD + _COLUMNS * (_PILL_WIDTH + _PAD)
    height = _PAD + rows * (_PILL_HEIGHT + _PAD)
    canvas = tk.Canvas(root, width=width, height=height, bg=_BG, highlightthickness=0)
    canvas.pack()

    pending_after_id: list[str | None] = [None]

    def render() -> None:
        canvas.delete("all")
        for index, control in enumerate(controls):
            col = index % _COLUMNS
            row = index // _COLUMNS
            x0 = _PAD + col * (_PILL_WIDTH + _PAD)
            y0 = _PAD + row * (_PILL_HEIGHT + _PAD)
            x1, y1 = x0 + _PILL_WIDTH, y0 + _PILL_HEIGHT

            enabled = control.is_enabled()
            active = control.is_active()
            if not enabled:
                fill, text_color = _PILL_DISABLED, _TEXT_DISABLED
            elif active:
                fill, text_color = _PILL_ON, _TEXT_ENABLED
            else:
                fill, text_color = _PILL_OFF, _TEXT_ENABLED

            pill_id = _round_rect(canvas, x0, y0, x1, y1, radius=_PILL_HEIGHT / 2, fill=fill, outline=fill)
            text_id = canvas.create_text(
                (x0 + x1) / 2, (y0 + y1) / 2, text=control.get_label(), fill=text_color,
                font=("Sans", 10, "bold"),
            )
            if enabled:
                handler = _make_click_handler(control)
                canvas.tag_bind(pill_id, "<Button-1>", handler)
                canvas.tag_bind(text_id, "<Button-1>", handler)

    def refresh() -> None:
        render()
        pending_after_id[0] = root.after(refresh_ms, refresh)

    def close() -> None:
        if pending_after_id[0] is not None:
            root.after_cancel(pending_after_id[0])
        root.destroy()

    render()
    pending_after_id[0] = root.after(refresh_ms, refresh)
    root.protocol("WM_DELETE_WINDOW", close)

    return _DashboardHandles(canvas=canvas, close=close, refresh=refresh)


def _make_click_handler(control: DashboardControl) -> Callable[[object], None]:
    def handler(event: object) -> None:
        control.on_click()

    return handler

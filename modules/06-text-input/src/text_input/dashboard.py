"""Custom dark "quick settings"-style panel opened from the tray's
"Dashboard..." item - a single window with three stacked sections (the
user's ask, see root `tmp.md` cleared into this writeup): a grid of
pill-shaped toggle/action buttons on top (same panel this module always
had, similar to GNOME's quick-settings tray popup), the activity log in
the middle, and a manual ask box on the bottom - so the tray menu no
longer needs separate "Ask..."/"Status / logs..." entries, see
`tray.py`'s docstring.

Kept orchestrator-agnostic like the rest of this module: callers hand in
plain `DashboardControl`s (a label/state/click callable each) and an
optional `DashboardSlider` rather than this module knowing anything about
mic/LLM/online/volume state itself.
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
class DashboardSlider:
    """One horizontal slider, e.g. the assistant's speaking volume.
    `get_value`/`on_change` work in a normalized `0.0`-`1.0` range
    regardless of the widget's own `0`-`100` scale, so callers don't need
    to know Tkinter `Scale` conventions."""

    label: str
    get_value: Callable[[], float]
    on_change: Callable[[float], None]


@dataclass
class _DashboardHandles:
    """`text_widget`/`ask_entry`/`submit_ask`/`volume_scale` are `None`
    when the corresponding section wasn't requested (`get_log_lines`/
    `on_ask`/`volume_control` respectively not given) - same optional-
    section pattern as the rest of this dataclass being test-driven
    directly instead of through real clicks/mainloop."""

    canvas: tk.Canvas
    close: Callable[[], None]
    refresh: Callable[[], None]
    text_widget: tk.Text | None = None
    ask_entry: tk.Entry | None = None
    submit_ask: Callable[[], None] | None = None
    volume_scale: tk.Scale | None = None


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
    get_log_lines: Callable[[], list[str]] | None = None,
    on_ask: Callable[[str], None] | None = None,
    volume_control: DashboardSlider | None = None,
    refresh_ms: int = 500,
) -> _DashboardHandles:
    """Three stacked sections, each independently optional so existing
    pill-only callers (and this module's own pill-focused tests) keep
    working unchanged:

    - top: the pill grid (`controls`) plus, if `volume_control` is given,
      a labeled slider underneath it.
    - middle: a read-only log view, if `get_log_lines` is given - same
      "re-read the source every refresh tick, not just at open" fix as
      the old standalone status window (a static snapshot looked "stuck"
      on whatever was current when the window opened).
    - bottom: a single-line ask box, if `on_ask` is given - lets the user
      type a follow-up without leaving the dashboard.
    """
    root.title("Gideon dashboard")
    root.attributes("-topmost", True)
    root.configure(bg=_BG)

    top_frame = tk.Frame(root, bg=_BG)
    top_frame.pack(padx=_PAD, pady=(_PAD, 0))

    rows = (len(controls) + _COLUMNS - 1) // _COLUMNS
    width = _PAD + _COLUMNS * (_PILL_WIDTH + _PAD)
    height = _PAD + rows * (_PILL_HEIGHT + _PAD)
    canvas = tk.Canvas(top_frame, width=width, height=height, bg=_BG, highlightthickness=0)
    canvas.pack()

    volume_scale = None
    if volume_control is not None:
        tk.Label(
            top_frame, text=volume_control.label, bg=_BG, fg=_TEXT_ENABLED, font=("Sans", 9),
        ).pack(pady=(_PAD, 0))
        volume_scale = tk.Scale(
            top_frame,
            from_=0,
            to=100,
            orient="horizontal",
            length=width - _PAD,
            bg=_BG,
            fg=_TEXT_ENABLED,
            troughcolor=_PILL_OFF,
            highlightthickness=0,
            command=lambda raw: volume_control.on_change(int(float(raw)) / 100),
        )
        volume_scale.set(round(volume_control.get_value() * 100))
        volume_scale.pack(pady=(0, _PAD))

    text_widget = None
    if get_log_lines is not None:
        text_widget = tk.Text(
            root, width=44, height=10, bg="#141414", fg=_TEXT_ENABLED, insertbackground=_TEXT_ENABLED,
        )
        text_widget.pack(padx=_PAD, pady=(_PAD, 0))

    ask_entry = None
    submit_ask = None
    if on_ask is not None:
        ask_frame = tk.Frame(root, bg=_BG)
        ask_frame.pack(padx=_PAD, pady=(_PAD, 0), fill="x")
        ask_entry = tk.Entry(ask_frame, width=34)
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

        if text_widget is not None:
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
    tk.Button(root, text="Close", command=close).pack(pady=_PAD)
    root.protocol("WM_DELETE_WINDOW", close)

    return _DashboardHandles(
        canvas=canvas,
        close=close,
        refresh=refresh,
        text_widget=text_widget,
        ask_entry=ask_entry,
        submit_ask=submit_ask,
        volume_scale=volume_scale,
    )


def _make_click_handler(control: DashboardControl) -> Callable[[object], None]:
    def handler(event: object) -> None:
        control.on_click()

    return handler

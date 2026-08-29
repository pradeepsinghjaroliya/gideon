import tkinter as tk

import pytest

from text_input.tray import build_status_window


def _requires_display() -> None:
    try:
        root = tk.Tk()
        root.destroy()
    except tk.TclError as exc:
        pytest.skip(f"no display available for Tkinter: {exc}")


def _noop(text: str) -> None:
    pass


def test_shows_log_lines_in_order():
    _requires_display()
    root = tk.Tk()

    handles = build_status_window(root, lambda: ["Idle - waiting", "Listening..."], on_ask=_noop)

    assert handles.text_widget.get("1.0", "end-1c") == "Idle - waiting\nListening..."
    handles.close()


def test_shows_placeholder_when_no_activity_yet():
    _requires_display()
    root = tk.Tk()

    handles = build_status_window(root, lambda: [], on_ask=_noop)

    assert handles.text_widget.get("1.0", "end-1c") == "(no activity yet)"
    handles.close()


def test_close_destroys_the_window():
    _requires_display()
    root = tk.Tk()
    handles = build_status_window(root, lambda: [], on_ask=_noop)

    handles.close()

    with pytest.raises(tk.TclError):
        root.winfo_exists()


def test_refresh_picks_up_new_log_lines():
    """Regression test: the window used to render a static snapshot taken
    at open time, so it looked "stuck" on whatever state was current when
    a user opened it and then left it open - it never showed later state
    changes. `refresh()` (also scheduled periodically via `root.after`)
    must re-read the log source each time it runs."""
    _requires_display()
    root = tk.Tk()
    log: list[str] = ["Idle - waiting"]

    handles = build_status_window(root, lambda: log, on_ask=_noop)
    assert handles.text_widget.get("1.0", "end-1c") == "Idle - waiting"

    log.append("Listening - recording your question")
    handles.refresh()

    assert handles.text_widget.get("1.0", "end-1c") == "Idle - waiting\nListening - recording your question"
    handles.close()


def test_ask_box_submits_text_and_clears_entry():
    """Regression test: with only the separate "Ask..." popup, leaving
    Status open blocked "Ask..." from ever being handled (`TrayApp.run()`
    only drives one Tk window at a time) - so Status now has its own ask
    box wired to the same callback."""
    _requires_display()
    root = tk.Tk()
    received: list[str] = []

    handles = build_status_window(root, lambda: [], on_ask=received.append)
    handles.ask_entry.insert(0, "what's the weather")
    handles.submit_ask()

    assert received == ["what's the weather"]
    assert handles.ask_entry.get() == ""
    handles.close()


def test_ask_box_ignores_blank_submission():
    _requires_display()
    root = tk.Tk()
    received: list[str] = []

    handles = build_status_window(root, lambda: [], on_ask=received.append)
    handles.ask_entry.insert(0, "   ")
    handles.submit_ask()

    assert received == []
    handles.close()

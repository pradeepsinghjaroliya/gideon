import tkinter as tk

import pytest

from text_input.popup import TkPopupProvider


def _requires_display() -> None:
    try:
        root = tk.Tk()
        root.destroy()
    except tk.TclError as exc:
        pytest.skip(f"no display available for Tkinter: {exc}")


def test_submit_returns_typed_text():
    _requires_display()
    root = tk.Tk()
    handles = TkPopupProvider()._build(root)

    handles.entry.insert(0, "what's the weather today")
    handles.submit()

    assert handles.result() == "what's the weather today"
    with pytest.raises(tk.TclError):
        root.winfo_exists()  # destroyed by submit()


def test_cancel_returns_none():
    _requires_display()
    root = tk.Tk()
    handles = TkPopupProvider()._build(root)

    handles.cancel()

    assert handles.result() is None
    with pytest.raises(tk.TclError):
        root.winfo_exists()  # destroyed by cancel()


def test_submit_with_blank_text_returns_none():
    _requires_display()
    root = tk.Tk()
    handles = TkPopupProvider()._build(root)

    handles.entry.insert(0, "   ")
    handles.submit()

    assert handles.result() is None


def test_full_mainloop_roundtrip_via_scheduled_submit():
    """Unlike the tests above (which call submit()/cancel() directly), this
    drives the real Tk event loop - `get_text()` itself just wraps
    `_build()` + `mainloop()`, so this is the closest thing to an actual
    user typing and hitting Enter that a headless test can do."""
    _requires_display()
    root = tk.Tk()
    handles = TkPopupProvider()._build(root)

    def auto_submit() -> None:
        handles.entry.insert(0, "auto typed text")
        handles.submit()

    root.after(50, auto_submit)
    root.after(3000, root.destroy)  # safety net so a bug can't hang the suite
    root.mainloop()

    assert handles.result() == "auto typed text"

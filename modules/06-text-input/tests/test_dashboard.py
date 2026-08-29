import tkinter as tk

import pytest

from text_input.dashboard import DashboardControl, _make_click_handler, build_dashboard_window


def _requires_display() -> None:
    try:
        root = tk.Tk()
        root.destroy()
    except tk.TclError as exc:
        pytest.skip(f"no display available for Tkinter: {exc}")


def _control(label="Mic: On", active=False, enabled=True, on_click=None):
    return DashboardControl(
        get_label=lambda: label,
        on_click=on_click or (lambda: None),
        is_active=lambda: active,
        is_enabled=lambda: enabled,
    )


def test_renders_two_canvas_items_per_control():
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(root, [_control("Mic: On"), _control("LLM: Stopped")])

    assert len(handles.canvas.find_withtag("all")) == 4  # pill + text, x2 controls
    handles.close()


def test_active_control_uses_on_color_inactive_uses_off_color():
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(root, [_control(active=True), _control(active=False)])

    pill_ids = [item for item in handles.canvas.find_withtag("all") if handles.canvas.type(item) == "polygon"]
    fills = [handles.canvas.itemcget(item, "fill") for item in pill_ids]

    assert fills[0] == "#7c5cff"
    assert fills[1] == "#3a3a3a"
    handles.close()


def test_disabled_control_has_no_click_binding():
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(root, [_control(enabled=False)])

    pill_id = next(item for item in handles.canvas.find_withtag("all") if handles.canvas.type(item) == "polygon")
    assert handles.canvas.tag_bind(pill_id, "<Button-1>") == ""
    handles.close()


def test_enabled_control_has_a_click_binding():
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(root, [_control(enabled=True)])
    pill_id = next(item for item in handles.canvas.find_withtag("all") if handles.canvas.type(item) == "polygon")

    assert handles.canvas.tag_bind(pill_id, "<Button-1>") != ""
    handles.close()


def test_click_handler_calls_the_control_on_click():
    clicks = []
    control = _control(on_click=lambda: clicks.append("clicked"))

    _make_click_handler(control)(event=None)

    assert clicks == ["clicked"]


def test_refresh_reflects_updated_state():
    _requires_display()
    root = tk.Tk()
    state = {"active": False, "label": "Mic: Off"}

    handles = build_dashboard_window(
        root,
        [DashboardControl(get_label=lambda: state["label"], on_click=lambda: None, is_active=lambda: state["active"])],
    )

    state["active"] = True
    state["label"] = "Mic: On"
    handles.refresh()

    text_id = next(item for item in handles.canvas.find_withtag("all") if handles.canvas.type(item) == "text")
    pill_id = next(item for item in handles.canvas.find_withtag("all") if handles.canvas.type(item) == "polygon")
    assert handles.canvas.itemcget(text_id, "text") == "Mic: On"
    assert handles.canvas.itemcget(pill_id, "fill") == "#7c5cff"
    handles.close()


def test_close_destroys_the_window():
    _requires_display()
    root = tk.Tk()
    handles = build_dashboard_window(root, [_control()])

    handles.close()

    with pytest.raises(tk.TclError):
        root.winfo_exists()

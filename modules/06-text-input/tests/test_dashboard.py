import tkinter as tk

import pytest

from text_input.dashboard import DashboardControl, DashboardSlider, _make_click_handler, build_dashboard_window


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


def test_log_and_ask_sections_absent_by_default():
    """Both sections are opt-in - pill-only callers (and the tests above)
    shouldn't get a log/ask box they never asked for."""
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(root, [_control()])

    assert handles.text_widget is None
    assert handles.ask_entry is None
    assert handles.submit_ask is None
    handles.close()


def test_log_section_shows_lines_in_order():
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(
        root, [_control()], get_log_lines=lambda: ["Idle - waiting", "Listening..."],
    )

    assert handles.text_widget.get("1.0", "end-1c") == "Idle - waiting\nListening..."
    handles.close()


def test_log_section_shows_placeholder_when_no_activity_yet():
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(root, [_control()], get_log_lines=lambda: [])

    assert handles.text_widget.get("1.0", "end-1c") == "(no activity yet)"
    handles.close()


def test_log_section_refresh_picks_up_new_log_lines():
    """Regression coverage carried over from the old standalone status
    window: a static snapshot taken at open time looked "stuck" on
    whatever was current when a user opened it and left it open -
    `refresh()` must re-read the log source every tick."""
    _requires_display()
    root = tk.Tk()
    log: list[str] = ["Idle - waiting"]

    handles = build_dashboard_window(root, [_control()], get_log_lines=lambda: log)
    assert handles.text_widget.get("1.0", "end-1c") == "Idle - waiting"

    log.append("Listening - recording your question")
    handles.refresh()

    assert handles.text_widget.get("1.0", "end-1c") == "Idle - waiting\nListening - recording your question"
    handles.close()


def test_ask_box_submits_text_and_clears_entry():
    _requires_display()
    root = tk.Tk()
    received: list[str] = []

    handles = build_dashboard_window(root, [_control()], on_ask=received.append)
    handles.ask_entry.insert(0, "what's the weather")
    handles.submit_ask()

    assert received == ["what's the weather"]
    assert handles.ask_entry.get() == ""
    handles.close()


def test_ask_box_ignores_blank_submission():
    _requires_display()
    root = tk.Tk()
    received: list[str] = []

    handles = build_dashboard_window(root, [_control()], on_ask=received.append)
    handles.ask_entry.insert(0, "   ")
    handles.submit_ask()

    assert received == []
    handles.close()


def test_volume_slider_absent_by_default():
    _requires_display()
    root = tk.Tk()

    handles = build_dashboard_window(root, [_control()])

    assert handles.volume_scale is None
    handles.close()


def test_volume_slider_initialized_from_get_value():
    _requires_display()
    root = tk.Tk()
    slider = DashboardSlider(label="Assistant voice volume", get_value=lambda: 0.4, on_change=lambda value: None)

    handles = build_dashboard_window(root, [_control()], volume_control=slider)

    assert handles.volume_scale.get() == 40
    handles.close()


def test_moving_volume_slider_calls_on_change_with_normalized_value():
    _requires_display()
    root = tk.Tk()
    received: list[float] = []
    slider = DashboardSlider(label="Assistant voice volume", get_value=lambda: 1.0, on_change=received.append)

    handles = build_dashboard_window(root, [_control()], volume_control=slider)
    handles.volume_scale.set(25)
    root.update()

    assert received[-1] == 0.25
    handles.close()

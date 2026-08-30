import pystray

from text_input.dashboard import DashboardControl
from text_input.tray import TrayApp


class FakeIcon:
    """Stands in for pystray.Icon - TrayApp drives everything through its
    own request queue, so the icon's `run()` doesn't need a real tray."""

    def __init__(self) -> None:
        self.stopped = False

    def run(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


def test_quit_stops_icon():
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, icon=icon)

    app._request_quit(icon, None)
    app.run()

    assert icon.stopped


def test_set_status_appends_to_log_and_updates_icon_title():
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, icon=icon)

    app.set_status("Idle - waiting for the wake word")
    app.set_status("Listening - recording your question")

    assert list(app._log) == [
        "Idle - waiting for the wake word",
        "Listening - recording your question",
    ]
    assert icon.title == "Gideon - Listening - recording your question"


def test_set_status_survives_icon_title_failure():
    class BrokenTitleIcon(FakeIcon):
        @property
        def title(self):
            return self._title

        @title.setter
        def title(self, value):
            raise RuntimeError("backend doesn't support live title updates")

    app = TrayApp(on_text=lambda text: None, icon=BrokenTitleIcon())

    app.set_status("Idle")  # must not raise

    assert list(app._log) == ["Idle"]


def test_set_icon_state_updates_icon_to_the_state_color():
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, icon=icon)

    app.set_icon_state("listening")

    assert icon.icon is not None
    assert icon.icon.getpixel((32, 32))[:3] == (67, 176, 71)


def test_set_icon_state_falls_back_to_idle_color_for_unknown_state():
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, icon=icon)

    app.set_icon_state("not-a-real-state")

    assert icon.icon.getpixel((32, 32))[:3] == (158, 158, 158)


def test_set_icon_state_survives_icon_assignment_failure():
    class BrokenIconIcon(FakeIcon):
        @property
        def icon(self):
            return self._icon

        @icon.setter
        def icon(self, value):
            raise RuntimeError("backend doesn't support live icon updates")

    app = TrayApp(on_text=lambda text: None, icon=BrokenIconIcon())

    app.set_icon_state("speaking")  # must not raise


def test_extra_menu_items_inserted_before_quit():
    extra = pystray.MenuItem("Mute mic", lambda icon, item: None)
    app = TrayApp(on_text=lambda text: None, extra_menu_items=[extra])

    labels = [item.text for item in app._icon.menu]

    assert labels == ["Mute mic", "Quit"]


def test_no_dashboard_menu_item_when_no_dashboard_controls_given():
    app = TrayApp(on_text=lambda text: None)

    labels = [item.text for item in app._icon.menu]

    assert "Dashboard..." not in labels


def test_dashboard_menu_item_present_when_controls_given():
    control = DashboardControl(get_label=lambda: "Mic: On", on_click=lambda: None)
    app = TrayApp(on_text=lambda text: None, dashboard_controls=[control])

    labels = [item.text for item in app._icon.menu]

    assert labels == ["Dashboard...", "Quit"]


def test_dashboard_request_opens_dashboard_window():
    calls = []
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, icon=icon)
    app._show_dashboard_window = lambda: calls.append(True)

    app._request_dashboard(icon, None)
    app._request_quit(icon, None)
    app.run()

    assert calls == [True]


def test_quick_menu_controls_render_as_live_native_items():
    state = {"label": "Mic: On"}
    clicks = []
    control = DashboardControl(get_label=lambda: state["label"], on_click=lambda: clicks.append("clicked"))
    app = TrayApp(on_text=lambda text: None, quick_menu_controls=[control])

    item = next(iter(app._icon.menu))
    assert str(item) == "Mic: On"

    item(app._icon)
    assert clicks == ["clicked"]

    state["label"] = "Mic: Muted"
    assert str(item) == "Mic: Muted"


def test_quick_menu_items_come_before_dashboard_and_quit():
    control = DashboardControl(get_label=lambda: "LLM: Running", on_click=lambda: None)
    dashboard_control = DashboardControl(get_label=lambda: "Stop speaking", on_click=lambda: None)
    app = TrayApp(
        on_text=lambda text: None,
        quick_menu_controls=[control],
        dashboard_controls=[dashboard_control],
    )

    labels = [str(item) for item in app._icon.menu]

    assert labels == ["LLM: Running", "Dashboard...", "Quit"]

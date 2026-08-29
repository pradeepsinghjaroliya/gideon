import pystray

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


class FakeProvider:
    def __init__(self, texts: list[str | None]) -> None:
        self._texts = list(texts)

    def get_text(self) -> str | None:
        return self._texts.pop(0)


def test_ask_then_quit_calls_on_text_and_stops_icon():
    received: list[str] = []
    icon = FakeIcon()
    app = TrayApp(on_text=received.append, provider=FakeProvider(["hello"]), icon=icon)

    app._request_ask(icon, None)
    app._request_quit(icon, None)
    app.run()

    assert received == ["hello"]
    assert icon.stopped


def test_cancelled_popup_does_not_call_on_text():
    received: list[str] = []
    icon = FakeIcon()
    app = TrayApp(on_text=received.append, provider=FakeProvider([None]), icon=icon)

    app._request_ask(icon, None)
    app._request_quit(icon, None)
    app.run()

    assert received == []


def test_multiple_asks_processed_in_order():
    received: list[str] = []
    icon = FakeIcon()
    app = TrayApp(on_text=received.append, provider=FakeProvider(["first", "second"]), icon=icon)

    app._request_ask(icon, None)
    app._request_ask(icon, None)
    app._request_quit(icon, None)
    app.run()

    assert received == ["first", "second"]


def test_set_status_appends_to_log_and_updates_icon_title():
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, provider=FakeProvider([]), icon=icon)

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

    app = TrayApp(on_text=lambda text: None, provider=FakeProvider([]), icon=BrokenTitleIcon())

    app.set_status("Idle")  # must not raise

    assert list(app._log) == ["Idle"]


def test_status_request_opens_status_window():
    calls: list[list[str]] = []
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, provider=FakeProvider([]), icon=icon)
    app._show_status_window = lambda: calls.append(list(app._log))
    app.set_status("Idle - waiting for the wake word")

    app._request_status(icon, None)
    app._request_quit(icon, None)
    app.run()

    assert calls == [["Idle - waiting for the wake word"]]


def test_extra_menu_items_inserted_between_ask_and_status():
    extra = pystray.MenuItem("Mute mic", lambda icon, item: None)
    app = TrayApp(on_text=lambda text: None, extra_menu_items=[extra])

    labels = [item.text for item in app._icon.menu]

    assert labels == ["Ask...", "Mute mic", "Status / logs...", "Quit"]


def test_no_dashboard_menu_item_when_no_dashboard_controls_given():
    app = TrayApp(on_text=lambda text: None)

    labels = [item.text for item in app._icon.menu]

    assert "Dashboard..." not in labels


def test_dashboard_menu_item_present_when_controls_given():
    from text_input.dashboard import DashboardControl

    control = DashboardControl(get_label=lambda: "Mic: On", on_click=lambda: None)
    app = TrayApp(on_text=lambda text: None, dashboard_controls=[control])

    labels = [item.text for item in app._icon.menu]

    assert labels == ["Dashboard...", "Ask...", "Status / logs...", "Quit"]


def test_dashboard_request_opens_dashboard_window():
    calls = []
    icon = FakeIcon()
    app = TrayApp(on_text=lambda text: None, provider=FakeProvider([]), icon=icon)
    app._show_dashboard_window = lambda: calls.append(True)

    app._request_dashboard(icon, None)
    app._request_quit(icon, None)
    app.run()

    assert calls == [True]

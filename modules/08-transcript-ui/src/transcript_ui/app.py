"""Wires the socket server, the transcript model and the GTK view into one
runnable overlay process.

Run it with `python -m transcript_ui` (or let `07-orchestrator`'s `main.py`
start it automatically - see `transcript_ui.autostart` in config.yaml).
"""

from __future__ import annotations

import logging
import signal
from pathlib import Path

from shared.transcript import TranscriptEvent

from transcript_ui.model import TranscriptModel
from transcript_ui.server import AlreadyRunningError, TranscriptServer
from transcript_ui.surface import (
    GtkUnavailableError,
    load_gtk,
    detect_backend,
    reexec_under_x11,
    should_reexec_under_x11,
)
from transcript_ui.view import TranscriptView


class OverlayApp:
    def __init__(
        self,
        socket_path: Path | str | None = None,
        width: int = 720,
        bottom_margin: int = 48,
        max_turns: int = 4,
        typewriter_cps: float = 55.0,
        hide_after_seconds: float = 4.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._log = logger or logging.getLogger("transcript_ui")
        self._model = TranscriptModel(
            max_turns=max_turns,
            typewriter_cps=typewriter_cps,
            hide_after_seconds=hide_after_seconds,
        )
        # Claim the socket first: if another overlay already owns it this
        # raises `AlreadyRunningError` here, before any window exists, so
        # the duplicate exits with one clear log line instead of briefly
        # flashing a card on screen.
        self._server = TranscriptServer(self._dispatch, socket_path=socket_path, logger=self._log)
        self._server.bind()
        self._view = TranscriptView(
            self._model,
            width=width,
            bottom_margin=bottom_margin,
            on_ready=lambda backend: self._log.info("overlay ready on the %s backend", backend),
        )

    def _dispatch(self, event: TranscriptEvent) -> None:
        """Hand an event from the server's reader thread to the GTK main
        thread.

        `idle_add` is mandatory, not a nicety: GTK widgets may only be
        touched from the thread running the main loop, and calling into
        them from the socket reader would corrupt GTK's internal state in
        ways that surface as random crashes much later. Returning `False`
        from the closure makes it a one-shot callback rather than a
        repeating idle handler.
        """
        GLib = self._view.GLib
        GLib.idle_add(lambda: (self._view.handle_event(event), False)[1])

    def run(self) -> None:
        Gtk = self._view.Gtk
        GLib = self._view.GLib

        self._server.start()
        self._view.start()

        # `unix_signal_add` rather than `signal.signal`: a Python signal
        # handler only runs between bytecodes, and while GTK is blocked in
        # its main loop that can be an arbitrarily long wait. This routes
        # the signal through GLib's own loop so Ctrl-C and systemd's
        # SIGTERM both quit promptly.
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._quit)

        try:
            Gtk.main()
        finally:
            self._view.stop()
            self._server.stop()

    def _quit(self) -> bool:
        self._log.info("shutting down the transcript overlay")
        self._view.Gtk.main_quit()
        return False

    # Exposed for the demo/dev loop, which feeds scripted events in without
    # a socket round-trip.
    def inject(self, event: TranscriptEvent) -> None:
        self._dispatch(event)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m transcript_ui",
        description="Gideon's real-time conversation transcript overlay.",
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="unix socket to listen on (default: $XDG_RUNTIME_DIR/gideon-transcript.sock)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="replay a scripted conversation instead of waiting for the orchestrator - "
        "the dev loop for restyling the overlay with no mic, model or LLM running",
    )
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    from shared.logging_setup import setup_logging

    log = setup_logging("transcript_ui")
    if args.verbose:
        log.setLevel(logging.DEBUG)

    try:
        Gtk, Gdk, _GLib = load_gtk()
    except GtkUnavailableError as exc:
        log.error("%s", exc)
        return 1

    # Must happen before any window is built: on a Wayland compositor with
    # no layer-shell (GNOME, KDE) there is no way to position a window, so
    # restart onto XWayland where there is. See `surface.py`'s docstring.
    backend = detect_backend(Gdk)
    if should_reexec_under_x11(backend):
        reexec_under_x11([] if argv is None else list(argv))

    from shared.config import load_config

    config = load_config()
    ui = config.transcript_ui

    try:
        app = OverlayApp(
            socket_path=args.socket or ui.socket_path,
            width=ui.width,
            bottom_margin=ui.bottom_margin,
            max_turns=ui.max_turns,
            typewriter_cps=ui.typewriter_cps,
            hide_after_seconds=ui.hide_after_seconds,
            logger=log,
        )
    except AlreadyRunningError as exc:
        log.error("%s", exc)
        return 1

    if args.demo:
        from transcript_ui.demo import start_demo_thread

        start_demo_thread(app.inject)

    app.run()
    return 0

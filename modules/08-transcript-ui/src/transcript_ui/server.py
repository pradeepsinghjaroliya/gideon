"""Overlay-side end of the transcript link: a unix socket the orchestrator
connects to, decoded into `TranscriptEvent`s.

GTK-free on purpose. It takes a plain callback and calls it from a reader
thread; `app.py` is what wraps that callback in `GLib.idle_add` to hop onto
the GTK main thread. Keeping the marshalling out of here means the socket
handling - stale-socket recovery, reconnects, malformed input - can be
tested with no display and no event loop.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from typing import Callable

from shared.transcript import TranscriptEvent

from transcript_ui.protocol import default_socket_path, iter_events

# How long to wait when probing whether an existing socket file has a live
# listener behind it. Local and either instant or refused, so this only
# needs to be long enough to not false-negative under load.
_PROBE_TIMEOUT_SECONDS = 0.4


class AlreadyRunningError(RuntimeError):
    """Another overlay is already listening on this socket."""


class TranscriptServer:
    def __init__(
        self,
        on_event: Callable[[TranscriptEvent], None],
        socket_path: Path | str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._on_event = on_event
        self._socket_path = Path(socket_path) if socket_path is not None else default_socket_path()
        self._log = logger or logging.getLogger("transcript_ui.server")
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def bind(self) -> None:
        """Claim the socket. Raises `AlreadyRunningError` if a live overlay
        already owns it.

        Split from `start()` so a caller can find out whether it is allowed
        to run *before* building a GTK window - otherwise a second overlay
        maps a window on screen and only then discovers it has to exit,
        which the user sees as a flash.
        """
        if self._listener is not None:
            return
        self._prepare_socket_path()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self._socket_path))
        listener.listen(2)
        # Keeps `accept()` from blocking forever so the loop can notice
        # `stop()`; the alternative (shutting down the listener from another
        # thread to break the accept) behaves inconsistently across kernels.
        listener.settimeout(0.5)
        self._listener = listener
        self._log.info("transcript overlay listening on %s", self._socket_path)

    def start(self) -> None:
        """Begin accepting connections. Binds first if `bind()` was not
        already called, so either order works."""
        self.bind()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="transcript-server", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stopping.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        self._unlink_socket()

    # --- internals ---------------------------------------------------------

    def _prepare_socket_path(self) -> None:
        """Clear the way to bind.

        A unix socket file outlives the process that made it, so a crashed
        or SIGKILLed overlay leaves one behind and `bind()` would fail with
        EADDRINUSE forever after. Distinguishing "stale file" from "another
        overlay is genuinely running" is done the only reliable way: try to
        connect to it. A refused connection means nothing is listening and
        the file is safe to remove; a successful one means a real overlay
        owns it and this process should not start a second.
        """
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._socket_path.exists():
            return

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(_PROBE_TIMEOUT_SECONDS)
            probe.connect(str(self._socket_path))
        except OSError:
            self._log.info("removing stale transcript socket at %s", self._socket_path)
            self._unlink_socket()
            return
        finally:
            try:
                probe.close()
            except OSError:
                pass
        raise AlreadyRunningError(
            f"a transcript overlay is already running on {self._socket_path}"
        )

    def _unlink_socket(self) -> None:
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            pass
        except OSError:
            self._log.debug("could not remove %s", self._socket_path, exc_info=True)

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                conn, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stopping.is_set():
                    return
                self._log.debug("accept failed", exc_info=True)
                continue
            self._serve(conn)

    def _serve(self, conn: socket.socket) -> None:
        """Read one connection to completion.

        Served inline rather than on a per-connection thread: there is only
        ever one producer (the orchestrator), and handling connections
        sequentially means a reconnect after an orchestrator restart is
        picked up automatically by the next `accept()` - with no risk of two
        readers interleaving events into the model.
        """
        self._log.info("orchestrator connected")
        try:
            with conn, conn.makefile("rb") as stream:
                for event in iter_events(stream):
                    if self._stopping.is_set():
                        return
                    try:
                        self._on_event(event)
                    except Exception:
                        # A rendering bug must not kill the connection and
                        # freeze the transcript for the rest of the session.
                        self._log.warning("transcript event handler failed", exc_info=True)
        except OSError:
            self._log.debug("connection dropped", exc_info=True)
        finally:
            self._log.info("orchestrator disconnected")

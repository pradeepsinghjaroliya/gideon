"""Pinning a borderless card to the bottom of the screen, portably.

This is the only part of the overlay that cares what desktop it is running
on. `view.py` builds one GTK widget tree with one stylesheet; this module
decides how the resulting window gets placed, and nothing else differs
between environments.

There is no single mechanism that works everywhere, because Wayland
deliberately removed the ability for a client to position its own window:

- **wlroots compositors** (Hyprland, Sway, river, wayfire, labwc) implement
  `wlr-layer-shell`, which is exactly the right tool - it is the protocol
  panels and notification daemons use. Anchor to the bottom edge, ask for
  no keyboard focus, request no exclusive zone, done. The compositor keeps
  it there across workspaces with no cooperation needed from us.
- **X11** (any window manager, and XWayland) has no layer shell, but it
  does let a client move its own window, so an undecorated keep-above
  window repositioned to bottom-centre gets the same result.
- **GNOME Mutter and KDE KWin on Wayland** support neither: no layer shell,
  and no client positioning. A plain Wayland window would be placed
  wherever the compositor felt like, which is unusable for this. Since both
  ship XWayland, the fix is to re-launch ourselves with `GDK_BACKEND=x11`
  and use the X11 path - the window is then a normal X client that we can
  position, and it still composites correctly against Wayland surfaces.

`choose_backend` is kept as a pure function of two facts so the decision
table above is unit-testable without a display; `detect_backend` is the
thin wrapper that gathers those facts from the live GDK display.
"""

from __future__ import annotations

import logging
import os
import sys

BACKEND_LAYER_SHELL = "layer-shell"
BACKEND_X11 = "x11"
BACKEND_WAYLAND_PLAIN = "wayland-plain"

DISPLAY_WAYLAND = "wayland"
DISPLAY_X11 = "x11"
DISPLAY_UNKNOWN = "unknown"

# Set on the re-exec'd child so it cannot decide to re-exec again. Without
# it, a machine where `GDK_BACKEND=x11` fails to take effect (no XWayland
# installed, say) would fork-bomb itself.
REEXEC_GUARD_ENV = "GIDEON_TRANSCRIPT_REEXEC"

# Window/surface identity, so one name addresses the overlay on every
# backend: the wlr-layer-shell namespace, the X11 WM_CLASS and the GTK
# window role are all this. Users on window managers that need an explicit
# always-on-top/floating rule (i3, awesome, openbox) match on it, and
# wlroots users can hang a blur rule off it.
WM_NAME = "gideon-transcript"

_log = logging.getLogger("transcript_ui.surface")

# Set once by `load_gtk`; see the comment there.
_NAMED = False


class GtkUnavailableError(RuntimeError):
    """PyGObject/GTK3 could not be imported - raised with an actionable
    install hint rather than a bare ImportError, since this is the single
    most likely first-run failure on a fresh machine."""


def load_gtk():
    """Import and return `(Gtk, Gdk, GLib)`.

    Centralised so the `gi.require_version` calls happen exactly once and
    in one place - calling them twice with different versions is a hard
    error in PyGObject, and this process also links GTK indirectly through
    pystray in the orchestrator's address space if the two are ever merged.
    """
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        # Before the `Gtk` import, which is what initialises GTK and fixes
        # the program name GDK derives the X11 WM_CLASS from. Set later it
        # has no effect, and the class stays whatever argv implied - for
        # `python -m transcript_ui` that is the unusable "__main__.py".
        #
        # Guarded because `load_gtk()` is called more than once per process
        # (once by `app.main` to detect the backend, once by the view), and
        # `g_set_application_name` warns on a second call.
        global _NAMED
        if not _NAMED:
            GLib.set_prgname(WM_NAME)
            GLib.set_application_name("Gideon transcript")
            _NAMED = True

        from gi.repository import Gdk, Gtk
    except (ImportError, ValueError) as exc:
        raise GtkUnavailableError(
            "the transcript overlay needs PyGObject with GTK 3. Install it with:\n"
            "  Debian/Ubuntu:  sudo apt install python3-gi gir1.2-gtk-3.0\n"
            "  Fedora:         sudo dnf install python3-gobject gtk3\n"
            "  Arch:           sudo pacman -S python-gobject gtk3\n"
            "and, for the best result on Hyprland/Sway, the layer-shell binding:\n"
            "  Debian/Ubuntu:  sudo apt install gir1.2-gtklayershell-0.1\n"
            "  Fedora:         sudo dnf install gtk-layer-shell\n"
            "  Arch:           sudo pacman -S gtk-layer-shell\n"
            f"(underlying error: {exc})"
        ) from exc
    return Gtk, Gdk, GLib


def load_layer_shell():
    """Return the `GtkLayerShell` module, or `None` if it is not installed.

    `None` is an expected, non-exceptional outcome: the binding only exists
    for wlroots compositors, so its absence on GNOME/KDE/X11 is normal and
    simply selects a different backend.
    """
    try:
        import gi

        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
    except (ImportError, ValueError):
        return None
    return GtkLayerShell


def choose_backend(display_kind: str, layer_shell_supported: bool) -> str:
    """The decision table from the module docstring, as a pure function."""
    if layer_shell_supported:
        return BACKEND_LAYER_SHELL
    if display_kind == DISPLAY_X11:
        return BACKEND_X11
    return BACKEND_WAYLAND_PLAIN


def display_kind(Gdk, display=None) -> str:
    """Whether GDK is talking Wayland or X11 *right now*.

    Deliberately inspects the live display rather than `$XDG_SESSION_TYPE`:
    under XWayland the session type still says "wayland" while GDK is
    actually speaking X11, which is precisely the case the re-exec below
    creates - trusting the environment variable would misroute it.
    """
    display = display or Gdk.Display.get_default()
    if display is None:
        return DISPLAY_UNKNOWN
    type_name = type(display).__name__
    if "Wayland" in type_name:
        return DISPLAY_WAYLAND
    if "X11" in type_name:
        return DISPLAY_X11
    return DISPLAY_UNKNOWN


def detect_backend(Gdk) -> str:
    layer_shell = load_layer_shell()
    supported = False
    if layer_shell is not None:
        try:
            supported = bool(layer_shell.is_supported())
        except Exception:
            # Older gtk-layer-shell releases predate `is_supported()`.
            # Presence of the binding plus a Wayland display is a good
            # enough proxy; a failed `init_for_window` is caught later.
            supported = display_kind(Gdk) == DISPLAY_WAYLAND
    return choose_backend(display_kind(Gdk), supported)


def should_reexec_under_x11(backend: str) -> bool:
    """True when we are on a Wayland compositor with no layer shell, and an
    X11 route is available to fall back to.

    Requires `$DISPLAY` to be set, since that is what proves XWayland is
    actually running - without it, `GDK_BACKEND=x11` would just fail to
    open a display and we would be better off with the (imperfect) plain
    Wayland window than with no window at all.
    """
    if backend != BACKEND_WAYLAND_PLAIN:
        return False
    if os.environ.get(REEXEC_GUARD_ENV):
        return False
    return bool(os.environ.get("DISPLAY"))


def reexec_under_x11(argv: list[str] | None = None) -> None:
    """Replace this process with a copy forced onto GDK's X11 backend.

    `execve` rather than spawning a child so there is still exactly one
    overlay process for the orchestrator to manage and reap - a child would
    leave the parent hanging around doing nothing, and would break
    `main.py`'s terminate-on-shutdown handling.
    """
    argv = argv if argv is not None else sys.argv[1:]
    env = dict(os.environ)
    env["GDK_BACKEND"] = "x11"
    env[REEXEC_GUARD_ENV] = "1"
    _log.info("no layer-shell on this Wayland compositor - restarting under XWayland")
    os.execve(sys.executable, [sys.executable, "-m", "transcript_ui", *argv], env)


# --- applying a backend to a real window -----------------------------------


def apply_layer_shell(window, Gdk, *, bottom_margin: int, namespace: str) -> bool:
    """Anchor `window` to the bottom edge via wlr-layer-shell.

    - OVERLAY layer so it sits above normal windows *and* above panels,
      matching the "always visible while talking" intent.
    - Pinned to the monitor holding the pointer. Without an explicit
      monitor the compositor picks one, and on a multi-head setup that is
      routinely the wrong one - confirmed here, where the overlay opened on
      a side monitor while the user was working on the centre one.
    - `KeyboardMode.NONE`: the compositor never routes a key press here, so
      the overlay cannot steal a keystroke from whatever the user is
      actually typing in. This is a protocol-level guarantee, not a hint.
    - Exclusive zone `-1`: explicitly do **not** reserve screen space, and
      do not let other panels' reserved space push us around. Without it a
      top/bottom bar's exclusive zone would shift the overlay upward.
    - A namespace so compositor rules can target the surface, e.g. on
      Hyprland `layerrule = blur, gideon-transcript` gives it real
      background blur. Documented in plan.md rather than written into the
      user's compositor config.
    """
    layer_shell = load_layer_shell()
    if layer_shell is None:
        return False
    try:
        layer_shell.init_for_window(window)
        if hasattr(layer_shell, "set_namespace"):
            layer_shell.set_namespace(window, namespace)
        layer_shell.set_layer(window, layer_shell.Layer.OVERLAY)
        monitor = _monitor_at_pointer(Gdk.Display.get_default())
        if monitor is not None and hasattr(layer_shell, "set_monitor"):
            layer_shell.set_monitor(window, monitor)
        layer_shell.set_anchor(window, layer_shell.Edge.BOTTOM, True)
        layer_shell.set_margin(window, layer_shell.Edge.BOTTOM, bottom_margin)
        layer_shell.set_keyboard_mode(window, layer_shell.KeyboardMode.NONE)
        layer_shell.set_exclusive_zone(window, -1)
    except Exception:
        _log.warning("layer-shell setup failed, falling back", exc_info=True)
        return False
    return True


def apply_x11(window, Gdk, *, bottom_margin: int):
    """Make `window` an undecorated, unfocusable, always-on-top card and
    keep it at the bottom-centre of the monitor.

    `UTILITY` (rather than `DOCK` or `NOTIFICATION`) is the type hint that
    behaves most consistently across window managers here: `DOCK` makes
    many WMs reserve screen space for it, and `NOTIFICATION` makes some of
    them position it themselves - both fight what we are trying to do.
    `UTILITY` plus keep-above gets the layering without either side effect.

    Repositioning is wired to `size-allocate` rather than done once,
    because the card's height changes as lines of transcript are added and
    removed; re-deriving the origin from the current size on every
    allocation is what keeps the *bottom* edge pinned instead of the top.

    Returns a `set_offset(pixels)` callable that shifts the resting
    position downwards, which is how `BottomSurface` drives the slide
    animation on this backend.
    """
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_keep_above(True)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    # Show on every virtual desktop, so the transcript does not vanish when
    # the user switches workspace mid-conversation.
    window.stick()

    # Mutable so the slide animation (see `BottomSurface.set_slide_offset`)
    # can nudge the resting position without re-deriving the monitor
    # geometry itself.
    slide_offset = [0]

    def place() -> None:
        geometry = _monitor_geometry(window, Gdk)
        if geometry is None:
            return
        mx, my, mw, mh = geometry
        width, height = window.get_size()
        x = mx + max(0, (mw - width) // 2)
        y = my + mh - height - bottom_margin + slide_offset[0]
        # `get_position` first so we only call `move` when it would actually
        # change something - some WMs flash or re-raise the window on every
        # move request, and `size-allocate` fires often.
        if window.get_position() != (x, y):
            window.move(x, y)

    window.connect("size-allocate", lambda *_args: place())

    def set_offset(offset: int) -> None:
        slide_offset[0] = offset
        place()

    return set_offset


def _monitor_geometry(window, Gdk):
    """Usable area of the monitor the overlay should appear on, as
    `(x, y, width, height)`.

    Prefers the monitor the window is already on, then the one holding the
    pointer (so on a multi-head setup the transcript shows up on the screen
    the user is actually working on), then the primary. Uses the *workarea*
    where available so the card sits above a panel/taskbar rather than
    under it.
    """
    display = window.get_display() if hasattr(window, "get_display") else Gdk.Display.get_default()
    if display is None:
        return None

    monitor = None
    gdk_window = window.get_window() if hasattr(window, "get_window") else None
    if gdk_window is not None and hasattr(display, "get_monitor_at_window"):
        monitor = display.get_monitor_at_window(gdk_window)
    if monitor is None:
        monitor = _monitor_at_pointer(display)
    if monitor is None and hasattr(display, "get_primary_monitor"):
        monitor = display.get_primary_monitor()
    if monitor is None and hasattr(display, "get_monitor"):
        monitor = display.get_monitor(0)
    if monitor is None:
        return None

    area = monitor.get_workarea() if hasattr(monitor, "get_workarea") else monitor.get_geometry()
    return area.x, area.y, area.width, area.height


def _monitor_at_pointer(display):
    try:
        seat = display.get_default_seat()
        pointer = seat.get_pointer() if seat is not None else None
        if pointer is None:
            return None
        _screen, x, y = pointer.get_position()
        return display.get_monitor_at_point(x, y)
    except Exception:
        return None


def make_click_through(window) -> bool:
    """Give the window an empty input region, so every click, scroll and
    hover passes straight through to whatever is underneath.

    This is what makes an always-on-top overlay safe to leave on screen:
    it is physically incapable of intercepting input, so it can never
    swallow a click meant for the editor behind it. Combined with
    layer-shell's `KeyboardMode.NONE` (or X11's `accept_focus=False`), the
    overlay is purely something to look at - which is exactly the
    display-only behaviour this was specified to have.

    Must be called after the window is realized, since it operates on the
    underlying `GdkWindow`. Returns False if the platform declined, which
    is cosmetic only - worst case the card is clickable.
    """
    try:
        import cairo

        gdk_window = window.get_window()
        if gdk_window is None:
            return False
        gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
        return True
    except Exception:
        _log.debug("could not set an empty input region", exc_info=True)
        return False


def enable_transparency(window) -> bool:
    """Switch the window to an RGBA visual so the stylesheet's translucent
    background and rounded corners composite correctly.

    Returns False when no RGBA visual is available - an X11 session with no
    compositor running. That is a real configuration (a bare i3/openbox
    setup), and the honest response is for `view.py` to fall back to an
    opaque background: with no alpha channel, "rounded corners" would
    render as four black triangles.
    """
    window.set_app_paintable(True)
    screen = window.get_screen()
    if screen is None:
        return False
    visual = screen.get_rgba_visual()
    if visual is None or not screen.is_composited():
        return False
    window.set_visual(visual)
    return True


class BottomSurface:
    """Composes the pieces above into one handle `view.py` can use without
    knowing which backend it got.

    The only behaviour it adds on top of the free functions is
    `set_slide_offset`, which exists so the show/hide animation can be
    written once. "Slide the card up from below the bottom edge" is
    expressed completely differently per backend - a layer-shell margin
    change versus an X11 window move - and pushing that difference down
    here keeps `view.py`'s animation loop backend-agnostic.
    """

    def __init__(self, window, Gdk, *, bottom_margin: int = 48, namespace: str = WM_NAME) -> None:
        self._window = window
        self._Gdk = Gdk
        self._bottom_margin = bottom_margin
        self._namespace = namespace
        self._offset = 0
        self._layer_shell = None
        self._x11_reposition = None
        self.backend = detect_backend(Gdk)
        self.has_alpha = False

    def attach(self) -> str:
        """Configure the window for this machine. Returns the backend name
        actually used, which may differ from `self.backend` if layer-shell
        setup failed at runtime and we degraded to the X11/plain path."""
        self.has_alpha = enable_transparency(self._window)

        if self.backend == BACKEND_LAYER_SHELL:
            if apply_layer_shell(
                self._window,
                self._Gdk,
                bottom_margin=self._bottom_margin,
                namespace=self._namespace,
            ):
                self._layer_shell = load_layer_shell()
            else:
                self.backend = (
                    BACKEND_X11 if display_kind(self._Gdk) == DISPLAY_X11 else BACKEND_WAYLAND_PLAIN
                )

        if self.backend == BACKEND_X11:
            self._x11_reposition = apply_x11(
                self._window, self._Gdk, bottom_margin=self._bottom_margin
            )
        elif self.backend == BACKEND_WAYLAND_PLAIN:
            # Nothing here can pin the window; the compositor decides where
            # it goes. Still make it borderless, unfocusable and on-top so
            # it is as close to an overlay as the protocol permits, and say
            # so plainly in the log rather than pretending it worked.
            self._window.set_decorated(False)
            self._window.set_keep_above(True)
            self._window.set_accept_focus(False)
            self._window.set_focus_on_map(False)
            _log.warning(
                "this Wayland compositor supports neither wlr-layer-shell nor client "
                "window positioning, and XWayland was unavailable - the transcript "
                "window will appear wherever the compositor places it. Install "
                "gtk-layer-shell (Hyprland/Sway) or ensure XWayland is running for "
                "proper bottom-of-screen placement."
            )

        _log.info("transcript overlay using the %s backend", self.backend)
        return self.backend

    def finish_realize(self) -> None:
        """Post-realize setup - the input region can only be set once the
        underlying GdkWindow exists."""
        make_click_through(self._window)

    def set_slide_offset(self, pixels: float) -> None:
        """Push the card `pixels` further down (towards/past the bottom
        edge). `0` is its resting position."""
        offset = int(round(pixels))
        if offset == self._offset:
            return
        self._offset = offset
        if self._layer_shell is not None:
            # Clamped at 0: a negative layer-shell margin is not meaningful
            # and some compositors reject it outright.
            margin = max(0, self._bottom_margin - offset)
            try:
                self._layer_shell.set_margin(self._window, self._layer_shell.Edge.BOTTOM, margin)
            except Exception:
                _log.debug("could not update layer-shell margin", exc_info=True)
        elif self._x11_reposition is not None:
            self._x11_reposition(offset)

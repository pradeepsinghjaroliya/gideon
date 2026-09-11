"""Tests for the portability layer.

The backend *decision* is a pure function of two facts, precisely so the
whole cross-desktop decision table can be checked here without a display,
a compositor, or a second machine to run them on. The parts that need a
live GDK (`display_kind`, `detect_backend`) are checked lightly and only
when a display happens to be available.
"""

import os

import pytest

from transcript_ui.surface import (
    BACKEND_LAYER_SHELL,
    BACKEND_WAYLAND_PLAIN,
    BACKEND_X11,
    DISPLAY_UNKNOWN,
    DISPLAY_WAYLAND,
    DISPLAY_X11,
    REEXEC_GUARD_ENV,
    WM_NAME,
    choose_backend,
    load_layer_shell,
    should_reexec_under_x11,
)


# --- the decision table ----------------------------------------------------


def test_layer_shell_wins_wherever_it_is_supported():
    """wlroots compositors (Hyprland, Sway, river, wayfire) - the protocol
    panels and notification daemons use, and the only one that can pin a
    surface to an edge without client-side positioning."""
    assert choose_backend(DISPLAY_WAYLAND, True) == BACKEND_LAYER_SHELL


def test_x11_is_used_when_there_is_no_layer_shell():
    assert choose_backend(DISPLAY_X11, False) == BACKEND_X11


def test_layer_shell_is_preferred_even_on_x11_if_somehow_supported():
    assert choose_backend(DISPLAY_X11, True) == BACKEND_LAYER_SHELL


def test_wayland_without_layer_shell_falls_through_to_the_plain_backend():
    """GNOME Mutter and KDE KWin: no layer shell and no client
    positioning."""
    assert choose_backend(DISPLAY_WAYLAND, False) == BACKEND_WAYLAND_PLAIN


def test_an_unknown_display_falls_through_to_the_plain_backend():
    assert choose_backend(DISPLAY_UNKNOWN, False) == BACKEND_WAYLAND_PLAIN


# --- the XWayland re-exec ---------------------------------------------------


def test_reexec_is_requested_on_wayland_without_layer_shell(monkeypatch):
    monkeypatch.delenv(REEXEC_GUARD_ENV, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    assert should_reexec_under_x11(BACKEND_WAYLAND_PLAIN) is True


def test_no_reexec_when_layer_shell_already_works(monkeypatch):
    monkeypatch.delenv(REEXEC_GUARD_ENV, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    assert should_reexec_under_x11(BACKEND_LAYER_SHELL) is False


def test_no_reexec_when_already_on_x11(monkeypatch):
    monkeypatch.delenv(REEXEC_GUARD_ENV, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    assert should_reexec_under_x11(BACKEND_X11) is False


def test_no_reexec_without_xwayland(monkeypatch):
    """No `$DISPLAY` means no XWayland, so `GDK_BACKEND=x11` would just
    fail to open a display - better an imperfectly-placed window than no
    window at all."""
    monkeypatch.delenv(REEXEC_GUARD_ENV, raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    assert should_reexec_under_x11(BACKEND_WAYLAND_PLAIN) is False


def test_the_guard_prevents_a_second_reexec(monkeypatch):
    """Without it, a machine where `GDK_BACKEND=x11` fails to take effect
    would fork-bomb itself."""
    monkeypatch.setenv(REEXEC_GUARD_ENV, "1")
    monkeypatch.setenv("DISPLAY", ":0")

    assert should_reexec_under_x11(BACKEND_WAYLAND_PLAIN) is False


# --- graceful absence ------------------------------------------------------


def test_a_missing_layer_shell_binding_is_not_an_error():
    """Its absence on GNOME/KDE/X11 is normal and simply selects a
    different backend, so this must return None rather than raise."""
    result = load_layer_shell()

    assert result is None or hasattr(result, "init_for_window")


def test_the_wm_name_is_shared_so_one_rule_matches_either_backend():
    from transcript_ui import view

    assert view.WM_NAME == WM_NAME


# --- live display (skipped where there is none) ----------------------------

_HAS_DISPLAY = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
_needs_display = pytest.mark.skipif(not _HAS_DISPLAY, reason="no graphical display")


@_needs_display
def test_detect_backend_returns_a_known_backend_on_a_live_display():
    from transcript_ui.surface import detect_backend, display_kind, load_gtk

    _Gtk, Gdk, _GLib = load_gtk()

    assert display_kind(Gdk) in (DISPLAY_WAYLAND, DISPLAY_X11, DISPLAY_UNKNOWN)
    assert detect_backend(Gdk) in (BACKEND_LAYER_SHELL, BACKEND_X11, BACKEND_WAYLAND_PLAIN)


@_needs_display
def test_display_kind_reflects_gdk_not_the_session_type_env_var():
    """Under XWayland `$XDG_SESSION_TYPE` still says "wayland" while GDK is
    actually speaking X11 - which is exactly the state the re-exec creates,
    so trusting the env var would misroute it."""
    from transcript_ui.surface import display_kind, load_gtk

    _Gtk, Gdk, _GLib = load_gtk()
    display = Gdk.Display.get_default()
    if display is None:
        pytest.skip("no display could be opened")

    kind = display_kind(Gdk, display)

    assert kind in (DISPLAY_WAYLAND, DISPLAY_X11)
    assert kind == (DISPLAY_WAYLAND if "Wayland" in type(display).__name__ else DISPLAY_X11)

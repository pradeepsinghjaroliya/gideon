"""The overlay itself: one GTK3 window, drawn once and restyled per state.

Everything here is "turn `TranscriptModel` into widgets". The interesting
*logic* - turn bookkeeping, typewriter pacing, auto-hide - lives in
`model.py` precisely so it can be tested without any of this; and how the
window gets pinned to the bottom of the screen lives in `surface.py` so
this file never branches on which desktop it is running under.

The layout, which is the whole of the "minimal" brief:

    +----------------------------------------------+
    |  * Listening                   .:|iI|:.      |   status row
    |                                              |
    |  YOU                                         |
    |  what's the weather like today               |
    |                                              |
    |  GIDEON                                      |
    |  It's 18 degrees and clear right now.|       |   <- caret while streaming
    +----------------------------------------------+

Colours are taken from `text_input/tray.py`'s `_STATE_COLORS` and
`text_input/dashboard.py`'s accent, deliberately rather than freshly
chosen: the tray dot, the dashboard pills and this overlay should read as
one product, and three independently-picked purples would not.
"""

from __future__ import annotations

import math
from typing import Callable

from shared.transcript import (
    STATE_ERROR,
    STATE_IDLE,
    STATE_LISTENING,
    STATE_PROCESSING,
    STATE_SPEAKING,
)

from transcript_ui.model import ROLE_ASSISTANT, TranscriptModel
from transcript_ui.surface import WM_NAME, BottomSurface, load_gtk

# Same vocabulary and same colours as the tray icon (`tray.py`'s
# `_STATE_COLORS`), expressed as CSS here. An unrecognized state falls back
# to idle rather than raising, matching how the tray handles it.
_STATE_STYLE: dict[str, tuple[str, str]] = {
    STATE_IDLE: ("#9e9e9e", "Idle"),
    STATE_LISTENING: ("#43b047", "Listening"),
    STATE_PROCESSING: ("#ff9800", "Thinking"),
    STATE_SPEAKING: ("#7c5cff", "Speaking"),
    STATE_ERROR: ("#e53935", "Error"),
}
_DEFAULT_STATE = STATE_IDLE

_ROLE_LABELS = {"user": "You", ROLE_ASSISTANT: "Gideon"}

# 60fps. Both the typewriter and the fade read from the same tick, so there
# is exactly one timer driving the whole overlay.
_FRAME_MS = 16

_FADE_SECONDS = 0.22
_SLIDE_PIXELS = 22.0

# Must match `#card`'s horizontal padding in `_CSS` - used to derive the
# text wrap width.
_CARD_H_PADDING = 22

_LEVEL_BARS = 22
_METER_WIDTH = 76
_METER_HEIGHT = 14

_CSS = b"""
#card {
  /* Kept only slightly translucent. At 0.86 the window behind showed
     through enough to compete with the transcript text - the card has to
     stay readable over an arbitrary background, since it sits on top of
     whatever the user happens to be working in. wlroots users who want a
     glassier look can add real background blur instead; see plan.md. */
  background-color: rgba(16, 16, 20, 0.94);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 18px;
  padding: 18px 22px;
}
/* No compositor means no alpha: a translucent fill would composite against
   black and the rounded corners would show as dark triangles. Go opaque
   and square off the radius instead of rendering something broken. */
#card.no-alpha {
  background-color: #12121a;
  border-radius: 8px;
}
#state-label {
  color: rgba(255, 255, 255, 0.55);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.4px;
}
#role-label {
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 1.6px;
}
#role-label.user { color: rgba(255, 255, 255, 0.58); }
#role-label.assistant { color: rgba(124, 92, 255, 0.95); }
#turn-text {
  color: rgba(255, 255, 255, 0.94);
  font-size: 15px;
}
#turn-text.assistant { color: rgba(236, 233, 255, 0.96); }
/* Older turns stay for context but must not compete with the live one. */
.faded #role-label, .faded #turn-text { opacity: 0.38; }
"""


class TranscriptView:
    """Owns the window, the widgets and the single animation timer.

    `handle_event` is the only entry point for new data, and it must be
    called from the GTK main thread - `server.py` marshals events across
    with `GLib.idle_add` to guarantee that.
    """

    def __init__(
        self,
        model: TranscriptModel,
        width: int = 720,
        bottom_margin: int = 48,
        max_width_fraction: float = 0.55,
        on_ready: Callable[[str], None] | None = None,
    ) -> None:
        self.Gtk, self.Gdk, self.GLib = load_gtk()
        self._model = model
        self._on_ready = on_ready
        self._max_width_fraction = max_width_fraction

        self._opacity = 0.0
        self._target_opacity = 0.0
        self._phase = 0.0
        self._turn_rows: list = []
        self._tick_id: int | None = None
        self._chars_per_line_cache: int | None = None

        self._width = self._clamp_width(width)
        self._window = self.Gtk.Window(type=self.Gtk.WindowType.TOPLEVEL)
        self._window.set_title("Gideon transcript")
        # A stable, greppable identity (see `surface.WM_NAME`), so the same
        # name matches the overlay whichever backend it ends up on.
        # `set_wmclass` is deprecated in GTK3 but is still the only thing
        # that reliably sets WM_CLASS on an already-initialised GTK - hence
        # both it and `surface.load_gtk`'s earlier `set_prgname`.
        self._window.set_role(WM_NAME)
        if hasattr(self._window, "set_wmclass"):
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                self._window.set_wmclass(WM_NAME, "Gideon")
        # Load-bearing, not cosmetic. A resizable window keeps whatever
        # size it was first allocated, and a layer-shell surface is first
        # allocated at the height of an *empty* card - so as transcript
        # lines were added the card's natural height grew (confirmed: 58 ->
        # 144) while the surface stayed at 58, and GTK squashed every row
        # into a 1x1 allocation. Nothing was visible but the status line.
        # A non-resizable window instead tracks its child's natural size and
        # re-configures the surface on every change, which is exactly the
        # "card grows and shrinks with the conversation" behaviour wanted
        # here. The user cannot resize it by hand anyway - it is
        # click-through.
        self._window.set_resizable(False)

        self._surface = BottomSurface(self._window, self.Gdk, bottom_margin=bottom_margin)
        backend = self._surface.attach()

        self._build_widgets()
        self._install_css()
        if not self._surface.has_alpha:
            self._card.get_style_context().add_class("no-alpha")

        self._window.connect("realize", self._on_realize)
        self._window.connect("delete-event", lambda *_a: True)  # never user-closable
        # Start fully transparent and off-position: the card must fade in on
        # the first wake word, not flash on screen the moment the process
        # starts.
        self._set_opacity(0.0)
        self._surface.set_slide_offset(_SLIDE_PIXELS)

        if self._on_ready is not None:
            self._on_ready(backend)

    # --- construction ------------------------------------------------------

    def _clamp_width(self, width: int) -> int:
        """Keep the card a sane size on a small screen or a scaled display -
        a fixed 720px is most of the width of a 1366px laptop panel."""
        try:
            display = self.Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            usable = int(monitor.get_workarea().width * self._max_width_fraction)
            return max(360, min(width, usable))
        except Exception:
            return width

    @property
    def _chars_per_line(self) -> int:
        """How many characters fit across the card's inner width, from the
        font actually in use.

        Hardcoding a character count instead would be a guess about the
        user's font and scaling factor, and would wrap early on a wide font
        or overflow on a narrow one. Measuring a reference string through
        Pango costs one layout at startup and is correct on any theme.
        """
        if self._chars_per_line_cache is not None:
            return self._chars_per_line_cache

        inner = self._width - _CARD_H_PADDING * 2
        chars = max(20, int(inner / 7.5))  # fallback if measurement fails
        try:
            import gi

            gi.require_version("Pango", "1.0")
            from gi.repository import Pango

            sample = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            layout = self._probe_label.create_pango_layout(sample)
            width_px = layout.get_pixel_size().width
            if width_px > 0:
                per_char = width_px / len(sample)
                chars = max(20, int(inner / per_char))
            del Pango
        except Exception:
            pass
        self._chars_per_line_cache = chars
        return chars

    def _set_opacity(self, value: float) -> None:
        """`Gtk.Window.set_opacity` is deprecated in GTK3 in favour of the
        `Gtk.Widget` one, but the Window override shadows it on a Window
        instance - so call the widget implementation explicitly rather than
        emit a deprecation warning on every animation frame."""
        self.Gtk.Widget.set_opacity(self._window, value)

    def _opacity_of_window(self) -> float:
        return self.Gtk.Widget.get_opacity(self._window)

    def _build_widgets(self) -> None:
        Gtk = self.Gtk

        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._card.set_name("card")
        # A size *request* on the content, not `set_default_size` on the
        # window. A layer-shell surface anchored to one edge takes its size
        # from the content's natural size and ignores the window's default
        # size entirely - which had the card rendering 205px wide (confirmed
        # via `hyprctl layers`) instead of the configured width. Requesting
        # it on the card works on every backend, since it makes the window's
        # natural width the width we actually want.
        self._card.set_size_request(self._width, -1)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._state_dot = Gtk.DrawingArea()
        self._state_dot.set_size_request(8, 8)
        self._state_dot.set_valign(Gtk.Align.CENTER)
        self._state_dot.connect("draw", self._draw_state_dot)
        self._state_label = Gtk.Label(label="Idle")
        self._state_label.set_name("state-label")
        self._state_label.set_xalign(0.0)
        self._meter = Gtk.DrawingArea()
        self._meter.set_size_request(_METER_WIDTH, _METER_HEIGHT)
        self._meter.set_valign(Gtk.Align.CENTER)
        self._meter.connect("draw", self._draw_meter)

        header.pack_start(self._state_dot, False, False, 0)
        header.pack_start(self._state_label, False, False, 0)
        header.pack_end(self._meter, False, False, 0)

        self._turns_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Never packed anywhere - it exists only so `_chars_per_line` can
        # measure the transcript font through the same `#turn-text` style
        # the real rows use.
        self._probe_label = Gtk.Label()
        self._probe_label.set_name("turn-text")

        # The transcript lives inside a scroller purely as a width clamp.
        # A GTK size request is a *minimum*, so a wrapping label still
        # reports the full unwrapped line as its natural width and the
        # non-resizable window happily grows to fit it - the card was
        # reaching 1211px against a configured 720px (confirmed via
        # `hyprctl layers`). A `GtkScrolledWindow` with the horizontal
        # policy set to NEVER allocates its child exactly the viewport
        # width, which forces the labels to wrap there instead; letting the
        # natural *height* propagate keeps the "card grows with the
        # conversation" behaviour. No scrollbar ever appears - both
        # policies are NEVER; `max_turns` is what bounds the content.
        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self._scroller.set_propagate_natural_height(True)
        self._scroller.set_propagate_natural_width(False)
        self._scroller.set_size_request(self._width - _CARD_H_PADDING * 2, -1)
        self._scroller.add(self._turns_box)

        self._card.pack_start(header, False, False, 0)
        self._card.pack_start(self._scroller, False, False, 0)
        self._window.add(self._card)

    def _install_css(self) -> None:
        provider = self.Gtk.CssProvider()
        provider.load_from_data(_CSS)
        self.Gtk.StyleContext.add_provider_for_screen(
            self.Gdk.Screen.get_default(),
            provider,
            self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _on_realize(self, *_args) -> None:
        self._surface.finish_realize()

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Show the window (still fully transparent) and start the frame
        timer.

        The window is mapped immediately rather than on the first event,
        because mapping is the expensive, visibly-janky part (the
        compositor allocates a surface, the WM may animate it). Doing it up
        front means the first wake word only has to change an opacity value,
        which is what makes the appearance feel instant.
        """
        self._window.show_all()
        self._meter.set_visible(False)
        if self._tick_id is None:
            self._tick_id = self.GLib.timeout_add(_FRAME_MS, self._tick)

    def stop(self) -> None:
        if self._tick_id is not None:
            self.GLib.source_remove(self._tick_id)
            self._tick_id = None
        self._window.hide()

    # --- data in -----------------------------------------------------------

    def handle_event(self, event) -> None:
        """Fold an event into the model and repaint if it changed anything.
        GTK main thread only."""
        if self._model.handle(event):
            self.render()

    # --- frame -------------------------------------------------------------

    def _tick(self) -> bool:
        model = self._model
        needs_render = model.tick()

        self._target_opacity = 1.0 if model.visible and not model.is_empty() else 0.0
        step = _FRAME_MS / 1000.0 / _FADE_SECONDS
        if abs(self._opacity - self._target_opacity) > 1e-3:
            direction = 1.0 if self._target_opacity > self._opacity else -1.0
            self._opacity = max(0.0, min(1.0, self._opacity + direction * step))
            # Ease-out on the way in, ease-in on the way out, so the card
            # decelerates into place instead of arriving at constant speed.
            eased = 1.0 - (1.0 - self._opacity) ** 2
            self._set_opacity(eased)
            self._surface.set_slide_offset((1.0 - eased) * _SLIDE_PIXELS)
        elif self._opacity != self._target_opacity:
            self._opacity = self._target_opacity
            self._set_opacity(self._opacity)
            self._surface.set_slide_offset((1.0 - self._opacity) * _SLIDE_PIXELS)

        if model.state == STATE_LISTENING:
            # Only advance the meter's animation phase while it is actually
            # on screen, so an idle overlay costs nothing but the timer.
            self._phase += 0.18
            self._meter.queue_draw()

        if needs_render:
            self.render()
        return True  # keep the timer alive

    # --- rendering ---------------------------------------------------------

    def render(self) -> None:
        colour, label = _STATE_STYLE.get(self._model.state, _STATE_STYLE[_DEFAULT_STATE])
        self._state_label.set_text(label.upper())
        self._state_dot.queue_draw()
        self._meter.set_visible(self._model.state == STATE_LISTENING)

        turns = self._model.visible_turns()
        self._sync_row_count(len(turns))

        live_index = len(turns) - 1
        for index, (turn, row) in enumerate(zip(turns, self._turn_rows)):
            role_label, text_label, box = row
            role_label.set_text(_ROLE_LABELS.get(turn.role, turn.role).upper())
            _set_style_class(role_label, "assistant", turn.role == ROLE_ASSISTANT)
            _set_style_class(role_label, "user", turn.role != ROLE_ASSISTANT)
            _set_style_class(text_label, "assistant", turn.role == ROLE_ASSISTANT)

            # A caret only while this turn is genuinely still arriving -
            # it is the cue that more text is coming, so leaving it on a
            # finished line would be a lie.
            streaming = index == live_index and not turn.is_settled
            text_label.set_text(turn.text + ("▌" if streaming else ""))
            _set_style_class(box, "faded", index < live_index - 1)

    def _sync_row_count(self, count: int) -> None:
        """Add or remove turn rows to match the model.

        Widgets are reused rather than rebuilt on every repaint: the
        typewriter repaints at 60fps, and destroying/recreating labels that
        fast would thrash GTK's layout and make the text visibly flicker.
        """
        Gtk = self.Gtk
        while len(self._turn_rows) < count:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            role_label = Gtk.Label()
            role_label.set_name("role-label")
            role_label.set_xalign(0.0)
            text_label = Gtk.Label()
            text_label.set_name("turn-text")
            text_label.set_xalign(0.0)
            text_label.set_line_wrap(True)
            text_label.set_line_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR
            # `self._scroller` clamps the *allocated* width, but GTK also
            # needs a bounded natural width to compute the right natural
            # height: a wrapping label's natural width is its whole
            # unwrapped line, so the scroller would ask for the height of a
            # single line and then squash the wrapped text into it.
            # `max_width_chars` supplies that bound, measured from the real
            # font rather than guessed (see `_chars_per_line`).
            text_label.set_max_width_chars(self._chars_per_line)
            box.pack_start(role_label, False, False, 0)
            box.pack_start(text_label, False, False, 0)
            self._turns_box.pack_start(box, False, False, 0)
            box.show_all()
            self._turn_rows.append((role_label, text_label, box))

        while len(self._turn_rows) > count:
            _role, _text, box = self._turn_rows.pop()
            self._turns_box.remove(box)
            box.destroy()

    # --- cairo bits --------------------------------------------------------

    def _draw_state_dot(self, widget, cr) -> bool:
        colour, _label = _STATE_STYLE.get(self._model.state, _STATE_STYLE[_DEFAULT_STATE])
        red, green, blue = _hex_to_rgb(colour)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        radius = min(width, height) / 2.0
        cr.set_source_rgba(red, green, blue, 0.95)
        cr.arc(width / 2.0, height / 2.0, radius, 0, 2 * math.pi)
        cr.fill()
        return False

    def _draw_meter(self, widget, cr) -> bool:
        """A small bar meter showing mic input.

        Its point is reassurance: while the user is speaking but before any
        words have been decoded, this is the only thing on screen proving
        Gideon can actually hear them. Bar heights combine the smoothed
        level with a travelling sine so it reads as live audio rather than a
        static bar chart.
        """
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        level = self._model.level
        bar_width = max(1.0, width / (_LEVEL_BARS * 1.8))
        gap = (width - _LEVEL_BARS * bar_width) / max(1, _LEVEL_BARS - 1)
        red, green, blue = _hex_to_rgb(_STATE_STYLE[STATE_LISTENING][0])

        for index in range(_LEVEL_BARS):
            wave = 0.55 + 0.45 * math.sin(self._phase + index * 0.55)
            # Taper towards both ends so the meter reads as a shape rather
            # than a wall of equal bars.
            taper = math.sin(math.pi * (index + 0.5) / _LEVEL_BARS) ** 0.6
            bar_height = max(1.5, height * level * wave * taper)
            x = index * (bar_width + gap)
            y = (height - bar_height) / 2.0
            cr.set_source_rgba(red, green, blue, 0.35 + 0.5 * level)
            cr.rectangle(x, y, bar_width, bar_height)
            cr.fill()
        return False


def _set_style_class(widget, name: str, enabled: bool) -> None:
    context = widget.get_style_context()
    if enabled:
        context.add_class(name)
    else:
        context.remove_class(name)


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

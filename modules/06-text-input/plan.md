# 06-text-input

## Goal

Provide a manual fallback to typing a request instead of speaking it —
useful when voice isn't practical (noisy room, don't want to talk out
loud) or for quick testing without touching the mic pipeline at all.

## Depends on

`00-shared` (interfaces, config).

## Interface implemented

`TextInputProvider` (see `../../ARCHITECTURE.md`).

## Recommended approach

A small always-available entry point rather than a full app:

- **Tray icon** (`pystray`) sitting in the system tray, click opens a
  popup.
- **Popup** — simplest reliable option is a small **Tkinter** window (ships
  with Python, no extra system packages) with a single text entry + submit
  button. GTK is an alternative if the rest of the desktop is GTK-themed,
  but Tkinter is the lower-effort starting point and swappable later.

## Deliverables

- `src/text_input/popup.py` — `TkPopupProvider` implementing
  `TextInputProvider`: `get_text()` opens a small always-on-top window with
  a text field, blocks until the user submits (Enter) or closes the window
  (returns `None` on close/cancel).
- `src/text_input/tray.py` — a tray icon (`pystray`) with at least one menu
  item "Ask..." that triggers the popup, and "Quit" to exit. This is the
  always-running piece the orchestrator will launch in the background;
  the popup itself only exists while answering a question.
- A standalone demo script that runs the tray icon standalone, click
  "Ask...", type something, print what was returned to the terminal.

## Standalone test plan

1. Run the tray demo, confirm the icon appears in Ubuntu's system tray
   (note: some Ubuntu/GNOME setups need a tray extension like "AppIndicator
   and KStatusNotifierItem" — check this early, it's a common snag, and
   note the outcome here).
2. Click "Ask...", type a sentence, submit — confirm it prints correctly.
3. Open the popup and close it without typing anything — confirm `None` is
   returned, not an empty string or a crash.
4. Confirm the popup grabs focus and appears on top (so the user doesn't
   have to hunt for it).

## Out of scope

- Global hotkey to open the popup (config has a `text_input.hotkey` field
  reserved for this — implement only if time allows, otherwise leave it
  unset and rely on tray click; note which was done here).
- Rich input (voice-to-text inside the popup, attachments, etc.) — plain
  text only.

## Setup

```
pip install -r modules/06-text-input/requirements.txt
```

Tkinter ships with Python; only `pystray`/`Pillow` (the tray icon) are
pip installs.

## Verification status

Implemented and unit-tested (7 tests):

- `test_tk_popup.py` — drives `TkPopupProvider`'s real `tkinter.Entry`/
  `Tk` widgets directly (submit with text, submit blank -> `None`, cancel
  -> `None`), plus one test that runs the actual `mainloop()` with an
  `after()`-scheduled auto-submit, to exercise the same event-loop path
  `get_text()` uses (not just the callback logic in isolation).
- `test_tray_app.py` — drives `TrayApp`'s request-queue loop with a fake
  icon/provider (ask -> `on_text` called with the typed text; cancel ->
  not called; multiple asks processed in order; quit stops the icon).

**Tested against the real backend 2026-08-28** (this machine, GNOME on
Wayland via XWayland, `DISPLAY=:0`):

- `tkinter.Tk()` creates real windows fine (used above for the mainloop
  test).
- `pystray.Icon` initially only loaded the **Xorg backend**
  (`pystray._xorg`) — `gi` (PyGObject) wasn't importable in this venv, and
  this system has no XEmbed tray manager (`_NET_SYSTEM_TRAY_S0` unowned,
  checked via `xprop -root _NET_SYSTEM_TRAY_S0`), so that backend's icon
  ran without error but was never actually visible. **Fixed**: after
  `sudo apt install libgirepository-2.0-dev` (the user ran this) and
  `pip install PyGObject`, pystray now loads `pystray._appindicator`
  instead, using the already-installed `gnome-shell-extension-appindicator`.

**Confirmed on real hardware 2026-08-28**: user ran `tray_demo.py`, saw
the icon in the tray, clicked "Ask...", the popup appeared, typed "hii",
pressed Enter — popup closed and `got: 'hii'` printed to the terminal.
Full tray-click -> popup -> submit -> callback path verified end to end.
`06-text-input` is fully done, not just self-tested.

## Status / logs menu item (added 2026-08-28, requested by the user)

After trying the full pipeline via `07-orchestrator`, the user asked for a
way to see the assistant's current state (idle/listening/thinking/speaking)
without reading the terminal - useful for a non-technical user to tell
"is it still listening, or did it already finish and go back to idle?"
without needing to say the wake word again just to check.

Added to `TrayApp`:
- `set_status(message: str)` - appends to a bounded (`maxlen=200`) internal
  log and best-effort updates the tray icon's tooltip
  (`icon.title = f"Gideon - {message}"`); wrapped in `try/except Exception`
  since not every tray backend is guaranteed to support a live tooltip
  update (untested whether AppIndicator actually shows this on hover -
  the "Status / logs..." window below is the reliable surface).
- A third menu item, **"Status / logs..."**, between "Ask..." and "Quit" -
  opens a read-only Tk window (`build_status_window()`) listing the log,
  most-recent-last. Built the same way as the "Ask..." popup (its own
  `Tk()` root, processed through the same request queue in `run()`), so
  it's never open at the same time as the ask popup.
- `07-orchestrator`'s `Orchestrator` takes an `on_status` callback, wired
  in `main.py` to `tray_app.set_status`, and calls it at each state
  transition with a plain-English message (`"Idle - waiting for the wake
  word or a typed question"`, `"Listening - recording your question"`,
  `"Transcribing your question"`, `"Thinking - waiting on the local LLM
  for a reply"`, `"Speaking: <reply text>"`) - see
  `modules/07-orchestrator/src/orchestrator/state_machine.py`.

**Bug found and fixed 2026-08-28**: the user tried this on real hardware and
found the status window looked "stuck" always showing "Idle" - the window
took a one-time snapshot of the log at open time and never updated again
for as long as it stayed open, so opening it right after a question and
leaving it open just showed the "Idle" line from before that question was
even asked. **Fixed**: `build_status_window()` now takes a
`get_log_lines: Callable[[], list[str]]` instead of a static `list[str]`,
and re-renders every `refresh_ms` (default 500ms) via `root.after()` until
the window is closed (which cancels the pending `after()` call). Covered
by a new regression test, `test_refresh_picks_up_new_log_lines`.

**Confirmed on real hardware 2026-08-28**: user retested and the window
now updates live. Tooltip support was not separately confirmed either way.

## Ask box in the Status window + generic dashboard menu items (added
2026-08-28, requested by the user)

Two more ad hoc requests (see root `tmp.md`, cleared into this writeup
and `07-orchestrator/plan.md`'s "Tray dashboard controls" section):

- **"When logs are open I can't open ask dialog box"**: real limitation,
  not a bug - `TrayApp.run()` only ever drives one Tk window at a time
  (its request queue processes one item at a time), so leaving the Status
  window open (blocked on its own `root.mainloop()`) meant a queued
  "Ask..." request just sat unprocessed until Status was closed. Rather
  than trying to run two Tk windows concurrently (real threading
  complexity for little benefit), gave the Status window its own ask box
  wired to the *same* `on_text` callback (`build_status_window(...,
  on_ask=...)`, new `ask_entry`/`submit_ask` on `_StatusHandles`) - so the
  user can ask a follow-up without leaving Status at all, instead of
  making the two windows coexist.
- **`TrayApp(extra_menu_items=...)`**: `07-orchestrator` wanted several
  new tray controls (LLM running/start/stop, mic mute, online/offline,
  stop speaking) that are all orchestrator-specific state, not something
  this module should know about. Rather than hardcoding those into
  `TrayApp`, it now accepts a list of already-built `pystray.MenuItem`s,
  inserted between "Ask..." and "Status / logs..." - keeps this module
  independently buildable/testable (no orchestrator import), matching the
  project's per-module isolation convention. See
  `07-orchestrator/plan.md`'s "Tray dashboard controls" section for what
  `main.py` actually builds with this.

**Not yet confirmed on real hardware** - both are unit-tested only
(`test_status_window.py`'s `test_ask_box_*`, `test_tray_app.py`'s
`test_extra_menu_items_inserted_between_ask_and_status`). Needs the user
to confirm asking from inside the Status window actually triggers a
reply, and that the new dashboard menu items render and click correctly
under the real AppIndicator backend.

## Dashboard panel (added 2026-08-28, requested by the user)

The plain `extra_menu_items` checkbox/text menu entries above weren't
what the user had in mind - they wanted something closer to a GNOME
quick-settings popup: a small dark panel of rounded pill-shaped
toggle/action buttons, not a text dropdown.

Added `src/text_input/dashboard.py`:
- `DashboardControl` - one pill button: `get_label`/`is_active`/
  `is_enabled` callables (called fresh on every render, so e.g. "Stop
  speaking" greys out live once playback ends without needing the panel
  closed and reopened) plus `on_click`.
- `build_dashboard_window(root, controls, refresh_ms=500)` - draws a grid
  of pills on a `tk.Canvas` (rounded rectangles via the standard
  smooth-polygon recipe, since Tkinter has no native rounded-rect widget),
  purple when active, dark grey when inactive, a dimmer grey with no click
  binding when disabled. Same open-window/refresh/close pattern as
  `build_status_window` - fully redraws the canvas each refresh tick
  rather than mutating individual items, simple and fast enough for a
  handful of buttons at 2/sec.
- `TrayApp(dashboard_controls=...)`: a new constructor param (alongside
  the still-available `extra_menu_items`) that adds one "Dashboard..."
  entry to the native menu (right after "Ask...") which opens this panel
  through the same request-queue/one-Tk-window-at-a-time mechanism as
  "Ask..." and "Status / logs...". Kept orchestrator-agnostic like
  everything else here - `07-orchestrator/main.py` builds the actual
  `DashboardControl`s (see its plan.md's "Tray dashboard controls"
  section, now superseded by this to use the panel instead of plain menu
  items).

Testing note: simulating a real mouse click on a canvas item
(`event_generate`) turned out unreliable/flaky in this headless-but-real-X
test environment - `test_dashboard.py` instead verifies the click
*binding* exists for enabled pills (`canvas.tag_bind(...) != ""`) and unit
tests the handler-building function directly, rather than simulating an
actual click end-to-end - same "drive the callback directly" testing
style already used for `popup.py`'s `submit()`/`cancel()`.

**Confirmed on real hardware 2026-08-28** (the LLM start/stop half, at
least - see `07-orchestrator/plan.md`'s matching note): user confirmed
"llm start works perfectly." The panel's visual appearance under the real
AppIndicator backend and the other three controls (mic/online/stop
speaking) are still unconfirmed.

**Follow-up 2026-08-28**: user asked why the tray icon can't open the
dashboard panel directly on click, the way GNOME's own quick-settings
icon does, instead of a dropdown menu first. Investigated: this is a hard
limitation of pystray's AppIndicator backend
(`pystray/_appindicator.py` hardcodes `HAS_DEFAULT_ACTION = False`,
comment: "we expand the menu on primary button click") - every click
always opens the dropdown, regardless of a `default=True` marking. This
reflects the StatusNotifierItem/AppIndicator protocol itself (unlike the
legacy X11 tray protocol, which did support a distinct left-click default
action separate from a right-click context menu) - not something
fixable without abandoning pystray/AppIndicator and hand-rolling a
StatusNotifierItem D-Bus service, which is out of scope. Made the one
improvement that *is* possible: reordered the menu so "Dashboard..." is
now the first item (was previously after "Ask...").

## Colored tray icon, unified dashboard window, volume slider (added
2026-08-30, requested by the user)

Another batch of ad hoc requests (see root `tmp.md`, cleared into this
writeup and `07-orchestrator/plan.md`'s "Tray dashboard controls"
section):

- **"Can we have a better icon than a blue dot, and change its color
  based on what's going on (grey=idle, green=listening,
  orange=processing, etc)?"**: `_make_icon_image()` now takes a `color`
  (still the same generated dot, no bundled asset needed - the ask was
  about color, not shape). New `TrayApp.set_icon_state(state)` maps a
  symbolic state string to a color via `_STATE_COLORS` (grey/idle,
  green/listening, orange/processing, purple/speaking - reusing the
  dashboard panel's own "active" pill color rather than inventing a new
  one, so the tray and panel read as one visual language - red/error
  reserved but not yet driven by anything) and swaps `icon.icon`, falling
  back to idle's grey for an unrecognized state rather than raising.
  Wrapped in `try/except Exception` like `set_status`'s tooltip update -
  not every tray backend is guaranteed to support a live icon swap.
  `07-orchestrator/state_machine.py` calls it via a new `on_state`
  callback, parallel to the existing `on_status` but carrying a symbolic
  state instead of a human-readable message - kept separate so this
  module never has to pattern-match `on_status`'s free text.
- **"Instead of Dashboard/Ask/Status-logs menu items, put LLM status, mic
  status, and other quick insights in the menu itself"** + **"clicking
  Dashboard should open a window with quick buttons on top, logs in the
  middle, and a manual ask box on the bottom"**: read together as one
  redesign - the standalone "Ask..." popup and "Status / logs..." window
  are gone, folded into `dashboard.py`'s panel as two new optional
  sections (`build_dashboard_window(..., get_log_lines=, on_ask=)`, same
  live-refresh-not-static-snapshot pattern the old status window already
  used). The tray menu keeps exactly one entry point, "Dashboard...",
  plus new **quick-insight entries above it** (`TrayApp(quick_menu_controls=...)`)
  - native `pystray.MenuItem`s built from the *same* `DashboardControl`
    objects the panel's pills use, via pystray's callable `text`/
  `enabled` params (re-evaluated whenever the menu is shown - no separate
  polling needed, same mechanism `main.py`'s old `llm_label()`/
  `mic_label()` closures already relied on for the panel). `main.py`
  passes the LLM and mic controls here; "Online" and "Stop speaking" stay
  dashboard-only since they're checked less often.
- **"Have a slider for volume, lowering it should lower the AI
  assistant's voice"**: new `DashboardSlider` dataclass (label/
  get_value/on_change, normalized to `0.0`-`1.0` so callers don't need to
  know Tk `Scale`'s `0`-`100` convention) rendered in the panel's top
  section below the pill grid. The slider itself only calls
  `on_change`/`get_value` - the actual volume multiply happens in
  `07-orchestrator/state_machine.py`'s `_apply_volume()`, since this
  module has no opinion on audio, only the widget.

Testing note: driving a Tk `Scale`'s `command` callback in a test needs
an explicit `root.update()` after `.set(...)` - unlike most of this
module's widgets, `Scale`'s command fires through the Tk event loop
rather than synchronously.

**Not yet confirmed on real hardware** - all of the above is unit-tested
only (`test_tray_app.py`'s icon-state/quick-menu tests, `test_dashboard.py`'s
log/ask/volume-section tests). Needs the user to see the tray dot
actually change color through a real conversation, click the new
LLM/mic quick-menu entries, and confirm the slider audibly changes
playback volume.

## Open decisions for this module

- **GNOME tray icon support — resolved.** Needed `sudo apt install
  libgirepository-2.0-dev` (system package, so left for the user to run)
  plus `pip install PyGObject` in the venv, so pystray can use its
  AppIndicator backend (`pystray._appindicator`) instead of the Xorg/XEmbed
  one — GNOME Shell doesn't support the legacy XEmbed systray protocol,
  only StatusNotifierItem/AppIndicator (via the already-installed
  `gnome-shell-extension-appindicator`). `PyGObject>=3.50.0` added to
  `requirements.txt` with a comment on the system-package prerequisite.
- Global hotkey (`text_input.hotkey`): **deferred**, per the "out of
  scope" note above — not implemented, since the tray icon now works fine
  as the way to trigger the popup. Left unset in `config.yaml`
  (`text_input.hotkey: null`).

## When done

Update `../../task.md`: check off `06-text-input`, record any tray-icon
extension dependency discovered.

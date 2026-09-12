# 08-transcript-ui

## Goal

Show the conversation on screen, live, in a minimal card pinned to the
**bottom of the display** — so that while you are talking to Gideon you can
see what it heard you say and what it is saying back, as it happens, rather
than reading a terminal log or opening the tray Dashboard.

Requested by the user after using the voice pipeline end to end: the
assistant worked, but a conversation was invisible. The tray Dashboard's
log pane shows *status* lines ("Thinking - waiting on the local LLM for a
reply"), not the words.

## Depends on

`00-shared` (the `TranscriptEvent`/`TranscriptSink` contract in
`shared/transcript.py`), and at runtime `07-orchestrator` as the producer.
Live partial transcription comes from `03-stt`'s `StreamingTranscriber`.
This module imports neither — the contract is the only coupling.

## Decisions taken with the user

| Question | Decision |
|---|---|
| How real-time should the user's own speech be? | **True live partials** — words appear while still speaking, via a second tiny whisper model. Gideon's replies already stream token-by-token. |
| When is the overlay on screen? | **Auto show / auto hide** — hidden while idle, fades in on the wake word, fades out a few seconds after returning to IDLE. |
| Is it interactive? | **Display only** — click-through, never takes keyboard focus, so it can never steal a keystroke. The tray Dashboard stays the place to type/stop/mute. |
| Which desktops? | **All of them** — Hyprland/Sway, GNOME/KDE Wayland, and plain X11. No compositor-specific assumptions. |

## Why a separate process

The overlay runs as its own process, fed newline-delimited JSON over a unix
socket, rather than as a thread inside the orchestrator:

- `text_input.tray.TrayApp.run()` already owns the main thread with
  Tkinter's event loop, and builds a fresh `tk.Tk()` per dashboard open. A
  persistent `Gtk.main()` cannot coexist with that.
- pystray's AppIndicator backend already runs a `Gtk.main()` on a
  background thread, and GTK is not thread-safe — a second in-process GTK
  window would be a latent crash.
- Crash isolation: a UI bug costs the transcript and nothing else.
- The UI can be developed and restyled with `--demo`, no mic or LLM needed.

## Portability: one renderer, three surfacing strategies

`surface.py` is the only file that cares what desktop it is on. The widget
tree and stylesheet are written once; only how the window gets pinned
differs, auto-detected at runtime.

| Environment | Strategy |
|---|---|
| wlroots Wayland (Hyprland, Sway, river, wayfire, labwc) | `gtk-layer-shell`: OVERLAY layer, BOTTOM anchor, `KeyboardMode.NONE`, exclusive zone `-1`, pinned to the monitor under the pointer. |
| X11, any WM (and XWayland) | Undecorated `UTILITY` window, keep-above, skip taskbar/pager, unfocusable, `move()`d to bottom-centre on every `size-allocate`. |
| Wayland *without* layer-shell (GNOME Mutter, KDE KWin) | Re-`execve` self once with `GDK_BACKEND=x11` and use the X11 strategy — Wayland proper offers no client window positioning at all. |

If none of those apply (a Wayland compositor with no layer-shell *and* no
XWayland) the overlay still runs, borderless and on-top, wherever the
compositor puts it, and logs plainly that placement is out of its hands.

`UTILITY` rather than `DOCK` or `NOTIFICATION` for the X11 path: `DOCK`
makes many WMs reserve screen space, and `NOTIFICATION` makes some position
the window themselves — both fight what we want.

## Deliverables

- `src/transcript_ui/protocol.py` — NDJSON framing and socket-path
  resolution (`$XDG_RUNTIME_DIR`, falling back to a uid-qualified temp
  path). Tolerant of garbage: a bad line costs one event, never the
  connection.
- `src/transcript_ui/client.py` — `TranscriptClient`, the orchestrator-side
  `TranscriptSink`. `emit()` only enqueues and returns; a background thread
  owns the socket, reconnects with backoff, and drops events when nobody is
  listening. Never blocks, never raises. Plus `NullTranscriptClient` for
  when the overlay is switched off in config.
- `src/transcript_ui/server.py` — the socket listener. `bind()` is split
  from `start()` so a duplicate instance exits before building a window
  instead of flashing a card on screen; a stale socket left by a crashed
  overlay is detected (by connecting to it) and reclaimed.
- `src/transcript_ui/model.py` — **all** the interesting logic, GTK-free:
  turn bookkeeping, partial→final swap, the typewriter, the auto-hide
  timer, level smoothing. Pure function of events plus a clock.
- `src/transcript_ui/view.py` — GTK3 widget tree, stylesheet, cairo status
  dot and level meter, and the single 60fps animation timer.
- `src/transcript_ui/surface.py` — the portability layer above.
- `src/transcript_ui/app.py` + `__main__.py` — `python -m transcript_ui`.
- `src/transcript_ui/launcher.py` — `OverlayProcess`, used by
  `07-orchestrator/main.py` when `transcript_ui.autostart` is on.
- `src/transcript_ui/demo.py` — a scripted fake conversation.

## Design notes worth keeping

**The typewriter.** LLM tokens do not arrive at a steady rate — Ollama
delivers them in bursts with irregular gaps. Painting each delta the
instant it lands reproduces that jitter on screen and looks broken. Deltas
accumulate in `Turn.pending` and are revealed at a constant ~55 chars/sec
in `tick()`. Once a reply is `final` the remaining backlog is flushed
within ~1.2s, so a fast model on a long answer does not leave text crawling
after Gideon has stopped speaking. User partials are **not** typewritten —
they are whole-utterance replacements shown immediately, since delaying the
user's own words would put the preview permanently behind reality.

**Click-through.** An empty cairo input region (`surface.make_click_through`)
means every click, scroll and hover passes through to whatever is
underneath — the overlay is physically incapable of intercepting input.
Combined with layer-shell's `KeyboardMode.NONE` (or X11's
`accept_focus=False`), it is purely something to look at.

**Transparency degrades honestly.** With no compositor there is no RGBA
visual, so `#card` gets a `no-alpha` class: opaque background, smaller
radius. Otherwise "rounded corners" would render as four black triangles.

**Colours are borrowed, not invented.** The state-dot colours come from
`text_input/tray.py`'s `_STATE_COLORS` and the accent from
`dashboard.py`'s `_PILL_ON`, so the tray dot, the dashboard pills and this
overlay read as one product.

## Bugs found and fixed while building this

These were all found by running the real thing and inspecting the live
surface with `hyprctl layers` / `hyprctl clients`, not by unit tests —
noted here because each one is invisible in code review:

1. **Card rendered 205px wide against a configured 720px.** A layer-shell
   surface anchored to one edge takes its size from the content's natural
   size and ignores `Gtk.Window.set_default_size` entirely. Fixed by a
   size *request* on the card instead. (`test_the_card_is_no_wider_than_configured`)
2. **Every transcript row squashed to a 1x1 allocation** — nothing visible
   but the status line. A resizable window keeps whatever size it was first
   allocated, and for a layer surface that is the height of an *empty*
   card: the card's natural height grew 58→144px while the surface stayed
   at 58. Fixed with `set_resizable(False)`, which makes the window track
   its child's natural size. (`test_the_card_grows_taller_as_turns_are_added`)
3. **Card grew to 1211px on long lines.** A GTK size request is a
   *minimum*, and a wrapping label reports its whole unwrapped line as its
   natural width. Fixed by wrapping the transcript in a
   `GtkScrolledWindow` with `hscrollbar_policy=NEVER` (which allocates its
   child exactly the viewport width) plus a `max_width_chars` measured from
   the real font through Pango, which GTK needs in order to compute the
   right natural *height* for the wrapped text.
4. **Overlay opened on the wrong monitor.** Without an explicit
   `GtkLayerShell.set_monitor`, the compositor picks — and on this
   three-head setup it routinely picked a side monitor while the user was
   working on the centre one. Now pinned to the monitor under the pointer.
5. **`AlreadyRunningError` escaped as an unhandled traceback**, because the
   socket was claimed in `run()` — after the window was built. Fixed by
   splitting `bind()` out of `start()`.
6. **X11 WM_CLASS was `__main__.py`.** `GLib.set_prgname` has to be called
   *before* the `Gtk` import that initialises GTK. Now `gideon-transcript`
   / `Gideon`, matching the layer-shell namespace, so one name addresses
   the overlay on either backend.
7. **A stale transcript was never cleared for a new conversation.** The
   staleness check read `visible` after it had already been set `True`, so
   it could never fire. (`test_a_new_conversation_clears_a_transcript_that_was_already_hidden`)
8. **A spoken follow-up drew its question as two identical "You" rows.**
   Reported by the user from a screenshot of the running overlay.
   `step()`'s follow-up branch had `self._emit_user_text(text)` *outside*
   its `if/else`, so the voice path published `user_final` twice - once
   from `_transcribe_and_log()`, once from the stray call. The typed path
   was correct (nothing else publishes for it), which is why only spoken
   follow-ups duplicated, and only follow-ups, not first turns.

   Missed by the tests because of a gap in how they were sliced: the
   follow-up tests drove `step()` but only asserted on the LLM calls, and
   the transcript tests asserted on events but called `_transcribe_and_log`
   in isolation. Nothing counted events across a whole multi-turn
   conversation. Now four tests do, one per path
   (`test_a_voice_followup_publishes_its_question_exactly_once` and
   friends), and the voice one is verified to fail against the old code.

   Also hardened the overlay side, since the same shape of bug can arrive
   from a source the orchestrator does not control: `_handle_user` now
   refuses to open a second row for anything that arrives after a user turn
   is already final - a duplicate `user_final`, or a partial that was still
   being computed when the final landed. `StreamingTranscriber` invalidates
   late partials by generation counter, but its callback fires outside that
   check, so end-to-end ordering was never actually guaranteed.
   (`test_a_duplicate_final_does_not_create_a_second_row`,
   `test_a_partial_arriving_after_the_final_is_ignored`)

## Standalone test plan

1. `python -m pytest modules/08-transcript-ui` — 108 tests. The model,
   protocol, client, server and backend-decision tests need no display;
   the view tests build a real window and skip automatically where there
   is none.
2. `python -m transcript_ui --demo` — replays a scripted conversation into
   the real overlay with no mic, model or LLM running. This is the loop for
   tuning the look.
3. `GDK_BACKEND=x11 python -m transcript_ui --demo` — exercises the X11
   fallback on a Wayland machine, proving the portable path without a
   second distro.
4. `python -m transcript_ui` in one terminal, `python -m transcript_ui.demo`
   in another — drives the overlay over the real socket, so it also checks
   the wire format end to end.
5. Confirm it is click-through: with the card on screen, click where it is
   and confirm the window underneath receives the click.

## Verification status

**Implemented and unit-tested (108 tests in this module, plus 14 for the shared event contract, 14 for `03-stt`'s `StreamingTranscriber` and 15 new orchestrator tests).**

**Confirmed on real hardware 2026-09-12** (this machine: Ubuntu 24.04,
Hyprland on Wayland, three 1920x1080 monitors):

- **layer-shell backend**: verified via `hyprctl layers` — namespace
  `gideon-transcript`, level 3 (overlay), on the pointer's monitor,
  horizontally centred, 722px wide, height tracking the conversation
  (95px → 264px), bottom edge consistently 48px above the screen edge.
- **X11 backend** (`GDK_BACKEND=x11`): verified via `hyprctl clients` —
  same width, same bottom-anchored positions, `class=Gideon`, floating.
- **Visual**: screenshotted mid-conversation. Both states render as
  designed — green dot + `LISTENING` + animated level meter with the live
  partial growing and correcting itself ("what's the whether" → "what's the
  weather like in"), then purple dot + `SPEAKING` with the reply streaming
  under a `GIDEON` label.
- Duplicate-instance guard produces one clear log line and no window.
- Clean shutdown on SIGTERM, socket unlinked.

**Not yet confirmed on real hardware**: the GNOME/KDE Wayland re-exec path
(this machine has layer-shell, so it takes the direct route — the X11
target of that fallback *is* verified, only the automatic re-exec trigger
is not), and the no-compositor `no-alpha` styling.

## Optional: real background blur on wlroots

The layer surface is namespaced, so Hyprland users who want a glassier card
can add one line to `hyprland.conf` — deliberately documented rather than
written into anyone's config:

```
layerrule = blur, gideon-transcript
layerrule = ignorealpha 0.2, gideon-transcript
```

## Out of scope

- Scrollback / conversation history beyond `max_turns` rows.
- Any interaction (typing, stop button) — see the decision table.
- A GTK4 port. GTK3 is what `06-text-input` already pulls in, and
  `gtk3-layer-shell` is more widely packaged than the GTK4 binding.

## When done

Update `../../docs/task.md`: check off `08-transcript-ui`, and note the
`gtk-layer-shell` system package as an optional (wlroots-only) dependency.

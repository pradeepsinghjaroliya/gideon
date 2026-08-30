# 07-orchestrator

## Goal

Wire every other module together into the actual background assistant: run
the state machine described in `../../ARCHITECTURE.md`, manage the
conversation history, and run as a proper background service on login.

**Build this last** — it depends on every other module being done (or at
least stable enough to import).

## Depends on

`00-shared`, `01-audio-io`, `02-wake-word`, `03-stt`, `04-llm-client`,
`05-tts`, `06-text-input`.

## Deliverables

- `src/orchestrator/state_machine.py` — implements the `IDLE -> LISTENING
  -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE` loop from
  `ARCHITECTURE.md`:
  - `IDLE`: feed mic frames to the wake-word detector; also watch for a
    text-input submission (run the tray icon on its own thread/process and
    check a queue) — either one moves to the next state.
  - On wake word: `LISTENING` — start recording, feed frames to VAD, stop
    when VAD says speech ended (plus a max-duration safety cutoff).
  - `TRANSCRIBING`: hand the recorded buffer to `STTEngine.transcribe`.
    Skipped entirely if this turn came from text input.
  - `THINKING`: call `LLMClient.generate` with the transcribed/typed text
    plus the last `config.orchestrator.history_turns` turns.
  - `SPEAKING`: mute the mic (per whatever contract `01-audio-io` defined),
    call `TTSEngine.synthesize`, play via `AudioSink.play`, un-mute when
    done.
  - Back to `IDLE`.
- `src/orchestrator/main.py` — entry point that loads config, constructs
  all module implementations from it, and runs the state machine loop
  forever (with clean shutdown on SIGTERM/SIGINT).
- A systemd **user** service unit (`gideon.service`, installed to
  `~/.config/systemd/user/`) that runs `main.py` on login, restarts on
  failure, and logs to journald. Document the enable/start commands in
  this file once written.

## Standalone test plan

This is the integration test — by now the "standalone" tests are really
end-to-end tests of the whole assistant:

1. Say the wake word, ask a simple factual question, confirm you hear a
   spoken answer.
2. Use the tray "Ask..." popup instead of voice, confirm the same flow
   works and produces a spoken answer.
3. Have a multi-turn conversation (voice or text) and confirm the LLM's
   answers show it remembers earlier turns.
4. Say something quietly/naturally right after the assistant *finishes*
   speaking — confirm it doesn't react to its own tail-end audio (mic
   gating working correctly).
5. Install the systemd service, reboot (or `systemctl --user restart`),
   confirm it comes up automatically and the tray icon appears without
   manually running anything.
6. Kill the process ungracefully, confirm systemd restarts it (if
   `Restart=on-failure` is set) and it comes back to a clean `IDLE` state.

## Out of scope (deferred beyond v1, note here if picked up later)

- Barge-in (interrupting TTS by speaking over it) — depends on the
  `AudioSink.stop()` capability `01-audio-io` exposes; wire it up here if
  in scope, otherwise note it's deferred.
- Multiple wake words / multiple "personas".
- Any GUI beyond the tray icon + popup from `06-text-input`.

## Setup

No new pip packages of its own — `orchestrator` only imports the other
modules, so `pip install -e ".[dev]"` from the repo root (with every other
module's own `requirements.txt` already installed) is enough.

```
.venv/bin/python -m orchestrator.main
```

### systemd user service

```
mkdir -p ~/.config/systemd/user
cp modules/07-orchestrator/systemd/gideon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gideon.service
journalctl --user -u gideon.service -f   # tail logs
```

The unit file hardcodes this machine's repo path and venv
(`/home/pjaroliya/pjaroliya/private/gideon/.venv/bin/python`) — update
`ExecStart`/`WorkingDirectory` if the repo ever moves. It needs the
desktop session's `DISPLAY`/`WAYLAND_DISPLAY`/`DBUS_SESSION_BUS_ADDRESS`
for the tray icon (Tkinter + pystray/AppIndicator) — modern GNOME on
Ubuntu imports these into the `systemd --user` environment automatically
on login, so no extra `Environment=` lines were added; if the tray icon
doesn't appear after `systemctl --user start`, check
`systemctl --user show-environment | grep -E 'DISPLAY|WAYLAND'` first.

## Open decisions for this module

- **`max_listen_seconds` safety cutoff: 15.0s**, hardcoded in
  `Orchestrator`'s constructor default rather than a new `config.yaml`
  field — same anti-speculative-config pattern used for `03-stt`'s
  `compute_type` and `05-tts`'s sentence gap. Revisit (or promote to a
  config field) if real usage shows it's too short/long.
- **Text-input integration**: rather than calling `TrayApp.run()` from
  `orchestrator.main`'s own thread (which would block it on `TrayApp`'s
  own queue loop instead of also watching the mic for the wake word),
  `main.py` runs the *entire* `TrayApp.run()` — tray icon thread and its
  own popup-driving loop — on one background thread, with `on_text` set to
  `text_queue.put`. The `IDLE` state polls that queue non-blockingly
  (`get_nowait()`) alongside reading mic frames, matching the "run the
  tray icon on its own thread ... check a queue" approach this plan
  originally sketched. This relies on Tkinter working from a non-main
  thread, which `06-text-input`'s own plan.md flagged as a macOS
  restriction, not a Linux one — fine for this Ubuntu-only project.
- **Barge-in: deferred**, per `../../ARCHITECTURE.md`'s open
  cross-cutting decision — not implemented. Mic is simply muted for the
  full `SPEAKING` duration (`AudioSink.play()` blocks until done), so
  talking over the assistant does nothing yet; `AudioSink.stop()` exists
  in `01-audio-io` for this to hook into later if wanted.
- **Empty-result handling**: if a turn produces no usable text (blank STT
  transcript, e.g. the wake word fired but the VAD's max-duration cutoff
  hit before any speech was heard), the state machine returns straight to
  `IDLE` without calling the LLM/TTS at all, rather than sending an empty
  prompt.

## Status reporting to the tray icon (added 2026-08-28, requested by the user)

After a first real-hardware run, the user asked how to tell whether the
assistant was still listening or already back to idle, and asked for a
tray-icon way to see this (helpful for a non-technical user, not just
someone reading `journalctl`). `Orchestrator` now takes an `on_status`
callback, called with a plain-English message at every state transition
(idle/listening/transcribing/thinking/speaking, including the reply text
once spoken); `main.py` wires it to `TrayApp.set_status` (see
`06-text-input/plan.md`'s "Status / logs menu item" section for the tray
side). Detailed timing (`%.2fs` durations) stays in the regular
`self._log.info(...)` calls, not the tray-facing status text, to keep the
tray log readable for a non-technical user - the timing numbers are still
in `journalctl --user -u gideon.service` for debugging.

## Follow-up window after speaking (added 2026-08-28, requested by the user)

After a first real-hardware run, the user asked not to require the wake
word again immediately for a follow-up question. Added an
`AWAITING_FOLLOWUP` phase (see `../../ARCHITECTURE.md`'s state machine
diagram) between SPEAKING and true IDLE: for `config.orchestrator.
followup_seconds` (default 10s), `Orchestrator._await_followup()` watches
for either speech (via VAD, no wake word needed - detected the same way
`_listen()` detects it) or a tray "Ask..." submission; either one starts a
new THINKING/SPEAKING turn directly (looping inside the same `step()`
call, not through `_idle()`/the wake word again); nothing within the
window returns to true IDLE. `followup_seconds` is a real `config.yaml`
field (`orchestrator.followup_seconds`), not hardcoded like
`max_listen_seconds`, since this is a user-facing tuning knob the user
explicitly asked to be able to control the feel of, not an internal
implementation detail.

**Bug found and fixed 2026-08-28**: the user tried this and reported the
follow-up window didn't seem to wait at all - it stopped listening right
after they finished a sentence. Root cause: nothing was reading the mic
during `_transcribe`/`_think`/`_speak` (STT, the LLM call, TTS synthesis +
playback all block the single orchestrator thread), but `MicAudioSource`'s
background capture callback keeps pushing real frames into its internal
queue regardless of whether anything's reading it - so a multi-second
backlog piled up un-drained during those phases. The very next read (in
`_await_followup`, right after SPEAKING) then drained that whole backlog
almost instantly: the original timeout math counted *samples read*, which
raced through the stale backlog in a fraction of a real second, and worse,
that stale audio (potentially including the assistant's own voice
picked up by the mic - no echo cancellation) could falsely look like a
live "speech detected" to the VAD. **Fixed** two ways:
1. `_await_followup`'s timeout is now wall-clock (`clock`, defaulting to
   `time.monotonic`, injectable for tests), not a sample count - correct
   regardless of any backlog.
2. `_transcribe_and_log`/`_think`/`_speak` now wrap their blocking call in
   `_default_drain_context()`, which runs a background thread that keeps
   calling `audio_source.read_chunk()` (discarding the result) for the
   duration - so the mic queue never backs up in the first place, and
   `_await_followup` never sees stale/backlogged audio at all.
`drain_context` is a constructor param (defaults to the real threaded
version) so unit tests inject `contextlib.nullcontext` instead - see
`test_state_machine.py`'s `_make_orchestrator` default and the dedicated
`test_speak_drains_mic_in_background_during_slow_playback`/
`test_think_drains_mic_in_background_during_slow_llm_call` regression
tests (which explicitly opt back into the real threaded drain).

**Confirmed on real hardware 2026-08-28**: user retested after the
wall-clock-timeout + mic-draining fix and confirmed it now works - the
follow-up window genuinely waits, and a follow-up question is correctly
picked up without needing the wake word again.

## Tray dashboard controls (added 2026-08-28, requested by the user)

Requested as a batch of ad hoc improvements (see root `tmp.md`, now
cleared into this writeup): more tray controls than just "Ask..."/"Status
/ logs...". Added four items to the tray menu (`main.py`'s
`_build_dashboard_menu_items`, inserted via `06-text-input`'s new
`TrayApp(extra_menu_items=...)` param - see that module's plan.md), plus a
new `Orchestrator` method per control:

- **LLM running indicator + start/stop** (`orchestrator/ollama_control.py`,
  new `OllamaControl` class): the menu label itself shows 🟢/🔴 running/
  stopped (re-checked - a live `GET {base_url}/api/tags` with a 1s
  timeout - every time the tray menu is opened, via pystray's callable
  `MenuItem` text, not a background poller); clicking it starts or stops
  Ollama. **Investigated first**: `ollama.service` is installed as a
  **system** (not user) systemd unit on this machine (confirmed via
  `systemctl status ollama` - "disabled; inactive (dead)"), so
  `systemctl start/stop` would need `sudo`, which the assistant must never
  run itself (standing rule from this session). Since the service is
  disabled anyway, the user must already be running `ollama serve`
  manually when testing - so `OllamaControl` manages a plain `ollama
  serve` process directly (`Popen`) and stops it via `pkill -f "ollama
  serve"`, entirely within the user's own privileges, no systemd/sudo
  involved. Flag this to the user to confirm it matches how they actually
  run Ollama.
- **Mic mute/unmute** (`Orchestrator.set_mic_muted`/`is_mic_muted`,
  checkable menu item): stacks with (doesn't replace) the existing
  auto-mute-during-SPEAKING - `_apply_mute()` (renamed from `_set_muted`)
  ORs `_user_muted_mic` and `_auto_muted_for_speaking`, so unmuting after
  a reply finishes can't silently override a manual mute the user set
  during that reply. While manually muted, `read_chunk()` returns zeros
  (the `01-audio-io` drain-but-zero contract), so the wake word/VAD just
  never trigger - no other state-machine changes needed for "mic off" to
  actually mean the assistant stops reacting to voice.
- **Online/offline** (`Orchestrator.set_online`/`is_online`, checkable
  menu item): offline means not watching for the wake word or a typed
  question at all. Implemented via a `threading.Event`
  (`_online_event`) - `run_forever()`'s loop blocks on it between turns
  (no busy-looping while offline), and `_idle()` also checks it on every
  mic frame so toggling offline mid-wait is noticed within one frame,
  not just between turns. `step()` itself doesn't block on the event
  (kept synchronously testable) - only `run_forever()` does.
- **Stop speaking** (`Orchestrator.stop_speaking`/`is_speaking`, enabled
  only while actually speaking): calls `AudioSink.stop()` (the
  interrupt/barge-in hook `01-audio-io` always had, per
  `../../ARCHITECTURE.md`, but never previously used) from the tray
  thread while `_speak()` blocks the main loop on `play()`. `_speak()`
  tracks a `_stop_requested` flag (reset to `False` *before* `_speaking`
  goes `True`, to close a narrow cross-thread race) so it can tell "the
  user asked for this" apart from a genuine playback error: only an
  exception raised after `stop_speaking()` was actually called is
  swallowed (logged, not re-raised) - an unrelated real error from
  `AudioSink.play()` still propagates normally. Either way, SPEAKING ends
  and the state machine proceeds straight to the follow-up window, per
  the user's ask ("after that it should maybe listen to me").

`06-text-input` stays orchestrator-agnostic - it just accepts a list of
already-built `pystray.MenuItem`s (`TrayApp(extra_menu_items=...)`)
instead of importing anything orchestrator-specific.

**Superseded 2026-08-28**: the four controls above initially shipped as
plain native tray menu items (`TrayApp(extra_menu_items=...)`). The user
wanted something closer to a GNOME quick-settings panel instead (a custom
grid of pill-shaped toggle buttons, not a text dropdown) - see
`06-text-input/plan.md`'s "Dashboard panel" section for the new
`text_input/dashboard.py` widget. `main.py`'s `_build_dashboard_controls`
now builds `DashboardControl`s instead of `pystray.MenuItem`s, wired to
the exact same `Orchestrator`/`OllamaControl` methods as before - only the
tray-side presentation changed, not the underlying control logic.

**Bug found and fixed 2026-08-28**: the user also reported clicking the
LLM control did not actually run `ollama serve` - no visible error, no
feedback, nothing happened. Root cause candidates: (a) a menu/panel click
callback that raises has nowhere to surface the error (pystray/GTK swallow
it), so a `FileNotFoundError` from `Popen(["ollama", "serve"])` if `ollama`
weren't resolvable on `PATH` in whatever context `main.py` runs under
would fail completely silently; (b) even on success, there was no
visible confirmation that anything happened, and the label only
re-evaluates when the panel/menu is next opened, so a successful click
could look like nothing happened for a few seconds. **Fixed**:
`OllamaControl.start()` now resolves the `ollama` binary explicitly via
`shutil.which` (rather than relying on `Popen`'s own `PATH` lookup) and
raises a clear `OllamaControlError` instead of letting `FileNotFoundError`
propagate silently if it's not found or fails to launch;
`main.py`'s `toggle_llm()` catches that and reports it through
`tray_app.set_status(...)` (visible in the "Status / logs..." window), and
also posts an immediate "Starting Ollama..."/"Stopping Ollama..." status
line so a successful action is visible right away, not just once the
panel/menu is reopened. Directly running the equivalent `Popen` call from
this session confirmed the underlying mechanism itself works fine on this
machine (`ollama` resolves to `/usr/local/bin/ollama`) - the fix targets
the *visibility* of success/failure, not a broken launch mechanism.

**Confirmed on real hardware 2026-08-28**: user confirmed "llm start
works perfectly" - the `shutil.which` resolution + status-log feedback
fix resolved it. Mic mute/offline toggle and "Stop speaking" are still
unconfirmed.

Follow-up: user asked why clicking the tray icon shows a dropdown menu
first instead of opening the dashboard panel directly, like GNOME's own
quick-settings tray icon. That's a pystray/AppIndicator protocol
limitation, not something specific to this project - see
`06-text-input/plan.md`'s matching follow-up note for the investigation.
"Dashboard..." is now the first menu item as the closest available
improvement.

## Icon state + voice volume (added 2026-08-30, requested by the user)

Two more items from the same batch as `06-text-input/plan.md`'s matching
section (see root `tmp.md`):

- **Colored tray icon**: `_set_status()` now takes an optional `state=`
  in addition to its existing free-text `message` - a small, fixed set of
  symbolic strings (`"idle"`, `"listening"`, `"processing"`, `"speaking"`)
  passed at every call site in `step()`, forwarded through a new
  `on_state` constructor param to `TrayApp.set_icon_state` (wired in
  `main.py`). Kept as a separate parameter rather than having `06-text-input`
  pattern-match `message`'s human-readable text, which is meant for the
  log, not machine parsing, and could change wording without meaning to
  change the icon color.
- **Assistant voice volume**: `set_volume`/`get_volume` store a plain
  `0.0`-`1.0` multiplier (`set_volume` clamps out-of-range input rather
  than raising, since a slider can't produce anything else meaningful
  anyway). Applied in `_speak()` via `_apply_volume()` just after
  `tts.synthesize()`, before the mic-mute/`audio_sink.play()` calls -
  multiplies in `float64` and clips to the dtype's range before casting
  back to `int16`, so a quiet multiply can't wrap around into a loud
  artifact the way a naive in-place int16 multiply would at high-amplitude
  samples. Deliberately *not* added to `config.yaml` - runtime-only state
  set from the dashboard slider (`06-text-input/plan.md`'s new
  `DashboardSlider`), since the ask was about a live control, not a
  persisted default.

Both unit-tested only so far (`test_step_reports_icon_state_at_each_stage_for_voice_turn`,
`test_set_volume_clamps_to_zero_one_range`, `test_speak_scales_audio_by_volume_before_playback`,
`test_speak_clips_instead_of_wrapping_at_high_volume`) - **not yet
confirmed on real hardware**, needs the user to watch the tray icon
change color through a real conversation and confirm the slider audibly
changes playback volume.

## Diagnosing the volume report + two crash/hang bugs found and fixed
(2026-08-30, reported by the user)

The user tested the icon/menu/volume batch above and reported three
things, in order:

1. **Volume didn't audibly lower output**, even though the system volume
   slider does. Reviewed `_apply_volume()` and the dashboard slider
   wiring end to end and found no logic bug - added temporary info-level
   logging (`set_volume`/`_apply_volume`, still in place) to make this
   diagnosable from the terminal/journal if it recurs. Confirmed the user
   *had* restarted the process with the current code (ruling out the
   obvious "stale running process" explanation), and the volume report
   turned out to be expected behavior, not a bug - see item 3 below for
   why (a change now takes effect on the very next *sentence*, not just
   the next full turn).
2. **"Stop speaking" crashed the whole service and closed the
   dashboard.** Real bug, root-caused and fixed in
   `01-audio-io/src/audio_io/sink.py` - see its plan.md's "Stop-speaking
   crash fixed" section (two threads racing to `close()` the same
   PortAudio stream).
3. **After that, typed input in the dashboard's ask box "did nothing" -
   a silent hang**, not a crash (the dashboard itself stayed responsive).
   Root-caused to `01-audio-io/src/audio_io/source.py`'s
   `MicAudioSource.read_chunk()` having no timeout - see its plan.md's
   "Mic-read hang fixed" section. The dashboard staying responsive while
   nothing happened was the key clue: it meant the *orchestrator's own
   thread* was stuck (in `_default_drain_context()`'s `thread.join()`),
   not the whole process.

The user's own diagnosis of item 1 while this was being investigated -
"if i lower the volume it reflects in this next output, not on going
output... can we do streaming instead... this will also solve volume
issue as it will get reflected next translation properly in streaming
mode" - was exactly right, and is what "Streaming replies" below
implements ("translation" here means TTS/speech synthesis, not a
separate feature - there isn't one in this project; see that section's
opening note).

## Streaming replies + "Stop generating" (added 2026-08-30, requested by
the user)

The user's core complaint: "currently user has to wait for whole llm
output to be generated, then the whole output gets translated [TTS'd],
so there is a gap in between where user is just sitting, waiting -
doesn't feel natural." Asked for streaming of both the LLM output and
the TTS/playback, with "stop generating" gracefully stopping both and
still accepting the next prompt properly.

`step()`'s inner loop no longer calls `_think()` then `_speak()` once
each per turn - it now calls one new method, **`_think_and_speak()`**,
which:

- Reads `LLMClient.generate_stream()`'s incremental text deltas through a
  new module-level `_stream_sentences()` generator (a plain punctuation-
  plus-whitespace boundary check - simple, not NLP-grade, documented as
  such; good enough since it only controls *where `_speak()` chunk
  boundaries fall*, not the correctness of the reply text itself).
- Synthesizes and speaks (`_speak()`, otherwise unchanged) each sentence
  the moment it's complete, instead of waiting for the whole reply - the
  assistant starts talking after the first sentence instead of the whole
  answer.
- Records the full accumulated reply to history in a `finally`, whether
  the turn completed normally or was stopped early - `_think()`'s history
  bookkeeping was extracted into a shared `_record_reply()` helper so
  both paths use identical trim/append logic.

`_think()` and `_speak()` themselves are **unchanged** and still directly
tested as standalone primitives (`_speak()` is in fact reused as-is, once
per sentence) - `step()` just no longer calls `_think()` directly.

**New unified stop control**: `stop_generating()` (replacing "Stop
speaking" in the dashboard - `main.py`'s `_build_dashboard_controls` now
wires "Stop generating" to it, enabled via the new `is_responding()`
rather than `is_speaking()`, so it stays clickable through *both*
sub-phases) sets `_stop_requested`, aborts current playback via
`AudioSink.stop()` if a sentence is mid-play (same mechanism
`stop_speaking()` already had), and calls the new `LLMClient.cancel()` to
interrupt the LLM stream even if it's sitting on a slow network read
between tokens - see `04-llm-client/plan.md`'s matching section for how
`OllamaClient.cancel()` does that safely.

**A real bug found while writing the streaming tests, fixed before this
shipped**: `_speak()` used to reset `_stop_requested = False` *after*
calling `TTSEngine.synthesize()` but *before* `AudioSink.play()`. That
was harmless in the old one-`_speak()`-call-per-turn design, but once
`_think_and_speak()` calls `_speak()` repeatedly per turn, a stop request
that arrived *during* that call's own `synthesize()` (a real, plausible
timing - Piper synthesis isn't instant) got silently wiped out right
before playback started, and the loop would carry on through the rest of
the reply completely unimpeded - a "stop" that visibly did nothing.
**Fixed**: the reset now happens *before* `synthesize()` instead of
after, and `_speak()` checks `_stop_requested` again right after
`synthesize()` returns, skipping `play()` entirely if a stop landed
during synthesis rather than clearing the flag. Caught immediately by
`test_stop_generating_cancels_llm_and_skips_remaining_sentences`, which
now asserts `sink.played == []` for exactly this case.

**Volume side effect (this is what actually answers the user's own
volume report above)**: `_apply_volume()` now runs once per sentence
instead of once per whole reply, so a volume change made mid-reply is
heard starting with the very next sentence rather than only the next
full turn - directly what the user asked for ("this will also solve
volume issue").

Unit-tested (11 new tests in `test_state_machine.py`: streaming/history/
status-per-sentence, `is_responding()` timing, three `stop_generating()`
scenarios including the synthesis-time-stop regression above, and three
for `_stream_sentences()` itself) - all 145 tests passing repo-wide.
**Not yet confirmed on real hardware** - needs the user to hear the first
sentence start before the rest of a longer reply finishes generating,
and to confirm "Stop generating" cleanly stops a real in-progress
Ollama + Piper turn and that the next prompt is accepted normally
afterward.

**Follow-up question from the user**: why does streaming look like "a
line at a time" in the log rather than "a word at a time," the way
OpenAI-style token streaming looks? Not a model or `generate_stream()`
limitation - Ollama really is delivering individual token deltas via its
NDJSON stream, same mechanism. It's a deliberate choice in
`_stream_sentences()`: deltas are buffered into whole sentences before
anything is synthesized, spoken, or logged, because Piper needs a
complete sentence to produce natural-sounding speech - synthesizing
word-by-word would sound choppy/robotic. Added `_think_and_speak()`'s new
`_log_deltas()` helper, which logs each raw token delta at `logging.DEBUG`
(the orchestrator's default level is `INFO`, per
`00-shared/src/shared/logging_setup.py` - the user needs to bump that to
see this) so real per-token arrival is directly visible if they want to
confirm it themselves, separate from the sentence-level speaking/log
granularity.

**Also fixed while addressing the ALSA crash above**: `_speak()` now
logs "playback stopped early by request" from a normal (non-raising)
`_audio_sink.play()` return too, not just from a caught exception - see
`01-audio-io/plan.md`'s "Abort-triggered ALSA xrun replaced with
cooperative chunked writes" section for why `SpeakerAudioSink.play()` no
longer raises on interruption at all.

## Verification status

Implemented and unit-tested (12 tests, `test_state_machine.py`, fully
scripted fake `AudioSource`/`AudioSink`/`VAD`/`WakeWordDetector`/`STTEngine`/
`LLMClient`/`TTSEngine` — no real mic/models/Ollama/tray needed): IDLE
correctly prioritizes/detects a wake-word rising edge vs. a queued text
submission vs. neither; LISTENING stops once VAD reports speech-then-silence
and separately respects the `max_listen_seconds` safety cutoff even if VAD
never reports silence; THINKING appends both turns to history and trims to
`history_turns`; SPEAKING mutes the audio source for the duration of
`AudioSink.play()` and reliably un-mutes afterward even if `play()` raises;
a full voice-triggered `step()` and a full text-triggered `step()` (which
skips STT entirely) both drive the whole pipeline correctly; an empty STT
transcript short-circuits back to IDLE without calling the LLM.

Real hardware: wake-word and tray-icon-triggered turns, multi-turn
history, and the follow-up window are all confirmed (see the dated
sections above). **Systemd (test plan items 5-6), confirmed 2026-08-29**:
installed the unit to `~/.config/systemd/user/gideon.service` and ran
`systemctl --user enable --now gideon.service` — came up
`active (running)` with a clean `starting mic and tray icon` -> `ready`
-> `Idle - waiting...` log sequence in `journalctl --user -u
gideon.service`, and `enable` registered it in `default.target.wants` so
it starts on every login without a manual step (item 5's mechanism -
not a literal reboot, but the piece that makes a reboot work). Then
`kill -9` on the running `MainPID` produced
`Failed with result 'signal'` followed by an automatic restart ~2s later
(`RestartSec=2`) back to the same clean `Idle` log line (item 6).
`DISPLAY`/`WAYLAND_DISPLAY`/`DBUS_SESSION_BUS_ADDRESS` were present in
`systemctl --user show-environment`, matching what the tray icon needs.

**Still needs the user directly**: confirming the tray icon is actually
*visible* when launched via systemd (a shell can't observe a GUI), test
plan item 4 (mic not reacting to the assistant's own tail-end audio -
needs a live voice check), and the measured wake-word ->
first-spoken-word latency (flagged as a possible 5-8s+ concern by
`05-tts`'s plan.md, given `qwen2.5:1.5b` + `en_US-lessac-high`'s combined
latency).

## When done

Update `../../task.md`: check off `07-orchestrator`, mark all modules
complete, and record the systemd enable/start commands used plus any
end-to-end latency measured (wake word -> spoken answer, start to finish)
so future performance work has a baseline.

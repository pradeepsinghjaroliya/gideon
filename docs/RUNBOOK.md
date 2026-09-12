# Running and updating Gideon

Day-to-day operator notes — running it locally while you work on it,
starting it back up, and rebuilding/testing after a code change. See
`../README.md`/`ARCHITECTURE.md` for the design, `task.md` for progress.

Two ways to run it, for two different purposes: `scripts/dev.sh` in a
terminal while you're changing the code, and `gideon.service` in the
background the rest of the time. They both drive the same
`python -m orchestrator.main`, and only one of them can hold the mic and
tray at a time.

## Running it locally with `scripts/dev.sh`

The development entry point: it runs Gideon in the *foreground*, attached to
your terminal, so log lines and exceptions land where you can read them
immediately. `Ctrl+C` stops it.

```
scripts/dev.sh
```

Before starting anything it does the setup and preflight work that is
otherwise easy to forget:

- **Venv and dependencies** — creates `.venv` if it is missing and installs
  everything, including the two awkward cases the plain `pip install -r`
  path gets wrong: `01-audio-io`'s torch/torchaudio from PyTorch's CPU wheel
  index, and `02-wake-word`'s openWakeWord via `--no-deps` (its declared
  `tflite-runtime` has no Python 3.12 wheel and the ONNX backend is forced
  anyway). On later runs it only checks that `orchestrator.main` imports.
- **openWakeWord's feature extractors** — downloaded once if absent. They
  are not in the pip wheel, and the committed `hey_gideon.onnx` still needs
  them.
- **Preflight** — config present; the configured wake-word model present;
  `sounddevice` and GTK 3 importable, each with the exact `apt` line if not.
- **Cursor-theme check** — a self-inheriting `~/.icons/default`
  (`Inherits=default`, which `nwg-look` can write) segfaults *every* GTK3
  Wayland app at display open, and surfaces here as a misleading
  pystray/Gdk traceback. The script refuses to start and names the file
  rather than letting you debug the Python.
- **Gets the service out of the way** — stops `gideon.service` if it is
  running (it would otherwise be holding the mic and tray), and reminds you
  to start it again afterwards. Also warns if another `orchestrator.main` is
  already running.
- **Ollama** — reads `llm.base_url`/`llm.model` from `config/config.yaml`,
  starts `ollama serve` detached if nothing is answering, waits for it, and
  warns if the configured model has not been pulled.

Flags:

```
scripts/dev.sh --setup       # force a dependency (re)install first
scripts/dev.sh --tests       # run the unit tests first, then start
scripts/dev.sh --no-ollama   # leave Ollama alone even if it is down
scripts/dev.sh --check       # preflight only - don't start Gideon
scripts/dev.sh --help
```

`--check` is the one to reach for when something is wrong but you are not
sure what: it prints the same diagnosis and exits without touching the mic.

## Starting it again after "Quit"

Gideon runs as a systemd **user** service (`gideon.service`), already
installed and enabled to start automatically on every login. (On a machine
where it was never installed — `systemctl --user status gideon.service` says
it can't find the unit — skip this section; `scripts/dev.sh` runs Gideon
without it, and the unit file under
`modules/07-orchestrator/systemd/` has absolute paths that need to match the
checkout before it is copied into `~/.config/systemd/user/`.) Clicking "Quit"
in the tray menu only stops the *current* run — it doesn't disable the
service, so the easiest way back is:

```
systemctl --user start gideon.service
```

Useful related commands:

```
systemctl --user status gideon.service        # is it running right now?
journalctl --user -u gideon.service -f         # tail live logs (Ctrl+C to stop)
systemctl --user stop gideon.service           # stop it (same effect as Quit)
systemctl --user restart gideon.service        # stop + start in one go
```

You don't need to touch `systemctl --user enable` again unless you ran
`disable` at some point — `enable --now` was already run once during setup,
so a reboot or fresh login starts Gideon on its own without any of the
above.

## Rebuilding/reinstalling after a code change

There's no compiled build step — it's plain Python running from an editable
install, so most changes just need the service restarted to pick them up.

1. **Make your code change** under `modules/<name>/src/...`.
2. **If you added/removed a module's package, or a new module's
   `pyproject.toml` entry** (rare — only when adding a whole new module, not
   for edits inside an existing one), reinstall:
   ```
   .venv/bin/pip install -e ".[dev]"
   ```
   For an ordinary edit inside an existing module's files, this step isn't
   needed — the editable install already points at your source tree.
3. **Run the tests** before restarting the live service:
   ```
   .venv/bin/python -m pytest modules/ -q
   ```
   (`scripts/dev.sh --tests` runs these and then starts Gideon in the
   foreground, if you want both in one command.)
4. **Restart the service** so it picks up the change:
   ```
   systemctl --user restart gideon.service
   ```
5. **Watch the logs** to confirm it came back up cleanly:
   ```
   journalctl --user -u gideon.service -f
   ```
   Look for `starting mic and tray icon` → `ready` → `Idle - waiting...`.
   `Ctrl+C` stops tailing (doesn't stop the service).
6. **Exercise the change for real** — say the wake word or use the tray
   "Ask..."/Dashboard, per whatever you changed. Unit tests catch logic
   bugs, not "does this actually work with the real mic/LLM/tray."

### If you'd rather test without the service running

Use `scripts/dev.sh` (above) — it stops the service for you, runs the
preflight, and starts Gideon in the terminal. `Ctrl+C` stops it; start the
service again afterwards so it goes back to running in the background:

```
scripts/dev.sh
# ... Ctrl+C when done ...
systemctl --user start gideon.service
```

The equivalent by hand, if you want to skip the preflight entirely or need
to pass something unusual to the interpreter:

```
systemctl --user stop gideon.service
.venv/bin/python -m orchestrator.main
```

Individual modules also have their own demo scripts (e.g.
`wake_word.listen_demo`, `stt.transcribe_file`, `llm_client.chat_demo`,
`tts.speak_demo`, `text_input.tray_demo`) for exercising one stage on its
own without the whole pipeline.

### If you changed the systemd unit file itself

Only needed if you edit `modules/07-orchestrator/systemd/gideon.service`
(e.g. changing `ExecStart`, `Restart`, environment). Re-copy it and reload:

```
cp modules/07-orchestrator/systemd/gideon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart gideon.service
```

## Transcript overlay

The bottom-of-screen conversation transcript (`08-transcript-ui`) is a
separate process. With `transcript_ui.autostart: true` (the default) the
orchestrator starts and stops it for you, so there is nothing extra to run.

To run it by hand — useful when developing, or if you set
`autostart: false`:

```
. .venv/bin/activate
python -m transcript_ui
```

To see it working with no mic, model or LLM involved:

```
python -m transcript_ui --demo
```

To exercise the X11 fallback path on a Wayland machine:

```
GDK_BACKEND=x11 python -m transcript_ui --demo
```

### If the overlay does not appear

1. **Check which backend it chose** — it logs one line at startup:
   `transcript overlay using the <layer-shell|x11|wayland-plain> backend`.
2. **`wayland-plain`** means the compositor supports neither
   `wlr-layer-shell` nor client window positioning (GNOME, KDE), and no
   XWayland was available to fall back to. Install `gtk-layer-shell` if you
   are on Hyprland/Sway (see `modules/08-transcript-ui/requirements.txt`),
   or make sure XWayland is running.
3. **Nothing on screen at all, no errors** — the overlay only shows itself
   while a conversation is happening. Say the wake word, or run with
   `--demo` to confirm it can draw.
4. **`a transcript overlay is already running on ...`** — exactly what it
   says; one instance owns the socket. Kill the old one
   (`pkill -f 'm transcript_ui'`) or leave it be.
5. **Stale socket after a hard kill** — handled automatically: the next
   start detects that nothing is listening behind the file and reclaims it.

The overlay is click-through by design: clicks, scrolling and hovering all
pass through to whatever is underneath, and it never takes keyboard focus.
That is intentional, not a bug — it cannot steal a keystroke from whatever
you are actually working in.

# Running and updating Gideon

Day-to-day operator notes — starting it back up, and rebuilding/testing after
a code change. See `README.md`/`ARCHITECTURE.md` for the design, `task.md`
for progress.

## Starting it again after "Quit"

Gideon runs as a systemd **user** service (`gideon.service`), already
installed and enabled to start automatically on every login. Clicking "Quit"
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

Stop the service first so it isn't also holding the mic/tray, then run it
directly in a terminal (handy for reading exceptions immediately or using
`--voice`/other demo-script flags on individual modules):

```
systemctl --user stop gideon.service
.venv/bin/python -m orchestrator.main
```

`Ctrl+C` stops it. Start the service again afterwards (`systemctl --user
start gideon.service`) so it goes back to running normally in the
background.

### If you changed the systemd unit file itself

Only needed if you edit `modules/07-orchestrator/systemd/gideon.service`
(e.g. changing `ExecStart`, `Restart`, environment). Re-copy it and reload:

```
cp modules/07-orchestrator/systemd/gideon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart gideon.service
```

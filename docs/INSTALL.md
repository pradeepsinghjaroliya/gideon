# Installing Gideon

A single `.deb` containing Gideon, its own Python runtime, every Python
dependency, PortAudio, the tray-icon stack and all model weights. **Nothing is
downloaded during installation.**

- **Target:** Ubuntu 24.04 LTS (noble), amd64, GNOME desktop
- **Installed size:** ~2.1 GB
- **One prerequisite:** a local Ollama instance (see below)

## Install

```
sudo apt install ./gideon_0.1.0-1_amd64.deb
```

`apt` is preferred over `dpkg -i` because it resolves the handful of GTK
dependencies. On a machine that already has a stock Ubuntu desktop they are
all present, so nothing is fetched.

Verify the download first if you like:

```
sha256sum -c gideon_0.1.0-1_amd64.deb.sha256
```

### Start it

The service is enabled for every user at their next login. To start it
immediately in your current session, **as your desktop user, not root**:

```
systemctl --user start gideon.service
```

A package install cannot reliably start a *user* service in an
already-running session, which is why this one step is manual.

### Check it worked

```
systemctl --user status gideon.service
journalctl --user -u gideon.service -f
```

A healthy start logs:

```
starting mic and tray icon
ready - say the wake word or use the tray icon's 'Ask...'
Idle - waiting for the wake word or a typed question
```

Then say **"hey gideon"**, or click the tray icon → **Ask...** to type.

## The Ollama prerequisite

Gideon bundles everything except the language model backend. It talks to
Ollama over HTTP at `http://localhost:11434`; Ollama is a separate ~3 GB
upstream product with its own release cadence and model store, so it is not
vendored here.

Without it Gideon still installs, starts, hears the wake word and transcribes
speech — but answering fails with:

```
could not connect to Ollama at http://localhost:11434 - is 'ollama serve' running?
```

To satisfy it:

```
ollama serve &
ollama pull qwen2.5:1.5b     # the model named in /etc/gideon/config.yaml
```

The tray dashboard has an **LLM** toggle that starts and stops `ollama serve`
for you, provided `ollama` is on `PATH`. Note that Ollama does not start at
boot unless you have configured it to.

## Configuration

| File | Contents |
|---|---|
| `/etc/gideon/config.yaml` | models, audio devices, system prompt |
| `/etc/default/gideon` | environment: offline guards, thread limits, pystray backend |

Both are dpkg **conffiles** — your edits survive package upgrades, and dpkg
prompts if an upgrade changes the shipped version.

After editing either:

```
systemctl --user restart gideon.service
```

The packaged config differs from the repo's only in using absolute paths, so
that nothing depends on a working directory or reaches the network:

```yaml
wake_word:
  model: /opt/gideon/models/wake_word/hey_gideon.onnx
stt:
  model_path: /opt/gideon/models/whisper/small
tts:
  voices_dir: /opt/gideon/models/piper
```

## What gets installed where

```
/opt/gideon/python/       bundled CPython 3.12 + all 51 dependencies
/opt/gideon/app/          the 8 Gideon packages
/opt/gideon/models/       whisper small, piper voice, wake word, oWW extractors
/opt/gideon/native/       vendored PortAudio, libjack, Ayatana libraries
/opt/gideon/typelib/      AyatanaAppIndicator3 GI typelib
/opt/gideon/syslink/      symlinks to the system's python3-gi and python3-cairo
/etc/gideon/config.yaml
/etc/default/gideon
/usr/bin/gideon
/usr/lib/systemd/user/gideon.service
/usr/share/doc/gideon/
```

Gideon writes **no** state, logs, sockets or PID files. Logging goes to the
journal.

## Running it by hand

`/usr/bin/gideon` is a plain wrapper that sets up the environment and runs the
orchestrator, so you can run it directly to see output on your terminal:

```
gideon
```

`Ctrl+C` stops it. Stop the service first, or the two will fight over the
microphone.

## Upgrading

```
sudo apt install ./gideon_0.1.0-2_amd64.deb
systemctl --user restart gideon.service
```

The running instance is deliberately left alone during the upgrade
transaction; restart it when you are ready.

## Uninstalling

```
sudo apt remove gideon     # stops and disables the service, removes /opt/gideon
sudo apt purge gideon      # the above, plus /etc/gideon and /etc/default/gideon
```

No system user or group is ever created, so none is left behind. `purge`
leaves nothing: no units, no config, no directories.

## Troubleshooting

**No tray icon, but voice works.** GNOME needs a StatusNotifierItem host —
the `gnome-shell-extension-appindicator` package (a `Recommends`, normally
already installed). Check it is enabled:

```
gnome-extensions list --enabled | grep appindicator
```

Without it the pipeline is fine but "Ask..." and the dashboard are
unreachable. Forcing `PYSTRAY_BACKEND=xorg` in `/etc/default/gideon` also
runs, but the icon stays invisible on GNOME.

**`PortAudio library not found`.** The vendored copy is in
`/opt/gideon/native`, located via a shim in the bundled interpreter. If this
appears, check the shim survived:

```
ls /opt/gideon/python/lib/python3.12/site-packages/zz_gideon_native.pth
ls /opt/gideon/native/libportaudio.so.2
```

**No audio devices / mic silent.** Gideon uses the session's PipeWire, so it
must run as your logged-in desktop user — `systemctl --user`, never
`systemctl` or `sudo`. List what it can see:

```
/opt/gideon/python/bin/python3 -c "import sounddevice; print(sounddevice.query_devices())"
```

**Service does not start at login.** Confirm it is enabled for your user:

```
systemctl --user is-enabled gideon.service
systemctl --global enable gideon.service    # re-run as root if not
```

**It answers slowly.** `OMP_NUM_THREADS=4` in `/etc/default/gideon` caps CPU
threads — torch and ctranslate2 each bundle a private OpenMP runtime and
oversubscribe the machine at their defaults. Raise it on a bigger box.

# Building the Gideon `.deb`

Produces `dist/gideon_0.1.0-1_amd64.deb` — a single package that installs
Gideon and every dependency onto a machine with **no internet access**.

## Build host requirements

The build host must match the target: **Ubuntu 24.04 (noble), amd64**. Native
extensions and the vendored system libraries are release-specific.

| Tool | Why | Ubuntu package |
|---|---|---|
| `dpkg-deb`, `dpkg-dev` | builds the archive | `dpkg-dev` |
| `fakeroot` | `root:root` ownership without sudo | `fakeroot` |
| `apt-get download` | fetches the vendored system libraries | (base) |
| a CPython 3.12 to bundle | becomes `/opt/gideon/python` | see below |

No compiler is needed: every Python dependency resolves to a wheel, and the
vendored `.so` files are extracted from official Ubuntu binary packages.

`debhelper` and `dpkg-buildpackage` are deliberately **not** used — the payload
is a plain file tree with four short maintainer scripts, so `dpkg-deb` on a
hand-assembled tree is sufficient and has no build-host coupling.

### The bundled interpreter

`build.sh` looks for a [python-build-standalone][pbs] CPython 3.12 at
`~/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu` and copies it to
`packaging/cpython/`. The easiest way to get one:

```
uv python install 3.12
```

It must be a *relocatable* build (`RPATH=$ORIGIN/../lib`). A system
`/usr/bin/python3.12` will **not** work: a `venv` built from it bakes absolute
paths into `pyvenv.cfg` and every shebang, and creating one at the final
`/opt/gideon` path would need root on the build host.

Bundling the interpreter also means the package needs no `python3-tk`
(tkinter, Tcl and Tk ship inside it) and is immune to the target's python3
patch level.

[pbs]: https://github.com/astral-sh/python-build-standalone

## Build

```
./packaging/build.sh
```

Roughly 5 minutes, most of it `xz` compression. Output:

```
dist/gideon_0.1.0-1_amd64.deb
dist/gideon_0.1.0-1_amd64.deb.sha256
```

Options:

| Flag | Effect |
|---|---|
| `--offline` | fail instead of fetching; rebuild purely from what is already in `packaging/` |
| `--lint` | additionally run `lintian` if it is installed |

Every fetch stage is skipped when its output already exists, so a second run
never touches the network — `--offline` just makes that guarantee explicit.

## What the build fetches (once)

| Stage | Into | Size |
|---|---|---|
| 51 Python wheels | `packaging/wheelhouse/` | 392 MB |
| 9 Ubuntu `.deb`s | `packaging/vendor/debs/` | 1.3 MB |
| CPython 3.12 | `packaging/cpython/` | 110 MB |
| Model weights | `packaging/assets/` | 575 MB |

All four are `.gitignore`d.

### Staging the model assets

`build.sh` expects `packaging/assets/` to already exist — the models are large
and come from three different upstreams, so they are staged once by hand:

```
A=packaging/assets
mkdir -p $A/wake_word $A/openwakeword $A/whisper/small $A/piper

# custom wake word (in the repo)
cp modules/02-wake-word/models/hey_gideon.onnx $A/wake_word/

# openWakeWord shared feature extractors
python -c "from openwakeword import utils; utils.download_models()"
OWW=.venv/lib/python3.12/site-packages/openwakeword/resources/models
cp $OWW/melspectrogram.onnx $OWW/embedding_model.onnx $A/openwakeword/

# faster-whisper small - -L dereferences the Hugging Face cache symlinks
SNAP=$(ls -d ~/.cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots/*/ | head -1)
cp -L "$SNAP"/* $A/whisper/small/

# piper voice
cp ~/.cache/piper-voices/en_US-lessac-high.onnx{,.json} $A/piper/
```

The Whisper and Piper files land in those caches the first time Gideon runs
from a checkout; `openwakeword.utils.download_models()` is a one-off manual
step (the library never calls it itself).

## Dependency pinning

`packaging/constraints.txt` is the lock file this repo otherwise lacks. It was
generated from the working `.venv` — the only place the transitive closure has
ever actually been resolved — minus `pytest`, `pluggy`, `iniconfig`,
`Pygments` (dev-only) and the `gideon` editable itself. **51 distributions.**

Two entries need care when regenerating it:

- **`torch` / `torchaudio`** carry a `+cpu` local version that exists only on
  `https://download.pytorch.org/whl/cpu`, and are tagged plain
  `cp312-cp312-linux_x86_64` (not manylinux). pip refuses such a wheel from an
  index — it installs only via `--find-links`, which is what the wheelhouse
  gives us. Both `pip download` and `pip install` therefore pass
  `--extra-index-url`/`--find-links` accordingly.
- **`openwakeword`** declares `tflite-runtime <3,>=2.8.0`, which has **no cp312
  wheel**. Both pip steps pass `--no-deps` (correct anyway, since
  `constraints.txt` is already the full closure). `detector.py:39` forces
  `inference_framework="onnx"`, so tflite is never imported. Dropping
  `--no-deps` makes the build fail outright.

Completeness is not taken on trust: the verification step imports and exercises
every subsystem for real (see INSTALL.md).

## Vendored system libraries

Nine Ubuntu 24.04 binary packages are extracted into `/opt/gideon/native` and
`/opt/gideon/typelib`, because they are **not** present on a stock Ubuntu 24.04
desktop and an offline target could not fetch them:

| Package | Version | Provides |
|---|---|---|
| `libportaudio2` | 19.6.0-1.2build3 | `libportaudio.so.2` for `sounddevice` |
| `libjack-jackd2-0` | 1.9.21~dfsg-3ubuntu3 | `libjack.so.0` — a hard `NEEDED` of libportaudio |
| `libdb5.3t64` | 5.3.28+dfsg2-7 | `libdb-5.3.so` — pulled in by libjack |
| `libayatana-appindicator3-1` | 0.5.93-1build3 | tray icon backend |
| `libayatana-indicator3-7` | 0.9.4-1build1 | ↑ dependency |
| `libayatana-ido3-0.4-0` | 0.10.1-1build2 | ↑ dependency |
| `libdbusmenu-glib4` | 18.10.20180917…3.1ubuntu5 | ↑ dependency |
| `libdbusmenu-gtk3-4` | 18.10.20180917…3.1ubuntu5 | ↑ dependency |
| `gir1.2-ayatanaappindicator3-0.1` | 0.5.93-1build3 | the GI typelib |

Everything else Gideon needs from the system (`python3-gi`, `python3-cairo`,
`gir1.2-gtk-3.0`, the GTK/GLib libraries, `libstdc++6`, `libgcc-s1`, `procps`,
`dbus-user-session`) *is* on every Ubuntu 24.04 GNOME desktop and is declared
as a normal `Depends:`.

### How they are found at runtime, and why not `ld.so.conf.d`

The wrapper sets `LD_LIBRARY_PATH=/opt/gideon/native`, which covers everything
resolved by `dlopen()` — including GObject-Introspection loading the Ayatana
libraries, and libportaudio's own `NEEDED` on libjack.

That leaves exactly one gap. `sounddevice` locates PortAudio with
`ctypes.util.find_library('portaudio')` and raises
`OSError('PortAudio library not found')` if it returns `None` — and on Linux
`find_library()` consults `ld.so.cache` only. This was measured, not assumed:

```
$ LD_LIBRARY_PATH=./native python3 -c "from ctypes.util import find_library; print(find_library('zzgideonprobe'))"
None
$ LD_LIBRARY_PATH=./native python3 -c "import ctypes; print(ctypes.CDLL('libzzgideonprobe.so.1'))"
<CDLL 'libzzgideonprobe.so.1', ...>
```

The obvious fix — dropping `/opt/gideon/native` into `/etc/ld.so.conf.d/` and
running `ldconfig` — works, but registers all nine libraries **system-wide**,
where they can shadow the distro's own copies for unrelated processes. That is
far too large a blast radius for a desktop application.

Instead, `packaging/gideon_native.py` (installed into the bundled
interpreter's `site-packages`, loaded by `zz_gideon_native.pth`) points
`find_library()` at `/opt/gideon/native` first. It affects Gideon's
interpreter and nothing else, and the package writes nothing outside
`/opt/gideon`, `/etc/gideon`, `/etc/default/gideon`, `/usr/bin`,
`/usr/lib/systemd/user` and `/usr/share/doc`.

## PyGObject and pycairo

Neither is pip-installable here without a full GTK build toolchain
(`libgirepository-2.0-dev`, meson, ninja), and Ubuntu 24.04 ships PyGObject
**3.48.2** — which is why `modules/06-text-input/requirements.txt` had its
`>=3.50.0` floor relaxed to `>=3.48`.

The package therefore `Depends:` on `python3-gi` and `python3-cairo` and
symlinks exactly those two modules into `/opt/gideon/syslink`, exposed via a
`.pth`. Their compiled extensions are `cp312`-tagged and load fine in the
bundled 3.12 runtime. Because `.pth` entries append to the **end** of
`sys.path`, bundled packages always take precedence — unlike the development
`.venv`'s `system-gi.pth`, which put all of `/usr/lib/python3/dist-packages`
on the path.

## Source changes packaging depends on

Four patches make an installed (non-editable) layout viable. Each keeps the
checkout workflow working — the old behaviour remains the fallback:

| # | File | Change |
|---|---|---|
| P1 | `modules/00-shared/src/shared/config.py` | config lookup order: explicit `path=` → `$GIDEON_CONFIG` → `/etc/gideon/config.yaml` → repo-relative. Adds `SttConfig.model_path` and `TtsConfig.voices_dir`. |
| P2 | `modules/07-orchestrator/src/orchestrator/main.py` | passes `model_path=` and `voices_dir=` through to the STT and TTS engines |
| P3 | `modules/03-stt/src/stt/engine.py` | accepts a pre-shipped CTranslate2 model directory, with `local_files_only=True` |
| P4 | `modules/06-text-input/requirements.txt` | `PyGObject>=3.48`; corrects the tkinter comment |

Without P1 the installed `shared/config.py` cannot find its config
(`parents[4]` only resolves under an editable install). Without P2/P3 the
service downloads ~595 MB from Hugging Face on first run.

## Version bumps

Edit `VERSION` in `packaging/build.sh` and add an entry to
`packaging/debian/changelog`, then rebuild.

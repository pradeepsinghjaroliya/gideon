# gideon

A fully local, offline voice assistant for Ubuntu. Runs in the background,
wakes on a voice command (or a manual popup/tray trigger), converts speech to
text, sends the request to a local LLM, and speaks the answer back.

Everything runs locally on open-source components. No cloud APIs.

## Pipeline

```
[wake word] --> [audio capture + VAD] --> [speech-to-text] --> [local LLM] --> [text-to-speech] --> [playback]
                        ^                                                                              |
                        |____________________ mic gated while speaking _________________________|

[popup/tray text input] ---------------------------------------------------------> (skips STT, feeds LLM directly)

Every stage also reports what it heard/said to the transcript overlay - a
minimal card pinned to the bottom of the screen showing the conversation as
it happens (live partial transcripts while you speak, the reply streaming
token-by-token as it is generated).
```

## Working model: one module, one session

Each module under `modules/` is designed to be built **independently, in its
own fresh Claude Code session**, using only that module's `plan.md` plus
`docs/ARCHITECTURE.md` (the shared contracts). A session working on module N does
not need the history of how module N-1 was built — just its interface.

Workflow:
1. Open a new session.
2. Read `docs/ARCHITECTURE.md` (shared interfaces/config schema) and the target
   module's `modules/<name>/plan.md`.
3. Implement the module so it satisfies its interface and passes its own
   standalone test plan (no dependency on other unfinished modules).
4. Update `docs/task.md` — check off the module and note any decisions/deviations.
5. If the module's plan.md needed changes to match reality, update it so the
   next reader has the correct picture.

`00-shared` should be done first (it defines the interfaces every other
module implements). `07-orchestrator` must be done last (it wires all
modules together into the background service). The modules in between
(`01`–`06`) can be done in any order once `00-shared` exists.

## Setup

The quickest path — `scripts/dev.sh` creates the venv, installs everything
(including the two dependency quirks below), checks the mic/GTK/Ollama
prerequisites, and then runs Gideon in the foreground:

```
scripts/dev.sh --setup
```

Later runs need no flag (`scripts/dev.sh`); `scripts/dev.sh --check` does the
preflight without starting anything. See `docs/RUNBOOK.md` for the full set
of flags and for running it as a background service instead.

To do it by hand — single shared venv at the repo root, editable install:

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Some modules need extra dependencies that can't live in the root
`pyproject.toml` (e.g. `01-audio-io`'s torch/torchaudio need a pinned pair
from PyTorch's own CPU wheel index). Check for a `requirements.txt` in the
module's directory and `pip install -r` it. Two of them have caveats worth
reading before installing by hand: `02-wake-word`'s openWakeWord needs
`--no-deps` (its declared `tflite-runtime` has no Python 3.12 wheel, and the
ONNX backend is forced anyway), and `06-text-input`/`08-transcript-ui`'s
PyGObject needs the system `libgirepository-2.0-dev` present first. Each
module's `requirements.txt` documents its own.

## Docs

- `docs/ARCHITECTURE.md` — pipeline, interface contracts, audio format
  conventions, config schema. Read this before starting any module.
- `docs/task.md` — progress tracker across all modules.
- `modules/<name>/plan.md` — per-module goal, chosen library, deliverables,
  and standalone test plan.
- `docs/RUNBOOK.md` — running it locally with `scripts/dev.sh`, restarting
  the service after "Quit", and how to rebuild/test/reinstall after a code
  change.

## Tech choices (starting point, swappable later)

| Stage | Library | Notes |
|---|---|---|
| Wake word | openWakeWord | Local, open source, pretrained + custom-trainable |
| Audio capture/playback + VAD | sounddevice/pyaudio + Silero VAD | Standard mic/speaker I/O |
| Speech-to-text | faster-whisper | CTranslate2-based, fast on CPU |
| Local LLM | Ollama | Local HTTP API, swap models by name |
| Text-to-speech | Piper | Fast, local, many voices |
| Text input fallback | Tkinter/GTK popup or tray icon | For typing instead of speaking |
| Transcript overlay | GTK3 + gtk-layer-shell | Bottom-of-screen live conversation transcript; falls back to X11/XWayland where layer-shell is unavailable |

Every stage is behind an interface (see `docs/ARCHITECTURE.md`) so a library can
be swapped or replaced by a custom implementation later without touching the
orchestrator.

# gideon

A fully local, offline voice assistant for Ubuntu. Runs in the background,
wakes on a voice command (or a manual popup/tray trigger), converts speech to
text, sends the request to a local LLM, and speaks the answer back.

Everything runs locally on open-source components. No cloud APIs.

## Pipeline

```
[wake word] --> [audio capture + VAD] --> [speech-to-text] --> [local LLM] --> [text-to-speech] --> [playback]
                        ^                                                                                |
                        |________________________ mic gated while speaking ________________________|
[popup/tray text input] -------------------------------------------------------------------------> (skips STT, feeds LLM directly)
```

## Working model: one module, one session

Each module under `modules/` is designed to be built **independently, in its
own fresh Claude Code session**, using only that module's `plan.md` plus
`ARCHITECTURE.md` (the shared contracts). A session working on module N does
not need the history of how module N-1 was built — just its interface.

Workflow:
1. Open a new session.
2. Read `ARCHITECTURE.md` (shared interfaces/config schema) and the target
   module's `modules/<name>/plan.md`.
3. Implement the module so it satisfies its interface and passes its own
   standalone test plan (no dependency on other unfinished modules).
4. Update `task.md` — check off the module and note any decisions/deviations.
5. If the module's plan.md needed changes to match reality, update it so the
   next reader has the correct picture.

`00-shared` should be done first (it defines the interfaces every other
module implements). `07-orchestrator` must be done last (it wires all
modules together into the background service). The modules in between
(`01`–`06`) can be done in any order once `00-shared` exists.

## Setup

Single shared venv at the repo root, editable install:

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Some modules need extra dependencies that can't live in the root
`pyproject.toml` (e.g. `01-audio-io`'s torch/torchaudio need a pinned pair
from PyTorch's own CPU wheel index). Check for a `requirements.txt` in the
module's directory and `pip install -r` it.

## Docs

- `ARCHITECTURE.md` — pipeline, interface contracts, audio format
  conventions, config schema. Read this before starting any module.
- `task.md` — progress tracker across all modules.
- `modules/<name>/plan.md` — per-module goal, chosen library, deliverables,
  and standalone test plan.
- `RUNBOOK.md` — restarting the service after "Quit", and how to
  rebuild/test/reinstall after a code change.

## Tech choices (starting point, swappable later)

| Stage | Library | Notes |
|---|---|---|
| Wake word | openWakeWord | Local, open source, pretrained + custom-trainable |
| Audio capture/playback + VAD | sounddevice/pyaudio + Silero VAD | Standard mic/speaker I/O |
| Speech-to-text | faster-whisper | CTranslate2-based, fast on CPU |
| Local LLM | Ollama | Local HTTP API, swap models by name |
| Text-to-speech | Piper | Fast, local, many voices |
| Text input fallback | Tkinter/GTK popup or tray icon | For typing instead of speaking |

Every stage is behind an interface (see `ARCHITECTURE.md`) so a library can
be swapped or replaced by a custom implementation later without touching the
orchestrator.

# Task tracker

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done

Build order: `00-shared` first, `07-orchestrator` last. `01`–`06` can happen
in any order in between.

- [x] `00-shared` — interfaces, config loader, logging setup
- [x] `01-audio-io` — mic capture, speaker playback, VAD, device selection
- [x] `02-wake-word` — openWakeWord integration (works; recall is weak, see notes below)
- [x] `03-stt` — faster-whisper wrapper (`small` model confirmed by measurement)
- [x] `04-llm-client` — Ollama HTTP client wrapper (`qwen2.5:1.5b` confirmed by measurement)
- [x] `05-tts` — Piper wrapper (`en_US-lessac-high` confirmed by listening test)
- [x] `06-text-input` — popup/tray fallback text input (tray click -> popup -> submit confirmed on real hardware)
- [x] `07-orchestrator` — state machine, systemd service, ties all modules together (core wake-word -> reply loop, follow-up window, and the systemd install/kill-recovery test plan items all confirmed; see notes below)

## Open decisions log

Record decisions here as they're made (which session, what was decided, why):

- Wake word/phrase: **"hey jarvis"** — openWakeWord's pretrained
  `hey_jarvis` model, used as-is for v1 (2026-08-26). `config.yaml` was
  already stubbed to this value, so no custom training was needed.
- STT model size: **`small`** — measured against real speech (2026-08-26,
  Open Speech Repository Harvard-sentence recordings), notably more
  accurate than `tiny` and still well under 1s for a typical few-second
  utterance on CPU; see `modules/03-stt/plan.md` for the full comparison.
- LLM model (name + size): **`qwen2.5:1.5b`** — measured against a real
  local Ollama server (2026-08-26) on this CPU-only machine (no GPU); the
  pre-existing `llama3.1:8b` stub was never even pulled once an already-
  installed 8B model (`qwen3:8b`) measured 31-35s/reply. `qwen2.5:1.5b`
  beat `llama3.2:3b` on latency (1-4s typical vs 1.3-23.6s) with no
  observed correctness difference; see `modules/04-llm-client/plan.md`
  for the full comparison and a flagged follow-up (occasional verbose
  replies from small models cause latency outliers - revisit once TTS is
  wired up).
- Barge-in in v1 scope: _not yet decided_
- Tray icon backend for `06-text-input`: **resolved (2026-08-28)**.
  pystray initially only loaded its Xorg (XEmbed) backend - `gi`/PyGObject
  wasn't importable, and this GNOME/Wayland system has no XEmbed tray
  manager (`_NET_SYSTEM_TRAY_S0` unowned, confirmed via `xprop -root`), so
  that backend's icon ran without error but was never visible. Fixed by
  the user running `sudo apt install libgirepository-2.0-dev` (a
  system-level change, so left for them to run rather than done
  autonomously), then `pip install PyGObject` in the venv - pystray now
  loads its AppIndicator backend (`pystray._appindicator`), which the
  already-installed `gnome-shell-extension-appindicator` supports.
  Confirmed working end-to-end by the user on real hardware. See
  `modules/06-text-input/plan.md` for the full writeup.
- TTS voice: **`en_US-lessac-high`** — confirmed by the user actually
  listening to it (2026-08-27) via `speak_demo.py --voice <name>` against
  `en_US-lessac-medium` (the original stub) and `en_US-amy-medium`. Picked
  for sound quality despite being the slowest to synthesize on this
  CPU-only machine (~4.2s vs ~0.6-0.8s for a ~30-word reply). Combined
  with `04-llm-client`'s 1-4s typical latency, wake-to-first-spoken-word
  could run 5-8s+ — flagged for the orchestrator to watch once the full
  pipeline is wired up, but not blocking since the user has heard and
  accepted it. See `modules/05-tts/plan.md` for the full comparison.

## Notes / deviations from plan

Add a dated bullet here whenever a module's implementation diverges from
its `plan.md`, so later modules (and the orchestrator) aren't surprised.

- 2026-08-26: `00-shared` done. Packaging approach: single root
  `pyproject.toml` (setuptools), one shared venv at `<repo_root>/.venv`,
  editable install via `pip install -e ".[dev]"` run once from repo root.
  Each module's `src/` hosts one top-level package registered in
  `pyproject.toml`'s `[tool.setuptools]` `package-dir`/`packages`. When a
  new module is implemented, add its package to both lists there, then
  re-run `pip install -e ".[dev]"`. `shared.interfaces` uses
  `@runtime_checkable` Protocols (structural typing - no subclassing
  needed). `shared.config.load_config()` fills in dataclass defaults for
  any missing section/file, so partially-filled `config.yaml` is fine
  while modules are built incrementally; unknown fields inside a known
  section raise a clear `ConfigError`.
- 2026-08-26: `01-audio-io` done (`MicAudioSource`, `SpeakerAudioSink`,
  `SileroVAD`, `devices.py` CLI). Decisions/deviations:
  - **Mute contract**: while `MicAudioSource.muted` is True, `read_chunk()`
    still drains the real frame from the internal queue (so capture never
    backs up/stalls) but returns a same-shaped array of zeros instead of
    the real samples.
  - **VAD silence hangover**: 800ms. `SileroVAD.is_speech()` buffers input
    into Silero's required fixed windows (512 samples/32ms at 16kHz) and
    only flips from speaking to not-speaking after 800ms of continuous
    sub-threshold windows, so brief mid-utterance dips don't cause
    premature cutoff. Default `threshold` is 0.5 (unchanged from config).
  - **Playback resampling**: `SpeakerAudioSink` queries the output
    device's native sample rate and resamples via linear interpolation
    (`numpy.interp`) before writing — no `scipy` dependency added.
  - **Packaging deviation**: `sounddevice`, `torch`, `torchaudio`,
    `silero-vad`, `onnxruntime` are deliberately **not** in root
    `pyproject.toml`. `torch`/`torchaudio` must come from PyTorch's own CPU
    wheel index (`https://download.pytorch.org/whl/cpu`) as a **matched
    pinned pair** (`torch==2.5.1`+`torchaudio==2.5.1` verified working) —
    installing them from plain PyPI can resolve a `torchaudio` build that
    expects a CUDA runtime and crashes on import even on a CPU-only
    machine, and mismatched torch/torchaudio versions crash too (both
    failure modes hit during this module's implementation). Install via
    `pip install -r modules/01-audio-io/requirements.txt`. Also requires
    the system package `libportaudio2` (`sudo apt install libportaudio2`)
    for `sounddevice` to import at all — not installable from pip.
  - **Verified on real hardware 2026-08-26**: after `sudo apt install
    libportaudio2`, all 4 standalone test plan items passed on the user's
    machine — device listing, mic-record -> speaker-playback round-trip
    (correct speed/pitch), live VAD flipping True/False with the ~800ms
    hangover feeling right, and the mute toggle producing all-zero
    `read_chunk()` output while muted. No retuning of threshold/hangover
    needed. `01-audio-io` is fully done, not just unit-test-done.
- 2026-08-26: `02-wake-word` implemented (`OpenWakeWordDetector`,
  `listen_demo.py`). Decisions/deviations:
  - **Wake phrase**: "hey jarvis" (openWakeWord's pretrained
    `hey_jarvis` model) — see Open decisions log above.
  - **Edge-triggered detection**: `process_chunk()` returns `True` only
    on the rising edge (score crosses from below to at-or-above
    `threshold`), auto-rearming once the score drops back below
    threshold so one sustained utterance doesn't fire repeatedly.
    `reset()` is a separate manual re-arm (also delegates to the
    underlying openWakeWord model's own `reset()` if present) for
    `07-orchestrator` to call after handling a detection.
  - **Packaging**: like `01-audio-io`, `openwakeword` lives in
    `modules/02-wake-word/requirements.txt`, not the root
    `pyproject.toml`. Unlike silero-vad, no torch/CUDA pitfall — pure
    ONNX/tflite. First run may need
    `python -c "from openwakeword import utils; utils.download_models()"`
    if the pretrained model files aren't bundled for the installed
    version.
  - **ONNX backend forced**: `openwakeword.model.Model(...,
    inference_framework="onnx")` — `tflite-runtime` (openwakeword's
    default backend when installed) is compiled against the NumPy 1.x
    ABI and crashes under NumPy 2.x (`AttributeError: _ARRAY_API not
    found`), which this repo has. onnxruntime doesn't have this issue.
  - **Model files aren't bundled in the pip package** — first run needs
    `python -c "from openwakeword import utils; utils.download_models()"`
    once to fetch them.
  - **Verified on real hardware 2026-08-26** via `listen_demo.py`:
    detects reliably, but **recall is weak at the default
    `threshold=0.5`** — "hey jarvis" has to be said quite
    deliberately/clearly to trigger; casual/fast speech often misses.
    No false positives during normal background talk. This is a known
    characteristic of the pretrained `hey_jarvis` model, not a bug in
    the wrapper. **Deferred follow-up, not yet done**: try lowering
    `wake_word.threshold`, or train a custom wake word via
    openWakeWord's synthetic-TTS pipeline for better recall — revisit
    once the full pipeline is wired up and this is felt end-to-end.
- 2026-08-26: `03-stt` implemented (`FasterWhisperEngine`,
  `transcribe_file.py`). Decisions/deviations:
  - **Model loading**: faster-whisper (CTranslate2 backend) fetches and
    caches weights from Hugging Face automatically on first
    `WhisperModel(model_size, ...)` construction — no separate
    `download_models()`-style step like `02-wake-word` needed.
  - **Compute type**: `int8` on CPU (falls back internally if
    unsupported for a given model), `float16` on CUDA — kept simple
    rather than exposing another config knob for v1.
  - **Packaging**: `faster-whisper` lives in
    `modules/03-stt/requirements.txt`, not the root `pyproject.toml`,
    same convention as `01-audio-io`/`02-wake-word`. No torch/CUDA
    pairing pitfall this time — ctranslate2 handles its own backend.
  - **Verification so far**: unit-tested (4 tests, scripted `model_fn`)
    plus a real-backend smoke test (`tiny` model, CPU, zeroed/silent
    buffer -> empty string, confirms wrapper plumbing works end-to-end).
    **Not yet run against real recorded speech** — the standalone test
    plan (multiple `.wav` clips, accuracy eyeball, timing
    tiny/base/small) hasn't happened yet, so `config.stt.model_size`
    stays at the stubbed `small` default rather than a measured choice.
    Marked `[~]` in the checklist above until that pass is done.
  - **Real-speech test 2026-08-26**: downloaded two public-domain Harvard
    sentence recordings (female + male, 8kHz) from the Open Speech
    Repository and ran `transcribe_file.py` with both `tiny` and `small`.
    `small` was clearly more accurate (fixed several tiny-model
    word-level errors that changed meaning, e.g. "pork chuck" ->
    "parked truck") at a cost that's still comfortably sub-second for
    the short utterances this assistant will actually see (worst case
    here: 20s to transcribe a 58s clip). Confirmed `config.stt.model_size:
    small` as the right default — full comparison table in
    `modules/03-stt/plan.md`. Checklist entry above updated to `[x]`.
- 2026-08-27: `04-llm-client` implemented (`OllamaClient`, `chat_demo.py`).
  Decisions/deviations:
  - **DI for testability**: `post_fn` constructor param, same pattern as
    `model_fn` in `03-stt` — unit tests script the HTTP call instead of
    needing a real Ollama server.
  - **Error handling**: a `requests.exceptions.ConnectionError` from the
    default post function is caught and re-raised as
    `OllamaConnectionError` with a message telling the user to check
    `ollama serve` — not a raw connection-refused traceback. Verified for
    real by stopping the service and running the client against it.
  - **Packaging**: `requests` lives in
    `modules/04-llm-client/requirements.txt`, same per-module
    `requirements.txt` convention as `01-audio-io`/`02-wake-word`/`03-stt`.
  - **Model choice, tested against a real local Ollama server
    2026-08-26/27**: this machine is CPU-only (no GPU). The pre-existing
    `llama3.1:8b` config stub was never pulled — an already-installed 8B
    model (`qwen3:8b`) measured 31-35s per short reply, clearly too slow.
    Pulled and compared `llama3.2:3b` (1.3-7.8s typical, one 23.65s
    verbose outlier) and `qwen2.5:1.5b` (1.0-3.7s typical, one 12.4s
    verbose outlier) on arithmetic, factual, and multi-turn-context
    prompts (all correct on both models). Chose **`qwen2.5:1.5b`** —
    consistently 2-4x faster with no correctness difference observed.
    `config.llm.model` (and its dataclass default in `shared/config.py`,
    and the example in `ARCHITECTURE.md`) updated from the `llama3.1:8b`
    stub accordingly. Full comparison table in
    `modules/04-llm-client/plan.md`. Checklist entry above updated to
    `[x]`.
  - **Flagged follow-up, not yet done**: neither small model reliably
    obeys a "be concise" system-prompt instruction under a harder
    prompt, and the resulting verbose replies are the source of both
    outlier latencies above. Not blocking now, but worth revisiting
    (e.g. capping `num_predict`) once `05-tts` and the orchestrator make
    this felt end-to-end — a verbose reply means a long TTS readout, not
    just a slow LLM call.
  - **Verified on real hardware 2026-08-27**: user ran `chat_demo.py`
    themselves against the live `qwen2.5:1.5b` setup and confirmed
    multi-turn context (name/fact recall across turns) works. `04-llm-client`
    is fully done, not just self-tested.
- 2026-08-27: `05-tts` implemented (`PiperEngine`, `speak_demo.py`).
  Decisions/deviations:
  - **DI for testability**: `synth_fn` constructor param, same pattern as
    `model_fn`/`post_fn` in `03-stt`/`04-llm-client` - unit tests script the
    synthesis call instead of needing a real Piper voice loaded.
  - **Voice model files auto-download on first use**: like
    `02-wake-word`'s pretrained models and `03-stt`'s Hugging Face cache,
    Piper voice files aren't bundled in the repo. `PiperEngine` checks
    `~/.cache/piper-voices/<voice>.onnx` and calls
    `piper.download_voices.download_voice()` if it's missing, rather than
    requiring a separate manual download step.
  - **Sentence gap added**: Piper's own CLI defaults to zero silence
    between multi-sentence chunks of one `synthesize()` call. This module
    inserts a fixed 0.2s gap (`_SENTENCE_GAP_SECONDS` in `engine.py`) when
    concatenating chunks, since a multi-sentence assistant reply with zero
    gap sounds run-on. Hardcoded, not a config knob, same
    anti-speculative-config pattern as `03-stt`'s `compute_type`.
  - **Packaging**: `piper-tts` and `sounddevice` (needed only for this
    module's own `speak_demo.py` playback) live in
    `modules/05-tts/requirements.txt`. `sounddevice` is duplicated with
    `01-audio-io`'s requirements.txt rather than shared, since each module
    must stay independently buildable/testable on its own.
  - **Test file naming**: named this module's test file
    `test_piper_engine.py`, not `test_engine.py` like `03-stt`'s - pytest's
    default rootdir import mode fails to collect two same-named test files
    across module directories that don't have `tests/__init__.py`
    (`import file mismatch` error), so every module's test file needs a
    globally-unique basename.
  - **Measured against the real Piper backend 2026-08-27** (CPU-only
    machine): loaded three candidate voices for real and timed synthesis
    of a 28-word sample reply - `en_US-lessac-medium` (the pre-existing
    config stub, ~0.6-0.7s), `en_US-amy-medium` (~0.8s, comparable), and
    `en_US-lessac-high` (~4.2s, ~4x slower for higher quality). Full table
    in `modules/05-tts/plan.md`.
  - **`--voice` override flag added to `speak_demo.py`**: lets the voice
    be picked per-run (`--voice en_US-amy-medium`) without editing
    `config.yaml`, so comparing candidates by ear doesn't require
    round-tripping the config file for each one.
  - **Verified on real hardware 2026-08-27**: user listened to all three
    candidates via `speak_demo.py --voice <name>` and picked
    `en_US-lessac-high` for sound quality, confirming it "works fine"
    despite being the slowest to synthesize (~4.2s vs ~0.6-0.8s for the
    `-medium` voices on this CPU-only machine - see the comparison table
    above). `config.tts.voice` (and its dataclass default in
    `shared/config.py`, and the example in `ARCHITECTURE.md`) updated from
    the `en_US-lessac-medium` stub accordingly. `05-tts` is fully done,
    not just self-tested. Checklist entry above updated to `[x]`.
- 2026-08-28: `06-text-input` implemented (`TkPopupProvider`, `TrayApp`,
  `tray_demo.py`). Decisions/deviations:
  - **Popup testability**: `TkPopupProvider._build(root)` returns the
    entry widget plus its `submit`/`cancel`/`result` callables directly,
    so tests can drive real Tkinter widgets (insert text, call `submit()`)
    without needing `mainloop()` to be running - same DI spirit as
    `synth_fn`/`model_fn`/`post_fn` elsewhere, adapted for a GUI. One test
    does run the real `mainloop()` (via an `after()`-scheduled auto-submit)
    to verify the actual event-loop path `get_text()` uses, not just the
    callback logic.
  - **Tray/Tkinter threading**: pystray and Tkinter each want to own an
    event loop, and Tkinter's must run on the main thread, so `TrayApp.run()`
    runs the pystray icon in a background thread and drives popup creation
    itself from the calling (main) thread via a `queue.Queue`, rather than
    building the popup from the tray's own callback thread.
  - **DI for testability**: `TrayApp` takes an optional `icon=` param (a
    stand-in with just `run()`/`stop()`) so `test_tray_app.py` can test the
    ask/quit queue logic without needing a real pystray/X11 icon.
  - **Test file naming**: `test_tk_popup.py`/`test_tray_app.py` - checked
    no basename collision with existing modules' test files (recurring
    gotcha, see `05-tts`'s note above).
  - **Packaging**: `pystray`/`Pillow` live in
    `modules/06-text-input/requirements.txt`; Tkinter ships with Python so
    needs no pip entry.
  - **Tested against the real backend 2026-08-28**: real `tkinter.Tk()`
    windows and the full `mainloop()` round-trip work fine on this
    machine. **Tray icon does not work yet** - see the "Tray icon backend"
    open-decisions entry above; this is a real, confirmed gap
    (`_NET_SYSTEM_TRAY_S0` unowned), not just an untested guess. Global
    hotkey (`text_input.hotkey`) was **deferred**, per the plan's
    "implement only if time allows" - with the tray also not working right
    now, the only confirmed way to trigger the popup on this machine today
    is calling `TkPopupProvider().get_text()` directly (which `07-orchestrator`
    can do regardless of the tray question).
  - **Tray backend fixed 2026-08-28**: user ran `sudo apt install
    libgirepository-2.0-dev`, then `pip install PyGObject` was run in the
    venv - pystray switched from its Xorg backend to `pystray._appindicator`,
    which registers as a real `org.freedesktop.StatusNotifierItem` on
    D-Bus (confirmed via `gdbus call ... ListNames`) and is picked up by
    GNOME's already-installed AppIndicator extension. `PyGObject>=3.50.0`
    added to `modules/06-text-input/requirements.txt` with a comment on
    the system-package prerequisite.
  - **Verified on real hardware 2026-08-28**: user ran `tray_demo.py`, saw
    the tray icon, clicked "Ask...", the popup appeared, typed "hii" and
    pressed Enter - popup closed and `got: 'hii'` printed to the terminal
    (confirmed by the user as their own input, not a stray leftover).
    Full tray -> popup -> submit -> callback path verified end to end.
    `06-text-input` is fully done, not just self-tested. Checklist entry
    above updated to `[x]`.
- 2026-08-28: `07-orchestrator` implemented (`Orchestrator` state machine,
  `main.py` entry point, `systemd/gideon.service`). Decisions/deviations:
  - **State machine as methods**: `_idle`/`_listen`/`_transcribe`/`_think`/
    `_speak`, each independently unit-testable with fake
    `shared.interfaces` implementations (same DI spirit as every other
    module's `model_fn`/`synth_fn`/`post_fn`) — no real mic, VAD model,
    wake-word model, Whisper, Ollama, Piper, or tray icon needed for the
    12-test suite.
  - **Text-input wiring**: rather than the orchestrator's main loop calling
    `TrayApp.run()` directly (which would block it from also watching the
    mic), `main.py` runs `TrayApp.run()` on its own background thread with
    `on_text=text_queue.put`; `IDLE` polls that queue non-blockingly
    alongside reading mic frames every ~30ms. Relies on Tkinter working
    off the main thread, which is fine on Linux (the restriction
    `06-text-input`'s plan.md flagged is macOS-specific).
  - **Mic gating during SPEAKING**: relies on the `MicAudioSource.muted`
    drain-but-zero contract `01-audio-io` defined — set `True` right
    before `AudioSink.play()`, `False` in a `finally` so it un-mutes even
    if playback raises. Done via `hasattr` rather than adding `muted` to
    the formal `AudioSource` Protocol, since it's a `MicAudioSource`-
    specific extension, not something every possible `AudioSource` needs.
  - **LISTENING stop condition**: records frames until VAD reports speech
    *then* silence (not just "no speech", so a pause before the user
    starts talking doesn't cut the recording short), or a hardcoded
    15-second safety cutoff, whichever comes first.
  - **history_turns trimming**: keeps the most recent `history_turns`
    turns (`history_turns * 2` messages, since each turn is a user+
    assistant pair) via `config.orchestrator.history_turns`.
  - **Clean shutdown**: `SIGINT`/`SIGTERM` call `Orchestrator.stop()`,
    which just flips a flag — the IDLE loop's `read_chunk()` only blocks
    until the next mic frame (~30ms), not indefinitely, so shutdown is
    fast without needing a read timeout the `AudioSource` Protocol doesn't
    support.
  - **Packaging**: no new pip dependencies — `orchestrator` only imports
    every other module, all already installed. Registered in root
    `pyproject.toml` like every prior module.
  - **Not yet tested against the real backend / real hardware** — needs a
    live mic + wake word + Ollama + tray icon all running together at
    once, plus the systemd service actually installed, neither of which
    has happened yet this session. Checklist entry above is `[~]`, not
    `[x]`, until that happens (mirrors every other module's completion
    bar in this project). See `modules/07-orchestrator/plan.md`'s
    "Verification status" for exactly what's still outstanding.
- 2026-08-28: after a first real-hardware run, the user reported the core
  wake-word -> reply loop worked, asked whether it needs the wake word said
  again for each new question (**yes** - it always returns to `IDLE`, no
  continuous-listening follow-up mode; not in scope per
  `ARCHITECTURE.md`'s state machine, would be a deliberate future feature
  if wanted), and asked for a tray-icon way to see the assistant's current
  state for a non-technical user. Added a **"Status / logs..."** tray menu
  item (`06-text-input/src/text_input/tray.py`) showing a scrollback of
  plain-English state changes ("Idle - waiting...", "Listening...",
  "Thinking...", "Speaking: <reply>"), fed by a new `Orchestrator(on_status=...)`
  callback wired to `TrayApp.set_status` in `07-orchestrator/src/orchestrator/main.py`.
  Unit-tested (`test_tray_app.py`, `test_status_window.py`,
  `test_state_machine.py`'s new status-reporting tests - 73/73 passing
  repo-wide) but **not yet confirmed on real hardware** - full writeup in
  both modules' `plan.md`.
- 2026-08-28: user tried the status feature and the follow-up idea and
  reported two things, both addressed:
  - **Bug**: the "Status / logs..." window looked stuck on "Idle" - it was
    a one-time snapshot taken when opened, never refreshed while left
    open. **Fixed**: `build_status_window()` now takes a log-getter
    callable and re-renders on a `root.after()` timer (500ms) until
    closed. See `modules/06-text-input/plan.md`.
  - **Feature request**: don't require the wake word again immediately
    for a follow-up question - wait ~10s for a follow-up first. Added an
    `AWAITING_FOLLOWUP` phase to the state machine
    (`Orchestrator._await_followup()`, new `config.orchestrator.
    followup_seconds` field, default 10) - after speaking, listens for
    speech (no wake word needed) or a tray submission for that long
    before finally requiring the wake word again. See
    `modules/07-orchestrator/plan.md` and the updated diagram in
    `ARCHITECTURE.md`.
  Both changes are unit-tested only so far (79/79 passing repo-wide) -
  not yet confirmed on real hardware.
- 2026-08-28: user confirmed the status window now reflects live updates,
  but reported the follow-up window didn't actually wait - it stopped
  listening right after they finished talking. Real root cause: nothing
  read the mic during TRANSCRIBING/THINKING/SPEAKING (STT/LLM/TTS all
  block the single orchestrator thread), but the mic's background capture
  thread keeps enqueueing real frames regardless - so a multi-second
  backlog piled up un-drained, and the very next read
  (`_await_followup`, right after SPEAKING) drained it almost instantly.
  That both broke the sample-count-based timeout math and risked feeding
  stale/self-echo audio to the VAD as a false "speech detected." **Fixed**:
  `_await_followup`'s timeout is now wall-clock-based
  (injectable `clock` param), and `_transcribe`/`_think`/`_speak` now run
  a background drain thread (`Orchestrator._default_drain_context`,
  injectable via `drain_context` for tests) that keeps consuming and
  discarding mic frames for the duration of each blocking call, so the
  queue never backs up. 81/81 tests passing repo-wide. Full writeup in
  `modules/07-orchestrator/plan.md`. **Confirmed working on real hardware
  2026-08-28** - user retested and the follow-up window now genuinely
  waits and picks up a follow-up question without needing the wake word.
- 2026-08-28: user filed a batch of ad hoc improvement requests (root
  `tmp.md`, now cleared - full writeups in
  `modules/07-orchestrator/plan.md`'s "Tray dashboard controls" and
  `modules/06-text-input/plan.md`'s "Ask box in the Status window..."
  sections). Implemented:
  - Tray dashboard: LLM running indicator + start/stop (new
    `orchestrator/ollama_control.py` - manages a plain `ollama serve`
    process directly, not systemd, since `ollama.service` is a disabled
    system unit here and starting/stopping it would need `sudo`, which
    the assistant must never run itself), mic mute/unmute (stacks with
    the existing auto-mute-during-SPEAKING rather than replacing it),
    online/offline (fully stops watching for the wake word/typed
    questions while offline), and "Stop speaking" (finally exercises
    `AudioSink.stop()`, in the architecture since `01-audio-io` but never
    used until now - cuts a long-winded reply short, then proceeds to the
    follow-up window as if it had finished normally).
  - `TrayApp(extra_menu_items=...)`: `06-text-input` stays
    orchestrator-agnostic - `main.py` builds the `pystray.MenuItem`s,
    `TrayApp` just inserts them into its menu.
  - Status window now has its own ask box (`on_ask` callback), since
    leaving it open previously blocked the separate "Ask..." popup from
    ever being processed (`TrayApp.run()` only drives one Tk window at a
    time).
  99/99 tests passing repo-wide. **Not yet confirmed on real hardware.**
- 2026-08-28: user followed up on the dashboard with two things:
  1. Wanted a custom panel like GNOME's quick-settings tray popup (rounded
     pill-shaped toggle buttons), not the plain text/checkbox menu items
     from the previous entry.
  2. The LLM start/stop control showed "not running" correctly but
     clicking it didn't actually run `ollama serve` - no error, no
     feedback, nothing observable happened.
  Addressed both:
  - New `text_input/dashboard.py` (`DashboardControl` +
    `build_dashboard_window`) draws a grid of purple/grey pill buttons on
    a `tk.Canvas` (rounded rectangles via a smooth-polygon trick, since
    Tkinter has no native rounded-rect widget), opened via a new
    "Dashboard..." tray menu item (`TrayApp(dashboard_controls=...)`).
    `07-orchestrator/main.py`'s LLM/mic/online/stop-speaking controls now
    build `DashboardControl`s instead of plain `pystray.MenuItem`s -
    same underlying logic, new presentation. See
    `06-text-input/plan.md`'s "Dashboard panel" section.
  - `OllamaControl.start()` now resolves the `ollama` binary via
    `shutil.which` explicitly and raises a clear `OllamaControlError`
    instead of letting a bare `FileNotFoundError` vanish silently inside
    a pystray/GTK menu-click callback (a real, confirmed failure mode -
    such callbacks have nowhere to surface an exception). `main.py`'s
    click handler now also posts an immediate "Starting/Stopping
    Ollama..." status line and reports any `OllamaControlError` through
    the Status log, so success or failure is visible either way. See
    `07-orchestrator/plan.md`'s "Tray dashboard controls" section
    (updated with a "Bug found and fixed" note).
  111/111 tests passing repo-wide.
- 2026-08-28: user confirmed "llm start works perfectly" after the fix
  above, then asked why the tray icon shows a dropdown menu first instead
  of opening the dashboard panel directly on click, like GNOME's own
  quick-settings tray icon. **Investigated, not fixable**: pystray's
  AppIndicator backend hardcodes `HAS_DEFAULT_ACTION = False` ("we expand
  the menu on primary button click") - every click always opens the
  dropdown, reflecting the StatusNotifierItem/AppIndicator protocol
  itself (unlike the legacy X11 tray protocol's distinct left/right-click
  actions), not a pystray restriction that can be configured around
  without hand-rolling a StatusNotifierItem D-Bus service from scratch
  (out of scope). Made "Dashboard..." the first menu item instead, the
  best available improvement. See both modules' plan.md for the full
  writeup. 111/111 tests still passing (one test's expected menu order
  updated for the reorder).
- 2026-08-29: installed and verified the systemd **user** service, the
  last outstanding `07-orchestrator` test plan item. Commands used (also
  documented in `modules/07-orchestrator/plan.md`):
  ```
  mkdir -p ~/.config/systemd/user
  cp modules/07-orchestrator/systemd/gideon.service ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now gideon.service
  ```
  Verified directly from the shell: `systemctl --user status` showed
  `active (running)` within 5s of `enable --now`, with clean
  `starting mic and tray icon` / `ready` / `Idle - waiting...` log lines
  in `journalctl --user -u gideon.service`; `enable` symlinked the unit
  into `default.target.wants`, so it starts automatically on every login
  without a manual `systemctl start` (covers test plan item 5 - a literal
  reboot wasn't done, but `enable`'s mechanism is what makes that work and
  was confirmed in place). Then `kill -9` on the running `MainPID`
  produced `Main process exited, code=killed, status=9/KILL` /
  `Failed with result 'signal'` in the journal, followed by
  `Scheduled restart job, restart counter is at 1` ~2s later (per
  `RestartSec=2`) and a fresh PID back to the same clean
  `Idle - waiting...` log line - confirms `Restart=on-failure` recovers
  from an ungraceful kill into a clean state (test plan item 6).
  `DISPLAY`/`WAYLAND_DISPLAY`/`DBUS_SESSION_BUS_ADDRESS` were all present
  in `systemctl --user show-environment` on this already-logged-in
  session, as `07-orchestrator/plan.md` expected, so the tray icon should
  render under the service the same as it does run manually - **still
  needs the user's own eyes to confirm the tray icon is actually visible
  when launched this way** (a shell can't observe that), and test plan
  item 4 (mic not reacting to the assistant's own tail-end audio) still
  needs a live voice check. Everything else in the standalone test plan
  (items 1-3, and now 5-6) is confirmed. All modules `00`-`07` are now
  checked off above.
- 2026-08-30: another batch of ad hoc tray/dashboard requests (see root
  `tmp.md`, cleared into this writeup and both modules' plan.md):
  - **Colored tray icon** (`06-text-input/src/text_input/tray.py`): the
    generated dot now recolors per assistant state instead of always
    being blue - grey/idle, green/listening, orange/processing, purple/
    speaking (matching the dashboard panel's own "active" pill color),
    red/error reserved for future use. `state_machine.py`'s `_set_status`
    now takes an optional `state=` alongside its existing free-text
    `message`, forwarded to a new `on_state` callback
    (`TrayApp.set_icon_state`) wired in `main.py` - kept as a separate
    symbolic channel rather than having the tray pattern-match the
    human-readable message.
  - **Tray menu restructured + unified dashboard window**: the native
    menu no longer has standalone "Ask..."/"Status / logs..." items -
    both were folded into `dashboard.py`'s panel, now three stacked
    sections (quick pill buttons, then the activity log, then an ask box)
    opened from the one remaining "Dashboard..." item.
    `build_dashboard_window()` takes new optional `get_log_lines`/
    `on_ask` params for the middle/bottom sections (each independently
    opt-in, so pill-only callers/tests are unaffected). The tray menu
    also gained live "quick insight" entries above "Dashboard..." -
    `TrayApp(quick_menu_controls=...)`, reusing the same `DashboardControl`
    objects as the panel's pills via pystray's callable `text`/`enabled`
    (re-evaluated whenever the menu is shown, no separate polling needed);
    `main.py` surfaces the LLM and mic controls there.
  - **Assistant voice volume slider**: new `DashboardSlider` dataclass in
    `dashboard.py` (label/get_value/on_change, normalized `0.0`-`1.0`
    regardless of the underlying Tk `Scale`'s `0`-`100` range), rendered
    in the dashboard's top section. `Orchestrator.set_volume`/
    `get_volume` store the multiplier; `_speak()` applies it
    (`_apply_volume`) to the synthesized int16 audio before playback -
    scales in float64 and clips before casting back, so a naive multiply
    can't wrap around at high-amplitude samples. Volume is runtime-only
    (not persisted to `config.yaml`) since the ask didn't call for
    persistence.
  All unit-tested (121/121 tests passing repo-wide); **not yet confirmed
  on real hardware** - needs the user to see the icon actually change
  color through a real conversation, click the new quick-menu items, and
  confirm the volume slider audibly changes playback level.
- 2026-08-30: user tested the above and reported two issues:
  - **"Stop speaking" crashed the whole service and closed the
    dashboard.** Root-caused to a real thread-safety bug in
    `01-audio-io/src/audio_io/sink.py`: `stop()` and `play()`'s own
    `finally` block could both call `close()` on the same PortAudio
    stream at once (`stop()` runs on the dashboard's click-handler
    thread while `play()` blocks on `write()` on the orchestrator's own
    thread). **Fixed** - `play()` is now the sole owner of
    `close()`/clearing `self._stream`, guarded by a lock; `stop()` only
    calls `abort()` and swallows any exception from an already-finished
    stream. See `01-audio-io/plan.md`'s "Stop-speaking crash fixed"
    section. 125/125 tests passing repo-wide.
  - **Volume slider didn't audibly lower output**, even though the
    user's system volume slider does. Reviewed `_apply_volume()` and the
    dashboard slider wiring end to end - found no logic bug (the
    multiply-and-clip math and the slider's `on_change` wiring both check
    out, and are unit-tested). Since `07-orchestrator/main.py` runs as a
    long-lived process (a systemd user service per `RUNBOOK.md`, or a
    foreground `python -m orchestrator.main`), the leading suspect is
    that the running process predates this session's code change -
    Python doesn't hot-reload edited source, so the old process simply
    doesn't have the volume feature yet. **Asked the user to restart
    (`systemctl --user restart gideon.service`, or stop+rerun the
    foreground command) and retest before assuming a further code bug.**
- 2026-08-30: user confirmed they'd run `python -m orchestrator.main`
  directly (current code, ruling out the stale-process theory) and
  reported three more findings, which turned into two real bug fixes plus
  a feature:
  - **Two real concurrency bugs**, both root-caused and fixed:
    1. "Stop speaking" crashed the whole service and closed the
       dashboard - two threads racing to `close()` the same PortAudio
       stream (`01-audio-io/src/audio_io/sink.py`). See its plan.md's
       "Stop-speaking crash fixed" section.
    2. After that, typed input in the dashboard "did nothing" - a silent
       hang in the orchestrator's own thread (`MicAudioSource.read_chunk()`
       blocking forever with no timeout after the output-stream abort
       hiccuped the input stream). See `01-audio-io/plan.md`'s "Mic-read
       hang fixed" section.
  - **Streaming LLM + TTS output**: the user correctly diagnosed the
    volume report themselves - "lowering it reflects in the next output,
    not the ongoing one... can we do streaming instead... this will also
    solve the volume issue" - and asked for it explicitly, plus a unified
    "Stop generating" that gracefully stops both and still accepts the
    next prompt. Implemented: `LLMClient` gained `generate_stream()`/
    `cancel()` (`04-llm-client/plan.md`), `07-orchestrator`'s new
    `_think_and_speak()` speaks each sentence as soon as it's generated
    instead of waiting for the whole reply, and `stop_generating()`
    replaces "Stop speaking" in the dashboard, stopping both the LLM
    stream and any in-progress sentence together
    (`07-orchestrator/plan.md`'s "Streaming replies" section - including
    a real correctness bug found and fixed while writing its tests, where
    a stop request landing mid-synthesis used to get silently dropped).
  145/145 tests passing repo-wide. **Nothing in this batch is confirmed
  on real hardware yet** - needs the user to retest "Stop speaking"/"Stop
  generating" end to end, confirm typed input keeps working afterward,
  and hear the first sentence of a reply start before the rest finishes
  generating.
- 2026-08-30: user retested and reported "Stop speaking" now "crashed for
  a few seconds" with PortAudio C-library ALSA errors printed to stderr
  (`Expression '...' failed in 'pa_linux_alsa.c'`), plus a question about
  why streaming looks like "a line at a time" rather than "a word at a
  time" like OpenAI-style streaming.
  - **Root-caused and fixed**: `stop()` interrupting `play()`'s one big
    blocking `write()` via `abort()` (from another thread) was triggering
    this machine's ALSA backend's own internal xrun-recovery path, which
    itself failed/retried for several seconds before unblocking - not a
    process crash (already fixed), just a very disruptive stall.
    Replaced `abort()`-based interruption entirely: `play()` now writes
    in small ~100ms chunks, checking a plain `threading.Event` between
    them; `stop()` just sets that event, never touching the PortAudio
    stream from another thread at all. See `01-audio-io/plan.md`'s
    "Abort-triggered ALSA xrun replaced with cooperative chunked writes"
    section.
  - **Streaming granularity explained, not a bug**: Ollama does stream
    token-by-token under the hood (confirmed - same NDJSON mechanism
    OpenAI-style APIs use); `_stream_sentences()` deliberately buffers
    into whole sentences before speaking/logging, since Piper needs a
    full sentence for natural-sounding speech. Added a `DEBUG`-level
    per-token log (`_log_deltas()`) so the user can see the real
    token-by-token stream if they want to verify it themselves. See
    `07-orchestrator/plan.md`'s matching follow-up note.
  146/146 tests passing repo-wide. **Not yet confirmed on real
  hardware** - needs the user to retest "Stop speaking"/"Stop generating"
  and confirm the ALSA errors and stall are gone.

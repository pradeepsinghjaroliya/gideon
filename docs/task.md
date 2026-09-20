# Task tracker

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done

Build order: `00-shared` first, `07-orchestrator` last. `01`–`06` can happen
in any order in between.

- [x] `00-shared` — interfaces, config loader, logging setup
- [x] `01-audio-io` — mic capture, speaker playback, VAD, device selection
- [x] `02-wake-word` — openWakeWord integration (custom "hey gideon" model trained and confirmed working 2026-08-31, see notes below)
- [x] `03-stt` — faster-whisper wrapper (`small` model confirmed by measurement)
- [x] `04-llm-client` — Ollama HTTP client wrapper (`qwen2.5:1.5b` confirmed by measurement)
- [x] `05-tts` — Piper wrapper (`en_US-lessac-high` confirmed by listening test)
- [x] `06-text-input` — popup/tray fallback text input (tray click -> popup -> submit confirmed on real hardware)
- [x] `07-orchestrator` — state machine, systemd service, ties all modules together (core wake-word -> reply loop, follow-up window, and the systemd install/kill-recovery test plan items all confirmed; see notes below)
- [x] `08-transcript-ui` — on-screen live conversation transcript, pinned to the bottom of the screen (layer-shell and X11 backends both confirmed on real hardware 2026-09-12; see notes below)
- [x] `09-agentic` — pydantic-ai `Agent` + LLM provider registry/tools, replacing `04-llm-client`'s direct-HTTP Ollama call (tool use confirmed end-to-end 2026-09-14; see notes below)

## Open decisions log

Record decisions here as they're made (which session, what was decided, why):

- Wake word/phrase: **"hey gideon"** (2026-08-31) — a custom-trained
  openWakeWord model, replacing "hey jarvis" (openWakeWord's pretrained
  `hey_jarvis` model, used as-is for v1 on 2026-08-26 since it needed no
  training). Trained locally on CPU after several real upstream
  compatibility breakages were worked around; measured false-positive
  rate (4.87/hour) is above the 0.2/hour target due to training on 3,000
  samples instead of the recommended 20,000+, but confirmed working on
  real hardware. See `modules/02-wake-word/plan.md` for the full list of
  fixes and measured metrics.
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
- 2026-08-31: `02-wake-word` retrained with a custom "hey gideon" model,
  acting on the deferred follow-up above. Decisions/deviations:
  - **Trained locally on CPU, not Colab**, despite `training/`'s notebook
    being written for Colab — turned out fast enough (actual NN training
    ~13 min; TTS clip generation, the slower step, still well under a day)
    since the classifier trains on small precomputed feature vectors, not
    raw audio.
  - **11 separate real upstream compatibility breakages** found and
    fixed to get the pipeline running at all (openWakeWord's official
    tutorial has rotted since it was written): `speexdsp-ns` unavailable
    for current Python, a removed `torchaudio` API, `pkg_resources`
    dropped by new setuptools, `piper-sample-generator`'s repo
    restructured (deleted a file `train.py` imports) and that same change
    dropping a default argument `train.py` relies on, a missing
    `piper-tts` package, `datasets` broken two different ways, AudioSet's
    HF dataset reorganized (dead download link), FMA's loader script
    broken outright (dropped from the pipeline entirely), `torchaudio`
    routing through `torchcodec` which needs system ffmpeg libs not
    present, a 22050 Hz vs. 16000 Hz sample-rate mismatch between the
    Piper TTS voice and openWakeWord's pipeline, and a missing
    `onnxscript` package for ONNX export. All fixes are captured in
    `modules/02-wake-word/training/hey_gideon_training.ipynb` and
    `training/README.md` for future retraining.
  - **Training config**: `n_samples=3000` (openWakeWord recommends
    20,000+ for best results — deliberately smaller for a faster v1 run),
    `steps=50000` + two automatic ~5000-step fine-tuning cycles,
    `layer_size=32` `dnn` model.
  - **Measured metrics**: accuracy 0.807, recall 0.623, false positives
    4.87/hour (target 0.2/hour, not met — a known consequence of the
    reduced `n_samples`, not a pipeline bug).
  - **Real bug found and fixed in `detector.py`**: `_OpenWakeWordModel`
    indexed openWakeWord's predictions dict by the raw model path string,
    but openWakeWord actually keys it by
    `os.path.splitext(os.path.basename(path))[0]`. This only ever worked
    by accident for a bare built-in name like `hey_jarvis` (a no-op
    transform) and was silently broken for any custom path — missed
    during initial verification because that verification called
    `openwakeword.model.Model` directly rather than through this
    wrapper, and only surfaced when the user ran `listen_demo.py` for
    real and hit `KeyError: 'modules/02-wake-word/models/hey_gideon.onnx'`.
    Fixed by applying the same derivation in `_OpenWakeWordModel.__init__`.
  - **Confirmed working on real hardware 2026-08-31** via
    `.venv/bin/python -m wake_word.listen_demo` after that fix — user
    confirmed "works good" at `threshold=0.5`. Detailed true/false-positive
    counts (matching the `hey_jarvis` entry's test style) not yet
    recorded — worth doing if the measured false-positive rate above
    turns out to matter in real day-to-day use.
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

## 08-transcript-ui: live on-screen conversation transcript (2026-09-12,
requested by the user)

The user asked for "a minimal looking UI that shows the conversation
transcript in real time" at the bottom of the screen: while talking to
Gideon there was nothing on screen showing the words, only the terminal log
and the tray Dashboard's *status* lines. Then, as a follow-up, that it must
"work on all types of Linux system ... hyprland, wayland, x11, all sort and
should be generic" rather than being built around this machine's Hyprland
setup.

Four things were settled with the user before building:

- **Live partials**, not just the final transcript: their words appear as
  they speak. Gideon's replies already stream token-by-token.
- **Auto show / auto hide**: hidden while idle, fades in on the wake word,
  fades out a few seconds after returning to IDLE.
- **Display only**: click-through, never takes keyboard focus. The tray
  Dashboard stays the place to type/stop/mute.
- **Portable** across wlroots, GNOME/KDE Wayland, and X11.

### What was built

- New `modules/08-transcript-ui/` — the overlay, as its **own process**
  fed newline-delimited JSON over a unix socket. It cannot be a thread in
  the orchestrator: `TrayApp.run()` already owns the main thread with
  Tkinter, pystray's AppIndicator backend already runs a `Gtk.main()` on a
  background thread, and GTK is not thread-safe. A separate process also
  means a UI crash costs the transcript and nothing else.
- New `shared/transcript.py` — the `TranscriptEvent`/`TranscriptSink`
  contract, so neither side imports the other (see `ARCHITECTURE.md`).
- New `stt/streaming.py` — `StreamingTranscriber`, a **second, tiny**
  whisper model re-transcribing the growing audio buffer on a worker
  thread with a single-slot latest-wins mailbox. The main `small` model
  still produces the final transcript, so accuracy is unchanged and only
  the preview is cheap. `submit()` is contractually non-blocking because
  it is called from the mic loop.
- `07-orchestrator` gained optional `transcript` and `partial_transcriber`
  parameters. Both default to `None`, so nothing changes for a caller that
  does not want the overlay — which is what kept all 53 pre-existing
  orchestrator tests valid unmodified.
- New `transcript_ui` config section, plus `stt.partials` /
  `stt.partial_model_size`.

### Portability approach

One GTK3 renderer, three window-surfacing strategies, auto-detected:
`gtk-layer-shell` on wlroots (Hyprland/Sway/river/wayfire); an undecorated
keep-above `UTILITY` window repositioned to bottom-centre on X11 and
XWayland; and for GNOME Mutter / KDE KWin on Wayland — which support
neither layer-shell nor client window positioning — a one-shot `execve`
re-launch under `GDK_BACKEND=x11` onto the X11 path. The backend *decision*
is a pure function of two facts, so the whole table is unit-tested without
needing a second distro.

### Bugs found by running it, not by review

Each was found by inspecting the live surface with `hyprctl layers` /
`hyprctl clients` and screenshots; all are now covered by regression tests.
See `modules/08-transcript-ui/plan.md` for the full list with causes. In
short: layer-shell ignores `set_default_size` (card rendered 205px wide);
a resizable window keeps its first allocation, so every transcript row was
squashed to 1x1 and nothing but the status line was visible; a GTK size
request is a minimum, so long lines pushed the card to 1211px; the overlay
opened on the wrong monitor without an explicit `set_monitor`;
`AlreadyRunningError` escaped as a traceback because the socket was claimed
after the window was built; the X11 WM_CLASS came out as `__main__.py`; and
the stale-transcript check read `visible` after it had already been set.

304/304 tests passing repo-wide (was 232).

**Bug found by the user on real hardware 2026-09-12**: a spoken follow-up
showed its question twice in the overlay (screenshot). `step()`'s follow-up
branch emitted `user_final` outside its `if/else`, so the voice path
published it twice — once from `_transcribe_and_log()` and once from a
stray call. Typed follow-ups and first turns were unaffected, which is why
it took a real conversation to surface. Fixed, plus an overlay-side guard
so anything arriving after a user turn is final can never open a second
row. Four new orchestrator tests count `user_final` events across a whole
multi-turn conversation — the slice the old tests missed. See
`modules/08-transcript-ui/plan.md` item 8.

**Confirmed on real hardware 2026-09-12** — both the layer-shell and X11
backends verified by live surface geometry (correct monitor, centred, 722px
wide, height tracking the conversation, 48px bottom margin) and by
screenshot (listening state with animated level meter and a self-correcting
live partial; speaking state with the reply streaming). **Not yet
confirmed**: the GNOME/KDE re-exec *trigger* (this machine has layer-shell,
so it never fires — the X11 path it targets is verified), and the
no-compositor `no-alpha` styling.

## Repo layout + `scripts/dev.sh` (2026-09-13, requested by the user)

Two housekeeping changes, neither touching any module's code:

- **New `scripts/dev.sh`** — one command to run the whole thing locally in
  the foreground. It existed only as a sequence of RUNBOOK steps before, and
  the parts that are easy to get wrong (the openWakeWord `--no-deps`
  install, the PyTorch CPU index, stopping `gideon.service` so two copies
  don't fight over the mic, starting `ollama serve`) were all manual. It
  also fails fast on the self-inheriting `~/.icons/default` cursor theme
  that `nwg-look` can write: that segfaults every GTK3 Wayland app at
  display open and shows up here as a pystray/Gdk traceback that sends you
  debugging the wrong code entirely. Flags: `--setup`, `--tests`,
  `--no-ollama`, `--check`. Documented in `RUNBOOK.md`.
- **Docs moved into `docs/`** — `ARCHITECTURE.md`, `RUNBOOK.md` and this
  file now live under `docs/`; `README.md` stays at the repo root as the
  landing page. Every cross-reference was updated: module `plan.md` files
  now point at `../../docs/ARCHITECTURE.md`, and source docstrings use the
  repo-root-relative `docs/ARCHITECTURE.md` (several previously said
  `../../ARCHITECTURE.md` from inside `src/`, which never resolved).

**Confirmed on real hardware 2026-09-13**: `scripts/dev.sh` run end to end —
preflight passed, overlay came up on the layer-shell backend, reached
`ready`, and a full wake→STT→LLM→TTS turn completed before `Ctrl+C`.

## 09-agentic: agentic, multi-provider LLM layer (2026-09-14, requested by the user)

The user's ask: move off a single-shot chat completion to an agentic
architecture (the LLM can call tools in a loop), with LLM providers as
swappable/addable files (Ollama first), and tray controls that adapt to
the selected provider - keep the existing start/stop control for a local
provider, add a "pause" guard for a remote one so a background
conversation can't accidentally spend real API credits.

An initial direction using the Cline SDK was explored and ruled out: it's
Node/TS-only with no Python bindings, and this is a pure-Python project -
bridging it in would have meant running a second language runtime as a
subprocess for one piece. Landed on **pydantic-ai** instead (see
`modules/09-agentic/plan.md` for the comparison against openai-agents and
smolagents) - stays pure Python, no new runtime.

### What was built

- New `modules/09-agentic/` — `agentic.providers.registry` (`config.llm.backend`
  -> `ProviderDefinition`; `ollama.py` is the one provider so far, reached
  via Ollama's `/v1` OpenAI-compatible endpoint) and `agentic.tools.registry`
  (one concrete example tool, `get_current_datetime`, proving the tool-call
  loop end-to-end - real device-control tools get added the same way later).
- `04-llm-client`'s `OllamaClient` (direct HTTP to Ollama's own `/api/chat`)
  retired - `AgenticClient` replaces it as the one path every provider goes
  through, built on a `pydantic_ai.Agent`. `LLMClient`'s contract
  (`generate`/`generate_stream`/`cancel`) is unchanged, so `Orchestrator`
  needed no changes to its call sites - see `04-llm-client/plan.md`'s
  "Agentic rewrite" section for what changed under the hood (history
  conversion, the async/sync streaming bridge, `CancellationToken`).
- `Orchestrator.set_paused()`/`is_paused()` (`state_machine.py`, next to
  the existing mic-mute pair) - blocks LLM calls only, mic/STT/wake-word
  keep working. `main.py`'s `_build_dashboard_controls` now looks up
  `agentic.providers.registry.get_provider(config.llm.backend).is_local`
  to decide the tray's first LLM control: local provider (Ollama today)
  keeps today's start/stop `ollama serve` control; a non-local provider
  gets the pause toggle instead. No changes needed in `tray.py`/
  `dashboard.py` - both already just render whatever `DashboardControl`
  list they're given.
- `config.yaml`'s `llm` section gained `api_key_env` (the env var *name*
  to read a remote provider's key from - never the key itself, so it
  never ends up in `config.yaml`/git). Switching providers is still a
  config edit + restart, consistent with every other backend choice in
  this config (`stt.backend`, `tts.backend`, ...) - a live tray/dashboard
  picker is a bigger follow-up, not done here.

### Verified

Against a real local Ollama server (`qwen2.5:1.5b`) 2026-09-14: `generate`/
`generate_stream` (real token deltas), multi-turn history recall across
calls, the `get_current_datetime` tool actually being invoked (not the
model guessing), mid-stream `cancel()` (the streaming thread exits
cleanly, no hang), and the clear-error path when the provider is
unreachable. Full suite (`pytest modules/ -q`) green — 316 passed,
including new unit tests for `AgenticClient` (against pydantic-ai's
built-in `TestModel`/`FunctionModel`, no real server) and the provider
registry, plus a new `Orchestrator` test confirming `_think_and_speak`
skips the LLM call entirely while paused.

**Not yet confirmed**: a real end-to-end `scripts/dev.sh` voice turn with
the rewritten client (only `AgenticClient` itself was exercised directly,
not through the full mic→wake-word→STT→LLM→TTS pipeline), and the tray's
Pause/Active control against a second, real non-local provider (no second
provider is registered yet - only exercised by pointing `config.llm.backend`
at a value not marked `is_local` and confirming the dashboard slot swaps).

## Tool-calling reliability: qwen2.5:1.5b essentially never calls tools (2026-09-15, reported by the user)

The user asked Gideon for the current date/time and got a hallucinated
"random 2023 date" instead of the real answer from `get_current_datetime`
(the example tool added in `09-agentic`) - reported live from
`gideon.service`'s own log: *"can u use ur date time tool and tell"* ->
*"The current date and time is 20:18 today, Thursday, September 3, 2023."*

### Root cause

Not a bug in `09-agentic`/`AgenticClient` - confirmed by hitting Ollama's
`/v1/chat/completions` directly with the exact tool definition:
`qwen2.5:1.5b` almost never returns a real `tool_calls` response
(`finish_reason: "stop"`, no `tool_calls` field). Instead it either
writes text that merely *looks* like a tool reference (e.g. `"The
current local time is [get_current_datetime]"`), flatly refuses ("I am
using my knowledge... without needing external tools"), or hallucinates
a stale date from its training data - which is exactly the "random 2023
date" the user saw. Tried and ruled out as fixes on their own:
- Explicit prompt wording ("use your tool") - no effect.
- `temperature=0` for more deterministic output - no effect, same
  failure modes.
- A system prompt explicitly instructing tool use for real-time
  info - measurably helped (~0% -> ~1-in-3 success for `qwen2.5:1.5b`
  alone) but nowhere near reliable on its own.

Confirmed the wiring itself is correct by pointing the same code/tool at
larger models already pulled on this machine: `llama3.2:3b` and
`qwen3:8b` both reliably returned real `tool_calls` for the same prompt
via direct `curl` testing. This is a **model capability limitation**,
not a code defect - small (~1.5B parameter) local models are simply
unreliable at OpenAI-style structured tool calling, and reliability
scales with model size.

### Fix applied

User chose to switch the default model rather than keep `qwen2.5:1.5b` or
prompt-engineer around it. `config.yaml`'s `llm.model` is now
`llama3.2:3b` (also given the "always call tools, never guess" system
prompt as a further, free improvement). Verified via `AgenticClient`
against the real `config.yaml` end to end: the exact prompts that failed
before now correctly call `get_current_datetime` and return the real
current date/time about half the time (2/4 in one verification run) -
a real, measured improvement over `qwen2.5:1.5b`'s effectively 0%, but
**still not fully reliable** - `llama3.2:3b` sometimes still hallucinates
a tool-shaped response instead of a real call (e.g. one run answered
"I don't have real-time access... let me check with Google's date and
time API" without an actual tool call). The user was told this
explicitly before choosing - it's a genuine capability ceiling for a
3B CPU-only local model, not something prompt tuning alone fixes.

**Not yet done**: a real mic/voice round-trip with the new model+prompt
(only `AgenticClient.generate_stream()` was exercised directly against
the live config, not the full `scripts/dev.sh` pipeline) - and no
further mitigation (e.g. a deterministic non-LLM fast-path for
date/time-shaped queries) was attempted since the user didn't ask for
one; worth revisiting if `llama3.2:3b`'s ~50% success rate turns out to
be too unreliable in practice.

## OpenRouter provider + `.env` overrides (2026-09-15, requested by the user)

Same day as the `llama3.2:3b` fix above, the user decided local Ollama
models weren't worth the tool-calling reliability fight ("this small
model are wierd") and asked for an OpenRouter provider plus a free-model
recommendation good at agentic/tool-use tasks, fast and light.

### What was built

- New `modules/09-agentic/src/agentic/providers/openrouter.py`, registered
  in `providers/registry.py` alongside `ollama`. Uses pydantic-ai's
  dedicated `pydantic_ai.providers.openrouter.OpenRouterProvider` rather
  than a generic OpenAI-compatible `base_url` shim (which is all Ollama
  gets, since it has no pydantic-ai model class of its own) -
  `OpenRouterProvider` applies per-upstream-model tool-calling profiles
  (qwen/anthropic/meta-llama/etc. quirks), which is directly relevant
  given the tool-calling reliability problems this whole thread started
  from. `is_local=False`, so it automatically gets the tray's Pause/Active
  guard instead of a start/stop control (`main.py`'s existing
  `is_local`-branching logic needed no changes at all).
- **`.env` / `.env.example`** (new, at the repo root, `.env` gitignored):
  the user didn't want their personal model pick forced on `config.yaml`
  for anyone else using the repo, on top of the API key obviously never
  being committable. `config/config.yaml`'s `llm.backend`/`api_key_env`
  stay committed (shared architecture, like `stt.backend`), but
  `llm.model` is now overridable via `.env`'s `GIDEON_LLM_MODEL` without
  touching a tracked file. `shared/config.py`'s `load_config()` calls
  `python-dotenv`'s `load_dotenv()` on the repo-root `.env` and applies
  the override - **only when called with `path=None`** (the real default
  every entry point uses); an explicit `path` (every existing test but
  one) skips both, so a developer's own `.env` can never leak into a test
  pointed at its own scratch config file. `python-dotenv` was already an
  installed transitive dependency of `pydantic-ai`; added explicitly to
  the root `pyproject.toml`.
- `config/config.yaml`: `llm.backend: openrouter`,
  `llm.api_key_env: OPENROUTER_API_KEY`,
  `llm.model: nvidia/nemotron-3.5-lightning:free` (the committed shared
  default - a real personal choice still goes in `.env`, not here).
- `scripts/dev.sh`'s Ollama preflight became provider-aware
  (`preflight_local_ollama`/`preflight_remote_llm` functions, branching
  on `agentic.providers.registry.get_provider(...).is_local` the same way
  `main.py`'s tray control already did): a local provider gets the
  existing `ollama serve` start/health-check/model-pulled checks
  unchanged; a remote provider gets a check that its API key env var is
  actually set (from `.env` or the shell), `die`ing with the exact fix
  otherwise - checked in one Python process so `.env`'s contents are
  visible to the check (a bare bash `[ -n "$VAR" ]` would miss a
  `.env`-only key, since bash never sources that file itself).
- `docs/RUNBOOK.md` updated to describe both preflight paths and point at
  `.env.example`.

### Model choice: `nvidia/nemotron-3.5-lightning:free`

Training data is stale for anything this recent, so the model catalog was
pulled live from `https://openrouter.ai/api/v1/models` (2026-09-15) and
filtered for `pricing.prompt == 0 && pricing.completion == 0` plus
`"tools"` in `supported_parameters` - 22 free, tool-capable models, nearly
all from post-cutoff model families. Shortlisted three fast/light
candidates and the user picked **`nvidia/nemotron-3.5-lightning:free`**
(MoE, 3B active/30B total params, OpenRouter's own description: built for
"high-throughput agentic workloads") over `google/gemma-4-26b-a4b-it:free`
(3.8B active/25.2B total, "near-31B quality") and `liquid/lfm-2.5-2.6b:free`
(smallest at 2.6B dense, purpose-built for agent workflows but Liquid
explicitly advises against it for agentic coding).

**Real caveat, documented in code/config comments and `09-agentic/plan.md`**:
OpenRouter caps `:free` models at 50 requests/day (20/min) until the
account has purchased $10+ in credits (then 1000/day), and a single
tool-using turn can cost 2 requests. This provider is meant to be used
deliberately/occasionally - the tray's Pause/Active guard (built in the
earlier agentic-rewrite session specifically for non-local providers) is
the right tool for not burning through the daily cap by accident, not
just a nice-to-have.

**Not yet verified**: no OpenRouter API key was available in this
session, so - unlike the Ollama-vs-Ollama comparison earlier in this
file - `nvidia/nemotron-3.5-lightning:free`'s tool-call reliability is
un-tested. Once the user has a real key (pasted into `.env`), the
approved plan's verification steps should be run: hit
`https://openrouter.ai/api/v1/chat/completions` directly with the
`get_current_datetime` tool for a real `tool_calls` check, then the same
prompts through `AgenticClient` end-to-end, then a real `scripts/dev.sh`
voice/text turn. If this model underperforms, swap `GIDEON_LLM_MODEL` in
`.env` to one of the other two shortlisted models - no code change
needed, the provider is generic across any OpenRouter model id.

Full suite (`pytest modules/ -q`) green - 325 passed, including new
`openrouter` provider tests (mirroring the `ollama` ones) and new
`.env`/env-override tests in `00-shared/tests/test_config.py`
(override applies only for `load_config()`'s default path, missing
`.env` is a no-op, explicit paths are never affected).

## Agent-only demo + tool-call logging (2026-09-15, requested by the user)

Same day as the OpenRouter work above, the user asked for two smaller
follow-ups (jotted in a scratch `tmp.md`): "more detailed logs of agents
with a prefix maybe agent or something", and "a demo/tmp file to play
around only the agent" - both aimed at making the agent loop easier to
watch/debug in isolation, directly motivated by how painful the earlier
tool-calling reliability investigation was without any visibility into
whether a tool call actually happened.

### What was built

- **Logging**: `AgenticClient` (`04-llm-client/src/llm_client/agentic_client.py`)
  now logs to a `"agentic"` logger, and every tool in `09-agentic`'s
  registry is wrapped in a small `_logged()` decorator
  (`agentic/tools/registry.py`) that logs to `"agentic.tools"`. Since
  `shared.logging_setup`'s format is `... %(name)s: %(message)s`, this
  gives exactly the "prefix" the user asked for - every line reads
  `agentic: ...` or `agentic.tools: ...`. INFO covers run/tool
  start, finish (with elapsed time; streaming also logs delta count),
  cancellation, and errors; full prompt/reply text is DEBUG-only
  (mirrors `07-orchestrator`'s existing DEBUG-only "LLM token delta"
  logging, so a normal run stays quiet at INFO). The tool wrapper uses
  `functools.wraps`, confirmed (via `inspect.signature`) to preserve the
  wrapped tool's name/docstring/signature so pydantic-ai's schema
  generation sees the exact same shape it would from the unwrapped
  function.
- **`04-llm-client/src/llm_client/agent_demo.py`** (new): a standalone
  REPL (`python -m llm_client.agent_demo`) that talks to `AgenticClient`
  directly - no mic/wake-word/STT/TTS involved - replacing the
  `chat_demo.py` that the agentic rewrite deleted without a replacement.
  Turns on INFO logging for `agentic`/`agentic.tools` itself so tool
  calls are visible while chatting; `--debug` bumps to DEBUG for full
  prompt/reply text.

### Verified

Confirmed live against a scripted `pydantic_ai.models.function.FunctionModel`
end-to-end through `AgenticClient.generate()` - the exact output:
```
agentic: agent ready: backend=ollama model=fake
agentic: agent run starting (0 history turns)
agentic.tools: tool call: get_current_datetime()
agentic.tools: tool result: get_current_datetime -> 'Tuesday, September 15 2026 11:06' (0.000s)
agentic: agent run finished in 0.02s
```
Unit-tested: `09-agentic/tests/test_tools_registry.py` (new - wrapper
preserves name/doc/signature, calls through, logs call/result/errors)
and new `caplog`-based tests in `04-llm-client/tests/test_agentic_client.py`
(run start/finish, prompt/reply at DEBUG, stream start/finish with delta
count, error path, cancel path). Full suite green - 337 passed (up from
325).

**Not yet done**: `agent_demo.py` hasn't been run interactively by hand
yet - needs either a local Ollama server or a real OpenRouter key (see
the still-open verification step in `09-agentic/plan.md`'s Verification
status). `tmp.md` (the user's scratch note this work came from) was left
alone rather than deleted unilaterally.

## Agentic logs on the dashboard (2026-09-15, requested by the user)

Follow-up to the tool-call logging above - user's ask: "can we show
thoose logs on dashboard as well", after finding the terminal logs
themselves weren't showing (see the previous entry's root cause: `main.py`
never called `setup_logging()` for the "agentic"/"agentic.tools" loggers,
now fixed).

### What was built

- **`shared.logging_setup.CallbackHandler`** (new) - a plain
  `logging.Handler` that forwards each record as `"name: message"` text
  to a callback, with a raising callback swallowed via `handleError` so a
  UI hiccup can never take down logging elsewhere. Generic on purpose
  (just wraps a `Callable[[str], None]`) rather than tray-specific, so
  any future UI could reuse it the same way.
- **`text_input.tray.TrayApp.append_log(message)`** (new) - adds one line
  to the dashboard's existing activity-log deque (the same one
  `set_status` already fed) without touching the tray icon's tooltip,
  which stays reserved for the orchestrator's own single current-state
  message. `set_status` now calls `append_log` internally instead of
  touching `self._log` directly.
- **`07-orchestrator/main.py`**: after constructing `tray_app`, attaches
  one `CallbackHandler(tray_app.append_log)` to both the "agentic" and
  "agentic.tools" loggers (alongside the `StreamHandler` `setup_logging()`
  already gave them, so terminal output is unaffected) - every agent
  run/tool-call log line now appears in the dashboard's activity log
  panel too, interleaved with the orchestrator's own status lines.

### Verified

Unit-tested: `00-shared/tests/test_logging_setup.py` (`CallbackHandler`
forwards `"name: message"`, swallows a raising callback) and
`06-text-input/tests/test_tray_app.py` (`append_log` adds to the log
without touching the icon title). Confirmed live end-to-end by
replicating `main.py`'s exact wiring (`setup_logging` + `CallbackHandler`
+ a real `TrayApp`) against a scripted `FunctionModel` tool call through
`AgenticClient.generate()` - the dashboard's `tray._log` deque ends up
with the identical five lines the terminal shows. Full suite green - 340
passed (up from 337).

**Not yet done**: not confirmed against the real running app/dashboard
window (needs the user to open "Dashboard..." from the tray during a
real conversation) - the original "stuck on the time question" report
from the previous entry is also still unresolved; the user should retry
now that both the terminal and the dashboard will show exactly where a
stuck run stops (no `agentic:` line at all, a `tool call` with no
matching `tool result`, or `tool call`/`tool result` repeating forever
without an `agent run finished`).

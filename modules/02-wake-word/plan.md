# 02-wake-word

## Goal

Detect the wake phrase from a stream of audio frames, locally, with low
false-positive/negative rates.

## Depends on

`00-shared` (interfaces, config). Can be tested standalone with its own
mic capture for now — doesn't need to import `01-audio-io`'s code, just
match the same audio format (int16 mono 16kHz) so it slots into
`AudioSource` later.

## Interface implemented

`WakeWordDetector` (see `../../ARCHITECTURE.md`).

## Recommended library

**openWakeWord** — pip-installable, ships pretrained models (e.g.
"hey jarvis", "alexa"-style phrases), fully local inference (ONNX/tflite),
and has a documented pipeline for training a custom wake word from
synthetic TTS-generated data if a pretrained one isn't the phrase you want.

## Deliverables

- `src/wake_word/detector.py` — `OpenWakeWordDetector` implementing
  `WakeWordDetector`: wraps the openWakeWord model, `process_chunk(chunk)`
  feeds it a frame and returns `True` once the running score crosses
  `config.wake_word.threshold`, `reset()` clears internal state after a
  detection so it doesn't immediately re-trigger.
- A tiny standalone runner script (`src/wake_word/listen_demo.py`) that
  opens the mic directly (own minimal capture, doesn't need to wait for
  `01-audio-io`), runs the detector, and prints "WAKE WORD DETECTED" plus a
  timestamp when triggered.

## Standalone test plan

1. Pick a pretrained model that's close to the desired wake phrase (or
   accept a stand-in phrase for now — see open decision below).
2. Run `listen_demo.py`, say the wake phrase 10 times at normal speaking
   volume/distance, count true positives.
3. Talk normally in the background (TV, conversation) for a few minutes,
   confirm no/rare false triggers.
4. Note the measured true/false positive behavior in this file — it
   informs whether the threshold needs tuning or a custom model is worth
   training later.

## Out of scope

- Continuous audio capture lifecycle — that's `01-audio-io`'s job in the
  integrated system; this module just needs *some* frame source for its own
  test.

## Open decisions for this module

- **Wake phrase: switched from "hey jarvis" to a custom "hey gideon"
  model — done.** `hey_jarvis` (openWakeWord's pretrained model) was used
  as-is for v1 since it needed no training, but it was the wrong phrase
  and its recall was weak (see Verification status below). A custom
  `hey_gideon` model has now been trained (locally on CPU, not Colab —
  see below) and is committed at `models/hey_gideon.onnx`;
  `config.yaml`'s `wake_word.model` and `config.py`'s
  `WakeWordConfig.model` default both point at it, and `detector.py`'s /
  `listen_demo.py`'s own defaults were updated to match.
  `training/hey_gideon_training.ipynb` (+ `training/README.md`) still
  documents the Colab route for anyone who wants to retrain (e.g. with
  more samples - see the note below), but **local CPU training turned out
  to work and was actually used**, once several real upstream breakages
  were worked around:
  - `piper-sample-generator`'s mainline repo was restructured
    (2026-03-12, commit `1a8c49b`) removing the flat `generate_samples.py`
    that `openwakeword/train.py` imports - pinned to commit `c9d824c`
    (last commit before that restructuring) instead.
  - That pinned commit's `generate_samples()` no longer has a default
    `model` path (removed in the same commit that upgraded it to torch 2)
    - `train.py`'s 4 call sites were patched to pass `model=` explicitly.
  - `torch_audiomentations==0.11.0` calls the since-removed
    `torchaudio.set_audio_backend(...)` - patched to a no-op.
  - `pronouncing` imports `pkg_resources`, dropped by setuptools 81+ -
    pinned `setuptools<81`.
  - `datasets==2.14.6` (openWakeWord's own pin) doesn't import under
    current pyarrow; latest `datasets` needs `torchcodec` + system ffmpeg
    libs not present here - settled on `datasets==2.19.0` as the
    compatible middle ground.
  - The upstream notebook's AudioSet download (`bal_train09.tar`) 404s -
    that HF dataset was reorganized into parquet; switched to
    `datasets.load_dataset("agkphysics/AudioSet", "balanced", ...,
    streaming=True)`, the same pattern the MIT-RIR cell already used.
  - `rudraml/fma`'s dataset loading script is broken (streaming and
    non-streaming both fail) on current library versions - dropped FMA
    entirely, background augmentation uses AudioSet + MIT RIRs only.
  - Current `torchaudio` routes `.load()`/`.info()` through `torchcodec`,
    which needs system ffmpeg shared libraries (`libavdevice`/`libavutil`
    at specific versions) not present on this machine and not installable
    without sudo - added a `sitecustomize.py` shim in the training venv
    that reimplements both via `soundfile` instead, bypassing torchcodec.
  - `en_US-libritts_r-medium` (the Piper voice used for synthesis)
    generates audio at 22050 Hz, but openWakeWord's whole
    augmentation/feature pipeline hardcodes 16000 Hz - all 10,000
    generated clips were resampled to 16kHz (`scipy.signal.resample_poly`)
    as a one-time post-processing pass before `--augment_clips`.
  - PyTorch's newer ONNX exporter needs the separate `onnxscript` package.
  - The exporter also split weights into a companion `hey_gideon.onnx.data`
    file by default; re-saved with `onnx.save_model(...,
    save_as_external_data=False)` so the repo only needs to track one
    file.
  **Training config actually used**: `n_samples=3000` / `n_samples_val=2000`
  (openWakeWord's own recommendation is 20,000+ for best results - this
  was a deliberately smaller run to get a working v1 without a very long
  CPU training session), `steps=50000` (+ two ~5000-step fine-tuning
  cycles openWakeWord's training loop runs automatically), `layer_size=32`
  `dnn` model. **Measured final metrics**: accuracy 0.807, recall 0.623,
  false positives/hour 4.87 (target was 0.2/hour - not met; a spot-check
  of 10 negative clips showed 9 correctly scored near-zero but 1 false-
  triggered at 0.99, consistent with that measured FP rate). **This is a
  known, expected quality gap** from training on far fewer samples than
  recommended, not a bug in the pipeline. If false positives are a problem
  in real use, retraining with a larger `n_samples` (the notebook's own
  Colab route, or the same local pipeline given enough time) is the first
  thing to try - the compatibility fixes above should already be baked
  into `training/hey_gideon_training.ipynb`.
- **Detection edge behavior**: `process_chunk()` is edge-triggered - it
  returns `True` only on the transition from below-threshold to
  at-or-above-threshold (score `>=` threshold counts as triggering), and
  auto-rearms as soon as the score drops back below threshold, so a
  single sustained utterance doesn't fire repeatedly. `reset()` is a
  separate manual re-arm (and clears the underlying openWakeWord model's
  rolling audio buffer via its own `reset()` if present) for callers -
  e.g. `07-orchestrator` - that want a hard reset independent of the
  current score, such as after handling a detection and returning to
  IDLE.

## Setup

```
pip install -r modules/02-wake-word/requirements.txt
```

openWakeWord is pure ONNX/tflite - no torch/CUDA pitfalls like
`01-audio-io`'s silero-vad dependency. If `Model(wakeword_models=[...])`
raises about a missing model file on first run, fetch the pretrained
files once: `python -c "from openwakeword import utils; utils.download_models()"`.
`detector.py` forces `inference_framework="onnx"` and does not rely on
`tflite-runtime`, since that package's compiled extension is built
against the NumPy 1.x ABI and crashes (`AttributeError: _ARRAY_API not
found`) under NumPy 2.x, which this repo uses.

## Verification status

Implemented and unit-tested (scripted `model_fn`, no real audio/model
needed - 8 tests covering rising-edge trigger, no-retrigger while score
stays high, retrigger after a dip, threshold boundary, and `reset()`
behavior including delegating to an underlying model's own `reset()`).

**Confirmed working on real hardware 2026-08-26** via
`python -m wake_word.listen_demo`: detects "hey jarvis" at
`threshold=0.5`, but recall is weak - the user has to pronounce the
phrase quite specifically/deliberately for it to trigger; casual/fast
speech often misses. No false positives observed during normal
background talk. **This is a known limitation of the pretrained
`hey_jarvis` model at the default threshold, not a bug in this module's
wrapper logic.** Two follow-ups worth trying later, not done yet:
lowering `wake_word.threshold` below 0.5 (trades some false-positive
risk for recall - untested), or training a custom wake word model via
openWakeWord's synthetic-TTS pipeline for a phrase with better recall.
Tracked as a deferred improvement rather than blocking this module.

**Custom `hey_gideon` model trained 2026-08-31** (see the "Wake phrase"
open decision above for the full list of compatibility fixes this took).
Initially verified by calling `openwakeword.model.Model(wakeword_models=
[...], inference_framework="onnx")` directly from this repo's own `.venv`
(not just the training script) - loaded cleanly, scored real background
noise at ~0.0, scored real "hey gideon" clips (held-out `positive_test`
set) at 0.66-0.99, and a 10-clip spot-check of held-out negative clips
showed 9 correctly near-zero and 1 false-triggered at 0.99 (roughly
consistent with the measured 4.87 false-positives/hour vs. a 0.2/hour
target - not met, a known consequence of the reduced `n_samples`).

**That verification missed a real bug**, because it called
`openwakeword.model.Model` directly rather than going through this
module's own `detector.py` wrapper: `_OpenWakeWordModel.__init__` stored
`self._model_name` as the raw path string
(`"modules/02-wake-word/models/hey_gideon.onnx"`), but
`openwakeword.Model.predict()` keys its results dict by
`os.path.splitext(os.path.basename(path))[0]` (i.e. `"hey_gideon"`), not
by the raw path. This only ever worked for a bare built-in name like
`"hey_jarvis"` (where that derivation is a no-op), so it was silently
broken from the start for any custom-model path and only surfaced when
the user ran `python -m wake_word.listen_demo` for real and hit
`KeyError: 'modules/02-wake-word/models/hey_gideon.onnx'`. **Fixed**:
`_OpenWakeWordModel.__init__` now applies the same
`os.path.splitext(os.path.basename(...))[0]` derivation itself. Lesson
for next time: verify through the actual wrapper class, not just the
underlying library, even when the wrapper looks like a thin pass-through.

**Confirmed working on real hardware 2026-08-31** via
`.venv/bin/python -m wake_word.listen_demo` after the `KeyError` fix
above - user-reported "works good" at `threshold=0.5`. Detailed
true/false-positive counts (e.g. a same-style test to the `hey_jarvis`
entry above: 10 deliberate utterances + a few minutes of background talk)
haven't been recorded yet - worth doing if the measured 4.87
false-positives/hour from the held-out validation set (well above the
0.2/hour target) turns out to matter in real day-to-day use.

## When done

Update `../../task.md`: check off `02-wake-word`, record the chosen wake
phrase/model and the measured false-positive behavior.

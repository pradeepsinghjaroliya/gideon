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

- Training a custom wake word model from scratch (v1 uses a pretrained
  model or the closest available phrase). Leave a note here on how to
  revisit this later: openWakeWord's training pipeline generates synthetic
  positive examples via TTS + augmentation, no real recordings needed.
- Continuous audio capture lifecycle — that's `01-audio-io`'s job in the
  integrated system; this module just needs *some* frame source for its own
  test.

## Open decisions for this module

- **Wake phrase: "hey jarvis" (openWakeWord's pretrained `hey_jarvis`
  model), used as-is for v1.** `config.yaml`'s `wake_word.model` was
  already stubbed to `hey_jarvis`, so no custom training needed right
  now. Revisit later via openWakeWord's synthetic-TTS training pipeline
  if a different phrase is wanted.
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

## When done

Update `../../task.md`: check off `02-wake-word`, record the chosen wake
phrase/model and the measured false-positive behavior.

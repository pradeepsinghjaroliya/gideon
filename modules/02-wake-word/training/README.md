# Training a custom wake word model

**Status: done for "hey gideon".** A `hey_gideon` model is trained and
committed at `../models/hey_gideon.onnx`, wired up in `config/config.yaml`.
See `../plan.md`'s "Wake phrase" open decision and "Verification status"
section for the measured metrics (accuracy 0.807 / recall 0.623 / false
positives 4.87 per hour - above the 0.2/hour target, a known limitation of
training on 3,000 samples instead of openWakeWord's recommended 20,000+).

This directory stays as the documented, reusable way to **train a new
model** - for a different phrase, a larger sample count for better accuracy,
or on a GPU machine for speed. Two ways to do it:

## Option A: the automated script (recommended)

```
./train_wake_word.sh --target-phrase "hey gideon" --n-samples 10000
```

This does everything end-to-end in one command: creates an isolated venv,
applies every compatibility patch this pipeline currently needs (dependency
versions have drifted since openWakeWord's own tooling was written - see the
list below for exactly what and why), downloads the training data, and runs
the full generate/augment/train pipeline, producing a ready-to-use `.onnx`
file. Works unmodified on CPU or GPU (PyTorch auto-detects CUDA).

```
Usage: ./train_wake_word.sh [--work-dir DIR] [--target-phrase "hey gideon"]
                             [--n-samples 10000] [--n-samples-val 2000]
                             [--steps 50000] [--audioset-hours 3]
```

- `--work-dir` defaults to `./run` (gitignored) - venv, cloned repos,
  downloaded datasets (tens of GB) all live there, never in git.
- `--n-samples` is the main quality knob. openWakeWord recommends 20,000+ for
  best results; the default here (10,000) is a middle ground. More samples =
  better accuracy/fewer false positives, but proportionally longer
  `--generate_clips` time (the TTS synthesis step).
- Safe to re-run: each setup phase skips work already done (existing
  venv/clones/downloads are reused), and openWakeWord's own
  `--generate_clips`/`--augment_clips`/`--train_model` steps resume rather
  than restart if interrupted partway through.
- Output: `<work-dir>/my_custom_model/<model_name>.onnx` (a single
  self-contained file - no separate `.onnx.data`). Copy it into
  `../models/` and point `config/config.yaml`'s `wake_word.model` at it (see
  "Bring it into this repo" below).
- On a GPU machine: no flags needed, but make sure `pip` resolves a
  CUDA-enabled `torch` build for your CUDA version before running (the
  default on most Linux+NVIDIA setups already is) - the script doesn't pin a
  specific torch/CUDA combination itself.

Run `./train_wake_word.sh --help` for the flag summary from your own
checkout at any time.

## Option B: Google Colab notebook

`hey_gideon_training.ipynb` (this directory) is a Colab-native equivalent of
the same pipeline, patched with the same fixes. Upload it to
[Google Colab](https://colab.research.google.com/) with a T4 GPU runtime, or
open it directly from GitHub (File -> Open notebook -> GitHub) once pushed.
Useful if you don't have a local machine free for an extended run, or want a
GPU without setting one up yourself. See the notebook's own first cell for
usage notes.

## Every compatibility fix these two options apply, and why

openWakeWord's official tooling has drifted out of sync with its own and its
dependencies' current releases. Both the script and the notebook patch
around all of these; if you're troubleshooting a *third* way of running this
pipeline (a fresh from-scratch attempt, say), this is the map of what breaks
and why:

1. **`speexdsp-ns`** (a `pip install -e ./openwakeword` dependency) has no
   wheel for current Python versions, and is only used by an opt-in
   noise-suppression feature `train.py` never touches. Fix: install with
   `--no-deps`, add the real dependencies back explicitly.
2. **`piper-sample-generator`'s repo was restructured** (2026-03-12, commit
   `1a8c49b`, "Move to package"), deleting the flat `generate_samples.py`
   that `openwakeword/train.py` imports. Fix: pin the clone to commit
   `c9d824c` (the last commit before that restructuring).
3. **That pinned commit's `generate_samples()` has no default `model`
   path** (removed in the same commit that upgraded it to torch 2), but
   `train.py`'s 4 call sites never pass `model=`. Fix: patch `train.py` to
   compute the model path and pass it explicitly.
4. **`piper-tts` package needed**: at the pinned commit,
   `generate_samples.py` unconditionally imports `from piper import
   PiperVoice, SynthesisConfig` (only actually used by a sibling ONNX-based
   function, not `generate_samples()` itself, but still required at import
   time).
5. **`torch_audiomentations==0.11.0`** calls the since-removed
   `torchaudio.set_audio_backend(...)` (backend selection is automatic in
   current torchaudio). Fix: patch the one call site to a no-op.
6. **`pronouncing`** imports `pkg_resources`, which setuptools 81+ dropped
   entirely. Fix: pin `setuptools<81`.
7. **`datasets==2.14.6`** (openWakeWord's own pin) fails to import under
   current pyarrow (a removed API); the latest `datasets` (5.x) fixes that
   but then requires `torchcodec` for audio decoding, which needs system
   ffmpeg libraries not guaranteed present. Fix: pin the middle ground,
   `datasets==2.19.0`.
8. **AudioSet's download is dead**: the upstream notebook's raw
   `bal_train09.tar` download 404s - that HuggingFace dataset was
   reorganized into parquet format since. Fix: load it the same streaming
   way as the MIT-RIR dataset:
   `datasets.load_dataset("agkphysics/AudioSet", "balanced", ...,
   streaming=True)`.
9. **FMA is broken outright**: `rudraml/fma`'s dataset loading script fails
   on both streaming (`ValueError: Cannot seek streaming HTTP file`) and
   non-streaming access with current library versions. Fix: drop it -
   background augmentation uses AudioSet + MIT RIRs only.
10. **`torchaudio` routes all I/O through `torchcodec`**, which needs system
    ffmpeg shared libraries (`libavdevice`/`libavutil` at specific versions)
    that may not be present or installable without sudo. Fix: a
    `sitecustomize.py` shim that reimplements `torchaudio.load`/`.info()`
    via `soundfile` instead (every file this pipeline touches is a plain
    WAV, so this is safe).
11. **Sample rate mismatch**: `en_US-libritts_r-medium` (the Piper voice
    used for synthesis) generates audio at 22050 Hz, but openWakeWord's
    whole augmentation/feature pipeline hardcodes 16000 Hz. Fix: resample
    all 4 generated-clip directories to 16kHz (`scipy.signal.resample_poly`)
    right after `--generate_clips`, before `--augment_clips` - otherwise
    that step fails with `"Clip does not have the correct sample rate!"`.
12. **`deep-phonemizer` (imported as `dp`)** is needed by
    `generate_adversarial_texts()` as a fallback for any `--target-phrase`
    word not found in the standard pronunciation dictionary (an invented
    name, say) - "hey gideon" doesn't need it (both words are in-dictionary)
    but a different phrase might. Its own checkpoint loader calls
    `torch.load(...)` without `weights_only=False`, which PyTorch 2.6+ now
    blocks by default. Fix: install `deep-phonemizer==0.0.19` and patch its
    one `torch.load` call site.
13. **`--convert_to_tflite` is buggy in openWakeWord's own `train.py`**: the
    flag uses `action="store_true"` combined with `default="False"` - a
    non-empty *string*, which is truthy in Python, so the tflite conversion
    (needing `onnx_tf`/`tensorflow`, which we don't install - this repo only
    ever loads `.onnx`) runs unconditionally and crashes the whole training
    run right after the ONNX export succeeds. Fix: patch the default to a
    real `False`.
14. **`onnxscript`** is needed for PyTorch's newer ONNX exporter (used by
    `train.py`'s final export step) - a separate package from `onnx` itself.
15. **The ONNX exporter splits weights into a companion `<model>.onnx.data`
    file** by default, even for a model this tiny. Fix: re-save with
    `onnx.save_model(m, path, save_as_external_data=False)` after loading
    with `load_external_data=True`, for a single self-contained file.

## Bring the result into this repo

1. Copy the trained file: `cp run/my_custom_model/<model_name>.onnx
   ../models/<model_name>.onnx` (the script prints this exact command at the
   end).
2. Update `config/config.yaml`:
   ```yaml
   wake_word:
     backend: openwakeword
     model: modules/02-wake-word/models/<model_name>.onnx
     threshold: 0.5
   ```
   **Note**: openwakeword's `predict()` keys its results dict by
   `os.path.splitext(os.path.basename(path))[0]`, not by the raw path
   string - `detector.py` derives `self._model_name` the same way for
   exactly this reason (a bare built-in name like `hey_jarvis` needs no such
   derivation, which is why a bug here was easy to miss until switching to a
   path - see `../plan.md`'s "Wake phrase" entry for the full story).
3. Also update the default in `modules/00-shared/src/shared/config.py`
   (`WakeWordConfig.model`) if you want the new model to be the default
   rather than just a config override.
4. Re-run `.venv/bin/python -m wake_word.listen_demo` (from the repo root,
   using this repo's own venv - a bare `python -m wake_word.listen_demo`
   will use whatever Python is first on `PATH`, which may not have
   `wake_word` installed) to measure true/false-positive behavior with the
   new model, and re-tune `threshold` if needed.
5. Record the outcome in `../plan.md`'s "Verification status" section.

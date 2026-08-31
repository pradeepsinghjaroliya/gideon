#!/usr/bin/env bash
# End-to-end custom wake-word training: sets up an isolated venv, applies
# every compatibility patch this pipeline currently needs (see README.md
# for why each one exists), downloads the training data, and runs
# openWakeWord's generate/augment/train pipeline to produce a .onnx model.
#
# Works on CPU or GPU with no flags needed - PyTorch auto-detects CUDA, so
# on a GPU box just make sure `pip` resolves a CUDA-enabled torch build
# (the default on most Linux+NVIDIA setups) before running this.
#
# Safe to re-run: each phase skips work that's already done (existing
# venv/clones/downloads are reused), and openWakeWord's own
# --generate_clips/--augment_clips/--train_model steps resume rather than
# restart if interrupted partway through.
#
# Usage:
#   ./train_wake_word.sh [--work-dir DIR] [--target-phrase "hey gideon"]
#                         [--n-samples 10000] [--n-samples-val 2000]
#                         [--steps 50000] [--audioset-hours 3]
#
# Output: <work-dir>/my_custom_model/<model_name>.onnx
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WORK_DIR="$SCRIPT_DIR/run"
TARGET_PHRASE="hey gideon"
N_SAMPLES=10000
N_SAMPLES_VAL=2000
STEPS=50000
AUDIOSET_HOURS=3

while [ $# -gt 0 ]; do
  case "$1" in
    --work-dir) WORK_DIR="$2"; shift 2 ;;
    --target-phrase) TARGET_PHRASE="$2"; shift 2 ;;
    --n-samples) N_SAMPLES="$2"; shift 2 ;;
    --n-samples-val) N_SAMPLES_VAL="$2"; shift 2 ;;
    --steps) STEPS="$2"; shift 2 ;;
    --audioset-hours) AUDIOSET_HOURS="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,21p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

MODEL_NAME="$(echo "$TARGET_PHRASE" | tr ' ' '_')"

log() { echo "[train_wake_word] $*"; }

log "work dir:        $WORK_DIR"
log "target phrase:    \"$TARGET_PHRASE\" (model name: $MODEL_NAME)"
log "n_samples:        $N_SAMPLES (n_samples_val: $N_SAMPLES_VAL)"
log "steps:             $STEPS"
log "recommendation:   openWakeWord suggests 20,000+ n_samples for best results;"
log "                   smaller values train faster but recall/false-positive rate suffer"

mkdir -p "$WORK_DIR"
VENV="$WORK_DIR/venv"

# --- 1. venv ---------------------------------------------------------------
if [ ! -d "$VENV" ]; then
  log "Creating venv at $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip -q
else
  log "Reusing existing venv at $VENV"
fi

# --- 2. piper-sample-generator (pinned commit + TTS checkpoint) -----------
PIPER_DIR="$WORK_DIR/piper-sample-generator"
PIPER_PIN="c9d824c0e2cce8bdeb000c219dc9cbc84df086ea"
if [ ! -d "$PIPER_DIR" ]; then
  log "Cloning piper-sample-generator, pinned to the last commit before its"
  log "'Move to package' restructuring (2026-03-12) deleted the flat"
  log "generate_samples.py that openwakeword/train.py imports"
  git clone https://github.com/rhasspy/piper-sample-generator "$PIPER_DIR"
  (cd "$PIPER_DIR" && git checkout "$PIPER_PIN")
else
  log "Reusing existing piper-sample-generator clone at $PIPER_DIR"
fi
mkdir -p "$PIPER_DIR/models"
if [ ! -f "$PIPER_DIR/models/en_US-libritts_r-medium.pt" ]; then
  log "Downloading Piper TTS voice checkpoint (~200MB)"
  curl -sL -o "$PIPER_DIR/models/en_US-libritts_r-medium.pt" \
    'https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt'
fi

# --- 3. openwakeword (clone + patch) ---------------------------------------
OWW_DIR="$WORK_DIR/openwakeword"
if [ ! -d "$OWW_DIR" ]; then
  log "Cloning openwakeword"
  git clone --depth 1 https://github.com/dscripka/openwakeword "$OWW_DIR"
else
  log "Reusing existing openwakeword clone at $OWW_DIR"
fi

log "Patching openwakeword/train.py: the pinned piper-sample-generator commit's"
log "generate_samples() has no default 'model' path (removed in the same commit"
log "that upgraded it to torch 2), but train.py's calls never pass model= - patch"
log "them to pass it explicitly, or they'd fail with a missing-argument TypeError"
python3 - "$OWW_DIR" <<'PYEOF'
import pathlib
import sys

train_py = pathlib.Path(sys.argv[1]) / "openwakeword" / "train.py"
src = train_py.read_text()

anchor = "from generate_samples import generate_samples"
if anchor not in src:
    raise SystemExit(f"train.py's import of generate_samples has changed - re-check this patch: {train_py}")

if "piper_model_path = " not in src:
    src = src.replace(
        anchor,
        anchor + '\n\n    piper_model_path = os.path.join(config["piper_sample_generator_path"], "models", "en_US-libritts_r-medium.pt")',
        1,
    )

old_call = "generate_samples("
new_call = "generate_samples(model=piper_model_path, "
n = src.count(old_call) - src.count(new_call)
if n > 0:
    src = src.replace(old_call, new_call)
    print(f"patched {n} generate_samples() call site(s)")
else:
    print("generate_samples() calls already patched")

# Second, unrelated bug: --convert_to_tflite uses action="store_true" with
# default="False" - a non-empty STRING, which is truthy in Python, so
# `if args.convert_to_tflite:` always runs regardless of whether the flag
# is passed. That crashes the whole script with ModuleNotFoundError:
# onnx_tf as soon as the (successful) ONNX export finishes, since we don't
# install the tensorflow/onnx_tf stack (not needed - this repo's detector
# only ever loads the .onnx file). Fix the default to a real bool so the
# conversion is actually optional, as intended.
old_default = '''    parser.add_argument(
        "--convert_to_tflite",
        help="Convert the trained ONNX model to TFLite format",
        action="store_true",
        default="False",
        required=False
    )'''
if old_default in src:
    src = src.replace(old_default, old_default.replace('default="False"', "default=False"))
    print("patched --convert_to_tflite default (string \"False\" -> real bool False)")
else:
    print("--convert_to_tflite default already patched (or upstream changed) - check manually")

train_py.write_text(src)
PYEOF

mkdir -p "$OWW_DIR/openwakeword/resources/models"
for f in embedding_model.onnx embedding_model.tflite melspectrogram.onnx melspectrogram.tflite; do
  if [ ! -f "$OWW_DIR/openwakeword/resources/models/$f" ]; then
    log "Downloading openWakeWord resource model: $f"
    curl -sL -o "$OWW_DIR/openwakeword/resources/models/$f" \
      "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/$f"
  fi
done

# --- 4. pip installs ---------------------------------------------------------
log "Installing openwakeword (--no-deps: speexdsp-ns, one of its listed deps,"
log "has no wheel for current Python and is only used by an opt-in feature"
log "train.py never touches)"
"$VENV/bin/pip" install -q -e "$OWW_DIR" --no-deps
"$VENV/bin/pip" install -q \
  "onnxruntime>=1.10.0,<2" "ai-edge-litert>=2.0.2,<3" "tqdm>=4.0,<5.0" \
  "scipy>=1.3,<2" "scikit-learn>=1,<2" "requests>=2.0,<3"

log "Installing training dependencies"
"$VENV/bin/pip" install -q \
  mutagen==1.47.0 torchinfo==1.8.0 torchmetrics==1.2.0 speechbrain==0.5.14 \
  audiomentations==0.33.0 torch-audiomentations==0.11.0 acoustics==0.2.6 \
  pronouncing==0.2.0 "setuptools<81" piper-phonemize webrtcvad piper-tts \
  "datasets==2.19.0" onnxscript onnx deep-phonemizer==0.0.19
# deep-phonemizer (imported as `dp`) is only actually used by
# generate_adversarial_texts() as a fallback for words not found in the
# standard pronunciation dictionary (out-of-vocabulary - e.g. an invented
# name or non-dictionary word in --target-phrase). "hey gideon" doesn't
# need it since both words are in-dictionary, but install it unconditionally
# since this script is meant to work for any --target-phrase.

if "$VENV/bin/python" -c "import torch; exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  log "CUDA available - training/generation will use the GPU automatically"
else
  log "No CUDA detected - running on CPU (slower, but the actual NN training"
  log "step is small/fast regardless; TTS clip generation is the slow part)"
fi

# --- 5. compatibility patches ------------------------------------------------
log "Patching torch_audiomentations: it calls torchaudio.set_audio_backend(...),"
log "an API removed in current torchaudio (backend selection is automatic now)"
"$VENV/bin/python" <<'PYEOF'
import importlib.util
import pathlib

spec = importlib.util.find_spec("torch_audiomentations")
p = pathlib.Path(spec.origin).parent / "utils" / "io.py"
src = p.read_text()
old = 'torchaudio.set_audio_backend("soundfile")'
if old in src:
    p.write_text(src.replace(old, "pass  # patched: set_audio_backend removed in modern torchaudio"))
    print("patched torch_audiomentations")
else:
    print("torch_audiomentations already patched")
PYEOF

log "Patching deep-phonemizer: its checkpoint loader calls torch.load(...)"
log "without weights_only=False, which PyTorch 2.6+ now blocks by default"
log "(only exercised for a --target-phrase word not in the standard"
log "pronunciation dictionary, e.g. an invented name)"
"$VENV/bin/python" <<'PYEOF'
import importlib.util
import pathlib

spec = importlib.util.find_spec("dp.model.model")
p = pathlib.Path(spec.origin)
src = p.read_text()
old = "checkpoint = torch.load(checkpoint_path, map_location=device)"
new = "checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)"
if old in src:
    p.write_text(src.replace(old, new))
    print("patched deep-phonemizer")
else:
    print("deep-phonemizer already patched (or upstream changed) - check manually:", p)
PYEOF

log "Installing a sitecustomize.py shim: current torchaudio routes all audio"
log "I/O through torchcodec, which needs system ffmpeg libraries that may not"
log "be present/installable - bypass it via soundfile (every file here is a"
log "plain WAV, so this is safe)"
SITE_PACKAGES="$("$VENV/bin/python" -c "import site; print(site.getsitepackages()[0])")"
cat > "$SITE_PACKAGES/sitecustomize.py" <<'PYEOF'
import soundfile as sf
import torch
import torchaudio


class _AudioMetaDataShim:
    def __init__(self, info):
        self.sample_rate = info.samplerate
        self.num_frames = info.frames
        self.num_channels = info.channels
        self.bits_per_sample = 0
        self.encoding = "PCM_S"


def _info_via_soundfile(filepath, *args, **kwargs):
    try:
        info = sf.info(filepath)
    except Exception as e:
        raise RuntimeError(str(e))
    return _AudioMetaDataShim(info)


def _load_via_soundfile(filepath, *args, **kwargs):
    data, sr = sf.read(filepath, dtype="float32", always_2d=True)
    return torch.from_numpy(data.T).contiguous(), sr


torchaudio.load = _load_via_soundfile
torchaudio.info = _info_via_soundfile
PYEOF

# --- 6. download data ---------------------------------------------------------
DATA_DIR="$WORK_DIR/data"
mkdir -p "$DATA_DIR"

if [ ! -f "$DATA_DIR/validation_set_features.npy" ]; then
  log "Downloading validation features (~185MB)"
  curl -sL -o "$DATA_DIR/validation_set_features.npy" \
    'https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/validation_set_features.npy'
fi

if [ ! -f "$DATA_DIR/openwakeword_features_ACAV100M_2000_hrs_16bit.npy" ]; then
  log "Downloading ACAV100M negative features (~17GB - this is the biggest"
  log "download and will take a while)"
  curl -sL -o "$DATA_DIR/openwakeword_features_ACAV100M_2000_hrs_16bit.npy" \
    'https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy'
fi

mkdir -p "$DATA_DIR/mit_rirs" "$DATA_DIR/audioset_16k"
if [ -z "$(ls -A "$DATA_DIR/mit_rirs" 2>/dev/null)" ] || [ -z "$(ls -A "$DATA_DIR/audioset_16k" 2>/dev/null)" ]; then
  log "Downloading room-impulse-response + AudioSet background audio"
  log "(AudioSet loaded via datasets.load_dataset(...) streaming - the"
  log "upstream notebook's raw bal_train09.tar download 404s: that HF"
  log "dataset was reorganized into parquet format since)"
  AUDIOSET_HOURS="$AUDIOSET_HOURS" DATA_DIR="$DATA_DIR" "$VENV/bin/python" <<'PYEOF'
import os
import numpy as np
import scipy.io.wavfile
import datasets
from tqdm import tqdm

data_dir = os.environ["DATA_DIR"]
audioset_hours = float(os.environ["AUDIOSET_HOURS"])

rir_dir = os.path.join(data_dir, "mit_rirs")
if not os.listdir(rir_dir):
    print("Downloading MIT RIRs...")
    rir_dataset = datasets.load_dataset(
        "davidscripka/MIT_environmental_impulse_responses", split="train", streaming=True
    )
    for row in tqdm(rir_dataset):
        name = row["audio"]["path"].split("/")[-1]
        scipy.io.wavfile.write(
            os.path.join(rir_dir, name), 16000, (row["audio"]["array"] * 32767).astype(np.int16)
        )

audioset_dir = os.path.join(data_dir, "audioset_16k")
target_n = int(audioset_hours * 3600 // 10)
if len(os.listdir(audioset_dir)) < target_n * 0.9:
    print(f"Downloading ~{audioset_hours}h of AudioSet...")
    audioset_dataset = datasets.load_dataset("agkphysics/AudioSet", "balanced", split="train", streaming=True)
    audioset_dataset = audioset_dataset.cast_column("audio", datasets.Audio(sampling_rate=16000))
    count = 0
    for row in tqdm(audioset_dataset, total=target_n):
        name = row["video_id"] + ".wav"
        scipy.io.wavfile.write(
            os.path.join(audioset_dir, name), 16000, (row["audio"]["array"] * 32767).astype(np.int16)
        )
        count += 1
        if count >= target_n:
            break
print("done")
PYEOF
else
  log "Reusing existing RIR/AudioSet data in $DATA_DIR"
fi

# Note: FMA (originally a second background source in openWakeWord's own
# notebook) is intentionally not used here - rudraml/fma's dataset loading
# script is broken (both streaming and non-streaming) on current library
# versions. AudioSet + MIT RIRs alone is solid coverage for a personal
# wake word; add another background dataset later if needed.

# --- 7. write training config ------------------------------------------------
CONFIG_PATH="$WORK_DIR/training_config.yaml"
log "Writing training config to $CONFIG_PATH"
cat > "$CONFIG_PATH" <<EOF
model_name: "$MODEL_NAME"
target_phrase:
  - "$TARGET_PHRASE"
custom_negative_phrases: []
n_samples: $N_SAMPLES
n_samples_val: $N_SAMPLES_VAL
tts_batch_size: 50
augmentation_batch_size: 16
piper_sample_generator_path: "$PIPER_DIR"
output_dir: "$WORK_DIR/my_custom_model"
rir_paths:
  - "$DATA_DIR/mit_rirs"
background_paths:
  - "$DATA_DIR/audioset_16k"
background_paths_duplication_rate:
  - 1
false_positive_validation_data_path: "$DATA_DIR/validation_set_features.npy"
augmentation_rounds: 1
feature_data_files:
  "ACAV100M_sample": "$DATA_DIR/openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
batch_n_per_class:
  "ACAV100M_sample": 1024
  "adversarial_negative": 50
  "positive": 50
model_type: "dnn"
layer_size: 32
steps: $STEPS
max_negative_weight: 1500
target_false_positives_per_hour: 0.2
EOF

# --- 8. generate clips --------------------------------------------------------
log "Step 1/4: generating synthetic clips (the slow step - scales with n_samples)"
"$VENV/bin/python" "$OWW_DIR/openwakeword/train.py" --training_config "$CONFIG_PATH" --generate_clips

# --- 9. resample to 16kHz -----------------------------------------------------
log "Step 2/4: resampling generated clips to 16kHz (the Piper voice outputs"
log "22050Hz, but openWakeWord's pipeline hardcodes 16000Hz throughout)"
MODEL_NAME="$MODEL_NAME" WORK_DIR="$WORK_DIR" "$VENV/bin/python" <<'PYEOF'
import math
import os

import soundfile as sf
from scipy.signal import resample_poly

work_dir = os.environ["WORK_DIR"]
model_name = os.environ["MODEL_NAME"]
target_sr = 16000

for d in ["positive_train", "positive_test", "negative_train", "negative_test"]:
    d_path = os.path.join(work_dir, "my_custom_model", model_name, d)
    files = [f for f in os.listdir(d_path) if f.endswith(".wav")]
    n_resampled = 0
    for fname in files:
        fpath = os.path.join(d_path, fname)
        data, sr = sf.read(fpath, dtype="float32")
        if sr == target_sr:
            continue
        g = math.gcd(target_sr, sr)
        up, down = target_sr // g, sr // g
        resampled = resample_poly(data, up, down)
        sf.write(fpath, resampled, target_sr, subtype="PCM_16")
        n_resampled += 1
    print(f"{d}: resampled {n_resampled}/{len(files)} files")
PYEOF

# --- 10. augment clips ---------------------------------------------------------
log "Step 3/4: augmenting clips + computing features"
rm -f "$WORK_DIR/my_custom_model/$MODEL_NAME"/*.npy
"$VENV/bin/python" "$OWW_DIR/openwakeword/train.py" --training_config "$CONFIG_PATH" --augment_clips

# --- 11. train ------------------------------------------------------------------
log "Step 4/4: training model ($STEPS steps + automatic fine-tuning cycles)"
"$VENV/bin/python" "$OWW_DIR/openwakeword/train.py" --training_config "$CONFIG_PATH" --train_model

# --- 12. re-save onnx as a single inline file -----------------------------------
ONNX_PATH="$WORK_DIR/my_custom_model/$MODEL_NAME.onnx"
log "Re-saving the ONNX model as a single self-contained file (PyTorch's"
log "exporter defaults to splitting weights into a companion .onnx.data file)"
"$VENV/bin/python" - "$ONNX_PATH" <<'PYEOF'
import sys

import onnx

path = sys.argv[1]
m = onnx.load(path, load_external_data=True)
onnx.save_model(m, path, save_as_external_data=False)
PYEOF
rm -f "${ONNX_PATH}.data"

log ""
log "Done. Trained model: $ONNX_PATH"
log "To use it in this repo:"
log "  cp \"$ONNX_PATH\" \"$SCRIPT_DIR/../models/$MODEL_NAME.onnx\""
log "  # then set wake_word.model to that path in config/config.yaml"
log "  # and re-run: .venv/bin/python -m wake_word.listen_demo"

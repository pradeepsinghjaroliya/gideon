#!/usr/bin/env bash
#
# Verify the built .deb without installing it and without a network.
#
# Extracts the package to a temp dir and exercises every subsystem for real.
# That "for real" matters: every heavy import in this codebase is lazy, so a
# bundle with a missing dependency imports cleanly and only fails the first
# time someone speaks. Importing is not a test.
#
# Offline is enforced by pointing http(s)_proxy at a closed port, so any
# attempt to reach huggingface.co or github.com fails instantly rather than
# hanging. localhost is excluded so Ollama still works.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEB="${1:-$REPO/dist/gideon_0.1.0-2_amd64.deb}"
[ -f "$DEB" ] || { echo "no such .deb: $DEB" >&2; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

head_ "Package metadata"
dpkg-deb --info "$DEB" | sed -n '/^ Package:/,/^ Description:/p' | sed 's/^/  /'
echo "  size on disk: $(du -h "$DEB" | cut -f1)"

head_ "Static checks"
CONTENTS="$WORK/contents.txt"
dpkg-deb --contents "$DEB" > "$CONTENTS"
check_absent() {
    if grep -qE "$1" "$CONTENTS"; then bad "$2 present in package"; else ok "$2 absent"; fi
}
check_absent '/pip/'                      "pip"
check_absent 'site-packages/pytest'       "pytest"
check_absent 'system-gi\.pth'             "the dev venv's system-gi.pth"
check_absent '/\.venv/'                   ".venv"
check_absent 'tflite'                     "tflite-runtime"
for f in ./opt/gideon/python/bin/python3 ./opt/gideon/app/orchestrator/main.py \
         ./opt/gideon/models/whisper/small/model.bin \
         ./opt/gideon/models/piper/en_US-lessac-high.onnx \
         ./opt/gideon/models/wake_word/hey_gideon.onnx \
         ./opt/gideon/native/libportaudio.so.2 \
         ./opt/gideon/typelib/AyatanaAppIndicator3-0.1.typelib \
         ./usr/bin/gideon ./usr/lib/systemd/user/gideon.service \
         ./etc/gideon/config.yaml ./etc/default/gideon; do
    grep -qF " $f" "$CONTENTS" && ok "ships $f" || bad "MISSING $f"
done
# conffiles must be declared, or operator edits are lost on upgrade
CONFF=$(dpkg-deb --ctrl-tarfile "$DEB" | tar -xO ./conffiles 2>/dev/null)
echo "$CONFF" | grep -q '/etc/gideon/config.yaml' && ok "config.yaml declared as conffile" || bad "config.yaml NOT a conffile"
echo "$CONFF" | grep -q '/etc/default/gideon'     && ok "default/gideon declared as conffile" || bad "default/gideon NOT a conffile"
for s in preinst postinst prerm postrm; do
    if dpkg-deb --ctrl-tarfile "$DEB" | tar -xO ./$s 2>/dev/null | grep -q 'set -e'; then
        ok "$s uses set -e"; else bad "$s missing set -e"; fi
done

head_ "Extracting"
dpkg-deb -x "$DEB" "$WORK/root"
G="$WORK/root/opt/gideon"
PY="$G/python/bin/python3"
[ -x "$PY" ] && ok "interpreter executable: $("$PY" -V 2>&1)" || { bad "interpreter not runnable"; exit 1; }

# Rewrite the config for the extracted location.
sed -e "s#/opt/gideon#$G#g" "$WORK/root/etc/gideon/config.yaml" > "$WORK/config.yaml"

# Offline enforcement + point the shim and the gi bridge at the extracted tree.
export http_proxy=http://127.0.0.1:1 https_proxy=http://127.0.0.1:1
export no_proxy=localhost,127.0.0.1 NO_PROXY=localhost,127.0.0.1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export GIDEON_CONFIG="$WORK/config.yaml"
export GIDEON_NATIVE_DIR="$G/native"
export LD_LIBRARY_PATH="$G/native"
export GI_TYPELIB_PATH="$G/typelib"
export PYTHONPATH="$G/app:/usr/lib/python3/dist-packages"
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=4

head_ "Subsystem exercise (offline, extracted tree)"
"$PY" - <<'PYEOF'
import sys, traceback
import numpy as np

results = []
def check(name, fn):
    try:
        detail = fn()
        results.append((True, name, detail))
    except Exception as exc:
        results.append((False, name, f"{type(exc).__name__}: {exc}"))
        traceback.print_exc(limit=3)

def t_config():
    from shared.config import load_config
    c = load_config()
    assert c.stt.model_path and c.tts.voices_dir, "packaged config lacks absolute model paths"
    return f"whisper={c.stt.model_path.split('/')[-1]} voice={c.tts.voice}"

def t_portaudio():
    import sounddevice as sd
    ver = sd.get_portaudio_version()[1].split(',')[0]
    maps = open('/proc/self/maps').read()
    assert 'libportaudio' in maps, "portaudio not mapped"
    vendored = any('/native/libportaudio' in l for l in maps.splitlines())
    assert vendored, "loaded the SYSTEM portaudio, not the vendored one"
    return f"{ver} (vendored), {len(sd.query_devices())} devices"

def t_wakeword():
    from shared.config import load_config
    from wake_word.detector import OpenWakeWordDetector
    c = load_config()
    d = OpenWakeWordDetector(model=c.wake_word.model, threshold=c.wake_word.threshold)
    for _ in range(5):
        d.process_chunk(np.zeros(1280, dtype=np.int16))
    return "hey_gideon.onnx loaded, 5 frames processed"

def t_vad():
    from audio_io.vad import SileroVAD
    v = SileroVAD(sample_rate=16000)
    v.is_speech(np.zeros(1600, dtype=np.int16))
    return "silero (onnx+torch) ran"

def t_roundtrip():
    from shared.config import load_config
    from tts.engine import PiperEngine
    from stt.engine import FasterWhisperEngine
    c = load_config()
    phrase = "The weather in London is cold today."
    audio, sr = PiperEngine(voice=c.tts.voice, voices_dir=c.tts.voices_dir).synthesize(phrase)
    assert audio.size > 0, "piper produced no audio"
    text = FasterWhisperEngine(model_size=c.stt.model_size, device=c.stt.device,
                               model_path=c.stt.model_path).transcribe(audio, sr)
    assert text.strip().rstrip('.').lower() == phrase.rstrip('.').lower(), f"got {text!r}"
    return f"TTS {audio.shape} @{sr}Hz -> STT {text.strip()!r}"

def t_tray():
    import text_input.tray as tray
    import pystray
    return f"pystray backend={pystray.Icon.__module__.split('.')[-1]}, TrayApp={tray.TrayApp.__name__}"

def t_llm():
    from shared.config import load_config
    from llm_client.ollama_client import OllamaClient
    c = load_config()
    cl = OllamaClient(model=c.llm.model, base_url=c.llm.base_url, system_prompt=c.llm.system_prompt)
    return repr(cl.generate("Say hello in five words.", [])[:60])

for name, fn in [("config resolution", t_config), ("PortAudio (vendored)", t_portaudio),
                 ("wake word", t_wakeword), ("VAD", t_vad),
                 ("TTS->STT round trip", t_roundtrip), ("tray / pystray", t_tray),
                 ("LLM via Ollama", t_llm)]:
    check(name, fn)

print()
for good, name, detail in results:
    print(f"  {'PASS' if good else 'FAIL'} {name}: {detail}")
sys.exit(0 if all(g for g, _, _ in results) else 1)
PYEOF
rc=$?
[ $rc -eq 0 ] && ok "all subsystems exercised offline" || bad "subsystem exercise failed (rc=$rc)"

head_ "Result"
printf '  %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

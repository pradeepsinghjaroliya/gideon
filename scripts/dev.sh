#!/usr/bin/env bash
#
# Run Gideon locally, in the foreground, from a terminal.
#
# This is the "developing on it" entry point, the direct equivalent of
# docs/RUNBOOK.md's "If you'd rather test without the service running": it makes
# sure the venv/dependencies/Ollama are ready, gets the systemd copy out of
# the way so two instances aren't fighting over the mic and tray, then runs
# `python -m orchestrator.main` attached to this terminal so exceptions land
# where you can read them. Ctrl+C stops it.
#
#   scripts/dev.sh              # preflight, then run
#   scripts/dev.sh --setup      # (re)install deps first, then run
#   scripts/dev.sh --tests      # run the unit tests first, then run
#   scripts/dev.sh --no-ollama  # don't start `ollama serve` if it's down
#   scripts/dev.sh --check      # preflight only, don't start anything

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
CONFIG="$REPO_ROOT/config/config.yaml"

DO_SETUP=0
DO_TESTS=0
START_OLLAMA=1
CHECK_ONLY=0

# Colours only when stdout is a terminal, so redirected output stays clean.
if [ -t 1 ]; then
    C_INFO=$'\033[36m'; C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
else
    C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""; C_OFF=""
fi
info() { printf '%s==>%s %s\n' "$C_INFO" "$C_OFF" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%swarn%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die()  { printf '%s fail%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

# The header comment above is the help text - print it, minus the leading '#'.
usage() { awk 'NR>2 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --setup|--install) DO_SETUP=1 ;;
        --tests|--test)    DO_TESTS=1 ;;
        --no-ollama)       START_OLLAMA=0 ;;
        --check)           CHECK_ONLY=1 ;;
        -h|--help)         usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

cd "$REPO_ROOT"

# --- venv + dependencies ---------------------------------------------------

install_deps() {
    info "installing the root editable package"
    "$PIP" install -e ".[dev]"

    # Per the repo convention, extra/ML deps live in each module's own
    # requirements.txt rather than pyproject.toml. 01-audio-io's pins the
    # PyTorch CPU wheel index; the rest are plain PyPI.
    for module in 01-audio-io 03-stt 04-llm-client 05-tts 06-text-input 07-orchestrator 08-transcript-ui; do
        info "installing modules/$module/requirements.txt"
        "$PIP" install -r "modules/$module/requirements.txt"
    done

    # 02-wake-word is the exception: openwakeword hard-declares
    # tflite-runtime, which has no wheel for Python 3.12, so a plain
    # `-r requirements.txt` fails outright. detector.py forces the ONNX
    # backend, so that dependency is genuinely unused - install without deps
    # and supply the real ones (see that module's requirements.txt).
    info "installing openwakeword (--no-deps, see modules/02-wake-word/requirements.txt)"
    "$PIP" install --no-deps "openwakeword>=0.6.0"
    "$PIP" install "onnxruntime>=1.17" "scipy>=1.3,<2" "scikit-learn>=1,<2" "tqdm>=4.0,<5" "requests>=2.0,<3"
}

if [ ! -x "$PY" ]; then
    info "no .venv yet - creating one"
    python3 -m venv "$VENV"
    DO_SETUP=1
fi

if [ "$DO_SETUP" -eq 1 ]; then
    install_deps
else
    # Cheap guard against a half-built venv: if the entry point can't be
    # imported there is no point reaching the mic.
    "$PY" -c "import orchestrator.main" >/dev/null 2>&1 \
        || die "the venv can't import orchestrator - run: scripts/dev.sh --setup"
fi

# openWakeWord's feature extractors aren't bundled in the wheel; they're
# fetched once. The custom hey_gideon model is committed, but it still needs
# these two to run.
OWW_MODELS="$("$PY" -c "import openwakeword,os;print(os.path.join(os.path.dirname(openwakeword.__file__),'resources','models'))")"
if [ ! -f "$OWW_MODELS/melspectrogram.onnx" ] || [ ! -f "$OWW_MODELS/embedding_model.onnx" ]; then
    info "downloading openWakeWord's feature-extractor models (one time)"
    "$PY" -c "from openwakeword import utils; utils.download_models()"
fi

# --- preflight -------------------------------------------------------------

[ -f "$CONFIG" ] || die "missing $CONFIG"
ok "config: $CONFIG"

WAKE_MODEL="$("$PY" -c 'from shared.config import load_config; print(load_config().wake_word.model)')"
if [ -n "$WAKE_MODEL" ] && [ ! -f "$REPO_ROOT/$WAKE_MODEL" ] && [ ! -f "$WAKE_MODEL" ]; then
    warn "wake-word model not found: $WAKE_MODEL (wake word will fail; tray 'Ask...' still works)"
else
    ok "wake-word model: $WAKE_MODEL"
fi

# Imports that depend on system libraries, not just pip packages. Both are
# fatal - the orchestrator needs the mic and the tray.
"$PY" -c "import sounddevice" >/dev/null 2>&1 \
    || die "can't import sounddevice - install PortAudio: sudo apt install libportaudio2"
"$PY" -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" >/dev/null 2>&1 \
    || die "can't import GTK 3 - install: sudo apt install python3-gi gir1.2-gtk-3.0 libgirepository-2.0-dev"
ok "audio + GTK bindings import"

# Known local footgun: nwg-look can write a self-referential cursor theme
# (Inherits=default) into ~/.icons/default. libwayland-cursor then recurses
# until the stack blows and *every* GTK3 Wayland app segfaults at display
# open - here it surfaces as a misleading pystray/Gdk traceback.
CURSOR_THEME="$HOME/.icons/default/index.theme"
if [ -f "$CURSOR_THEME" ] && grep -qiE '^\s*Inherits\s*=\s*default\s*$' "$CURSOR_THEME"; then
    die "$CURSOR_THEME has a self-inheriting cursor theme (Inherits=default).
     Every GTK3 Wayland app segfaults on this - set Inherits= to a real theme
     (e.g. Bibata-Modern-Ice) or delete ~/.icons/default/, then re-run."
fi

# --- get the background service out of the way -----------------------------

if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet gideon.service 2>/dev/null; then
    info "stopping the gideon.service copy (it holds the mic and tray)"
    systemctl --user stop gideon.service
    STOPPED_SERVICE=1
else
    STOPPED_SERVICE=0
fi

# A hand-run instance from an earlier session would also hold the overlay
# socket; the overlay itself refuses to double-start, so just say so.
if command -v pgrep >/dev/null 2>&1 && pgrep -f "[p]ython.* -m orchestrator\.main" >/dev/null 2>&1; then
    warn "another 'orchestrator.main' is already running - two instances will fight over the mic and the overlay socket"
fi

# --- Ollama ----------------------------------------------------------------

LLM_INFO="$("$PY" -c 'from shared.config import load_config; c = load_config().llm; print(c.base_url); print(c.model)')"
LLM_URL="$(printf '%s\n' "$LLM_INFO" | sed -n 1p)"
LLM_MODEL="$(printf '%s\n' "$LLM_INFO" | sed -n 2p)"

llm_up() {
    "$PY" -c 'import sys, urllib.request
try: urllib.request.urlopen(sys.argv[1], timeout=1)
except Exception: sys.exit(1)' "$LLM_URL" >/dev/null 2>&1
}

if llm_up; then
    ok "Ollama already serving at $LLM_URL"
elif [ "$START_OLLAMA" -eq 0 ]; then
    warn "Ollama is not running at $LLM_URL and --no-ollama was passed - replies will fail until it's up"
elif command -v ollama >/dev/null 2>&1; then
    info "starting 'ollama serve' in the background"
    # Detached and log-to-file, so Ctrl+C on Gideon doesn't take the LLM with
    # it - matches how orchestrator/ollama_control.py launches it.
    nohup ollama serve >"${TMPDIR:-/tmp}/gideon-ollama.log" 2>&1 &
    for _ in $(seq 1 20); do
        llm_up && break
        sleep 0.5
    done
    if llm_up; then
        ok "Ollama up at $LLM_URL (log: ${TMPDIR:-/tmp}/gideon-ollama.log)"
    else
        warn "'ollama serve' didn't come up within 10s - see ${TMPDIR:-/tmp}/gideon-ollama.log"
    fi
else
    warn "'ollama' is not on PATH - install it, or the LLM stage will fail (https://ollama.com)"
fi

if llm_up && command -v ollama >/dev/null 2>&1; then
    if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$LLM_MODEL"; then
        ok "model '$LLM_MODEL' is pulled"
    else
        warn "model '$LLM_MODEL' (config/config.yaml llm.model) isn't pulled - run: ollama pull $LLM_MODEL"
    fi
fi

# --- tests -----------------------------------------------------------------

if [ "$DO_TESTS" -eq 1 ]; then
    info "running the unit tests"
    "$PY" -m pytest modules/ -q
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
    info "--check: everything above is the preflight; not starting Gideon"
    exit 0
fi

# --- run -------------------------------------------------------------------

if [ "$STOPPED_SERVICE" -eq 1 ]; then
    cat <<'EOF'

Note: gideon.service was stopped so this run could take the mic. Start it
again when you're done:  systemctl --user start gideon.service
EOF
fi

info "starting Gideon - Ctrl+C to stop"
echo
exec "$PY" -m orchestrator.main

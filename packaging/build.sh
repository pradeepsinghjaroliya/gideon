#!/usr/bin/env bash
#
# Build the fully-offline Gideon .deb.
#
#   ./packaging/build.sh              normal build (fetches what's missing)
#   ./packaging/build.sh --offline    fail rather than fetch; rebuild from
#                                     what's already in packaging/
#   ./packaging/build.sh --lint       also run lintian, if installed
#
# Every fetch stage is skipped when its output already exists, so a second
# run never touches the network.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$REPO/packaging"
BUILD="$REPO/build"
DIST="$REPO/dist"

VERSION="0.1.0-2"
ARCH="amd64"
PREFIX="/opt/gideon"

OFFLINE=0
LINT=0
for arg in "$@"; do
    case "$arg" in
        --offline) OFFLINE=1 ;;
        --lint)    LINT=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
step() { printf '    %s\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

need_fetch() {
    [ "$OFFLINE" -eq 1 ] && die "$1 is missing and --offline was given"
    return 0
}

# --------------------------------------------------------------- 0. checks
say "Checking build host"
[ "$(dpkg --print-architecture)" = "$ARCH" ] || die "build host is not $ARCH"
command -v dpkg-deb >/dev/null || die "dpkg-deb not found (install dpkg-dev)"
command -v fakeroot >/dev/null || die "fakeroot not found"
step "arch $ARCH, dpkg-deb and fakeroot present"

# --------------------------------------------------------- 1. interpreter
# A relocatable python-build-standalone CPython: RPATH is $ORIGIN/../lib, and
# it bundles tkinter/Tcl/Tk and OpenSSL, so the package needs neither
# python3-tk nor the host's python3.
PYSRC="$PKG/cpython"
if [ ! -x "$PYSRC/bin/python3" ]; then
    UVPY="$(readlink -f "$HOME/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu" 2>/dev/null || true)"
    [ -n "$UVPY" ] && [ -x "$UVPY/bin/python3" ] || {
        need_fetch "packaging/cpython"
        die "no bundled interpreter. Install one with: uv python install 3.12"
    }
    say "Staging bundled interpreter from $UVPY"
    rm -rf "$PYSRC"; mkdir -p "$PYSRC"
    cp -a "$UVPY/." "$PYSRC/"
    step "$("$PYSRC/bin/python3" -V) staged"
else
    say "Bundled interpreter already staged"
    step "$("$PYSRC/bin/python3" -V)"
fi

# --------------------------------------------------------- 2. wheelhouse
if [ ! -d "$PKG/wheelhouse" ] || [ -z "$(ls -A "$PKG/wheelhouse" 2>/dev/null)" ]; then
    need_fetch "packaging/wheelhouse"
    say "Downloading wheelhouse"
    mkdir -p "$PKG/wheelhouse"
    # --no-deps: constraints.txt is already the complete transitive closure,
    # and it stops pip resolving openwakeword's tflite-runtime (no cp312 wheel).
    # --extra-index-url: torch/torchaudio +cpu exist only on PyTorch's index.
    "$PYSRC/bin/python3" -m pip download \
        -r "$PKG/constraints.txt" --no-deps --only-binary=:all: \
        -d "$PKG/wheelhouse" \
        --extra-index-url https://download.pytorch.org/whl/cpu
else
    say "Wheelhouse present"
fi
step "$(ls "$PKG/wheelhouse"/*.whl | wc -l) wheels, $(du -sh "$PKG/wheelhouse" | cut -f1)"

# ------------------------------------------------------- 3. vendored debs
VENDOR_PKGS="libportaudio2 libjack-jackd2-0 libdb5.3t64
             libayatana-appindicator3-1 libayatana-indicator3-7
             libayatana-ido3-0.4-0 libdbusmenu-glib4 libdbusmenu-gtk3-4
             gir1.2-ayatanaappindicator3-0.1"
if [ ! -d "$PKG/vendor/debs" ] || [ -z "$(ls -A "$PKG/vendor/debs" 2>/dev/null)" ]; then
    need_fetch "packaging/vendor/debs"
    say "Downloading vendored system libraries"
    mkdir -p "$PKG/vendor/debs"
    ( cd "$PKG/vendor/debs" && apt-get download $VENDOR_PKGS )
else
    say "Vendored .debs present"
fi
step "$(ls "$PKG/vendor/debs"/*.deb | wc -l) .debs"

# -------------------------------------------------------------- 4. assets
[ -f "$PKG/assets/whisper/small/model.bin" ] || die "packaging/assets is not staged (see BUILD.md)"
say "Model assets present"
step "$(du -sh "$PKG/assets" | cut -f1)"

# ------------------------------------------------------------ 5. assemble
say "Assembling package tree"
rm -rf "$BUILD"
ROOT="$BUILD/root"
G="$ROOT$PREFIX"
mkdir -p "$G" "$ROOT/etc/gideon" "$ROOT/etc/default" \
         "$ROOT/usr/bin" "$ROOT/usr/lib/systemd/user" \
         "$ROOT/usr/share/doc/gideon" "$ROOT/DEBIAN"

step "interpreter"
cp -a "$PYSRC" "$G/python"
# uv marks its interpreters PEP 668 externally-managed. This copy is ours and
# is meant to be populated, so drop the marker rather than fighting it with
# --break-system-packages.
rm -f "$G/python/lib/python3.12/EXTERNALLY-MANAGED"

SP="$G/python/lib/python3.12/site-packages"

step "python dependencies (offline, from wheelhouse)"
# --no-deps for the same reason as the download step: constraints.txt is
# already the complete transitive closure, and openwakeword declares
# tflite-runtime, which has no cp312 wheel and which detector.py never uses
# (it forces inference_framework="onnx"). Without --no-deps pip aborts here.
# Completeness is verified afterwards by importing every module for real.
"$G/python/bin/python3" -m pip install \
    --no-index --find-links "$PKG/wheelhouse" \
    -r "$PKG/constraints.txt" --no-deps \
    --no-warn-script-location --disable-pip-version-check -q

step "pruning build-only bits"
"$G/python/bin/python3" -m pip uninstall -y pip -q >/dev/null 2>&1 || true
rm -rf "$SP"/pip "$SP"/pip-*.dist-info "$SP"/wheel "$SP"/wheel-*.dist-info
find "$G/python" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$G/python/lib/python3.12/test" "$G/python/lib/python3.12/idlelib" || true

step "openWakeWord feature extractors into package resources"
# openwakeword.utils looks for these inside its own resources/models dir and
# downloads them on first use if absent - which an offline install cannot do.
install -Dm644 "$PKG/assets/openwakeword/melspectrogram.onnx" \
               "$SP/openwakeword/resources/models/melspectrogram.onnx"
install -Dm644 "$PKG/assets/openwakeword/embedding_model.onnx" \
               "$SP/openwakeword/resources/models/embedding_model.onnx"

step "find_library shim"
install -Dm644 "$PKG/gideon_native.py"      "$SP/gideon_native.py"
install -Dm644 "$PKG/zz_gideon_native.pth"  "$SP/zz_gideon_native.pth"

step "system gi/cairo bridge"
# PyGObject and pycairo are not pip-installable here without a full GTK build
# toolchain, and the distro copies are ABI-compatible with this cp312 runtime.
# Expose exactly those two - not all of dist-packages - via a .pth. Because
# .pth entries append to the END of sys.path, bundled packages always win.
mkdir -p "$G/syslink"
for m in gi cairo; do ln -s "/usr/lib/python3/dist-packages/$m" "$G/syslink/$m"; done
echo "$PREFIX/syslink" > "$SP/zz_gideon_syslink.pth"

step "application source"
mkdir -p "$G/app"
for m in 00-shared:shared 01-audio-io:audio_io 02-wake-word:wake_word 03-stt:stt \
         04-llm-client:llm_client 05-tts:tts 06-text-input:text_input \
         07-orchestrator:orchestrator; do
    d="${m%%:*}"; p="${m##*:}"
    cp -a "$REPO/modules/$d/src/$p" "$G/app/$p"
done
find "$G/app" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

step "models"
mkdir -p "$G/models"
cp -a "$PKG/assets/wake_word" "$PKG/assets/whisper" "$PKG/assets/piper" "$G/models/"

step "vendored native libraries + typelib"
mkdir -p "$G/native" "$G/typelib" "$BUILD/vendor-x"
for d in "$PKG/vendor/debs"/*.deb; do dpkg-deb -x "$d" "$BUILD/vendor-x"; done
find "$BUILD/vendor-x" \( -name '*.so' -o -name '*.so.*' \) \( -type f -o -type l \) \
     -exec cp -a {} "$G/native/" \;
find "$BUILD/vendor-x" -name '*.typelib' -exec cp -a {} "$G/typelib/" \;
rm -rf "$BUILD/vendor-x"

step "wrapper, unit, config"
install -Dm755 "$PKG/gideon.wrapper"  "$ROOT/usr/bin/gideon"
install -Dm644 "$PKG/gideon.service"  "$ROOT/usr/lib/systemd/user/gideon.service"
install -Dm644 "$PKG/config.yaml"     "$ROOT/etc/gideon/config.yaml"
install -Dm644 "$PKG/default-gideon"  "$ROOT/etc/default/gideon"

step "documentation"
install -Dm644 "$PKG/debian/copyright"     "$ROOT/usr/share/doc/gideon/copyright"
install -Dm644 "$PKG/debian/README.Debian" "$ROOT/usr/share/doc/gideon/README.Debian"
for f in INSTALL.md BUILD.md; do
    [ -f "$REPO/$f" ] && install -Dm644 "$REPO/$f" "$ROOT/usr/share/doc/gideon/$f"
done
gzip -9nc "$PKG/debian/changelog" > "$ROOT/usr/share/doc/gideon/changelog.Debian.gz"
chmod 644 "$ROOT/usr/share/doc/gideon/changelog.Debian.gz"

step "byte-compiling"
# Done here because /opt is root-owned and the service runs as the desktop
# user, so runtime .pyc writes would fail (PYTHONDONTWRITEBYTECODE is set).
"$G/python/bin/python3" -m compileall -q -f "$G/app" >/dev/null 2>&1 || true
"$G/python/bin/python3" -m compileall -q "$SP" >/dev/null 2>&1 || true

# ------------------------------------------------------------- 6. control
say "Writing control metadata"
INSTALLED_SIZE=$(du -sk --apparent-size "$ROOT" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@INSTALLED_SIZE@/$INSTALLED_SIZE/" \
    "$PKG/debian/control.in" > "$ROOT/DEBIAN/control"
cp "$PKG/debian/conffiles" "$ROOT/DEBIAN/conffiles"
for s in preinst postinst prerm postrm; do
    install -Dm755 "$PKG/debian/$s" "$ROOT/DEBIAN/$s"
done
step "installed size ${INSTALLED_SIZE} KiB"

# --------------------------------------------------------------- 7. build
say "Building .deb"
mkdir -p "$DIST"
DEB="$DIST/gideon_${VERSION}_${ARCH}.deb"
rm -f "$DEB"
fakeroot dpkg-deb --root-owner-group -Zxz -z6 --build "$ROOT" "$DEB"

( cd "$DIST" && sha256sum "$(basename "$DEB")" > "$(basename "$DEB").sha256" )

say "Done"
step "$DEB"
step "$(du -h "$DEB" | cut -f1)  sha256 $(cut -c1-16 < "$DEB.sha256")..."

if [ "$LINT" -eq 1 ]; then
    if command -v lintian >/dev/null; then
        say "lintian"
        lintian --no-tag-display-limit "$DEB" || true
    else
        step "lintian not installed - skipping"
    fi
fi

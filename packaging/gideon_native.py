"""Make ctypes.util.find_library() see Gideon's vendored native libraries.

Why this exists: `sounddevice` locates PortAudio with
`ctypes.util.find_library('portaudio')` and raises
`OSError('PortAudio library not found')` if that returns None. On Linux
find_library() consults ld.so.cache only - it ignores LD_LIBRARY_PATH
(measured, not assumed). So the wrapper's LD_LIBRARY_PATH is enough for
everything resolved by dlopen()/GObject-Introspection, but not for this
one call.

The alternative would be registering /opt/gideon/native in
/etc/ld.so.conf.d, but that makes every vendored .so visible to every
process on the machine and able to shadow the distro's own copies. This
shim is scoped to Gideon's bundled interpreter instead: nothing outside
/opt/gideon is affected.

Loaded automatically via zz_gideon_native.pth in this same directory.
"""

import ctypes.util
import os

NATIVE_DIR = os.environ.get("GIDEON_NATIVE_DIR", "/opt/gideon/native")

_original_find_library = ctypes.util.find_library


def _find_library(name):
    """Prefer a vendored lib<name>.so* in NATIVE_DIR, else defer."""
    try:
        entries = sorted(os.listdir(NATIVE_DIR))
    except OSError:
        return _original_find_library(name)

    prefix = "lib%s.so" % name
    for entry in entries:
        if entry == prefix or entry.startswith(prefix + "."):
            return os.path.join(NATIVE_DIR, entry)
    return _original_find_library(name)


ctypes.util.find_library = _find_library

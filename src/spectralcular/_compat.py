"""Cross-platform shims that must run before importing weasyprint.

WeasyPrint links to system Pango/Cairo via dlopen. On Apple Silicon macOS
those Homebrew libs live under /opt/homebrew/lib, which dyld does not search
by default for un-codesigned bins. Setting DYLD_FALLBACK_LIBRARY_PATH before
the dlopen call inside ffi.py is enough — dyld re-reads the env on each call.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def patch_macos_dyld() -> None:
    if sys.platform != "darwin":
        return
    brew_lib = Path("/opt/homebrew/lib")
    if not brew_lib.exists():
        return
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    parts = existing.split(":") if existing else []
    if str(brew_lib) not in parts:
        parts.insert(0, str(brew_lib))
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)


patch_macos_dyld()

"""The library path a program started from VPinFE should see.

A frozen Linux build points `LD_LIBRARY_PATH` at its own bundled libraries. A program
VPinFE starts - VPX, a local Chromium, a tool - would load those ahead of its own, so it
gets back the path VPinFE itself was started with, or none.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Mapping

LIBRARY_PATH = "LD_LIBRARY_PATH"
SAVED_LIBRARY_PATH = "LD_LIBRARY_PATH_ORIG"


def for_child(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of `env` (this process's, by default) for a program VPinFE starts. Anything
    other than a frozen Linux build gets it unchanged, and the saved path stays in the copy."""
    child = dict(os.environ if env is None else env)
    if platform.system() == "Linux" and getattr(sys, "frozen", False):
        original = child.get(SAVED_LIBRARY_PATH)
        if original is not None:
            child[LIBRARY_PATH] = original
        else:
            child.pop(LIBRARY_PATH, None)
    return child

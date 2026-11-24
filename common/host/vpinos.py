"""Whether this device runs VPinOS, the appliance Linux that ships VPinFE.

What is VPinOS's alone is a core feature gated on this answer, not an extension.
"""

from __future__ import annotations

import functools
import platform
import sys

# How VPinOS names itself in os-release.
ID = "vpinos"


@functools.cache
def detected() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        return platform.freedesktop_os_release().get("ID", "") == ID
    except OSError:
        return False

"""The desktop session, read from an allow-list of environment variables.

Never the whole environment: a variable is read because it is named here, and a value that
does not look like a session setting is reported as set rather than copied.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping

VARIABLES = ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION",
             "WAYLAND_DISPLAY", "DISPLAY", "GDK_BACKEND", "QT_QPA_PLATFORM",
             "SDL_VIDEODRIVER", "GAMESCOPE_WAYLAND_DISPLAY")
# Their values are paths or identifiers, so only whether they are set is reported.
PRESENCE_ONLY = ("SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE")

_PLAIN = re.compile(r"[\w:;,\- ]{1,60}")
# A display on this device is a number; one that names a host is not copied.
_LOCAL_DISPLAY = re.compile(r":\d{1,3}(?:\.\d{1,2})?")


def read(env: Mapping[str, str] | None = None,
         platform: str = sys.platform) -> list[tuple[str, str]]:
    """(name, value) for each session variable, in a fixed order. Only Linux and the BSDs
    have a session described this way; elsewhere there is nothing to report."""
    if not platform.startswith(("linux", "freebsd", "openbsd")):
        return []
    held = os.environ if env is None else env
    found = []
    for name in VARIABLES:
        value = str(held.get(name, "")).strip()
        plain = (_LOCAL_DISPLAY if name == "DISPLAY" else _PLAIN).fullmatch(value)
        found.append((name, (value if plain else "set") if value else "not set"))
    for name in PRESENCE_ONLY:
        found.append((name, "set" if str(held.get(name, "")).strip() else "not set"))
    return found

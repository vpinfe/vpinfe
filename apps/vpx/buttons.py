"""The buttons a Visual Pinball cabinet is wired for, as the keys they send."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from . import keys
from .config import settings_file

logger = logging.getLogger("vpinfe.apps.vpx.buttons")

# Role -> the `Mapping.*` name VPX files it under, and the scancode it sends where the
# settings file maps nothing to it (`InputManager.cpp`).
_BUTTONS: dict[str, tuple[str, int]] = {
    "left_flipper": ("LeftFlipper", 225),
    "right_flipper": ("RightFlipper", 229),
    "left_magna": ("LeftMagna", 224),
    "right_magna": ("RightMagna", 228),
    "start": ("Start", 30),
    "fire": ("Lockbar", 226),
    "extra_ball": ("ExtraBall", 5),
    "tilt": ("Tilt", 23),
    "exit": ("ExitGame", 41),
}


# What has been said already, so asking again for the same file reads the same quietly.
_said: set[tuple[str, str]] = set()


class VPXButtons:
    def keys(self, settings: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
        """`settings` is the launcher's. The file's own mapping wins, and a button it maps
        nothing to sends VPX's own key. A file that cannot be read maps nothing."""
        file = settings_file(settings)
        mapped: dict[str, str] = {}
        written: dict[str, str] = {}
        if file is not None:
            try:
                mapped = keys.mappings(str(file))
                written = keys.entries(str(file))
            except (OSError, UnicodeError) as error:
                logger.warning("Could not read %s (%s), so the cabinet's buttons are "
                               "the keys VPX sends on its own", file, error)
        found: dict[str, str] = {}
        for role, (name, scancode) in _BUTTONS.items():
            found[role] = mapped.get(name) or keys.code_of(scancode)
            if name in written and written[name] and name not in mapped:
                self._say_fallback(name, written[name], found[role])
        return {role: (code,) for role, code in found.items() if code}

    @staticmethod
    def _say_fallback(name: str, written: str, used: str) -> None:
        if (name, written) in _said:
            return
        _said.add((name, written))
        logger.info("VPinballX.ini has Mapping.%s = %s, which no keyboard key answers to, "
                    "so the cabinet's %s is VPX's own %s", name, written, name, used)

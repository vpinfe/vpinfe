"""Keeping the Mac's Dock and menu bar off the screens a table plays on.

A Visual Pinball window is only in a full-screen space, which hides both, in its Borderless
Fullscreen mode; VPX ships every window windowed. The mode is given once, when a program is
first set up. It is never written at launch, so a later choice in the settings file stays.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from . import ini as vini
from .config import read_file, settings_file

BORDERLESS_FULLSCREEN = "1"
FLOATING = "1"
PLAYFIELD = ("Player", "Playfield")
# The windows that are drawn only when their Output is Floating.
OTHERS = (("Backglass", "Backglass"), ("ScoreView", "ScoreView"), ("Topper", "Topper"))


def _unplaced(app: vini.Ini, section: str, window: str) -> bool:
    """A window with a position or a size has been placed by hand, which only means
    something windowed."""
    return all(app.value(f"{section}.{window}{part}") is None
               for part in ("WndX", "WndY", "Width", "Height"))


def unset_windows(app: vini.Ini) -> dict[str, str]:
    """The Display Mode to write for each window nobody has said anything about.

    The playfield is always on screen. Another window is only taken to a screen of its own
    once it is floating and has been given the display it goes to: floating with no
    display is a window on the playfield's screen, and full screen would cover the playfield.
    """
    wanted = [PLAYFIELD]
    for section, window in OTHERS:
        if (app.value(f"{section}.{window}Output") == FLOATING
                and app.value(f"{section}.{window}Display") is not None):
            wanted.append((section, window))
    return {f"{section}.{window}FullScreen": BORDERLESS_FULLSCREEN
            for section, window in wanted
            if app.value(f"{section}.{window}FullScreen") is None
            and _unplaced(app, section, window)}


def first_setup_windows(settings: Mapping[str, Any], *,
                        system: str = sys.platform) -> dict[str, str]:
    """What to write into a program's settings file when it is first set up: nothing off
    a Mac, and on one the Display Mode of each window nobody has chosen a mode for."""
    if system != "darwin":
        return {}
    return unset_windows(read_file(settings_file(settings)))

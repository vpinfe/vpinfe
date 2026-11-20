"""Keeping the Mac's Dock and menu bar off the screens a table plays on.

A Visual Pinball window is only in a full-screen space, which hides both, in its Borderless
Fullscreen mode; VPX ships every window windowed.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any

from common.apps.contract import SCOPE_LAUNCHER

from . import ini as vini
from .config import VPXConfig, preferences_folder, read_file, settings_file

logger = logging.getLogger("vpinfe.apps.vpx.presentation")

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


class VPXPrepare:
    """On a Mac, a Display Mode nobody set is Borderless Fullscreen. Nothing else changes."""

    def before_launch(self, settings: Mapping[str, Any], *, system: str = sys.platform) -> None:
        """Writes into the settings file VPX is about to read, the way a save from the
        settings page does, backup included. Where there is no file yet it makes the bare
        one a program of the newest version looks for, which VPX fills in when it starts."""
        if system != "darwin":
            return
        held = settings_file(settings)
        values = unset_windows(read_file(held))
        if not values:
            return
        if held is None:
            folder = preferences_folder(str(settings.get("bin_path") or ""))
            if folder is None:
                return
            held = VPXConfig().create(folder.name, values, settings)
        else:
            VPXConfig().write(SCOPE_LAUNCHER, "", values, settings)
        logger.info("Set %s to Borderless Fullscreen in %s", ", ".join(sorted(values)), held)

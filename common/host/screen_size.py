"""How big a screen is, in millimeters, as the screen reports it.

A report is only worth offering when it is plausible: a virtual machine or a screen with no
EDID answers with a placeholder size, and a number that looks measured but is not would be
written over a person's own.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

from common.host import display_service

logger = logging.getLogger("vpinfe.common.host.screen_size")

# The long and short sides of anything a playfield could be drawn on, from a small monitor
# to a large television. Outside it the report is a placeholder.
_SIDE_MM = (100.0, 3000.0)
# The widest and narrowest shapes of a real panel: 4:3 up to ultrawide, long side over short.
_SHAPE = (1.2, 2.6)


def plausible(long_mm: float, short_mm: float) -> bool:
    if not long_mm or not short_mm or short_mm > long_mm:
        return False
    return (_SIDE_MM[0] <= short_mm and long_mm <= _SIDE_MM[1]
            and _SHAPE[0] <= long_mm / short_mm <= _SHAPE[1])


class _Size(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


def _mac_mm(index: int) -> tuple[float, float] | None:
    import AppKit

    screens = AppKit.NSScreen.screens()
    if not 0 <= index < len(screens):
        return None
    number = screens[index].deviceDescription()["NSScreenNumber"]
    graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    graphics.CGDisplayScreenSize.restype = _Size
    graphics.CGDisplayScreenSize.argtypes = [ctypes.c_uint32]
    size = graphics.CGDisplayScreenSize(int(number))
    return size.width, size.height


def _other_mm(index: int) -> tuple[float, float] | None:
    monitors: list[Any] = display_service.get_display_monitors()
    if not 0 <= index < len(monitors):
        return None
    return (float(getattr(monitors[index], "width_mm", 0) or 0),
            float(getattr(monitors[index], "height_mm", 0) or 0))


def reported_mm(index: int, system: str = sys.platform) -> tuple[float, float] | None:
    """The long and short sides, in millimeters, of the screen `index` counts from zero -
    or None when the screen is not there, will not say, or says something implausible.
    Blocking."""
    try:
        said = _mac_mm(index) if system == "darwin" else _other_mm(index)
    except Exception:
        logger.warning("Could not read the size of screen %s", index, exc_info=True)
        return None
    if said is None:
        return None
    long_mm, short_mm = max(said), min(said)
    return (long_mm, short_mm) if plausible(long_mm, short_mm) else None

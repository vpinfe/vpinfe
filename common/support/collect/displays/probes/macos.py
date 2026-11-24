"""macOS: each `NSScreen` with its Quartz rotation, and whether displays share Spaces.

`system_profiler` is never run: its display data carries serial numbers. A screen's own
name is its model string, which is all that prints.
"""

from __future__ import annotations

import platform
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from common.capture import geometry

from ..model import NOT_HERE, OK, TIMED_OUT, Reading, ScreenFact
from ..run import Run

SOURCE = "Quartz"

SPACES = ("defaults", "read", "com.apple.spaces", "spans-displays")


@dataclass(frozen=True)
class Display:
    """One `NSScreen`, in the points and top-left origin Chromium is given."""

    id: int
    name: str
    x: int
    y: int
    width: int
    height: int
    scale: float
    hz: int
    rotation: int
    main: bool
    asleep: bool
    # The id of the display this one shows a copy of, 0 where it shows its own picture.
    mirror_of: int


Reader = Callable[[], list[Display]]
Release = Callable[[], tuple[str, tuple[str, str, str], str]]


def screens(displays: list[Display]) -> list[ScreenFact]:
    names = {one.id: one.name for one in displays}
    return [ScreenFact(
        model=one.name or None, x=one.x, y=one.y, width=one.width, height=one.height,
        scale=one.scale, refresh_hz=float(one.hz) if one.hz else None,
        turn=geometry.degrees_turn(one.rotation), said=f"{one.rotation}°",
        primary=one.main, enabled=True, asleep=one.asleep or None,
        mirrors=(names.get(one.mirror_of) or "another display") if one.mirror_of else None,
        source=SOURCE) for one in displays]


def spaces(text: str, code: int) -> str:
    """Whether each display has its own Spaces, from `spans-displays` as `defaults` prints
    it. The key is not there until someone changes it, and the system then keeps them
    separate."""
    said = text.strip()
    if said == "1":
        return "Off, one Space spans all displays"
    if said == "0" or code == 1:
        return "On"
    return ""


def probe(env: Mapping[str, str], run: Run, read: Reader | None = None,
          release: Release = platform.mac_ver) -> Reading:
    try:
        displays = (read or _read)()
    except Exception:
        return Reading.failed(SOURCE, "macOS would not say")
    facts = [("Desktop", f"macOS {release()[0]} (Quartz), {platform.machine()}")]
    asked = run(SPACES)
    if asked.state not in (NOT_HERE, TIMED_OUT):
        said = spaces(asked.text, asked.code)
        if said:
            facts.append(("Displays have separate Spaces", said))
    return Reading(SOURCE, OK, screens=tuple(screens(displays)), facts=tuple(facts))


def _refresh(screen: object) -> float:
    """0 where the system is older than the call (before macOS 12), which reads as unknown."""
    ask = getattr(screen, "maximumFramesPerSecond", None)
    try:
        return float(ask()) if callable(ask) else 0.0
    except Exception:
        return 0.0


def _read() -> list[Display]:
    """Every screen, in `NSScreen.screens()` order, which is the order VPinFE numbers them
    by. Raises `ImportError` where `AppKit` is not installed."""
    import AppKit
    import Quartz

    found = list(AppKit.NSScreen.screens())
    if not found:
        return []
    top = found[0].frame().origin.y + found[0].frame().size.height
    displays = []
    for screen in found:
        frame = screen.frame()
        number = int(screen.deviceDescription()["NSScreenNumber"])
        displays.append(Display(
            number, str(screen.localizedName() or ""), int(frame.origin.x),
            int(top - frame.origin.y - frame.size.height), int(frame.size.width),
            int(frame.size.height), float(screen.backingScaleFactor()),
            int(_refresh(screen)), int(Quartz.CGDisplayRotation(number)),
            bool(Quartz.CGDisplayIsMain(number)), bool(Quartz.CGDisplayIsAsleep(number)),
            int(Quartz.CGDisplayMirrorsDisplay(number))))
    return displays

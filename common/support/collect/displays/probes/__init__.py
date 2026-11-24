"""One probe per source of the desktop's own view of its screens.

A probe is `probe(env, run) -> Reading`. It never raises: a program that is not installed
is `Reading.not_here`, a failure is `Reading.failed`, and a parser that cannot read what
the desktop printed keeps its first lines for the section to show.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from .. import desktop as desk
from ..model import NOT_HERE, TIMED_OUT, Reading
from ..run import Ran, Run

Probe = Callable[[Mapping[str, str], Run], Reading]

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
VERSION = re.compile(r"\d+\.\d+(?:\.\d+)*")


def version_in(text: str) -> str:
    found = VERSION.search(text)
    return found.group(0) if found else ""


def unavailable(program: str, ran: Ran) -> Reading:
    """The reading for a program that did not answer: not installed, too slow, or failed."""
    if ran.state == NOT_HERE:
        return Reading.not_here(program)
    if ran.state == TIMED_OUT:
        return Reading.timed_out(program)
    return Reading.failed(program, f"{program} exited with {ran.code}")


def chosen(here: desk.Desktop) -> list[tuple[str, Probe]]:
    """The probes this session can answer to, by the name each is listed under."""
    from . import distro, gnome, kde, macos, wayland_generic, windows, wlr, x11

    if here.system == desk.WINDOWS:
        return [(windows.SOURCE, windows.probe)]
    if here.system == desk.MACOS:
        return [(macos.SOURCE, macos.probe)]
    found: list[tuple[str, Probe]] = []
    if here.server == desk.X11:
        found.append(("xrandr", x11.probe))
    if here.shell == desk.SWAY:
        found.append(("sway", wlr.sway))
    elif here.shell == desk.HYPRLAND:
        found.append(("Hyprland", wlr.hyprland))
    elif here.shell == desk.KDE:
        found.append(("kscreen-doctor", kde.probe))
    elif here.shell == desk.GNOME and here.server == desk.WAYLAND:
        found.append(("Mutter", gnome.probe))
    elif here.server == desk.WAYLAND and here.shell != desk.GAMESCOPE:
        found.append(("wlr-randr", wayland_generic.probe))
    if here.distro in distro.APPLIANCES or here.game_mode:
        found.append(("os-release", distro.probe))
    return found

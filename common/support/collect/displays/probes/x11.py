"""X11: `xrandr --query` for the outputs, `xprop` for the window manager.

`--props` and `--verbose` are never asked for: they print the monitor's EDID.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace

from common.capture import geometry

from ..model import OK, Reading, ScreenFact
from ..run import Run
from . import unavailable

SOURCE = "xrandr"

_SCREEN = re.compile(r"^Screen \d+:", re.MULTILINE)
_OUTPUT = re.compile(
    r"^(?P<name>\S+) (?P<state>connected|disconnected)(?P<primary> primary)?"
    r"(?: (?P<w>\d+)x(?P<h>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+))?"
    r"(?: (?P<turn>normal|left|inverted|right))?(?P<axes>(?: X and Y axis| X axis| Y axis)*)"
    r" \(.*?\)(?: (?P<mmw>\d+)mm x (?P<mmh>\d+)mm)?\s*$")
_REFRESH = re.compile(r"(\d+(?:\.\d+)?)\*")
_CHECK = re.compile(r"window id # (0x[0-9a-fA-F]+)")
_NAME = re.compile(r'"(.*)"')


def parse_outputs(text: str) -> tuple[list[ScreenFact], int] | None:
    """The connected outputs, and how many are disconnected. None where the text is not
    `xrandr --query`'s."""
    if not _SCREEN.search(text):
        return None
    found: list[ScreenFact] = []
    disconnected = 0
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):
            refresh = _REFRESH.search(line)
            if refresh and found and found[-1].refresh_hz is None:
                found[-1] = replace(found[-1], refresh_hz=float(refresh.group(1)))
            continue
        said = _OUTPUT.match(line)
        if said is None:
            continue
        if said["state"] == "disconnected":
            disconnected += 1
            continue
        found.append(_screen(said))
    return found, disconnected


def _screen(said: re.Match[str]) -> ScreenFact:
    on = said["w"] is not None
    word = said["turn"] or "normal"
    axes = said["axes"] or ""
    return ScreenFact(
        connector=said["name"], source=SOURCE,
        x=int(said["x"]) if on else None, y=int(said["y"]) if on else None,
        width=int(said["w"]) if on else None, height=int(said["h"]) if on else None,
        turn=geometry.xrandr_turn(word, axes) if on else None,
        said=(word + axes).strip() if on else "",
        primary=bool(said["primary"]), enabled=on)


def parse_monitors(text: str) -> int | None:
    """How many logical monitors `xrandr --listmonitors` lists."""
    first = text.strip().splitlines()[:1]
    found = re.fullmatch(r"Monitors: (\d+)", first[0]) if first else None
    return int(found.group(1)) if found else None


def parse_check(text: str) -> str | None:
    found = _CHECK.search(text)
    return found.group(1) if found else None


def parse_name(text: str) -> str:
    found = _NAME.search(text)
    return found.group(1) if found else ""


def window_manager(run: Run) -> list[tuple[str, str]]:
    """The window manager's own name and whether a compositing manager runs. Nothing
    where `xprop` is not installed."""
    root = run(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK", "_NET_WM_CM_S0"])
    if not root.ok:
        return []
    facts: list[tuple[str, str]] = []
    window = parse_check(root.text)
    if window is not None:
        named = run(["xprop", "-id", window, "_NET_WM_NAME"])
        name = parse_name(named.text) if named.ok else ""
        if name:
            facts.append(("Window manager", name))
    facts.append(("Compositing", "Not running" if "not found" in _line(root.text, "_NET_WM_CM_S0")
                  else "Running"))
    return facts


def _line(text: str, key: str) -> str:
    return next((one for one in text.splitlines() if one.startswith(key)), "not found")


def probe(env: Mapping[str, str], run: Run) -> Reading:
    said = run(["xrandr", "--query"])
    if not said.ok:
        return unavailable(SOURCE, said)
    parsed = parse_outputs(said.text)
    if parsed is None or (not parsed[0] and not parsed[1]):
        return Reading.failed(SOURCE, "xrandr printed something this report cannot read",
                              said.text)
    screens, disconnected = parsed
    facts = window_manager(run)
    monitors = run(["xrandr", "--listmonitors"])
    count = parse_monitors(monitors.text) if monitors.ok else None
    if count is not None:
        facts.append(("Logical monitors", str(count)))
    if disconnected:
        facts.append(("Outputs with nothing connected", str(disconnected)))
    return Reading(SOURCE, OK, screens=tuple(screens), facts=tuple(facts))

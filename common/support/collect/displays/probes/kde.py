"""KDE Plasma: `kscreen-doctor -j`, and its text form where the JSON is not offered."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from common.capture import geometry

from ..model import NOT_HERE, OK, TIMED_OUT, Reading, ScreenFact
from ..run import Run
from . import ANSI, unavailable, version_in

SOURCE = "kscreen"

_TEXT_OUTPUT = re.compile(
    r"Output:\s+\d+\s+(?P<name>\S+)\s+(?P<enabled>enabled|disabled)"
    r"(?:\s+(?P<connected>connected|disconnected))?(?:\s+priority\s+(?P<priority>\d+))?")
_GEOMETRY = re.compile(r"Geometry:\s+(-?\d+),(-?\d+)\s+(\d+)x(\d+)")
_SCALE = re.compile(r"Scale:\s+([\d.]+)")
_ROTATION = re.compile(r"Rotation:\s+(\w+)")
_CURRENT_MODE = re.compile(r"\d+:\d+x\d+@([\d.]+)\*")
_RULE_GROUP = re.compile(r"^\[(?!General\])[^\]]+\]\s*$", re.MULTILINE)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def _int(value: object) -> int | None:
    found = _number(value)
    return None if found is None else int(found)


def _screen(**fields: Any) -> ScreenFact:
    word = geometry.kscreen_word(fields.pop("rotation"))
    return ScreenFact(turn=geometry.xrandr_turn(word) if word else None, said=word,
                      source=SOURCE, **fields)


def parse_json(text: str) -> list[ScreenFact] | None:
    """Every output `kscreen-doctor -j` lists that is connected. None where the text is not
    its JSON."""
    try:
        said = json.loads(text)
    except ValueError:
        return None
    outputs = said.get("outputs") if isinstance(said, dict) else None
    if not isinstance(outputs, list) or not all(isinstance(one, dict) for one in outputs):
        return None
    found = []
    for one in outputs:
        if one.get("connected") is False:
            continue
        pos, size = one.get("pos") or {}, one.get("size") or {}
        current = next((mode for mode in one.get("modes") or []
                        if isinstance(mode, dict) and mode.get("id") == one.get("currentModeId")),
                       {})
        enabled = one.get("enabled")
        priority = _int(one.get("priority"))
        found.append(_screen(
            connector=str(one.get("name") or "") or None,
            model=str(one["model"]) if isinstance(one.get("model"), str) else None,
            x=_int(pos.get("x")), y=_int(pos.get("y")),
            width=_int(size.get("width")), height=_int(size.get("height")),
            scale=_number(one.get("scale")), refresh_hz=_number(current.get("refreshRate")),
            rotation=one.get("rotation"),
            primary=None if priority is None else priority == 1,
            enabled=enabled if isinstance(enabled, bool) else None))
    return found


def parse_text(text: str) -> list[ScreenFact] | None:
    """The same from `kscreen-doctor -o`, with the color codes stripped first. None where
    there is no output line."""
    found = []
    for line in ANSI.sub("", text).splitlines():
        said = _TEXT_OUTPUT.search(line)
        if said is None:
            continue
        if said["connected"] == "disconnected":
            continue
        place = _GEOMETRY.search(line)
        scale = _SCALE.search(line)
        turn = _ROTATION.search(line)
        refresh = _CURRENT_MODE.search(line)
        found.append(_screen(
            connector=said["name"], model=None,
            x=int(place[1]) if place else None, y=int(place[2]) if place else None,
            width=int(place[3]) if place else None, height=int(place[4]) if place else None,
            scale=float(scale[1]) if scale else None,
            refresh_hz=float(refresh[1]) if refresh else None,
            rotation=int(turn[1]) if turn and turn[1].isdigit() else turn[1] if turn else "",
            primary=None if said["priority"] is None else said["priority"] == "1",
            enabled=said["enabled"] == "enabled"))
    return found or None


def rule_groups(text: str) -> int:
    """How many window rules `kwinrulesrc` defines. Only the count is kept: a rule names
    the windows it moves."""
    return len(_RULE_GROUP.findall(text))


def _config_file(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "kwinrulesrc"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def probe(env: Mapping[str, str], run: Run,
          read: Callable[[Path], str | None] = _read) -> Reading:
    said = run(["kscreen-doctor", "-j"])
    if said.state in (NOT_HERE, TIMED_OUT):
        return unavailable("kscreen-doctor", said)
    screens = parse_json(said.text) if said.ok else None
    shown = said.text
    if screens is None:
        # A Plasma that prints no JSON, or a build that answers `-j` with an error.
        text = run(["kscreen-doctor", "-o"])
        if not text.ok:
            return unavailable("kscreen-doctor", text)
        screens, shown = parse_text(text.text), ANSI.sub("", text.text)
    if screens is None:
        return Reading.failed(SOURCE, "kscreen-doctor printed something this report "
                              "cannot read", shown)
    facts = _versions(run)
    rules = read(_config_file(env))
    if rules is not None:
        facts.append(("Window rules defined in KWin", str(rule_groups(rules))))
    return Reading(SOURCE, OK, screens=tuple(screens), facts=tuple(facts),
                   notes=("Window placement here is the desktop's own; VPinFE cannot "
                          "set it",))


def _versions(run: Run) -> list[tuple[str, str]]:
    facts = []
    plasma = run(["plasmashell", "--version"])
    if plasma.ok and version_in(plasma.text):
        facts.append(("Plasma", version_in(plasma.text)))
    for program in ("kwin_wayland", "kwin_x11"):
        kwin = run([program, "--version"])
        if kwin.ok and version_in(kwin.text):
            facts.append(("KWin", f"{version_in(kwin.text)} ({program.removeprefix('kwin_')})"))
            break
    return facts

"""Any other Wayland compositor: `wlr-randr --json` where it is installed, else
`wayland-info`. Either one only reads what the compositor offers to every client."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from common.capture import geometry

from ..model import NOT_HERE, OK, Reading, ScreenFact
from ..run import Run
from . import unavailable

SOURCE = "wlr-randr"
INFO = "wayland-info"

# The protocols a report about windows on a compositor wants to know are there.
NOTABLE = ("zwlr_layer_shell_v1", "zwlr_output_manager_v1", "zwlr_screencopy_manager_v1",
           "zxdg_output_manager_v1", "xdg_wm_base", "xdg_activation_v1",
           "zxdg_decoration_manager_v1")

_INTERFACE = re.compile(r"^interface: '(?P<name>[^']+)'", re.MULTILINE)
_BLOCK = re.compile(r"^interface: 'wl_output'.*?(?=^interface: |\Z)", re.MULTILINE | re.DOTALL)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def parse_wlr_randr(text: str) -> list[ScreenFact] | None:
    try:
        said = json.loads(text)
    except ValueError:
        return None
    if not isinstance(said, list) or not all(isinstance(one, dict) for one in said):
        return None
    found = []
    for one in said:
        mode = next((m for m in one.get("modes") or [] if isinstance(m, dict) and m.get("current")),
                    {})
        place = one.get("position") or {}
        word = str(one.get("transform") or "normal")
        turn = geometry.sway_transform(word)
        scale = _number(one.get("scale")) or 1.0
        size = turn.size(int(mode["width"]), int(mode["height"])) \
            if isinstance(mode.get("width"), int) and isinstance(mode.get("height"), int) \
            else None
        enabled = one.get("enabled")
        found.append(ScreenFact(
            connector=str(one["name"]) if isinstance(one.get("name"), str) else None,
            model=str(one["model"]) if isinstance(one.get("model"), str) and one["model"]
            else None,
            x=int(place["x"]) if isinstance(place.get("x"), int) else None,
            y=int(place["y"]) if isinstance(place.get("y"), int) else None,
            width=round(size[0] / scale) if size else None,
            height=round(size[1] / scale) if size else None,
            scale=scale, refresh_hz=_number(mode.get("refresh")), turn=turn, said=word,
            enabled=enabled if isinstance(enabled, bool) else None, source=SOURCE))
    return found


def parse_wayland_info(text: str) -> list[ScreenFact] | None:
    """The `wl_output` globals. None where there are none."""
    found = []
    for block in _BLOCK.findall(text):
        def field(pattern: str, block: str = block) -> re.Match[str] | None:
            return re.search(pattern, block, re.MULTILINE)

        place, scale = field(r"\bx: (-?\d+), y: (-?\d+)"), field(r"\bscale: (\d+)")
        turn = field(r"output_transform: (\S+?),?\s*$") or field(r"\btransform: (\S+?),?\s*$")
        name, model = field(r"\bname: '([^']*)'"), field(r"\bmodel: '([^']*)'")
        mode = re.search(r"width: (\d+) px, height: (\d+) px, refresh: ([\d.]+) Hz,\s*"
                         r"flags:[^\n]*\bcurrent\b", block)
        word = turn[1] if turn else "normal"
        flip = geometry.sway_transform(word)
        size = flip.size(int(mode[1]), int(mode[2])) if mode else None
        factor = float(scale[1]) if scale else 1.0
        found.append(ScreenFact(
            connector=name[1] if name else None, model=model[1] if model and model[1] else None,
            x=int(place[1]) if place else None, y=int(place[2]) if place else None,
            width=round(size[0] / factor) if size else None,
            height=round(size[1] / factor) if size else None,
            scale=factor, refresh_hz=float(mode[3]) if mode else None, turn=flip, said=word,
            source=INFO))
    return found or None


def protocols(text: str) -> list[str]:
    offered = {one["name"] for one in _INTERFACE.finditer(text)}
    return [name for name in NOTABLE if name in offered]


def probe(env: Mapping[str, str], run: Run) -> Reading:
    said = run(["wlr-randr", "--json"])
    if said.ok:
        screens = parse_wlr_randr(said.text)
        if screens is None:
            return Reading.failed(SOURCE, "wlr-randr printed something this report cannot "
                                  "read", said.text)
        return Reading(SOURCE, OK, screens=tuple(screens))
    if said.state != NOT_HERE:
        return unavailable(SOURCE, said)
    info = run([INFO])
    if info.state == NOT_HERE:
        return Reading(SOURCE, NOT_HERE, SOURCE, notes=(
            "Not available here. Installing wlr-randr would add the compositor's own view",))
    if not info.ok:
        return unavailable(INFO, info)
    screens = parse_wayland_info(info.text)
    if screens is None:
        return Reading.failed(INFO, "wayland-info printed something this report cannot "
                              "read", info.text)
    offered = protocols(info.text)
    facts = (("Protocols offered", ", ".join(offered)),) if offered else ()
    return Reading(INFO, OK, screens=tuple(screens), facts=facts)

"""sway and Hyprland, asked over their own sockets: the IPC the capture feature already
speaks, with a wider allow-list of what each says about an output."""

from __future__ import annotations

import re
import socket
from collections.abc import Mapping

from apps.vpx import capture as vpx_capture
from common.capture import geometry
from common.capture.adapters import Window
from common.capture.adapters import wlr as ipc

from ..model import OK, Reading, ScreenFact, WindowFact
from ..run import PROBE_SECONDS, Run

GET_TREE = 4
GET_VERSION = 7

# A VPinFE window by the application id Chromium gives its `/app/<window>` page.
_VPINFE = re.compile(r"^chrom(?:e|ium)-.*__app_(?P<name>[a-z0-9_]+)-Default$")


def _connect(path: str) -> socket.socket:
    """The capture feature's connection, held to a program's limit so three answers fit in
    the report's time."""
    sock = ipc._unix(path)
    sock.settimeout(PROBE_SECONDS)
    return sock


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def _int(value: object) -> int | None:
    found = _number(value)
    return None if found is None else int(found)


def _text(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def parse_sway(said: object) -> list[ScreenFact] | None:
    """Every output sway lists, active or not. None where the answer is not a list of
    outputs."""
    if not isinstance(said, list) or not all(isinstance(one, dict) for one in said):
        return None
    found = []
    for one in said:
        rect, mode = one.get("rect") or {}, one.get("current_mode") or {}
        word = _text(one.get("transform")) or "normal"
        active = one.get("active")
        power = one.get("power")
        refresh = _number(mode.get("refresh"))
        found.append(ScreenFact(
            connector=_text(one.get("name")), model=_text(one.get("model")),
            x=_int(rect.get("x")), y=_int(rect.get("y")),
            width=_int(rect.get("width")), height=_int(rect.get("height")),
            scale=_number(one.get("scale")),
            refresh_hz=round(refresh / 1000, 3) if refresh else None,
            turn=geometry.sway_transform(word), said=word,
            primary=None, enabled=active if isinstance(active, bool) else None,
            asleep=(not power) if isinstance(power, bool) else None, source="sway"))
    return found


def parse_hyprland(said: object) -> list[ScreenFact] | None:
    """`width` and `height` are the mode's, before the transform and the scale."""
    if not isinstance(said, list) or not all(isinstance(one, dict) for one in said):
        return None
    found = []
    for one in said:
        turn = geometry.wayland_transform(one.get("transform"))
        scale = _number(one.get("scale")) or 1.0
        mode = (_int(one.get("width")), _int(one.get("height")))
        width, height = (turn.size(mode[0], mode[1])
                         if mode[0] is not None and mode[1] is not None else (None, None))
        disabled = one.get("disabled")
        dpms = one.get("dpmsStatus")
        mirror = _text(one.get("mirrorOf"))
        found.append(ScreenFact(
            connector=_text(one.get("name")), model=_text(one.get("model")),
            x=_int(one.get("x")), y=_int(one.get("y")),
            width=round(width / scale) if width is not None else None,
            height=round(height / scale) if height is not None else None,
            scale=scale, refresh_hz=_number(one.get("refreshRate")),
            turn=turn, said=str(one.get("transform", 0)), primary=None,
            enabled=(not disabled) if isinstance(disabled, bool) else None,
            asleep=(not dpms) if isinstance(dpms, bool) else None,
            mirrors=None if mirror in (None, "none") else mirror, source="Hyprland"))
    return found


def own_windows(found: list[Window]) -> tuple[tuple[WindowFact, ...], int]:
    """VPinFE's windows and Visual Pinball's, and how many others there are. No other
    window's title or application name is kept."""
    mine = []
    for one in found:
        said = _VPINFE.match(one.app_id)
        part = vpx_capture.TITLES.get(one.title)
        if said:
            mine.append(WindowFact(f"{said['name'].title()} (VPinFE)", one.output))
        elif part:
            mine.append(WindowFact(f"{part.title()} (Visual Pinball)", one.output))
    return tuple(mine), len(found) - len(mine)


def _facts(version: str, others: int) -> tuple[tuple[str, str], ...]:
    facts = [("Windows from other applications", str(others))]
    return (("Version", version), *facts) if version else tuple(facts)


def sway(env: Mapping[str, str], run: Run, connect: ipc.Connect = _connect) -> Reading:
    source = "sway"
    path = str(env.get("SWAYSOCK") or "")
    if not path:
        return Reading.failed(source, "SWAYSOCK is not set")
    try:
        said = ipc.ask_sway(connect(path))
        screens = parse_sway(said)
        if screens is None:
            return Reading.failed(source, "sway answered with something this report "
                                  "cannot read", repr(said))
        version = ipc.ask_sway(connect(path), GET_VERSION)
        windows, others = own_windows(ipc.sway_windows(ipc.ask_sway(connect(path), GET_TREE)))
    except (OSError, ValueError):
        return Reading.failed(source, "The compositor did not answer")
    human = version.get("human_readable") if isinstance(version, dict) else None
    return Reading(source, OK, screens=tuple(screens), windows=windows,
                   facts=_facts(str(human or ""), others))


def hyprland(env: Mapping[str, str], run: Run, connect: ipc.Connect = _connect) -> Reading:
    source = "Hyprland"
    path = ipc.hyprland_socket(env)
    try:
        monitors = ipc.ask_hyprland(connect(path))
        screens = parse_hyprland(monitors)
        if screens is None:
            return Reading.failed(source, "Hyprland answered with something this report "
                                  "cannot read", repr(monitors))
        version = ipc.ask_hyprland(connect(path), b"j/version")
        clients = ipc.ask_hyprland(connect(path), b"j/clients")
        windows, others = own_windows(ipc.hyprland_windows(clients, monitors))
    except (OSError, ValueError):
        return Reading.failed(source, "The compositor did not answer")
    tag = (version.get("tag") or version.get("version")) if isinstance(version, dict) else None
    return Reading(source, OK, screens=tuple(screens), windows=windows,
                   facts=_facts(str(tag or ""), others))


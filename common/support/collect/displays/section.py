"""Displays And Layout: the screens VPinFE sees, what the desktop says about them, and where
the two and Visual Pinball's settings disagree."""

from __future__ import annotations

import os
from collections.abc import Mapping

from apps.vpx import config as vpx_config
from apps.vpx import screens as vpx_screens
from common.config.config_access import DisplayConfig
from common.config.paths import get_ini_config
from common.games import launchers
from common.host import (
    about,
    display_service,
    frontend_state,
    frontend_windows,
    placement,
    screen_size,
)
from common.support import session
from common.support.sections import Section, SectionText

from . import compare, gather
from . import desktop as desk
from .model import NOT_HERE, OK, TIMED_OUT, Finding, Reading, ScreenFact, Seen

# The windows VPinFE always opens, in the order the summary lists them, with the key
# `DisplayConfig` answers each by.
WINDOWS = (("Playfield", "playfieldscreenid"), ("Backglass", "backglassscreenid"),
           ("Score View", "scoreviewscreenid"))

_SHELLS = {desk.KDE: "KDE", desk.GNOME: "GNOME", desk.SWAY: "Sway",
           desk.HYPRLAND: "Hyprland", desk.GAMESCOPE: "Gamescope", "lxqt": "LXQt",
           "i3": "i3", "unknown": "Not known"}
_SERVERS = {desk.X11: "X11", desk.WAYLAND: "Wayland", desk.NO_SERVER: "no display session"}
_STATES = {NOT_HERE: "Not installed", TIMED_OUT: "Timed out"}


def screens_seen() -> list[Seen]:
    monitors = display_service.get_display_monitors()
    names = display_service.get_display_names()
    return [Seen(number, names[number] if number < len(names) else "",
                 int(monitor.x), int(monitor.y), int(monitor.width), int(monitor.height))
            for number, monitor in enumerate(monitors)]


def collect() -> SectionText:
    here = desk.resolve()
    env: Mapping[str, str] = os.environ
    found = gather.readings(here, env)
    seen = screens_seen()
    config = DisplayConfig.from_config(get_ini_config())
    vp, vp_problem = _visual_pinball()
    facts = compare.Facts(
        here, tuple(seen), _windows(config), _device(found), config.playfield_orientation,
        config.playfield_rotation, vp, _rules_added(here, env))
    problems = compare.findings(facts)
    lines = _session_block(here, found)
    lines += [""] + _screens_block(facts)
    lines += _unmatched_block(facts)
    lines += _windows_block(facts) + _opened_block(here.native_wayland)
    lines += _desktop_windows_block(found)
    lines += [""] + _visual_pinball_block(facts, vp_problem)
    lines += [""] + _findings_block(problems)
    lines += [""] + _probes_block(found) + _unread_block(found)
    return SectionText.ok(lines)


def _device(found: list[Reading]) -> tuple[ScreenFact, ...]:
    return next((one.screens for one in found if one.ok and one.screens), ())


def _windows(config: DisplayConfig) -> tuple[tuple[str, str], ...]:
    named = [(label, config.window_screen_id(key)) for label, key in WINDOWS]
    named += [(key.removesuffix("screenid").replace("_", " ").title(), value)
              for key, value in sorted(config.extra_screen_ids.items())]
    return tuple(named)


def _rules_added(here: desk.Desktop, env: Mapping[str, str]) -> bool | None:
    if not here.native_wayland or here.shell not in (desk.SWAY, desk.HYPRLAND):
        return None
    offer = placement.offer(env)
    return None if offer is None or offer.rules is None else offer.added


def _visual_pinball() -> tuple[compare.VpFacts | None, str]:
    """The launcher's ini as the comparisons read it, or why there is none."""
    launcher = launchers.default_launcher("vpx")
    if launcher is None:
        return None, "No Visual Pinball launcher is set up."
    path = vpx_config.settings_file(launcher.settings)
    if path is None or not path.is_file():
        return None, ("The launcher names no settings file that exists, so nothing can be "
                      "compared.")
    ini = vpx_config.read_file(path)
    displays = tuple(
        compare.VpDisplay(part, str(ini.value(key) or ""),
                          str(ini.value(vpx_screens.OUTPUT[part]) or "")
                          if part in vpx_screens.OUTPUT else "")
        for part, key in vpx_screens.DISPLAY.items())
    rotation = ini.value(vpx_screens.VIEW_CAB_ROTATION)
    return compare.VpFacts(None if rotation is None else str(rotation), displays), ""


def _session_block(here: desk.Desktop, found: list[Reading]) -> list[str]:
    lines = ["### Session", "", f"- **Windowing system:** {about.windowing_system()}"]
    if here.system == desk.LINUX:
        lines.append(f"- **Desktop:** {_SHELLS.get(here.shell, here.shell.title())} on "
                     f"{_SERVERS.get(here.server, here.server)}")
        if here.container != desk.NO_CONTAINER:
            lines.append(f"- **Container:** {here.container}")
        if here.vm:
            lines.append(f"- **Virtual machine:** {here.vm}. Screen sizes may be placeholders")
    lines += [f"- **{name}:** {value}" for name, value in session.read()]
    for one in found:
        lines += [f"- **{name}:** {value}" for name, value in one.facts]
        lines += [f"- {note}" for note in one.notes if one.ok]
    return lines


def _screens_block(facts: compare.Facts) -> list[str]:
    if not facts.seen:
        return ["### Screens", "", "No screens were found. VPinFE could not list any, which "
                "is normal with no desktop session attached."]
    paired = compare.match(facts.seen, facts.device)
    lines: list[str] = []
    for one in facts.seen:
        lines += _screen(facts, one, paired.get(one.number)) + [""]
    return lines[:-1]


def _screen(facts: compare.Facts, one: Seen, device: ScreenFact | None) -> list[str]:
    names = [part for part in (device.connector if device else None,
                               one.name or (device.model if device else None)) if part]
    if device is not None and device.primary:
        names.append("primary")
    heading = f"### Screen {one.number}" + (": " + " · ".join(names) if names else "")
    place = [f"{one.width}x{one.height} at {one.x},{one.y}"]
    if device is not None and device.refresh_hz:
        place.append(f"{device.refresh_hz:g} Hz")
    if device is not None and device.scale is not None:
        place.append(f"scale {device.scale:g}")
    size = screen_size.reported_mm(one.number)
    if size is not None:
        place.append(f"{round(size[0])} x {round(size[1])} mm")
    lines = [heading, "", f"- **Size and place:** {' · '.join(place)}",
             f"- **Used for:** {_used_for(facts, one)}", "- **Rotation**"]
    here = facts.playfield is not None and facts.playfield.number == one.number
    if here:
        lines.append(f"  - **Set in VPinFE:** Orientation {facts.orientation.title()}, "
                     f"Rotation {facts.rotation}°")
    elif _vpinfe_windows(facts, one):
        lines.append("  - **Set in VPinFE:** VPinFE does not turn this window")
    cab = _playfield_here(facts, one)
    if here or cab:
        lines.append(f"  - **Set in Visual Pinball:** {_cab_line(facts)}")
    lines.append(f"  - **Reported by the device:** {compare.device_rotation(device)}")
    if here:
        lines.append(f"  - **What that makes:** {compare.what_that_makes(facts, device, one)}")
    return lines


def _vpinfe_windows(facts: compare.Facts, one: Seen) -> list[str]:
    return [label for label, _ in facts.windows if facts.number_of(label) == one.number]


def _vp_windows(facts: compare.Facts, one: Seen) -> list[str]:
    if facts.vp is None:
        return []
    return [display.part.title() for display in facts.vp.displays
            if display.stored and display.shown
            and (found := vpx_screens.screen_for(display.stored, facts.seen)) is not None
            and found.number == one.number]


def _playfield_here(facts: compare.Facts, one: Seen) -> bool:
    return "Playfield" in _vp_windows(facts, one)


def _used_for(facts: compare.Facts, one: Seen) -> str:
    used = [f"{label} (VPinFE)" for label in _vpinfe_windows(facts, one)]
    used += [f"{part} (Visual Pinball)" for part in _vp_windows(facts, one)]
    return ", ".join(used) if used else "No window is set to it"


def _cab_line(facts: compare.Facts) -> str:
    if facts.vp is None:
        return "No launcher ini"
    if facts.vp.cab_rotation is None:
        return "Cab rotation not set"
    return f"Cab rotation {facts.vp.cab_rotation}°"


def _unmatched_block(facts: compare.Facts) -> list[str]:
    paired = compare.match(facts.seen, facts.device)
    used = {id(one) for one in paired.values()}
    left = [one for one in facts.device if id(one) not in used]
    if not left:
        return []
    lines = ["", "### Other Screens The Desktop Reports", ""]
    for one in left:
        name = " · ".join(part for part in (one.connector, one.model) if part)
        lines.append(f"- **{name or 'Screen'}:** {_state(one)}")
    return lines


def _state(one: ScreenFact) -> str:
    if one.enabled is False:
        return "Off"
    if one.asleep:
        return "Asleep"
    if one.mirrors:
        return f"Shows a copy of {one.mirrors}"
    where = f" at {one.x},{one.y}" if one.x is not None and one.y is not None else ""
    size = f"{one.width}x{one.height}{where}" if one.width and one.height else "Not placed"
    return f"{size}, not one of the screens VPinFE found"


def _windows_block(facts: compare.Facts) -> list[str]:
    lines = ["", "### VPinFE Windows Against Screens", ""]
    return lines + [f"- **{label}:** {_window_line(value, facts.seen)}"
                    for label, value in facts.windows]


def _window_line(value: str, seen: tuple[Seen, ...]) -> str:
    said = str(value or "").strip()
    if not said:
        return "No screen set, so this window is not opened"
    try:
        number = int(said)
    except ValueError:
        return f"Set to {said}, which is not a screen number"
    if number >= len(seen):
        return (f"Set to screen {number}, but only {len(seen)} "
                f"{'screen was' if len(seen) == 1 else 'screens were'} found, "
                "so VPinFE does not open it")
    return f"Screen {number}"


def _opened_block(native_wayland: bool = False) -> list[str]:
    """Where each window says it opened. A window that reports nothing has not opened since
    reports were kept, and one reported while the frontend is down is the last one it made.
    A native Wayland window is never told its position, so none is printed for it."""
    held = frontend_windows.read()
    if not held:
        return []
    running = frontend_state.current().running
    lines = ["", "### Windows As They Opened", ""]
    for name, report in sorted(held.items()):
        where = (f"at {report['x']},{report['y']}"
                 if not native_wayland
                 and report.get("x") is not None and report.get("y") is not None else "")
        size = (f"{report['width']}x{report['height']}"
                if report.get("width") and report.get("height") else "")
        scale = f"scale {report['scale']:g}" if report.get("scale") else ""
        said = " · ".join(part for part in (size, where, scale) if part) or "Nothing measured"
        seen = str(report.get("reported_at") or "")[:16].replace("T", " ")
        lines.append(f"- **{name.replace('_', ' ').title()}:** {said}"
                     + (f". Last seen {seen} UTC" if seen else "")
                     + ("" if running else ". Not running"))
    return lines


def _desktop_windows_block(found: list[Reading]) -> list[str]:
    windows = [window for one in found for window in one.windows]
    if not windows:
        return []
    return ["", "### Windows The Desktop Reports", ""] + [
        f"- **{window.name}:** on {window.output}" for window in windows]


def _visual_pinball_block(facts: compare.Facts, problem: str) -> list[str]:
    heading = ["### Visual Pinball Against Screens", ""]
    if facts.vp is None:
        return heading + [problem]
    lines = [f"- **{display.part.title()}:** {_display_line(display.stored, facts.seen)}"
             for display in facts.vp.displays]
    rotation = facts.vp.cab_rotation
    lines.append(f"- **Cabinet rotation:** {rotation if rotation is not None else 'Not set'}")
    return heading + lines


def _display_line(stored: str, seen: tuple[Seen, ...]) -> str:
    if not stored:
        return "Not set"
    found = vpx_screens.screen_for(stored, seen)
    if found is None:
        return f"{stored} matches none of the screens VPinFE found"
    return f"{stored} is screen {found.number}"


def _findings_block(problems: list[Finding]) -> list[str]:
    heading = ["### Findings", ""]
    if not problems:
        return heading + ["Nothing found that looks wrong in what could be compared."]
    return heading + [f"- **{'Problem' if one.problem else 'Note'}:** {one.text}"
                      for one in problems]


def _probes_block(found: list[Reading]) -> list[str]:
    if not found:
        return []
    lines = ["### What The Desktop Was Asked", ""]
    for one in found:
        if one.ok:
            said = "Read"
        elif one.state in _STATES:
            said = _STATES[one.state]
        else:
            said = f"Could not read: {one.reason}" if one.reason else "Could not read"
        lines.append(f"- **{one.source}:** {said}")
        if not one.ok:
            lines += [f"  - {note}" for note in one.notes]
    return lines


def _unread_block(found: list[Reading]) -> list[str]:
    lines: list[str] = []
    for one in found:
        if one.unread:
            lines += ["", f"### Could Not Read: {one.source}", "", "```text", *one.unread, "```"]
    return lines


SECTION = Section("displays", "Displays And Layout", 30, collect)

__all__ = ["OK", "SECTION"]

"""Where VPinFE's view, the desktop's view and Visual Pinball's settings disagree, in a
sentence each. Pure: facts in, findings out. A rule fires only on the facts it has, so a
missing one never makes a finding."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from apps.vpx import screens as vpx_screens
from common.host import vpinos

from . import desktop as desk
from .model import Finding, ScreenFact, Seen

PLAYFIELD = "Playfield"


@dataclass(frozen=True)
class VpDisplay:
    """One Visual Pinball window: its ini key, the display id stored there, and its output
    mode ("" for the playfield, which always shows)."""

    part: str
    stored: str
    mode: str = ""

    @property
    def key(self) -> str:
        return vpx_screens.DISPLAY[self.part]

    @property
    def shown(self) -> bool:
        return not self.mode or self.mode == vpx_screens.FLOATING


@dataclass(frozen=True)
class VpFacts:
    """What the launcher's Visual Pinball ini holds. `cab_rotation` is None where the key
    is not in the file."""

    cab_rotation: str | None
    displays: tuple[VpDisplay, ...] = ()


@dataclass(frozen=True)
class Facts:
    here: desk.Desktop
    seen: tuple[Seen, ...]
    # Each window VPinFE opens, by its label, and the screen the config names for it.
    windows: tuple[tuple[str, str], ...]
    device: tuple[ScreenFact, ...] = ()
    orientation: str = "landscape"
    rotation: int = 0
    vp: VpFacts | None = None
    # Whether the rules include line is in the compositor's config, None where it does not
    # apply.
    rules_added: bool | None = None

    @property
    def playfield(self) -> Seen | None:
        number = self.number_of(PLAYFIELD)
        return next((one for one in self.seen if one.number == number), None)

    def number_of(self, label: str) -> int | None:
        stored = next((value for name, value in self.windows if name == label), "")
        try:
            return int(str(stored).strip())
        except ValueError:
            return None


def match(seen: Sequence[Seen], device: Sequence[ScreenFact]) -> dict[int, ScreenFact]:
    """The desktop's screen for each of VPinFE's: by the same place and size, then the same
    place, then the same size, taking each once."""
    free = [one for one in device if one.placed and one.width and one.height]
    found: dict[int, ScreenFact] = {}
    for key in (lambda a, b: (a.x, a.y, a.width, a.height) == (b.x, b.y, b.width, b.height),
                lambda a, b: (a.x, a.y) == (b.x, b.y),
                lambda a, b: (a.width, a.height) == (b.width, b.height)):
        for one in seen:
            if one.number in found:
                continue
            hit = next((d for d in free if key(one, d)), None)
            if hit is not None:
                found[one.number] = hit
                free.remove(hit)
    return found


def _plural(count: int) -> str:
    return f"{count} screen" if count == 1 else f"{count} screens"


def _quarter(value: int) -> str:
    return f"{value}°"


def findings(facts: Facts) -> list[Finding]:
    paired = match(facts.seen, facts.device)
    found = [*_r1(facts), *_r2(facts), *_r3(facts, paired), *_r4(facts, paired),
             *_r5(facts), *_r6(facts), *_r7(facts), *_r8(facts), *_r9(facts, paired),
             *_r10(facts), *_r11(facts)]
    return found


def _r1(facts: Facts) -> list[Finding]:
    if not facts.seen:
        return []
    found = []
    for label, stored in facts.windows:
        try:
            number = int(str(stored).strip())
        except ValueError:
            continue
        if number >= len(facts.seen):
            found.append(Finding("R1", True, (
                f"VPinFE's {label.lower()} window is set to screen {number}, but only "
                f"{_plural(len(facts.seen))} {'was' if len(facts.seen) == 1 else 'were'} found, "
                "so VPinFE does not open it.")))
    return found


def _r2(facts: Facts) -> list[Finding]:
    if not facts.device or len(facts.device) == len(facts.seen):
        return []
    return [Finding("R2", False, (
        f"VPinFE sees {_plural(len(facts.seen))} and the desktop reports {len(facts.device)}. "
        "A screen may be off, asleep or mirrored."))]


def _r3(facts: Facts, paired: dict[int, ScreenFact]) -> list[Finding]:
    found = []
    for one in facts.seen:
        device = paired.get(one.number)
        if device is None or device.width is None or device.height is None:
            continue
        if (one.width, one.height) == (device.width, device.height):
            continue
        scale = f" at scale {device.scale:g}" if device.scale not in (None, 1.0) else ""
        why = (" XWayland shows scaled pixels, and Chromium places windows by those."
               if facts.here.native_wayland else "")
        found.append(Finding("R3", False, (
            f"VPinFE sees screen {one.number} as {one.width}x{one.height} where the desktop "
            f"has {device.width}x{device.height}{scale}.{why}")))
    return found


def _r4(facts: Facts, paired: dict[int, ScreenFact]) -> list[Finding]:
    playfield = facts.playfield
    device = paired.get(playfield.number) if playfield is not None else None
    if device is None or not device.is_turned or not facts.rotation or device.turn is None:
        return []
    return [Finding("R4", False, (
        f"Two turns apply to screen {playfield.number if playfield else ''}: the device turns "
        f"it {_quarter(device.turn.ccw)} and VPinFE turns the picture "
        f"{_quarter(facts.rotation)}. That is right only if the picture is meant to be "
        "turned by both."))]


def _r5(facts: Facts) -> list[Finding]:
    playfield = facts.playfield
    if playfield is None or facts.rotation:
        return []
    shape = _shape(playfield.width, playfield.height)
    if shape is None or shape == facts.orientation:
        return []
    return [Finding("R5", True, (
        f"VPinFE is set to {facts.orientation} and screen {playfield.number} is {shape}, "
        "so the picture is not turned to fit."))]


def _shape(width: int, height: int) -> str | None:
    if width == height:
        return None
    return "portrait" if height > width else "landscape"


def expected_cab_rotation(facts: Facts) -> str | None:
    return vpx_screens.view_cab_rotation(facts.playfield, str(facts.rotation))


def _r6(facts: Facts) -> list[Finding]:
    if facts.vp is None:
        return []
    expected = expected_cab_rotation(facts)
    current = facts.vp.cab_rotation or ""
    if expected is None or not vpx_screens.rotation_differs(expected, current):
        return []
    held = vpx_screens.turn_of(current)
    if held is None:
        return []
    return [Finding("R6", True, (
        f"Visual Pinball turns the table {held:g}° where VPinFE's setting would give "
        f"{float(expected):g}°."))]


def _r7(facts: Facts) -> list[Finding]:
    if facts.vp is None or not facts.seen:
        return []
    return [Finding("R7", True, f"`{one.key}` matches none of the detected screens.")
            for one in facts.vp.displays
            if one.stored and one.shown
            and vpx_screens.screen_for(one.stored, facts.seen) is None]


def _r8(facts: Facts) -> list[Finding]:
    found = []
    for index, one in enumerate(facts.seen):
        for other in facts.seen[index + 1:]:
            if (one.x, one.y, one.width, one.height) == (other.x, other.y, other.width,
                                                           other.height):
                found.append(Finding("R8", True, (
                    f"Screens {one.number} and {other.number} are the same area: the desktop "
                    "is duplicating one screen.")))
    return found


def _r9(facts: Facts, paired: dict[int, ScreenFact]) -> list[Finding]:
    found = []
    for label, stored in facts.windows:
        number = facts.number_of(label)
        device = paired.get(number) if number is not None else None
        if device is None or str(stored).strip() == "":
            continue
        if device.asleep:
            found.append(Finding("R9", True, f"The screen set for the {label.lower()} is asleep."))
        elif device.enabled is False:
            found.append(Finding("R9", True, f"The screen set for the {label.lower()} is off."))
    return found


def _r10(facts: Facts) -> list[Finding]:
    if facts.rules_added is not False or not facts.here.native_wayland \
            or facts.here.shell not in (desk.SWAY, desk.HYPRLAND) \
            or facts.here.distro == vpinos.ID:
        return []
    name = "sway" if facts.here.shell == desk.SWAY else "Hyprland"
    return [Finding("R10", True, (
        "VPinFE's windows cannot be placed here yet: the rules file is not included in your "
        f"{name} configuration."))]


def _r11(facts: Facts) -> list[Finding]:
    configured = [label for label, value in facts.windows if str(value).strip()]
    if not facts.here.game_mode or len(configured) < 2:
        return []
    return [Finding("R11", False, (
        "Game Mode shows one screen, so the " + _joined([one.lower() for one in configured])
        + " windows share it."))]


def _joined(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def device_rotation(device: ScreenFact | None) -> str:
    """What the device says about this screen's turn, with the operating system's own word
    beside the degrees."""
    if device is None or device.turn is None:
        return "Not reported"
    size = f"{device.width}x{device.height}" if device.width and device.height else ""
    shape = device.shape
    mirrored = ", mirrored" if device.turn.flip else ""
    detail = ", ".join(one for one in (shape, size) if one)
    if not device.is_turned:
        return f"Not rotated{mirrored}" + (f" ({detail})" if detail else "")
    word = f" ({device.source} says {device.said})" if device.said else ""
    shown = f"{_quarter(device.turn.ccw)}{word}{mirrored}"
    return shown + (f", now {shape}" if shape else "") + (f" ({size})" if size else "")


def what_that_makes(facts: Facts, device: ScreenFact | None, seen: Seen) -> str:
    """One neutral sentence for a screen the playfield is on. Both ways of turning a
    playfield are valid, so this describes and never judges."""
    turned = device is not None and device.is_turned and device.turn is not None
    r = facts.rotation
    if device is None or device.turn is None:
        base = f"VPinFE turns the picture {_quarter(r)}." if r \
            else "VPinFE does not turn the picture."
    elif turned and r:
        base = (f"The device turns this screen {_quarter(device.turn.ccw)} and VPinFE turns "
                f"the picture {_quarter(r)} as well.")
    elif turned:
        base = (f"The device turns this screen {_quarter(device.turn.ccw)}; VPinFE does not "
                "turn the picture.")
    elif r:
        base = f"VPinFE turns the picture {_quarter(r)}; the device does not turn the screen."
    else:
        base = "Neither VPinFE nor the device turns this screen."
    if facts.vp is None:
        return base
    expected = vpx_screens.view_cab_rotation(seen, str(r))
    held = vpx_screens.turn_of(facts.vp.cab_rotation)
    if expected is None or held is None:
        return base
    if vpx_screens.rotation_differs(expected, facts.vp.cab_rotation or ""):
        return (f"{base} Visual Pinball turns the table {held:g}° where VPinFE's setting "
                f"would give {float(expected):g}°. They differ.")
    return (f"{base} Visual Pinball's {held:g}° is the turn that goes with VPinFE's "
            f"{_quarter(r)} on this screen. Agree.")

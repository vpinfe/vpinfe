"""Visual Pinball X's key actions, and what a `Mapping.<Name>` value says about their keys.

A value is alternatives joined by ` | `, each one parts joined by ` & ` that are down
together, each part `<device>;<id>`. Keys are read and written as sets of
`KeyboardEvent.code` names. A controller's button, hat direction or axis side is read and
written too where the caller asks for it, as a member named `vpx:<device>;<id>`; without
that, and for any other device, an alternative that is not keys is kept as it stands.

Blank means the program's default, so a value that says the default is written blank, and
nothing can be written for an action that has a default and no key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from . import keys

SECTION = "Input"
PREFIX = "Mapping."
_ALTERNATIVES = " | "
_TOGETHER = " & "
_KEY_PART = re.compile(r"Key;(\d+)")
_CONTROLLER_PART = re.compile(
    r"(SDLJoy_[0-9A-Fa-f]+_\d+);(\d+)(?:;([ox]);(-?\d+(?:\.\d+)?))?")
CONTROLLER_PREFIX = "vpx:"
JOYSTICK = 2

FLIPPERS = "flippers"
PLAY = "play"
GAME = "game"
MENU = "menu"
SERVICE = "service"
OTHER = "other"

# `Key;n` is SDL's scancode, which is the number of the USB usage of the key.
_LSHIFT, _RSHIFT, _LCTRL, _RCTRL, _LALT = 225, 229, 224, 228, 226


@dataclass(frozen=True)
class Action:
    """One action: its setting, the heading it is drawn under, and the key it ships on,
    as the program writes it. The setting is spelled out so a search for it finds this."""

    qualified: str
    heading: str
    default: str = ""

    @property
    def name(self) -> str:
        return self.qualified.removeprefix(f"{SECTION}.{PREFIX}")


def _key(scancode: int) -> str:
    return f"Key;{scancode}"


ACTIONS = (
    Action("Input.Mapping.LeftFlipper", FLIPPERS, _key(_LSHIFT)),
    Action("Input.Mapping.RightFlipper", FLIPPERS, _key(_RSHIFT)),
    Action("Input.Mapping.LeftStagedFlipper", FLIPPERS, _key(_LSHIFT)),
    Action("Input.Mapping.RightStagedFlipper", FLIPPERS, _key(_RSHIFT)),
    Action("Input.Mapping.LeftMagna", FLIPPERS, _key(_LCTRL)),
    Action("Input.Mapping.RightMagna", FLIPPERS, _key(_RCTRL)),
    Action("Input.Mapping.LaunchBall", PLAY, _key(40)),
    Action("Input.Mapping.Start", PLAY, _key(30)),
    Action("Input.Mapping.Credit1", PLAY, _key(34)),
    Action("Input.Mapping.Credit2", PLAY, _key(33)),
    Action("Input.Mapping.Credit3", PLAY, _key(32)),
    Action("Input.Mapping.Credit4", PLAY, _key(35)),
    Action("Input.Mapping.LeftNudge", PLAY, _key(29)),
    Action("Input.Mapping.RightNudge", PLAY, _key(56)),
    Action("Input.Mapping.CenterNudge", PLAY, _key(44)),
    Action("Input.Mapping.Tilt", PLAY, _key(23)),
    Action("Input.Mapping.ExtraBall", PLAY, _key(5)),
    Action("Input.Mapping.Lockbar", PLAY, _key(_LALT)),
    Action("Input.Mapping.ExitGame", GAME, _key(41)),
    Action("Input.Mapping.InGameUI", GAME, _key(69)),
    Action("Input.Mapping.Pause", GAME, _key(19)),
    Action("Input.Mapping.ShowRules", GAME),
    Action("Input.Mapping.VolumeDown", GAME, _key(45)),
    Action("Input.Mapping.VolumeUp", GAME, _key(46)),
    Action("Input.Mapping.PerfOverlay", GAME, _key(68)),
    Action("Input.Mapping.UIUp", MENU, _key(_LCTRL)),
    Action("Input.Mapping.UIDown", MENU, _key(_RCTRL)),
    Action("Input.Mapping.UILeft", MENU, _key(_LSHIFT)),
    Action("Input.Mapping.UIRight", MENU, _key(_RSHIFT)),
    Action("Input.Mapping.CoinDoor", SERVICE, _key(77)),
    Action("Input.Mapping.SlamTilt", SERVICE, _key(74)),
    Action("Input.Mapping.Reset", SERVICE, _key(60)),
    Action("Input.Mapping.Service1", SERVICE, _key(36)),
    Action("Input.Mapping.Service2", SERVICE, _key(37)),
    Action("Input.Mapping.Service3", SERVICE, _key(38)),
    Action("Input.Mapping.Service4", SERVICE, _key(39)),
    Action("Input.Mapping.Service5", SERVICE),
    Action("Input.Mapping.Service6", SERVICE, _key(75)),
    Action("Input.Mapping.Service7", SERVICE),
    Action("Input.Mapping.Service8", SERVICE),
    Action("Input.Mapping.VRCenter", OTHER, _key(93)),
    Action("Input.Mapping.ToggleStereo", OTHER, _key(67)),
    Action("Input.Mapping.GenTournament", OTHER, f"{_key(_LALT)}{_TOGETHER}{_key(30)}"),
    Action("Input.Mapping.DebugBalls", OTHER, _key(18)),
    Action("Input.Mapping.Debugger", OTHER, _key(7)),
    Action("Input.Mapping.Custom1", OTHER),
    Action("Input.Mapping.Custom2", OTHER),
    Action("Input.Mapping.Custom3", OTHER),
    Action("Input.Mapping.Custom4", OTHER),
)
HEADINGS = (FLIPPERS, PLAY, GAME, MENU, SERVICE, OTHER)
_BY_QUALIFIED = {one.qualified: one for one in ACTIONS}
_SCANCODES = {keys.code_of(number): number
              for number in (*range(4, 116), *range(224, 232)) if keys.code_of(number)}


def is_action(qualified: str) -> bool:
    return qualified in _BY_QUALIFIED


def default_of(qualified: str) -> str:
    """The value an action ships with, or "" for a setting that is none."""
    found = _BY_QUALIFIED.get(qualified)
    return found.default if found else ""


def is_mapping(qualified: str) -> bool:
    """Whether a setting key is a mapping of any action, this program's or a later one's."""
    return qualified.startswith(f"{SECTION}.{PREFIX}")


Chord = tuple[str, ...]


@dataclass(frozen=True)
class Held:
    """What a value maps an action to: `keys`, each a chord of one or more members down
    together in a settled order, and `kept`, the alternatives this does not read, each as
    written. A member is a key code, or a controller part as
    `vpx:<device>;<id>[;o|x;<threshold>]`."""

    keys: tuple[Chord, ...] = ()
    kept: tuple[str, ...] = ()


def is_controller(member: str) -> bool:
    return member.startswith(CONTROLLER_PREFIX)


def controller_member(part: str) -> str:
    """The member a part of a mapping names, or "" if it is not a joystick's."""
    found = _CONTROLLER_PART.fullmatch(part.strip())
    if not found:
        return ""
    device, element, side, threshold = found.groups()
    return controller_of(device, int(element), float(threshold or 0), side == "x")


def controller_part(member: str) -> tuple[str, int, float, bool]:
    """A controller member as its device, element number, threshold and whether the press
    is below the threshold rather than above it."""
    found = _CONTROLLER_PART.fullmatch(member[len(CONTROLLER_PREFIX):])
    if not found:
        raise ValueError(member)
    device, element, side, threshold = found.groups()
    return device, int(element), float(threshold or 0), side == "x"


def controller_of(device: str, element: int, threshold: float = 0.0,
                  reversed_: bool = False) -> str:
    """The member for one press: an element of a device, and for an axis the side of the
    threshold it counts on."""
    member = f"{CONTROLLER_PREFIX}{device};{element}"
    if threshold or reversed_:
        member += f";{'x' if reversed_ else 'o'};{threshold:g}"
    return member


def _controller_term(member: str) -> str:
    device, element, threshold, reversed_ = controller_part(member)
    term = f"{device};{element}"
    if threshold or reversed_:
        term += f";{'x' if reversed_ else 'o'};{threshold:.6f}"
    return term


def _chord(alternative: str, controllers: bool = False) -> Chord:
    """What an alternative holds down together, or () if any part is not a key this table
    has a name for - or, where `controllers` is on, a joystick's."""
    members = []
    for part in alternative.split("&"):
        found = _KEY_PART.fullmatch(part.strip())
        member = keys.code_of(int(found[1])) if found else ""
        if not member and controllers:
            member = controller_member(part)
        if not member:
            return ()
        members.append(member)
    return tuple(sorted(set(members)))


def _alternatives(value: str) -> list[str]:
    return [one.strip() for one in value.split("|") if one.strip()]


def parse(value: str, default: str = "", *, controllers: bool = False) -> Held:
    """What `value` maps an action to; the default's keys where it is blank."""
    found = _alternatives(value) or _alternatives(default)
    chords = [(_chord(one, controllers), one) for one in found]
    return Held(tuple(chord for chord, _ in chords if chord),
                tuple(one for chord, one in chords if not chord))


def _numbered(member: str) -> bool:
    if not is_controller(member):
        return member in _SCANCODES
    try:
        controller_part(member)
    except ValueError:
        return False
    return True


def writable(chord: Chord) -> bool:
    """Whether the program has a number for every member of the chord."""
    return bool(chord) and all(_numbered(member) for member in chord)


def _term(chord: Chord) -> str:
    return _TOGETHER.join(_controller_term(member) if is_controller(member)
                          else _key(_SCANCODES[member]) for member in chord)


def devices_in(chords: list[Chord]) -> list[str]:
    """The joysticks a set of chords names, each once, in the order met."""
    return list(dict.fromkeys(controller_part(member)[0] for chord in chords
                              for member in chord if is_controller(member) and _numbered(member)))


def value_of(chords: list[Chord], value: str, default: str = "", *,
             controllers: bool = False) -> str:
    """The value that maps an action to `chords`, kept alternatives and all.

    The chords take the place the value's first readable alternative had; the rest of
    those go. Written blank where that says the default.
    """
    terms = [_term(one) for one in chords if writable(one)]
    out: list[str] = []
    placed = False
    for alternative in _alternatives(value) or _alternatives(default):
        if not _chord(alternative, controllers):
            out.append(alternative)
        elif not placed:
            out += terms
            placed = True
    if not placed:
        out += terms
    shipped = parse("", default)
    if (not any(not _chord(one, controllers) for one in out) and not shipped.kept
            and {tuple(sorted(set(one))) for one in chords} == set(shipped.keys)):
        return ""
    return _ALTERNATIVES.join(out)


@dataclass(frozen=True)
class DeviceInfo:
    """A joystick as the program lists it: its name, and its elements as
    `(number, is_axis, name)`."""

    name: str
    elements: tuple[tuple[int, bool, str], ...] = ()


def _device_key(device: str, field: str) -> str:
    return f"{SECTION}.Device.{device}.{field}"


def device_writes(chords: list[Chord], devices: str,
                  known: Mapping[str, DeviceInfo]) -> dict[str, str]:
    """The program's own record of joysticks, as the launcher keys to write so it reads
    `chords` as given: each joystick they name is listed in `devices` (the value now in
    the file) with its name, type and elements if it was not, and has its automatic
    layout turned off.

    The layout is never turned back on: the program offers to replace every mapping of a
    joystick it has not been told to leave alone.
    """
    listed = [one for one in devices.split(";") if one]
    writes: dict[str, str] = {}
    for device in devices_in(chords):
        writes[_device_key(device, "NoAutoLayout")] = "1"
        info = known.get(device)
        if device in listed or info is None:
            continue
        listed.append(device)
        writes[_device_key(device, "Name")] = info.name
        writes[_device_key(device, "Type")] = str(JOYSTICK)
        for index, (number, axis, name) in enumerate(info.elements):
            writes[_device_key(device, f"Element{index}")] = (
                f"{number};{'A' if axis else 'B'};{name}")
    if len(listed) > len([one for one in devices.split(";") if one]):
        writes[f"{SECTION}.Devices"] = ";".join(listed)
    return writes

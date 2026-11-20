"""Hearing the controllers Visual Pinball X hears, through the SDL library it ships with.

The program names each controller and each of its buttons from SDL's own numbers, so the
only way to name them the way it does is to ask the same library. This loads the program's
copy, lists the controllers the way the program does (`SDLJoy_<guid>_<n>`, "<name> #<n>"),
and reports each press as a member `bindings.controller_of` builds.

One thread owns every SDL call, from the load to the unload. Nothing is held while no
page is listening: the library is unloaded with the last listener, because a loaded DLL
locks the program's folder on Windows and an open controller can be unreadable by the
program on macOS.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import struct
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import bindings
from .bindings import DeviceInfo

log = logging.getLogger(__name__)

_INIT_JOYSTICK = 0x200
_INIT_GAMEPAD = 0x2000
_HAT_UP, _HAT_RIGHT, _HAT_DOWN, _HAT_LEFT = 1, 2, 4, 8
_BIND_BUTTON, _BIND_AXIS, _BIND_HAT = 1, 2, 3
_HAT_BASE = 0x0100
_AXIS_BASE = 0x0200
_STICK_AT = 0.5
_STICK_LET_GO = 0.4
_TRIGGER_LET_GO = 0.1
_LOAD_WAIT = 20.0
_RESCAN = 1.0
_TICK = 0.01

# The hat's directions in the order the program numbers them, with the mask SDL reports.
_HAT_DIRECTIONS = ((_HAT_LEFT, "Left"), (_HAT_RIGHT, "Right"), (_HAT_UP, "Up"),
                   (_HAT_DOWN, "Down"))

# SDL_GamepadButtonLabel, then SDL_GamepadButton where a label says nothing.
_LABELS = {1: "A", 2: "B", 3: "X", 4: "Y", 5: "Cross", 6: "Circle", 7: "Square",
           8: "Triangle"}
_BUTTON_NAMES = {
    0: "South", 1: "East", 2: "West", 3: "North", 4: "Back", 5: "Guide", 6: "Start",
    7: "Left Stick", 8: "Right Stick", 9: "Left Shoulder", 10: "Right Shoulder",
    11: "DPad Up", 12: "DPad Down", 13: "DPad Left", 14: "DPad Right", 15: "Misc #1",
    16: "Right Paddle 1", 17: "Left Paddle 1", 18: "Right Paddle 2", 19: "Left Paddle 2",
    20: "Touchpad", 21: "Misc #2", 22: "Misc #3", 23: "Misc #4", 24: "Misc #5",
    25: "Misc #6"}
_AXIS_NAMES = {0: "Left Stick X", 1: "Left Stick Y", 2: "Right Stick X", 3: "Right Stick Y",
               4: "Left Trigger", 5: "Right Trigger"}
_TRIGGERS = (4, 5)

_PINSCAPE = "mjrnet Pinscape Controller"
_PINSCAPE_AXES = ("Nudge X Acceleration", "Nudge Y Acceleration", "Plunger Position",
                  "Nudge X Velocity", "Nudge Y Velocity", "Plunger Velocity")
# Arcade2TV-XR: the two modes of the dongle and of the wired unit.
_ARCADE_PINBALL = ("0300fa665e0400008e02000002037801", "0300fa675e0400008e02000040037801")
_ARCADE_RIGHT = ("0300ba675e0400008e02000002037801", "0300ba665e0400008e02000040037801")
_ARCADE_AXES = ("Left Stick X / Nudge X", "Left Stick Y / Nudge Y",
                "Left But. 7 / Left But. 8", "Right Stick X", "Right Stick Y / Plunger",
                "No idea...")
_ARCADE_BUTTONS = {
    2: "Left But. 1", 3: "Left But. 2", 4: "Left Flipper / Left But. 3",
    0: "Left & Right But. 4", 1: "Left But. 5", 5: "Right Flipper / Left But. 6",
    6: "Right But. 1", 0x0102: "Right But. 2", 8: "Right But. 3", 0x0103: "Right But. 5",
    9: "Right But. 6", 0x0100: "Right But. 7", 0x0101: "Right But. 8"}


@dataclass(frozen=True)
class Press:
    """A member of a mapping going down or up, as `bindings.controller_of` names it."""

    member: str
    down: bool


def find_library(program: str, system: str | None = None) -> Path | None:
    """The SDL library that ships with the program at `program`, or None.

    `program` is what a launcher's program setting holds: the application bundle on macOS,
    the executable elsewhere. A folder is taken as the folder holding it.
    """
    system = system or sys.platform
    here = Path(program)
    if system == "darwin":
        bundle = next((one for one in (here, *here.parents) if one.suffix == ".app"), None)
        folder = bundle / "Contents" / "Frameworks" if bundle else here
        names, pattern = ("libSDL3.0.dylib", "libSDL3.dylib"), "libSDL3*.dylib"
    elif system == "win32":
        folder = here if here.is_dir() else here.parent
        names, pattern = ("SDL364.dll", "SDL3.dll"), ""
    else:
        folder = here if here.is_dir() else here.parent
        names, pattern = ("libSDL3.so.0", "libSDL3.so"), "libSDL3.so.*"
    for name in names:
        if (folder / name).is_file():
            return folder / name
    if pattern and folder.is_dir():
        return next(iter(sorted(folder.glob(pattern))), None)
    return None


def pe_bits(path: Path) -> int:
    """32 or 64 for a Windows library, 0 where the header cannot be read."""
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
            if head[:2] != b"MZ":
                return 0
            handle.seek(struct.unpack_from("<I", head, 60)[0])
            signature = handle.read(6)
        if signature[:4] != b"PE\0\0":
            return 0
        machine = struct.unpack_from("<H", signature, 4)[0]
    except (OSError, struct.error):
        return 0
    return {0x014C: 32, 0x8664: 64, 0xAA64: 64}.get(machine, 0)


def why_not_loadable(library: Path, system: str | None = None) -> str:
    """Why this process cannot load `library`, or "" where it can try."""
    if (system or sys.platform) != "win32":
        return ""
    ours = struct.calcsize("P") * 8
    theirs = pe_bits(library)
    if theirs and theirs != ours:
        return f"{library.name} is {theirs}-bit and this VPinFE is {ours}-bit"
    return ""


class _Sdl:
    """The few SDL3 calls this uses, with the types they need."""

    def __init__(self, path: Path) -> None:
        self.cdll = ctypes.CDLL(str(path))
        guid = type("GUID", (ctypes.Structure,), {"_fields_": [("data", ctypes.c_uint8 * 16)]})
        self.guid = guid
        pointer, text, integer = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
        uint = ctypes.c_uint32
        calls = [
            ("SDL_SetHint", [text, text], ctypes.c_bool),
            ("SDL_InitSubSystem", [uint], ctypes.c_bool),
            ("SDL_QuitSubSystem", [uint], None),
            ("SDL_GetError", [], text),
            ("SDL_UpdateJoysticks", [], None),
            ("SDL_GetJoysticks", [ctypes.POINTER(integer)], ctypes.POINTER(uint)),
            ("SDL_free", [pointer], None),
            ("SDL_GetJoystickNameForID", [uint], text),
            ("SDL_GetJoystickGUIDForID", [uint], guid),
            ("SDL_GUIDToString", [guid, text, integer], None),
            ("SDL_OpenJoystick", [uint], pointer),
            ("SDL_CloseJoystick", [pointer], None),
            ("SDL_GetNumJoystickAxes", [pointer], integer),
            ("SDL_GetNumJoystickButtons", [pointer], integer),
            ("SDL_GetNumJoystickHats", [pointer], integer),
            ("SDL_GetJoystickButton", [pointer, integer], ctypes.c_bool),
            ("SDL_GetJoystickAxis", [pointer, integer], ctypes.c_int16),
            ("SDL_GetJoystickHat", [pointer, integer], ctypes.c_uint8),
            ("SDL_IsGamepad", [uint], ctypes.c_bool),
            ("SDL_OpenGamepad", [uint], pointer),
            ("SDL_CloseGamepad", [pointer], None),
            ("SDL_GetGamepadBindings", [pointer, ctypes.POINTER(integer)], pointer),
            ("SDL_GetGamepadButtonLabel", [pointer, integer], integer),
        ]
        try:
            for name, argtypes, restype in calls:
                function = getattr(self.cdll, name)
                function.argtypes = argtypes
                function.restype = restype
        except AttributeError:
            self.unload()
            raise

    def error(self) -> str:
        return (self.cdll.SDL_GetError() or b"").decode("utf-8", "replace")

    def start(self) -> str:
        self.cdll.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if not self.cdll.SDL_InitSubSystem(_INIT_JOYSTICK | _INIT_GAMEPAD):
            return self.error() or "SDL would not start"
        return ""

    def stop(self) -> None:
        self.cdll.SDL_QuitSubSystem(_INIT_JOYSTICK | _INIT_GAMEPAD)

    def unload(self) -> None:
        handle = self.cdll._handle
        try:
            import _ctypes
            if sys.platform == "win32":
                _ctypes.FreeLibrary(handle)
            else:
                _ctypes.dlclose(handle)
        except (ImportError, OSError, AttributeError):
            log.debug("SDL stays loaded", exc_info=True)

    def joystick_ids(self) -> list[int]:
        count = ctypes.c_int(0)
        found = self.cdll.SDL_GetJoysticks(ctypes.byref(count))
        if not found:
            return []
        try:
            return [found[index] for index in range(count.value)]
        finally:
            self.cdll.SDL_free(found)

    def guid_of(self, joystick_id: int) -> str:
        buffer = ctypes.create_string_buffer(33)
        self.cdll.SDL_GUIDToString(self.cdll.SDL_GetJoystickGUIDForID(joystick_id), buffer, 33)
        return buffer.value.decode("ascii")

    def name_of(self, joystick_id: int) -> str:
        return (self.cdll.SDL_GetJoystickNameForID(joystick_id) or b"").decode("utf-8", "replace")

    def gamepad_names(self, joystick_id: int) -> tuple[dict[int, str], dict[int, tuple[int, int]]]:
        """What SDL calls this controller's elements, if it knows the controller.

        Returns the names by element number, and for each axis that is a stick or a
        trigger `(gamepad axis, rest value in thousandths)`.
        """
        names: dict[int, str] = {}
        axes: dict[int, tuple[int, int]] = {}
        if not self.cdll.SDL_IsGamepad(joystick_id):
            return names, axes
        gamepad = self.cdll.SDL_OpenGamepad(joystick_id)
        if not gamepad:
            return names, axes
        try:
            count = ctypes.c_int(0)
            table = self.cdll.SDL_GetGamepadBindings(gamepad, ctypes.byref(count))
            if not table:
                return names, axes
            try:
                pointers = ctypes.cast(table, ctypes.POINTER(ctypes.POINTER(ctypes.c_int * 8)))
                for index in range(count.value):
                    self._one_binding(gamepad, list(pointers[index].contents), names, axes)
            finally:
                self.cdll.SDL_free(table)
        finally:
            self.cdll.SDL_CloseGamepad(gamepad)
        return names, axes

    def _one_binding(self, gamepad: int, binding: list[int], names: dict[int, str],
                     axes: dict[int, tuple[int, int]]) -> None:
        in_kind, in_a, in_b, _, out_kind, out_a, *_ = binding
        if out_kind == _BIND_BUTTON:
            name = self._button_name(gamepad, out_a)
            if not name:
                return
            if in_kind == _BIND_BUTTON:
                names[in_a] = name
            elif in_kind == _BIND_HAT:
                for number, (mask, _) in enumerate(_HAT_DIRECTIONS):
                    if in_b == mask:
                        names[_HAT_BASE | (in_a * 4 + number)] = name
        elif out_kind == _BIND_AXIS and in_kind == _BIND_AXIS:
            if out_a in _AXIS_NAMES:
                names[_AXIS_BASE | in_a] = _AXIS_NAMES[out_a]
                axes[in_a] = (out_a, in_b)

    def _button_name(self, gamepad: int, button: int) -> str:
        label = self.cdll.SDL_GetGamepadButtonLabel(gamepad, button)
        return _LABELS.get(label) or _BUTTON_NAMES.get(button, "")


@dataclass
class _Pad:
    """One open controller and what it last read."""

    joystick_id: int
    handle: int
    device: str
    info: DeviceInfo
    axes: dict[int, tuple[int, int]]
    buttons: list[bool] = field(default_factory=list)
    hats: list[int] = field(default_factory=list)
    down: dict[tuple[int, str], str] = field(default_factory=dict)


def _device_ids(sdl: _Sdl, ids: list[int]) -> dict[int, tuple[str, str, str]]:
    """Each joystick's id for the program's file, its name and its guid.

    Both counts start at 1 and count the joysticks listed before it with the same guid,
    and with the same name.
    """
    seen: list[tuple[str, str]] = []
    found: dict[int, tuple[str, str, str]] = {}
    for joystick_id in ids:
        guid, name = sdl.guid_of(joystick_id), sdl.name_of(joystick_id)
        id_index = 1 + sum(1 for one, _ in seen if one == guid)
        name_index = 1 + sum(1 for _, other in seen if other == name)
        seen.append((guid, name))
        shown = f"{name} #{name_index}"
        if guid in _ARCADE_PINBALL:
            shown = f"Arcade2TV-XR #{id_index}"
        elif guid in _ARCADE_RIGHT:
            shown = f"Arcade2TV-XR Right #{id_index}"
        found[joystick_id] = (f"SDLJoy_{guid}_{id_index}", shown, guid)
    return found


def _open(sdl: _Sdl, joystick_id: int, device: str, name: str, guid: str) -> _Pad | None:
    handle = sdl.cdll.SDL_OpenJoystick(joystick_id)
    if not handle:
        return None
    axis_count = sdl.cdll.SDL_GetNumJoystickAxes(handle)
    elements: dict[int, tuple[bool, str]] = {
        _AXIS_BASE | axis: (True, f"Axis #{axis}") for axis in range(axis_count)}
    given, axes = sdl.gamepad_names(joystick_id)
    for number, label in given.items():
        elements[number] = (number & _AXIS_BASE == _AXIS_BASE, label)
    if name.startswith(_PINSCAPE) and axis_count >= 6:
        for axis, label in enumerate(_PINSCAPE_AXES):
            elements[_AXIS_BASE | axis] = (True, label)
    elif guid in _ARCADE_PINBALL:
        for axis, label in enumerate(_ARCADE_AXES):
            elements[_AXIS_BASE | axis] = (True, label)
        elements.update({number: (False, label) for number, label in _ARCADE_BUTTONS.items()})
    elif guid in _ARCADE_RIGHT:
        axes = {}
    info = DeviceInfo(name, tuple((number, axis, label)
                                  for number, (axis, label) in sorted(elements.items())))
    return _Pad(joystick_id, handle, device, info, axes,
                [False] * sdl.cdll.SDL_GetNumJoystickButtons(handle),
                [0] * sdl.cdll.SDL_GetNumJoystickHats(handle))


def _changes(sdl: _Sdl, pad: _Pad) -> list[Press]:
    """What changed on this controller since it was last read."""
    out: list[Press] = []
    read = sdl.cdll
    for button, was in enumerate(pad.buttons):
        now = bool(read.SDL_GetJoystickButton(pad.handle, button))
        if now != was:
            pad.buttons[button] = now
            out.append(Press(bindings.controller_of(pad.device, button), now))
    for hat, before in enumerate(pad.hats):
        value = int(read.SDL_GetJoystickHat(pad.handle, hat))
        for number, (mask, _) in enumerate(_HAT_DIRECTIONS):
            if bool(value & mask) != bool(before & mask):
                out.append(Press(bindings.controller_of(
                    pad.device, _HAT_BASE | (hat * 4 + number)), bool(value & mask)))
        pad.hats[hat] = value
    for axis, (kind, rest) in pad.axes.items():
        value = read.SDL_GetJoystickAxis(pad.handle, axis) / 32768
        out += _axis_changes(pad, axis, value, kind in _TRIGGERS, rest / 32768)
    return out


def _axis_changes(pad: _Pad, axis: int, value: float, trigger: bool,
                  rest: float) -> list[Press]:
    """A trigger that rests at the bottom of its range is down past 0, any other past one
    half; a stick is down past one half on either side."""
    element = _AXIS_BASE | axis
    if trigger and rest <= -0.5:
        sides = [("", 0.0, False, value > 0, value < -_TRIGGER_LET_GO)]
    elif trigger:
        sides = [("", _STICK_AT, False, value >= _STICK_AT, value < _STICK_LET_GO)]
    else:
        sides = [("+", _STICK_AT, False, value >= _STICK_AT, value < _STICK_LET_GO),
                 ("-", -_STICK_AT, True, value <= -_STICK_AT, value > -_STICK_LET_GO)]
    out: list[Press] = []
    for side, threshold, reversed_, past, let_go in sides:
        member = bindings.controller_of(pad.device, element, threshold, reversed_)
        if (axis, side) not in pad.down and past:
            pad.down[(axis, side)] = member
            out.append(Press(member, True))
        elif (axis, side) in pad.down and let_go:
            out.append(Press(pad.down.pop((axis, side)), False))
    return out


def _release_all(pad: _Pad) -> list[Press]:
    """The presses that undo what a controller held when it went away."""
    out = [Press(bindings.controller_of(pad.device, button), False)
           for button, held in enumerate(pad.buttons) if held]
    for hat, value in enumerate(pad.hats):
        out += [Press(bindings.controller_of(pad.device, _HAT_BASE | (hat * 4 + number)), False)
                for number, (mask, _) in enumerate(_HAT_DIRECTIONS) if value & mask]
    out += [Press(member, False) for member in pad.down.values()]
    return out




class Tap:
    """One listener's view of a running reader: its own queue of presses."""

    def __init__(self, reader: _Reader) -> None:
        self._reader = reader
        self._queue: queue.SimpleQueue[Press] = queue.SimpleQueue()

    def put(self, press: Press) -> None:
        self._queue.put(press)

    def drain(self) -> list[Press]:
        out: list[Press] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                return out

    def devices(self) -> dict[str, DeviceInfo]:
        """The controllers plugged in, by the id the program's file uses."""
        return dict(self._reader.devices)

    def close(self) -> None:
        _detach(self._reader, self)


class _Reader:
    def __init__(self, library: Path) -> None:
        self.library = library
        self.devices: dict[str, DeviceInfo] = {}
        self.taps: list[Tap] = []
        self.error = ""
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="vpx-controllers", daemon=True)

    def start(self) -> str:
        self._thread.start()
        if not self._ready.wait(_LOAD_WAIT):
            self._stop.set()
            return "SDL took too long to start"
        return self.error

    def settled(self) -> str:
        """Why the reader did not start, once it has either started or failed."""
        self._ready.wait(_LOAD_WAIT)
        return self.error

    def dead(self) -> bool:
        return self._thread.ident is not None and not self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(1)

    def _push(self, presses: list[Press]) -> None:
        for tap in list(self.taps):
            for press in presses:
                tap.put(press)

    def _run(self) -> None:
        sdl: _Sdl | None = None
        try:
            sdl = _Sdl(self.library)
            self.error = sdl.start()
        except (OSError, AttributeError) as exc:
            self.error = f"{self.library.name} did not load: {exc}"
        self._ready.set()
        if sdl is None:
            return
        if self.error:
            sdl.unload()
            return
        pads: dict[int, _Pad] = {}
        try:
            self._loop(sdl, pads)
        except Exception as exc:  # noqa: BLE001 - a listener that died says so to the next ask
            log.warning("Controller listener stopped", exc_info=True)
            self.error = f"the controller listener stopped: {exc}"
        finally:
            for pad in pads.values():
                sdl.cdll.SDL_CloseJoystick(pad.handle)
            self.devices = {}
            sdl.stop()
            sdl.unload()

    def _loop(self, sdl: _Sdl, pads: dict[int, _Pad]) -> None:
        next_scan = 0.0
        while not self._stop.is_set():
            sdl.cdll.SDL_UpdateJoysticks()
            if time.monotonic() >= next_scan:
                self._scan(sdl, pads)
                next_scan = time.monotonic() + _RESCAN
            for pad in pads.values():
                changes = _changes(sdl, pad)
                if changes:
                    self._push(changes)
            self._stop.wait(_TICK)

    def _scan(self, sdl: _Sdl, pads: dict[int, _Pad]) -> None:
        listed = _device_ids(sdl, sdl.joystick_ids())
        for joystick_id in [one for one in pads if one not in listed]:
            gone = pads.pop(joystick_id)
            self._push(_release_all(gone))
            sdl.cdll.SDL_CloseJoystick(gone.handle)
        for joystick_id, (device, name, guid) in listed.items():
            if joystick_id in pads:
                pads[joystick_id].device = device
                continue
            opened = _open(sdl, joystick_id, device, name, guid)
            if opened:
                pads[joystick_id] = opened
        self.devices = {pad.device: pad.info for pad in pads.values()}


_lock = threading.Lock()
_readers: dict[Path, _Reader] = {}


def _detach(reader: _Reader, tap: Tap) -> None:
    with _lock:
        if tap in reader.taps:
            reader.taps.remove(tap)
        if reader.taps or _readers.get(reader.library) is not reader:
            return
        del _readers[reader.library]
    reader.stop()


def listen(program: str) -> tuple[Tap | None, str]:
    """Start hearing controllers through the library beside `program`.

    Blocks while the library loads, so call it off the event loop. Returns the tap to
    read presses from and close, or None and why it could not be had. Two listeners of
    one program share one reader.
    """
    library = find_library(program)
    if library is None:
        return None, f"no SDL library beside {program or 'the program'}"
    refusal = why_not_loadable(library)
    if refusal:
        return None, refusal
    with _lock:
        reader = _readers.get(library)
        if reader is not None and reader.dead():
            del _readers[library]
            reader = None
        if reader is None:
            reader = _Reader(library)
            _readers[library] = reader
            fresh = True
        else:
            fresh = False
        tap = Tap(reader)
        reader.taps.append(tap)
    if fresh:
        error = reader.start()
        if error:
            with _lock:
                if _readers.get(library) is reader:
                    del _readers[library]
            reader.stop()
            return None, error
    else:
        error = reader.settled()
        if error:
            tap.close()
            return None, error
    return tap, ""


def label_of(member: str, devices: Mapping[str, DeviceInfo]) -> tuple[str, str]:
    """What a chip says for a controller member, and what its tooltip says.

    The label is the program's name for the element; the tooltip is
    "<controller> - <element>" as the program shows it. A controller the program has no
    record of is shown by its id.
    """
    device, element, _, reversed_ = bindings.controller_part(member)
    info = devices.get(device)
    named = {number: (axis, name) for number, axis, name in info.elements} if info else {}
    axis, name = named.get(element) or _generic(element)
    label = _with_side(name, reversed_, axis)
    return label, f"{info.name if info else device} - {label}"


def _generic(element: int) -> tuple[bool, str]:
    if element & _AXIS_BASE == _AXIS_BASE:
        return True, f"Axis #{element & 0xFF}"
    if element & _HAT_BASE == _HAT_BASE:
        hat, direction = divmod(element & 0xFF, 4)
        word = _HAT_DIRECTIONS[direction][1]
        return False, f"Hat {word}" if hat == 0 else f"Hat #{hat} {word}"
    return False, f"Button #{element}"


_SIDES = {"X": ("Left", "Right"), "Y": ("Up", "Down")}


def _with_side(name: str, reversed_: bool, axis: bool) -> str:
    """A stick pushed one way is a different press from the other way, so it says which."""
    if not axis or name.endswith("Trigger"):
        return name
    if name.endswith((" Stick X", " Stick Y")):
        low, high = _SIDES[name[-1]]
        return f"{name[:-1]}{low if reversed_ else high}"
    return f"{name} {'-' if reversed_ else '+'}"

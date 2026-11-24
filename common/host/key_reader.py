"""Hearing the keyboard while a table runs, for the actions a player uses during play.

The frontend hears a key only while its page has focus, and during play the table has it.
So while a table runs, and only where a key is bound to Take Picture, this reads the
keyboard from outside the page - the keys bound to the play-time actions and no others -
and answers those actions in the host itself (`common/host/input_actions.py`).

Windows, X11 and macOS are read through pynput; a Wayland session from its input devices,
which its user reads only in the `input` group.
"""

from __future__ import annotations

import logging
import os
import select
import struct
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from common import events
from common.config import input_registry
from common.host import input_actions

from . import keys, launch_state, tools

logger = logging.getLogger("vpinfe.common.host.key_reader")

SOURCE = "keyboard"

PYNPUT = "pynput"
EVDEV = "evdev"

NOT_HEARD = "keys.hear.not_heard"
NO_INPUT_GROUP = "keys.hear.no_input_group"
JOIN_INPUT_GROUP = "keys.hear.join_input_group"
NO_INPUT_MONITORING = "keys.hear.no_input_monitoring"
ALLOW_INPUT_MONITORING = "keys.hear.grant_monitoring"
NO_SESSION = "keys.hear.no_session"

DEVICES = "/proc/bus/input/devices"

# struct input_event: a timeval, then type, code and value.
_EVENT = struct.Struct("llHHi")
_EV_KEY = 1
_KEY_DOWN = 1

# How long a read waits before looking again whether it has been stopped.
_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class Hearing:
    """How keys are heard here during play, or why they cannot be."""

    via: str
    reason: Mapping[str, Any] | None = None

    @property
    def available(self) -> bool:
        return bool(self.via)

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "via": self.via,
                "reason": dict(self.reason) if self.reason else None}


def _reason(key: str, remedy: str = "") -> dict[str, Any]:
    return {"key": key, "params": {}, "fix": tools.FIX_USER if remedy else tools.FIX_NONE,
            "remedy": {"key": remedy, "params": {}} if remedy else None}


def words(said: Mapping[str, Any]) -> str:
    from common.i18n import t

    text = t(str(said.get("key") or ""), **dict(said.get("params") or {}))
    remedy = said.get("remedy")
    return t("keys.send.with_remedy", reason=text, remedy=tools.words(remedy)) \
        if remedy else text


def keyboards(listing: str = DEVICES,
              readable: Callable[[str], bool] = lambda path: os.access(path, os.R_OK),
              ) -> list[str]:
    """The keyboards among this device's input devices that this process can read."""
    try:
        with open(listing, encoding="utf-8", errors="replace") as held:
            said = held.read()
    except OSError:
        return []
    found = []
    for block in said.split("\n\n"):
        handlers = next((line.split("=", 1)[1].split() for line in block.splitlines()
                         if line.startswith("H: Handlers=")), [])
        if "kbd" not in handlers:
            continue
        found += [f"/dev/input/{name}" for name in handlers
                  if name.startswith("event") and readable(f"/dev/input/{name}")]
    return found


def _listen_access() -> bool:
    """Whether macOS lets this process listen to the keyboard. Asks without prompting."""
    try:
        import Quartz

        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:  # noqa: BLE001 - an answer nobody could give is a no
        return False


def hearing(env: Mapping[str, str] | None = None, system: str | None = None,
            devices: Callable[[], list[str]] = keyboards,
            listen_access: Callable[[], bool] = _listen_access) -> Hearing:
    """How this device would hear a key during play, read off the session."""
    env = os.environ if env is None else env
    system = tools.here() if system is None else system
    if system == tools.WINDOWS:
        return Hearing(PYNPUT)
    if system == tools.DARWIN:
        return Hearing(PYNPUT) if listen_access() else Hearing(
            "", _reason(NO_INPUT_MONITORING, ALLOW_INPUT_MONITORING))
    if env.get("WAYLAND_DISPLAY"):
        return Hearing(EVDEV) if devices() else Hearing(
            "", _reason(NO_INPUT_GROUP, JOIN_INPUT_GROUP))
    if env.get("DISPLAY"):
        return Hearing(PYNPUT)
    return Hearing("", _reason(NO_SESSION))


def wanted(bound: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    """The keys to listen for, by code, and the actions each presses. Empty unless a key
    is bound to the first play-time action: the rest only answer it."""
    first, *_ = input_registry.IN_PLAY
    if not input_registry.keys_in(bound.get(first)):
        return {}
    out: dict[str, list[str]] = {}
    for action in input_registry.IN_PLAY:
        for name in input_registry.keys_in(bound.get(action)):
            code = input_registry.normalize(f"{input_registry.KEY_PREFIX}{name}")
            out.setdefault(code[len(input_registry.KEY_PREFIX):], []).append(action)
    return out


# pynput's names for keys, where they are not the key simulator's own.
_PYNPUT_ALIASES = {"shift": "ShiftLeft", "ctrl": "ControlLeft", "alt": "AltLeft",
                   "alt_gr": "AltRight", "cmd_l": "MetaLeft", "caps_lock": "CapsLock",
                   "num_lock": "NumLock", "scroll_lock": "ScrollLock",
                   "menu": "ContextMenu"}


def code_of_pynput(key: Any) -> str:
    """The code of a key pynput reports: a named `Key`, or a `KeyCode` by what it types."""
    name = getattr(key, "name", None)
    if isinstance(name, str):
        if name in _PYNPUT_ALIASES:
            return _PYNPUT_ALIASES[name]
        if (found := keys.BY_KEY_ID.get(name)) is not None:
            return found.code
        return name.upper() if name[:1] == "f" and name[1:].isdigit() else ""
    char = str(getattr(key, "char", "") or "").lower()
    found = keys.BY_KEY_ID.get(char) if len(char) == 1 else None
    return found.code if found else ""


class Reader:
    """One table's worth of listening. `press` stands in for `input_actions.play`."""

    def __init__(self, heard: Hearing, listening: Mapping[str, list[str]], *,
                 press: Callable[..., Any] = input_actions.play,
                 devices: Callable[[], list[str]] = keyboards,
                 listener: Callable[..., Any] | None = None) -> None:
        self.heard = heard
        self.listening = dict(listening)
        self._press = press
        self._devices = devices
        self._listener = listener
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._pynput: Any = None

    def hear(self, code: str) -> None:
        for action in self.listening.get(code, ()):
            logger.info("Heard %s during play: %s", code, action)
            self._press(action, source=SOURCE)

    def start(self) -> None:
        if self.heard.via == EVDEV:
            self._thread = threading.Thread(target=self._read_devices, daemon=True,
                                            name="play-keys")
            self._thread.start()
        elif self.heard.via == PYNPUT:
            make = self._listener or _pynput_listener
            self._pynput = make(on_press=lambda key: self.hear(code_of_pynput(key)))
            self._pynput.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._pynput is not None:
            self._pynput.stop()
            self._pynput = None
        if self._thread is not None:
            self._thread.join(2 * _POLL_SECONDS)
            self._thread = None

    def _read_devices(self) -> None:
        opened: dict[int, str] = {}
        for path in self._devices():
            try:
                opened[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
            except OSError as exc:
                logger.warning("Could not read %s during play: %s", path, exc)
        try:
            self.read(list(opened))
        finally:
            for fd in opened:
                os.close(fd)

    def read(self, fds: list[int]) -> None:
        """Keys going down on `fds`, until stopped. Open file descriptors of input devices,
        or anything else that yields their events."""
        pending = {fd: b"" for fd in fds}
        while pending and not self._stopped.is_set():
            ready, _, _ = select.select(list(pending), [], [], _POLL_SECONDS)
            for fd in ready:
                try:
                    chunk = os.read(fd, _EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    pending.pop(fd, None)
                    continue
                held = pending[fd] + chunk
                whole = len(held) - len(held) % _EVENT.size
                for _sec, _usec, kind, number, value in _EVENT.iter_unpack(held[:whole]):
                    if kind == _EV_KEY and value == _KEY_DOWN:
                        self.hear(keys.code_of_evdev(number))
                pending[fd] = held[whole:]


def event(number: int, value: int = _KEY_DOWN, kind: int = _EV_KEY) -> bytes:
    """One input event as a device writes it."""
    return _EVENT.pack(0, 0, kind, number, value)


def _pynput_listener(**handlers: Any) -> Any:
    from pynput import keyboard

    return keyboard.Listener(**handlers)


# --- while a table runs -------------------------------------------------------------

_lock = threading.Lock()
_running: Reader | None = None
_registered = False
_said: set[str] = set()


def _bound() -> dict[str, list[str]]:
    from common.config.paths import get_ini_config

    return input_registry.bound(get_ini_config())


def on_launched(**payload: Any) -> None:
    global _running
    if payload.get("source") == launch_state.SOURCE_CAPTURE:
        return
    listening = wanted(_bound())
    if not listening:
        return
    heard = hearing()
    if not heard.available:
        said = words(heard.reason or {})
        if said not in _said:
            _said.add(said)
            logger.warning("A key is bound to Take Picture, but keys can't be heard while "
                           "a table is running here: %s", said)
        return
    reader = Reader(heard, listening)
    with _lock:
        if _running is not None:
            _running.stop()
        _running = reader
    logger.info("Listening during play for %s, through %s", ", ".join(sorted(listening)),
                heard.via)
    reader.start()


def on_exited(**_payload: Any) -> None:
    global _running
    with _lock:
        reader, _running = _running, None
    if reader is not None:
        reader.stop()


def register() -> None:
    """Listen while tables run. Idempotent."""
    global _registered
    if _registered:
        return
    events.subscribe(events.TABLE_LAUNCHED, on_launched)
    events.subscribe(events.TABLE_EXITED, on_exited)
    _registered = True


def reset_for_tests() -> None:
    global _registered
    on_exited()
    events.unsubscribe(events.TABLE_LAUNCHED, on_launched)
    events.unsubscribe(events.TABLE_EXITED, on_exited)
    _registered = False
    _said.clear()

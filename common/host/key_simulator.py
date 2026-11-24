"""Pressing a key on this device's behalf, into whatever table is running.

Wayland will not let one process type into another, so a Wayland session presses through
a Tool - wtype, or ydotool and its service - and everything else through pynput.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import bundled_libraries, keys, tools

logger = logging.getLogger("vpinfe.common.host.key_simulator")


def _quartz_controller() -> Any:
    """The macOS typer, built on first use so that importing this module loads neither
    pynput nor Quartz."""
    from pynput.keyboard import Key
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        kCGHIDEventTap,
    )

    keycodes = {
        Key.enter: 36, Key.esc: 53, Key.backspace: 51, Key.tab: 48, Key.space: 49,
        Key.f1: 122, Key.f2: 120, Key.f3: 99, Key.f4: 118, Key.f5: 96, Key.f6: 97,
        Key.f7: 98, Key.f8: 100, Key.f9: 101, Key.f10: 109, Key.f11: 103, Key.f12: 111,
        Key.home: 115, Key.page_up: 116, Key.delete: 117, Key.end: 119, Key.page_down: 121,
        Key.right: 124, Key.left: 123, Key.down: 125, Key.up: 126,
        Key.ctrl_l: 59, Key.shift_l: 56, Key.alt_l: 58, Key.cmd: 55,
        Key.ctrl_r: 62, Key.shift_r: 60, Key.alt_r: 61,
        'a': 0, 'b': 11, 'c': 8, 'd': 2, 'e': 14, 'f': 3, 'g': 5, 'h': 4,
        'i': 34, 'j': 38, 'k': 40, 'l': 37, 'm': 46, 'n': 45, 'o': 31, 'p': 35,
        'q': 12, 'r': 15, 's': 1, 't': 17, 'u': 32, 'v': 9, 'w': 13, 'x': 7,
        'y': 16, 'z': 6,
        '1': 18, '2': 19, '3': 20, '4': 21, '5': 23, '6': 22, '7': 26, '8': 28,
        '9': 25, '0': 29,
        '-': 27, '=': 24, '[': 33, ']': 30, '\\': 42, ';': 41, "'": 39,
        '`': 50, ',': 43, '.': 47, '/': 44,
    }

    class QuartzKeyboardController:
        def press(self, key: Any) -> None:
            keycode = keycodes.get(key)
            if keycode is not None:
                event = CGEventCreateKeyboardEvent(None, keycode, True)
                CGEventPost(kCGHIDEventTap, event)

        def release(self, key: Any) -> None:
            keycode = keycodes.get(key)
            if keycode is not None:
                event = CGEventCreateKeyboardEvent(None, keycode, False)
                CGEventPost(kCGHIDEventTap, event)

    return QuartzKeyboardController()


def _controller() -> Any:
    """What types, built when something first types: pynput is imported here rather than
    at module scope, so a machine with no input backend can still import this module."""
    if sys.platform == "darwin":
        return _quartz_controller()
    from pynput.keyboard import Controller

    return Controller()


Run = Callable[..., Any]


class Backend(Protocol):
    # Whether `down` and `up` can be two separate calls. A tool that lets go of every key
    # when its process ends cannot.
    holds: bool

    def press(self, key_id: Any) -> bool: ...

    def down(self, key_id: Any) -> bool: ...

    def up(self, key_id: Any) -> bool: ...

    def hold(self, key_id: Any, seconds: float = 1) -> bool: ...

    def combo(self, *key_ids: Any) -> bool: ...


class PynputKeyboardBackend:
    """Types via pynput. Works under X11; Wayland will not let it reach another window."""

    holds = True

    def __init__(self, key_map: Mapping[Any, Any]) -> None:
        self.key_map = key_map
        self._keyboard: Any = None

    @property
    def keyboard(self) -> Any:
        if self._keyboard is None:
            self._keyboard = _controller()
        return self._keyboard

    def _translate(self, key_id: Any) -> Any:
        return self.key_map.get(key_id)

    def press(self, key_id: Any) -> bool:
        key = self._translate(key_id)
        if key is None:
            return False
        self.keyboard.press(key)
        self.keyboard.release(key)
        return True

    def down(self, key_id: Any) -> bool:
        key = self._translate(key_id)
        if key is None:
            return False
        self.keyboard.press(key)
        return True

    def up(self, key_id: Any) -> bool:
        key = self._translate(key_id)
        if key is None:
            return False
        self.keyboard.release(key)
        return True

    def hold(self, key_id: Any, seconds: float = 1) -> bool:
        if not self.down(key_id):
            return False
        time.sleep(seconds)
        return self.up(key_id)

    def combo(self, *key_ids: Any) -> bool:
        translated = [self._translate(key_id) for key_id in key_ids]
        if any(key is None for key in translated):
            return False
        for key in translated:
            self.keyboard.press(key)
        for key in reversed(translated):
            self.keyboard.release(key)
        return True


def _ran(tool: str, argv: list[str], runner: Run, env: Mapping[str, str] | None = None,
         debug: bool = False) -> bool:
    if debug:
        logger.debug("Running %s: %s", tool, argv)
    try:
        runner(argv, check=True, capture_output=True, text=True,
               stdin=subprocess.DEVNULL, timeout=tools.TIMEOUT,
               env=bundled_libraries.for_child(env))
        return True
    except OSError as exc:
        logger.warning("%s could not be started: %s", tool, exc)
    except subprocess.TimeoutExpired:
        logger.warning("%s did not finish in %ss", tool, tools.TIMEOUT)
    except subprocess.CalledProcessError as exc:
        said = (exc.stderr or "").strip()
        logger.warning("%s failed: %s", tool, said or f"exit code {exc.returncode}")
    return False


class WtypeKeyboardBackend:
    """Types via wtype. Keys are named by their XKB keysym. Its virtual keyboard goes with
    the process, so a key it pressed is let go when the command ends and cannot be held
    across two."""

    holds = False

    def __init__(self, path: Path, runner: Run = subprocess.run, debug: bool = False) -> None:
        self.path = path
        self.runner = runner
        self.debug = debug

    @staticmethod
    def _keysym(key_id: Any) -> str:
        key = keys.BY_KEY_ID.get(key_id) if isinstance(key_id, str) else None
        return key.keysym if key else ""

    def _run(self, *args: str) -> bool:
        return _ran("wtype", [str(self.path), *args], self.runner, debug=self.debug)

    def press(self, key_id: Any) -> bool:
        keysym = self._keysym(key_id)
        return self._run("-k", keysym) if keysym else False

    def down(self, key_id: Any) -> bool:
        return False

    def up(self, key_id: Any) -> bool:
        return False

    def hold(self, key_id: Any, seconds: float = 1) -> bool:
        keysym = self._keysym(key_id)
        if not keysym:
            return False
        return self._run("-P", keysym, "-s", str(int(seconds * 1000)), "-p", keysym)

    def combo(self, *key_ids: Any) -> bool:
        keysyms = [self._keysym(key_id) for key_id in key_ids]
        if not keysyms or not all(keysyms):
            return False
        args = [arg for keysym in keysyms for arg in ("-P", keysym)]
        args += [arg for keysym in reversed(keysyms) for arg in ("-p", keysym)]
        return self._run(*args)


class YdotoolKeyboardBackend:
    """Types via ydotool, through the service socket it was found with."""

    holds = True

    def __init__(self, key_map: Mapping[Any, int], path: Path | str = "ydotool",
                 socket_path: str = "", runner: Run = subprocess.run,
                 debug: bool = False) -> None:
        self.key_map = key_map
        self.path = path
        self.socket_path = socket_path
        self.runner = runner
        self.debug = debug

    def _translate(self, key_id: Any) -> int | None:
        return self.key_map.get(key_id)

    def _run_key_sequence(self, key_args: list[str]) -> bool:
        env = bundled_libraries.for_child()
        if self.socket_path:
            env["YDOTOOL_SOCKET"] = self.socket_path
        return _ran("ydotool", [str(self.path), "key", *key_args], self.runner, env,
                    self.debug)

    def press(self, key_id: Any) -> bool:
        code = self._translate(key_id)
        if code is None:
            return False
        return self._run_key_sequence([f"{code}:1", f"{code}:0"])

    def down(self, key_id: Any) -> bool:
        code = self._translate(key_id)
        return self._run_key_sequence([f"{code}:1"]) if code is not None else False

    def up(self, key_id: Any) -> bool:
        code = self._translate(key_id)
        return self._run_key_sequence([f"{code}:0"]) if code is not None else False

    def hold(self, key_id: Any, seconds: float = 1) -> bool:
        if not self.down(key_id):
            return False
        time.sleep(seconds)
        return self.up(key_id)

    def combo(self, *key_ids: Any) -> bool:
        codes = [self._translate(key_id) for key_id in key_ids]
        if any(code is None for code in codes):
            return False
        key_args = [f"{code}:1" for code in codes]
        key_args.extend(f"{code}:0" for code in reversed(codes))
        return self._run_key_sequence(key_args)


class UnsentKeyboardBackend:
    """Where this device has no way to press a key. Says why once, then answers False."""

    holds = False

    def __init__(self, reason: Mapping[str, Any]) -> None:
        self.reason = reason
        self._said = False

    def _refuse(self) -> bool:
        if not self._said:
            self._said = True
            logger.warning("No key can be pressed here: %s", words(self.reason))
        return False

    def press(self, key_id: Any) -> bool:
        return self._refuse()

    def down(self, key_id: Any) -> bool:
        return self._refuse()

    def up(self, key_id: Any) -> bool:
        return self._refuse()

    def hold(self, key_id: Any, seconds: float = 1) -> bool:
        return self._refuse()

    def combo(self, *key_ids: Any) -> bool:
        return self._refuse()


# --- which way this device presses keys ---------------------------------------------

PYNPUT = "pynput"
WTYPE = "wtype"
YDOTOOL = "ydotool"

NEEDS_TOOL = "keys.send.needs_tool"
NO_POST_ACCESS = "keys.send.no_post_access"
ALLOW_POST_ACCESS = "keys.send.allow_post_access"
DEVICE_CONTROL_PANE = "keys.send.device_control_pane"
NO_YDOTOOL_SERVICE = "keys.send.no_ydotool_service"
NO_SESSION = "keys.send.no_session"
START_YDOTOOL_SERVICE = "keys.send.start_ydotool_service"


@dataclass(frozen=True)
class Sender:
    """How keys are pressed here, or why they cannot be: `via` empty and a `reason`."""

    via: str
    path: Path | None = None
    socket: str = ""
    reason: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.via)

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "via": self.via,
                "reason": dict(self.reason) if self.reason else None}


def _reason(key: str, params: Mapping[str, str] | None = None,
            remedy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    fix = tools.FIX_USER if remedy else tools.FIX_NONE
    return {"key": key, "params": dict(params or {}), "fix": fix,
            "remedy": dict(remedy) if remedy else None}


def words(said: Mapping[str, Any]) -> str:
    """A reason in this install's language, with its remedy where it has one."""
    from common.i18n import t

    text = t(str(said.get("key") or ""), **dict(said.get("params") or {}))
    remedy = said.get("remedy")
    return t("keys.send.with_remedy", reason=text, remedy=tools.words(remedy)) \
        if remedy else text


def ydotool_socket(env: Mapping[str, str], exists: Callable[[str], bool] = os.path.exists,
                   ) -> str:
    """ydotool's service socket: where ydotool itself looks, then where packaged services
    put it. "" where none is there."""
    named = str(env.get("YDOTOOL_SOCKET") or "").strip()
    if named:
        return named if exists(named) else ""
    runtime = str(env.get("XDG_RUNTIME_DIR") or "").strip()
    uid = os.getuid() if hasattr(os, "getuid") else 0
    places = [f"{runtime}/.ydotool_socket" if runtime else "",
              f"/run/user/{uid}/.ydotool_socket", "/run/ydotool/socket",
              "/tmp/.ydotool_socket"]
    return next((place for place in places if place and exists(place)), "")


def _wlroots(env: Mapping[str, str]) -> bool:
    return bool(env.get("SWAYSOCK") or env.get("HYPRLAND_INSTANCE_SIGNATURE"))


def _post_access() -> bool:
    """Whether macOS lets this process post key events. Asks without prompting.

    Without it `CGEventPost` drops every event and reports nothing, so a press looks sent.
    """
    try:
        import Quartz

        return bool(Quartz.CGPreflightPostEventAccess())
    except Exception:  # noqa: BLE001 - an answer nobody could give is a no
        return False


def _request_post_access() -> bool:
    """Raises macOS's own permission dialog. macOS shows it once per binary; a later call
    returns the standing answer without a dialog."""
    try:
        import Quartz

        return bool(Quartz.CGRequestPostEventAccess())
    except Exception:  # noqa: BLE001 - an answer nobody could give is a no
        return False


def ask_for_post_access(system: str | None = None,
                        post_access: Callable[[], bool] = _post_access,
                        request: Callable[[], bool] = _request_post_access) -> bool:
    """On macOS without the key permission, asks for it. True where a press can be sent.

    Called at start so the dialog arrives while the person is at the machine, not on the
    first press from a phone. Other systems have nothing to ask for and return True.
    """
    system = tools.here() if system is None else system
    if system != tools.DARWIN or post_access():
        return True
    logger.info("macOS has not allowed VPinFE to press keys; asking for it")
    return request()


def _macos_major() -> int:
    try:
        return int(platform.mac_ver()[0].split(".")[0])
    except ValueError:
        return 0


def _post_access_remedy(macos_major: int) -> dict[str, Any]:
    """macOS 27 names the Accessibility pane Device Control and Data Access."""
    key = DEVICE_CONTROL_PANE if macos_major >= 27 else ALLOW_POST_ACCESS
    return {"key": key, "params": {}}


def sender(env: Mapping[str, str] | None = None, system: str | None = None,
           resolve: Callable[[tools.Tool], tools.Found] = tools.resolve,
           exists: Callable[[str], bool] = os.path.exists,
           post_access: Callable[[], bool] = _post_access,
           macos_major: Callable[[], int] = _macos_major) -> Sender:
    """How this device would press a key into a running table, read off the session."""
    env = os.environ if env is None else env
    system = tools.here() if system is None else system
    if system == tools.DARWIN:
        return Sender(PYNPUT) if post_access() else Sender(
            "", reason=_reason(NO_POST_ACCESS, None, _post_access_remedy(macos_major())))
    if system == tools.WINDOWS:
        return Sender(PYNPUT)
    if not env.get("WAYLAND_DISPLAY"):
        return Sender(PYNPUT) if env.get("DISPLAY") else Sender("", reason=_reason(NO_SESSION))
    order = (tools.WTYPE, tools.YDOTOOL) if _wlroots(env) else (tools.YDOTOOL, tools.WTYPE)
    found = {tool.id: resolve(tool) for tool in order}
    stopped = False
    for tool in order:
        have = found[tool.id]
        if have.state is not tools.State.FOUND or have.probe is None or not have.probe.works:
            continue
        if tool is tools.WTYPE:
            return Sender(WTYPE, have.path)
        socket = ydotool_socket(env, exists)
        if socket:
            return Sender(YDOTOOL, have.path, socket)
        stopped = True
    if stopped:
        return Sender("", reason=_reason(NO_YDOTOOL_SERVICE, {},
                                         {"key": START_YDOTOOL_SERVICE, "params": {}}))
    first = order[0]
    return Sender("", reason=_reason(NEEDS_TOOL, {"tool": first.name}, tools.remedy(first)))


def press_code(code: str, *, via: Sender | None = None) -> bool:
    """Press one key, named as a binding names it (`KeyP`), on this device."""
    return KeySimulator(sender_found=via).press_code(code)


def hold_code(code: str, seconds: float = 0.1, *, via: Sender | None = None) -> bool:
    """Hold one key down for `seconds`, then let go."""
    return KeySimulator(sender_found=via).hold_code(code, seconds)


def down_code(code: str, *, via: Sender | None = None) -> bool:
    """Press one key and leave it down, until `up_code` for the same key."""
    return KeySimulator(sender_found=via).down_code(code)


def up_code(code: str, *, via: Sender | None = None) -> bool:
    return KeySimulator(sender_found=via).up_code(code)


def can_hold(via: Sender | None = None) -> bool:
    """Whether a key can be left down across calls, which not every way of pressing one
    allows."""
    return KeySimulator(sender_found=via).backend.holds


def can_press(code: str) -> bool:
    """Whether `press_code` can name this key: a few a binding may use, the numpad and
    F13 to F24 among them, have no key id to send."""
    key = keys.BY_CODE.get(code)
    return key is not None and bool(key.key_id)


class KeySimulator:
    """Presses keys on this machine, through whichever backend the session allows.

    `mappings` names keys for `press_mapping`: a name an app gives an action of its own,
    to the code of the key it listens for.
    """

    # Every named key resolves to `Key.<the same name>`, so the map is derived rather
    # than written out. `cmd_r` is the one exception and stays stated.
    NAMED_KEYS = (
        "enter", "esc", "backspace", "tab", "space",
        "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
        "home", "page_up", "delete", "end", "page_down",
        "right", "left", "down", "up",
        "ctrl_l", "shift_l", "alt_l", "cmd", "ctrl_r", "shift_r", "alt_r",
    )

    # Not on macOS, where pynput does not define them.
    NAMED_KEYS_NON_DARWIN = ("print_screen", "pause", "insert")

    # The characters a key id and its pynput value spell the same way.
    LITERAL_KEYS = {
        **{str(n): str(n) for n in range(10)},
        **{chr(c): chr(c) for c in range(ord('a'), ord('z') + 1)},
        **{c: c for c in "-=[]\\;'`,./"},
    }

    @staticmethod
    def key_id_to_pynput() -> dict[str, Any]:
        """The pynput key map, built on first use.

        Keep it a call: every named value is a `pynput.Key`, pynput raises at import where
        there is no input backend, and a class attribute would make this module
        unimportable there.
        """
        from pynput.keyboard import Key

        mapping: dict[str, Any] = dict(KeySimulator.LITERAL_KEYS)
        names: tuple[str, ...] = KeySimulator.NAMED_KEYS
        if sys.platform != "darwin":
            names = (*names, *KeySimulator.NAMED_KEYS_NON_DARWIN)
        mapping.update({name: getattr(Key, name) for name in names})
        # Right command types as command; pynput's `cmd_r` is not used.
        mapping["cmd_r"] = Key.cmd
        return mapping

    KEY_ID_TO_YDOTOOL = {key_id: key.evdev for key_id, key in keys.BY_KEY_ID.items()}

    def __init__(self, debug: bool = False, *, mappings: Mapping[str, str] | None = None,
                 sender_found: Sender | None = None, runner: Run = subprocess.run) -> None:
        self.debug = debug
        self.runner = runner
        self.key_mappings = dict(mappings or {})
        self.sender = sender_found or sender()
        self.backend_name = self.sender.via
        self.backend = self.create_backend()
        if self.debug:
            logger.debug("Input backend: %s, %s key mappings", self.backend_name,
                         len(self.key_mappings))

    def press_mapping(self, name: str, seconds: float = 0) -> bool:
        code = self.key_mappings.get(name)
        time.sleep(seconds)
        if code is None:
            logger.info("No key is mapped to %s", name)
            return False
        return self.press_code(code)

    def hold_mapping(self, name: str, seconds: float = 0.1) -> bool:
        """Hold a mapped key for the specified duration"""
        code = self.key_mappings.get(name)
        if code is None:
            logger.info("No key is mapped to %s", name)
            return False
        return self.hold_code(code, seconds)

    def press(self, key_id: Any) -> bool:
        pressed = self.backend.press(key_id)
        if not pressed and self.debug:
            logger.warning("Unable to press key '%s' using backend '%s'", key_id,
                           self.backend_name)
        return pressed

    def hold(self, key_id: Any, seconds: float = 1) -> bool:
        held = self.backend.hold(key_id, seconds)
        if not held and self.debug:
            logger.warning("Unable to hold key '%s' using backend '%s'", key_id,
                           self.backend_name)
        return held

    def combo(self, *key_ids: Any) -> bool:
        sent = self.backend.combo(*key_ids)
        if not sent and self.debug:
            logger.warning("Unable to send combo %s using backend '%s'", key_ids,
                           self.backend_name)
        return sent

    def _key_id(self, code: str) -> str:
        key = keys.BY_CODE.get(code)
        if key is None or not key.key_id:
            logger.warning("No way to press %s: it is not a key VPinFE can name", code)
            return ""
        return key.key_id

    def press_code(self, code: str) -> bool:
        """Press the key a binding names `code`. False where it has no name here or could
        not be pressed."""
        key_id = self._key_id(code)
        return self.press(key_id) if key_id else False

    def hold_code(self, code: str, seconds: float = 0.1) -> bool:
        key_id = self._key_id(code)
        return self.hold(key_id, seconds) if key_id else False

    def down_code(self, code: str) -> bool:
        key_id = self._key_id(code)
        return self.backend.down(key_id) if key_id else False

    def up_code(self, code: str) -> bool:
        key_id = self._key_id(code)
        return self.backend.up(key_id) if key_id else False

    def create_backend(self) -> Backend:
        found = self.sender
        if found.via == WTYPE and found.path is not None:
            return WtypeKeyboardBackend(found.path, self.runner, self.debug)
        if found.via == YDOTOOL and found.path is not None:
            return YdotoolKeyboardBackend(self.KEY_ID_TO_YDOTOOL, found.path, found.socket,
                                          self.runner, self.debug)
        if found.via == PYNPUT:
            return PynputKeyboardBackend(self.key_id_to_pynput())
        return UnsentKeyboardBackend(found.reason)

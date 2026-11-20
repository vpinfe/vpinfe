"""GNOME on Wayland: who has the keyboard, and giving it to a window.

GNOME Shell is the one program that may list windows or bring one forward, so a small Shell
add-on, the files under `static/gnome-shell/`, does it over the session bus. VPinFE puts the
add-on in the person's own folder and switches it on; GNOME loads it at the next sign-in. The
table stays a native Wayland window.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from common.capture.adapters.portal import bus_address
from common.config.paths import bundled
from common.failures import why
from common.host.front import Window, on_wayland
from common.i18n import t

UUID = "table-focus@vpinfe.local"
SERVICE = "org.vpinfe.Focus"
OBJECT = "/org/vpinfe/Focus"
CALL_TIMEOUT = 2.0
_RUN_TIMEOUT = 5.0

# What `status` answers. WORKING is the one that needs nothing.
NOT_HERE = ""
WORKING = "working"
MISSING = "missing"
SIGN_IN = "sign_in"
OFF = "off"
UNSUPPORTED = "unsupported"
BROKEN = "broken"

_SHELL = "org.gnome.shell"
_ENABLED = "enabled-extensions"
_ALL_OFF = "disable-user-extensions"
_FILES = ("metadata.json", "extension.js")

Run = Callable[[Sequence[str]], tuple[int, str]]


def applies(env: Mapping[str, str]) -> bool:
    """GNOME on Wayland. On X11 GNOME answers the ordinary window requests."""
    return on_wayland(env) and "gnome" in str(env.get("XDG_CURRENT_DESKTOP") or "").lower()


def _source() -> Path:
    return bundled("common", "host", "static", "gnome-shell", UUID)


def _folder(env: Mapping[str, str]) -> Path:
    data = env.get("XDG_DATA_HOME")
    home = Path(data) if data else Path.home() / ".local" / "share"
    return home / "gnome-shell" / "extensions" / UUID


def _run(argv: Sequence[str]) -> tuple[int, str]:
    """The exit code and what the program printed, in English: `status` reads the words.
    `OSError` where it is not there or does not answer in time."""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8",
                              timeout=_RUN_TIMEOUT, check=False,  # noqa: S603
                              env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(why(exc)) from exc
    return done.returncode, done.stdout


def _setting(run: Run, key: str) -> str:
    code, out = run(["gsettings", "get", _SHELL, key])
    if code:
        raise OSError(t("error.front_gnome.unreadable"))
    return out.strip()


def _enabled_list(run: Run) -> list[str]:
    said = _setting(run, _ENABLED).removeprefix("@as").strip()
    try:
        return [str(one) for one in ast.literal_eval(said)]
    except (ValueError, SyntaxError) as exc:
        raise OSError(t("error.front_gnome.unreadable")) from exc


def _shell_major(run: Run) -> int | None:
    try:
        code, out = run(["gnome-shell", "--version"])
    except OSError:
        return None
    found = re.search(r"\d+", out) if not code else None
    return int(found.group()) if found else None


def supported_versions() -> list[int]:
    """The GNOME releases the shipped add-on says it works on."""
    held = json.loads((_source() / "metadata.json").read_text(encoding="utf-8"))
    return [int(one) for one in held["shell-version"]]


class FocusBus:
    """The session bus, and the add-on's name on it."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        address = bus_address(os.environ if env is None else env)
        if not address:
            raise ConnectionRefusedError
        try:
            from jeepney.io.blocking import open_dbus_connection

            self._connection = open_dbus_connection(bus=address)
        except (ImportError, ValueError, RuntimeError) as exc:
            raise OSError(why(exc)) from exc

    def close(self) -> None:
        self._connection.close()

    def _send(self, message: Any, timeout: float) -> Any:
        from jeepney.wrappers import DBusErrorResponse, unwrap_msg

        try:
            return unwrap_msg(self._connection.send_and_get_reply(message, timeout=timeout))
        except DBusErrorResponse as exc:
            raise OSError(why(exc)) from exc

    def owned(self) -> bool:
        from jeepney.bus_messages import message_bus

        return bool(self._send(message_bus.NameHasOwner(SERVICE), CALL_TIMEOUT)[0])

    def call(self, method: str, signature: str = "", body: tuple[Any, ...] = ()) -> Any:
        from jeepney import DBusAddress, new_method_call

        address = DBusAddress(OBJECT, bus_name=SERVICE, interface=SERVICE)
        return self._send(new_method_call(address, method, signature, body), CALL_TIMEOUT)


class GnomeBackend:
    """GNOME Shell, through the add-on."""

    name = "GNOME Shell"

    def __init__(self, bus: Any) -> None:
        self._bus = bus

    def close(self) -> None:
        self._bus.close()

    def _list(self) -> tuple[list[Window], str]:
        said = self._bus.call("List")[0]
        try:
            answer = json.loads(said)
            windows = [Window(str(one["id"]), int(one["pid"]), str(one["title"]))
                       for one in answer["windows"]]
            return windows, str(answer["active"])
        except (ValueError, KeyError, TypeError) as exc:
            raise OSError(f"GNOME sent an answer that is not a window list: {why(exc)}") from exc

    def windows(self) -> list[Window]:
        return self._list()[0]

    def holder(self) -> Window | None:
        windows, active = self._list()
        return next((one for one in windows if one.handle == active), None)

    def hand_over(self, window: Window) -> str:
        try:
            found = self._bus.call("Activate", "u", (int(window.handle),))[0]
        except OSError as exc:
            return why(exc)
        return "" if found else "GNOME has no window by that number"


def backend(env: Mapping[str, str]) -> GnomeBackend | None:
    """None while GNOME is not running the add-on, which is what `status` reports."""
    try:
        bus = FocusBus(env)
    except OSError:
        return None
    try:
        if bus.owned():
            return GnomeBackend(bus)
    except OSError:
        pass
    bus.close()
    return None


def _bus_owned(env: Mapping[str, str]) -> bool:
    found = backend(env)
    if found is None:
        return False
    found.close()
    return True


def status(env: Mapping[str, str], run: Run = _run,
           owned: Callable[[Mapping[str, str]], bool] = _bus_owned) -> str:
    """Whether GNOME lets VPinFE bring the table forward, as one of the states above.

    Where the add-on answers on the bus it is working. Otherwise the folder, GNOME's own
    list of what is switched on and, if GNOME has heard of it, what GNOME says of it tell
    the states apart. An add-on put in place and switched on after GNOME started is only
    heard of at the next sign-in.
    """
    if not applies(env):
        return NOT_HERE
    if owned(env):
        return WORKING
    major = _shell_major(run)
    if major is not None and major not in supported_versions():
        return UNSUPPORTED
    if not all((_folder(env) / name).is_file() for name in _FILES):
        return MISSING
    try:
        switched_on = UUID in _enabled_list(run) and _setting(run, _ALL_OFF) != "true"
    except OSError:
        return BROKEN
    if not switched_on:
        return OFF
    try:
        code, said = run(["gnome-extensions", "info", UUID])
    except OSError:
        return SIGN_IN
    if code:
        return SIGN_IN
    held = re.search(r"State:\s*(.+)", said)
    return BROKEN if held and held.group(1).strip().upper() in ("ERROR", "OUT OF DATE") \
        else SIGN_IN


def allow(env: Mapping[str, str], run: Run = _run) -> str:
    """Puts the add-on in the person's own folder and switches it on, then answers `status`.
    `OSError`, its text fit to show, where this is not GNOME on Wayland, a file cannot be
    written or GNOME will not change its settings."""
    if not applies(env):
        raise OSError(t("error.front_gnome.not_here"))
    folder = _folder(env)
    folder.mkdir(parents=True, exist_ok=True)
    for name in _FILES:
        shutil.copyfile(_source() / name, folder / name)
    held = _enabled_list(run)
    if UUID not in held:
        held.append(UUID)
        code, _out = run(["gsettings", "set", _SHELL, _ENABLED, repr(held)])
        if code:
            raise OSError(t("error.front_gnome.refused"))
    if _setting(run, _ALL_OFF) == "true":
        code, _out = run(["gsettings", "set", _SHELL, _ALL_OFF, "false"])
        if code:
            raise OSError(t("error.front_gnome.refused"))
    return status(env, run)


def why_not(env: Mapping[str, str]) -> str:
    """The log's account of why the add-on is not answering."""
    found = status(env)
    if found == UNSUPPORTED:
        return "this version of GNOME is not one the table focus add-on supports"
    if found == SIGN_IN:
        return "the table focus add-on is in place and GNOME loads it at the next sign-in"
    return ("GNOME is not letting VPinFE bring the table forward, so nothing is watched; "
            "Settings > Behavior has the fix")

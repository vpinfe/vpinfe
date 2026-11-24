"""KDE Plasma on Wayland: who has the keyboard, and giving it to a window.

KWin runs a script it is handed over D-Bus, and a script sees every window and may activate
one. The script reports back with a call to this process's own bus name, so nothing is
installed and no permission is asked. Plasma 5 and 6 name the same things differently, and the
script reads either.
"""

from __future__ import annotations

import itertools
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from common.capture.adapters.portal import bus_address
from common.failures import why
from common.host.front import Window

KWIN = "org.kde.KWin"
SCRIPTING = "/Scripting"
REPORT_PATH = "/vpinfe/front"
REPORT_INTERFACE = "org.vpinfe.Front"
REPORT_TIMEOUT = 3.0
# Windows and the active one are asked together, so a poll is one script, not two. Each poll
# loads a script into the compositor, so it is not made more often than this.
_FRESH_SECONDS = 1.0

# The desktop, panels and pop-ups are never a program's window: with one of them active the
# keyboard is nobody's.
_REPORT = """\
const every = typeof workspace.windowList === "function" ? workspace.windowList()
                                                         : workspace.clientList();
const all = every.filter(w => !(w.desktopWindow || w.dock || w.notification
                                || w.onScreenDisplay));
const front = workspace.activeWindow !== undefined ? workspace.activeWindow
                                                   : workspace.activeClient;
callDBus(%(service)s, %(path)s, %(interface)s, "Report", JSON.stringify({
  active: front ? String(front.internalId) : "",
  windows: all.map(w => ({id: String(w.internalId), pid: w.pid, title: String(w.caption)}))
}));
"""

# KWin evaluates a script after `run` has answered, so the script says when it has finished.
_ACTIVATE = """\
const all = typeof workspace.windowList === "function" ? workspace.windowList()
                                                      : workspace.clientList();
const target = all.find(w => String(w.internalId) === %(id)s);
if (target) {
  if (workspace.activeWindow !== undefined) { workspace.activeWindow = target; }
  else { workspace.activeClient = target; }
}
callDBus(%(service)s, %(path)s, %(interface)s, "Report", "done");
"""


def applies(env: Mapping[str, str]) -> bool:
    return "kde" in str(env.get("XDG_CURRENT_DESKTOP") or "").lower()


def _where(service: str) -> dict[str, str]:
    return {"service": json.dumps(service), "path": json.dumps(REPORT_PATH),
            "interface": json.dumps(REPORT_INTERFACE)}


def report_script(service: str) -> str:
    return _REPORT % _where(service)


def activate_script(window_id: str, service: str) -> str:
    return _ACTIVATE % {"id": json.dumps(window_id), **_where(service)}


class KWinBus:
    """The session bus, and KWin's scripting on it."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        address = bus_address(os.environ if env is None else env)
        if not address:
            raise ConnectionRefusedError
        try:
            from jeepney.io.blocking import open_dbus_connection

            self._connection = open_dbus_connection(bus=address)
        except (ImportError, ValueError, RuntimeError) as exc:
            raise OSError(why(exc)) from exc
        self.service = str(self._connection.unique_name)
        self._names = itertools.count(1)

    def close(self) -> None:
        self._connection.close()

    def _call(self, path: str, interface: str, method: str, signature: str = "",
              body: tuple[Any, ...] = (), *, timeout: float = REPORT_TIMEOUT) -> Any:
        """A refusal by KWin is a `LookupError`; no answer in time is a `TimeoutError`."""
        from jeepney import DBusAddress, new_method_call
        from jeepney.wrappers import DBusErrorResponse, unwrap_msg

        message = new_method_call(DBusAddress(path, bus_name=KWIN, interface=interface),
                                  method, signature, body)
        try:
            return unwrap_msg(self._connection.send_and_get_reply(message, timeout=timeout))
        except DBusErrorResponse as exc:
            raise LookupError(why(exc)) from exc

    def _start(self, ident: int, timeout: float) -> None:
        """Plasma 6 puts a loaded script at /Scripting/Script<n>, Plasma 5 at /<n>."""
        try:
            self._call(f"{SCRIPTING}/Script{ident}", "org.kde.kwin.Script", "run", timeout=timeout)
        except LookupError:
            self._call(f"/{ident}", "org.kde.kwin.Script", "run", timeout=timeout)

    def run(self, source: str) -> str:
        """Run `source` in KWin and return the string it reports back. `OSError` where KWin
        refuses it or nothing is reported within `REPORT_TIMEOUT` in all."""
        from jeepney import MatchRule, new_method_return

        deadline = time.monotonic() + REPORT_TIMEOUT

        def left() -> float:
            return max(deadline - time.monotonic(), 0.1)

        name = f"vpinfe-front-{os.getpid()}-{next(self._names)}"
        handle, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as written:
                written.write(source)
            try:
                ident = int(self._call(SCRIPTING, "org.kde.kwin.Scripting", "loadScript", "ss",
                                       (path, name), timeout=left())[0])
                if ident < 0:
                    raise OSError("KWin already has a script of this name")
                try:
                    rule = MatchRule(type="method_call", path=REPORT_PATH, member="Report")
                    with self._connection.filter(rule) as heard:
                        self._start(ident, left())
                        message = self._connection.recv_until_filtered(heard, timeout=left())
                    with suppress(OSError):
                        self._connection.send_message(new_method_return(message))
                    return str(message.body[0])
                finally:
                    with suppress(OSError, LookupError):
                        self._call(SCRIPTING, "org.kde.kwin.Scripting", "unloadScript", "s",
                                   (name,), timeout=1.0)
            except LookupError as exc:
                raise OSError(why(exc)) from exc
        finally:
            Path(path).unlink(missing_ok=True)


class KWinBackend:
    """Plasma, over KWin's scripting."""

    name = "KDE Plasma (KWin)"

    def __init__(self, bus: Any = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._bus = KWinBus() if bus is None else bus
        self._clock = clock
        self._seen: tuple[float, list[Window], str] | None = None

    def close(self) -> None:
        close = getattr(self._bus, "close", None)
        if close is not None:
            close()

    def _snapshot(self) -> tuple[list[Window], str]:
        if self._seen is not None and self._clock() - self._seen[0] < _FRESH_SECONDS:
            return self._seen[1], self._seen[2]
        said = self._bus.run(report_script(self._bus.service))
        try:
            answer = json.loads(said)
            windows = [Window(str(one["id"]), int(one["pid"]), str(one["title"]))
                       for one in answer["windows"]]
            active = str(answer["active"])
        except (ValueError, KeyError, TypeError) as exc:
            raise OSError(f"KWin sent an answer that is not a window list: {why(exc)}") from exc
        self._seen = (self._clock(), windows, active)
        return windows, active

    def windows(self) -> list[Window]:
        return self._snapshot()[0]

    def holder(self) -> Window | None:
        windows, active = self._snapshot()
        return next((one for one in windows if one.handle == active), None)

    def hand_over(self, window: Window) -> str:
        self._seen = None
        try:
            self._bus.run(activate_script(str(window.handle), self._bus.service))
        except OSError as exc:
            return why(exc)
        return ""

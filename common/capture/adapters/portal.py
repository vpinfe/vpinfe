"""KDE Plasma and GNOME on Wayland: every screen through the desktop's screen-sharing
portal, `org.freedesktop.portal.ScreenCast`, read by GStreamer's pipewiresrc.

Only `choose` may let the portal ask a person anything.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from common import service_errors
from common.atomic_write import write_atomic
from common.config.paths import CAPTURE_DIR
from common.failures import why
from common.host import launch_state, tools
from common.i18n import t
from common.runtime import jobs
from common.timestamps import utc_now_iso

from .. import commands, geometry
from . import NOT_CHOSEN, Output, Screen, Window
from .ffmpeg import x11_outputs

logger = logging.getLogger("vpinfe.common.capture.adapters.portal")

PORTAL = "org.freedesktop.portal.Desktop"
DESKTOP = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"

# Each ScreenCast method's arguments as D-Bus types them. The last is always its options.
SIGNATURES = {"CreateSession": "a{sv}", "SelectSources": "oa{sv}", "Start": "osa{sv}",
              "OpenPipeWireRemote": "oa{sv}"}

MONITOR = 1                 # SelectSources' `types`
UNTIL_REVOKED = 2           # its `persist_mode`
ANSWERED = 0                # a Response's `response`
CANCELLED_BY_PERSON = 1

# A restore answers without anyone; choosing waits for someone to reach the device.
RESTORE_SECONDS = 10.0
CHOOSE_SECONDS = 180.0

FORGOT = "capture.portal.forgot"
NOT_SHARED = "capture.portal.not_shared"
UNREACHABLE = "capture.portal.unreachable"
CANCELLED = "capture.portal.cancelled"
TIMED_OUT = "capture.portal.timed_out"
NOT_KEPT = "capture.portal.not_kept"
CHOOSING = "capture.progress.choosing"
NOTHING_TO_CHOOSE = "error.capture.nothing_to_choose"

# XDG_CURRENT_DESKTOP's word for each desktop, and its name said to people.
DESKTOPS = (("KDE", "KDE Plasma"), ("GNOME", "GNOME"))

KEPT = CAPTURE_DIR / "portal.json"


# --- the session bus ------------------------------------------------------------------

class Bus(Protocol):
    """The session bus in plain Python values. `connect` is the one that speaks D-Bus."""

    @property
    def sender(self) -> str: ...

    def ask(self, method: str, args: Sequence[Any], token: str,
            timeout: float) -> tuple[int, dict[str, Any]]:
        """A ScreenCast method answered on its Request: the response and its results.
        Raises TimeoutError, the request closed, where no answer came in `timeout`."""
        ...

    def call(self, method: str, args: Sequence[Any]) -> Any: ...

    def close_session(self, handle: str) -> None: ...

    def close(self) -> None: ...


def request_path(sender: str, token: str) -> str:
    """Where the portal answers a call made with `handle_token` `token`."""
    return f"{DESKTOP}/request/{sender.lstrip(':').replace('.', '_')}/{token}"


def variants(options: Mapping[str, Any]) -> dict[str, tuple[str, Any]]:
    """Options as `a{sv}` carries them, each typed as the portal documents it."""
    return {key: ("b" if isinstance(value, bool) else "u" if isinstance(value, int)
                  else "s", value) for key, value in options.items()}


def plain(value: Any) -> Any:
    """A reply's values, every `a{sv}` variant unwrapped."""
    if isinstance(value, dict):
        return {key: plain(one[1]) for key, one in value.items()}
    if isinstance(value, list):
        return [plain(one) for one in value]
    if isinstance(value, tuple):
        return tuple(plain(one) for one in value)
    return value


def method_call(method: str, args: Sequence[Any]) -> Any:
    """The message calling `method` on the portal's ScreenCast."""
    from jeepney import DBusAddress, new_method_call

    return new_method_call(DBusAddress(DESKTOP, bus_name=PORTAL, interface=SCREENCAST),
                           method, SIGNATURES[method], (*args[:-1], variants(args[-1])))


def bus_address(env: Mapping[str, str]) -> str:
    runtime = str(env.get("XDG_RUNTIME_DIR") or "")
    return str(env.get("DBUS_SESSION_BUS_ADDRESS") or "") \
        or (f"unix:path={runtime}/bus" if runtime else "")


class _Jeepney:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @property
    def sender(self) -> str:
        return str(self._connection.unique_name)

    def _reply(self, message: Any, timeout: float = tools.TIMEOUT) -> Any:
        from jeepney.wrappers import DBusErrorResponse, unwrap_msg

        try:
            return unwrap_msg(self._connection.send_and_get_reply(message, timeout=timeout))
        except DBusErrorResponse as exc:
            raise OSError(why(exc)) from exc

    def _close(self, path: str, interface: str) -> None:
        from jeepney import DBusAddress, new_method_call

        with suppress(OSError):
            self._reply(new_method_call(DBusAddress(path, bus_name=PORTAL,
                                                    interface=interface), "Close"))

    def ask(self, method: str, args: Sequence[Any], token: str,
            timeout: float) -> tuple[int, dict[str, Any]]:
        from jeepney import MatchRule, message_bus

        path = request_path(self.sender, token)
        rule = MatchRule(type="signal", interface=REQUEST, member="Response", path=path)
        with self._connection.filter(rule) as heard:
            self._reply(message_bus.AddMatch(rule))
            try:
                handle = str(self._reply(method_call(method, args))[0])
                if handle != path:
                    # Only a portal before 0.9 names its own, and none that old restores.
                    raise ConnectionRefusedError(handle)
                try:
                    said = self._connection.recv_until_filtered(heard, timeout=timeout)
                except TimeoutError:
                    self._close(handle, REQUEST)
                    raise
            finally:
                with suppress(OSError):
                    self._reply(message_bus.RemoveMatch(rule))
        response, results = said.body
        return int(response), plain(results)

    def call(self, method: str, args: Sequence[Any]) -> Any:
        [answer] = self._reply(method_call(method, args))
        take = getattr(answer, "to_raw_fd", None)
        return take() if take is not None else plain(answer)

    def close_session(self, handle: str) -> None:
        if handle:
            self._close(handle, SESSION)

    def close(self) -> None:
        self._connection.close()


def connect(env: Mapping[str, str] | None = None) -> Bus:
    """This session's bus, able to hand over file descriptors. Raises OSError where it
    cannot be reached."""
    address = bus_address(os.environ if env is None else env)
    if not address:
        raise ConnectionRefusedError
    try:
        from jeepney.io.blocking import open_dbus_connection

        return _Jeepney(open_dbus_connection(bus=address, enable_fds=True))
    except (ImportError, ValueError, RuntimeError) as exc:
        raise OSError(why(exc)) from exc


# --- one session ----------------------------------------------------------------------

@dataclass(frozen=True)
class Stream:
    """One screen the portal shares: its PipeWire node, and where the desktop lays out
    what it shows."""

    node: int
    position: tuple[int, int] | None
    size: tuple[int, int] | None


def _pair(value: Any) -> tuple[int, int] | None:
    try:
        first, second = value
        return int(first), int(second)
    except (TypeError, ValueError):
        return None


def streams_in(results: Mapping[str, Any]) -> list[Stream]:
    """Start's `streams`: `(node, {position, size, ...})` each."""
    found = []
    for one in results.get("streams") or []:
        try:
            node, properties = one
            number = int(node)
        except (TypeError, ValueError):
            continue
        said = properties if isinstance(properties, dict) else {}
        found.append(Stream(number, _pair(said.get("position")), _pair(said.get("size"))))
    return found


def matched(output: Output, streams: Sequence[Stream]) -> Stream | None:
    """The stream of `output`, by where it is and its size: streams carry no name."""
    return next((one for one in streams if one.position == (output.x, output.y)
                 and one.size == (output.width, output.height)), None)


class PortalError(Exception):
    """No session, as a catalog key. `spent` where the restore token went on the way."""

    def __init__(self, key: str, spent: bool = False) -> None:
        super().__init__(key)
        self.key = key
        self.spent = spent


@dataclass(frozen=True)
class Opened:
    handle: str
    streams: list[Stream]
    # What replaces the kept restore token: "" where the portal gave none.
    restore: str


def _token() -> str:
    return f"vpinfe_{secrets.token_hex(8)}"


def open_session(bus: Bus, restore: str, timeout: float) -> Opened:
    """A session sharing monitors, restored from `restore`, or asking a person where it is
    "". Start is given `timeout`. Raises PortalError, or OSError where the bus fails."""
    first = _token()
    response, results = bus.ask("CreateSession", [{"handle_token": first,
                                                   "session_handle_token": _token()}],
                                first, tools.TIMEOUT)
    handle = str(results.get("session_handle") or "")
    if response != ANSWERED or not handle:
        raise PortalError(UNREACHABLE)
    try:
        chosen: dict[str, Any] = {"handle_token": _token(), "types": MONITOR,
                                  "multiple": True, "persist_mode": UNTIL_REVOKED}
        if restore:
            chosen["restore_token"] = restore
        response, _ = bus.ask("SelectSources", [handle, chosen], chosen["handle_token"],
                              tools.TIMEOUT)
        if response != ANSWERED:
            raise PortalError(UNREACHABLE, spent=bool(restore))
        last = _token()
        try:
            response, results = bus.ask("Start", [handle, "", {"handle_token": last}],
                                        last, timeout)
        except TimeoutError as exc:
            raise PortalError(FORGOT if restore else TIMED_OUT, spent=True) from exc
        except OSError as exc:
            raise PortalError(UNREACHABLE, spent=bool(restore)) from exc
        if response != ANSWERED:
            refused = CANCELLED if response == CANCELLED_BY_PERSON else UNREACHABLE
            raise PortalError(FORGOT if restore else refused, spent=True)
    except BaseException:
        bus.close_session(handle)
        raise
    return Opened(handle, streams_in(results), str(results.get("restore_token") or ""))


# --- the kept restore token -------------------------------------------------------------

def kept(desktop: str, path: Path | None = None) -> str:
    """The restore token this desktop last answered, or ""."""
    try:
        said = json.loads((path or KEPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(said, dict) or said.get("desktop") != desktop:
        return ""
    return str(said.get("restore_token") or "")


def keep(token: str, desktop: str, path: Path | None = None) -> None:
    """Keep `token` in place of the last one; "" keeps none."""
    held = path or KEPT
    if not token:
        held.unlink(missing_ok=True)
        return
    held.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(held, lambda handle: json.dump(
        {"desktop": desktop, "restore_token": token, "kept_at": utc_now_iso()}, handle,
        indent=2))


# --- the adapter ----------------------------------------------------------------------

def _monitors() -> Sequence[Any]:
    from common.host import display_service

    return display_service.get_display_monitors()


class PortalAdapter:
    id = commands.PORTAL
    picture_tool = tools.GSTREAMER
    video_tool = tools.GSTREAMER
    # What recording asks of GStreamer, as gst-inspect-1.0 names each.
    NEEDS = ("pipewiresrc", "videoconvert", "videorate", "x264enc", "matroskamux",
                "pngenc", "filesink")

    def __init__(self, desktop: str, monitors: Callable[[], Sequence[Any]] = _monitors,
                 bus: Callable[[], Bus] = connect, kept_at: Path | None = None) -> None:
        self.desktop = desktop
        self._monitors = monitors
        self._connect = bus
        self._kept_at = kept_at
        self._lock = threading.Lock()
        self._bus: Bus | None = None
        self._handle = ""
        self._lent: list[int] = []

    @staticmethod
    def desktop_of(env: Mapping[str, str]) -> str:
        said = str(env.get("XDG_CURRENT_DESKTOP") or "").upper().split(":")
        return next((name for word, name in DESKTOPS if word in said), "")

    def requirements(self) -> tuple[tools.Tool, ...]:
        return (tools.FFMPEG, tools.GSTREAMER)

    def grabs(self, ffmpeg: tools.Found) -> bool:
        """FFmpeg only encodes here."""
        return True

    def lacks(self, found: Mapping[str, tools.Found]) -> tuple[tools.Tool, str] | None:
        gstreamer = found.get(tools.GSTREAMER.id)
        if gstreamer is None or gstreamer.state is not tools.State.FOUND \
                or gstreamer.probe is None:
            return None
        missing = next((one for one in self.NEEDS
                        if not gstreamer.probe.has(tools.ELEMENTS, one)), "")
        return (tools.GSTREAMER, missing) if missing else None

    def refused(self) -> tuple[str, Mapping[str, str]] | None:
        if kept(self.desktop, self._kept_at):
            return None
        return NOT_CHOSEN, {"desktop": self.desktop}

    def no_sound(self) -> tuple[str, Mapping[str, str]] | None:
        return None

    def outputs(self) -> list[Output]:
        """The display model's monitors, so reading them asks the portal nothing."""
        return x11_outputs(self._monitors())

    def windows(self) -> list[Window]:
        return []

    def at_once(self, ffmpeg: tools.Found) -> bool:
        return False

    def hardware(self, ffmpeg: tools.Found) -> str:
        return ""

    def still(self, found: Mapping[str, tools.Found], output: Output,
              dest: Path) -> list[str]:
        return [str(found[tools.GSTREAMER.id].path), "-q",
                *commands.inputs(self.id, output), "num-buffers=1", "!", "videoconvert",
                "!", "pngenc", "!", "filesink", f"location={dest}"]

    def still_turn(self, output: Output) -> geometry.Turn:
        return geometry.NONE

    def recording_turn(self, output: Output) -> geometry.Turn:
        return geometry.NONE

    # --- a session around the programs that read the screens -------------------------

    def _none(self, screens: Mapping[str, Output], key: str) -> dict[str, Screen]:
        return {window: Screen(window, reason=key, params={"desktop": self.desktop})
                for window in screens}

    def begin(self, screens: Mapping[str, Output]) -> dict[str, Screen]:
        """Each output's stream in a session restored from the kept token. Never asks."""
        restore = kept(self.desktop, self._kept_at)
        if not restore:
            return self._none(screens, NOT_CHOSEN)
        try:
            bus = self._connect()
        except OSError as exc:
            logger.warning("Recording: %s's screen sharing: %s", self.desktop, why(exc))
            return self._none(screens, UNREACHABLE)
        try:
            opened = open_session(bus, restore, RESTORE_SECONDS)
        except (PortalError, OSError) as exc:
            bus.close()
            spent = isinstance(exc, PortalError) and exc.spent
            logger.warning("Recording: %s's screen sharing did not restore (%s)%s",
                           self.desktop, exc, "; its choice is forgotten" if spent else "")
            if spent:
                keep("", self.desktop, self._kept_at)
            return self._none(screens, exc.key if isinstance(exc, PortalError)
                              else UNREACHABLE)
        keep(opened.restore, self.desktop, self._kept_at)
        with self._lock:
            self._bus, self._handle = bus, opened.handle
        found: dict[str, Screen] = {}
        for window, output in screens.items():
            stream = matched(output, opened.streams)
            found[window] = Screen(window, replace(output, index=stream.node)) \
                if stream is not None \
                else Screen(window, reason=NOT_SHARED, params={"desktop": self.desktop})
        return found

    def lend(self, output: Output) -> Output:
        """A PipeWire remote of its own for the program about to read `output`."""
        with self._lock:
            if self._bus is None:
                return output
            try:
                remote = int(self._bus.call("OpenPipeWireRemote", [self._handle, {}]))
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("Recording: no PipeWire remote for %s: %s", output.name, exc)
                return output
            self._lent.append(remote)
        return replace(output, remote=remote)

    def end(self) -> None:
        with self._lock:
            bus, handle, lent = self._bus, self._handle, self._lent
            self._bus, self._handle, self._lent = None, "", []
        for remote in lent:
            with suppress(OSError):
                os.close(remote)
        if bus is not None:
            bus.close_session(handle)
            bus.close()

    # --- asking -------------------------------------------------------------------------

    def _said(self, key: str) -> dict[str, Any]:
        return {"key": key, "params": {"desktop": self.desktop}}

    def choose(self, timeout: float = CHOOSE_SECONDS) -> dict[str, Any]:
        """Ask a person which screens VPinFE may record, and keep what the desktop
        answers. `shared` of `screens` is how many of this device's screens it shares."""
        outputs = self.outputs()
        answer: dict[str, Any] = {"kept": False, "shared": 0, "screens": len(outputs),
                                  "reason": None}
        try:
            bus = self._connect()
        except OSError as exc:
            logger.warning("Choose Screens: %s's screen sharing: %s", self.desktop,
                           why(exc))
            return {**answer, "reason": self._said(UNREACHABLE)}
        try:
            opened = open_session(bus, "", timeout)
            # The portal keeps what was chosen as the session closes.
            bus.close_session(opened.handle)
        except (PortalError, OSError) as exc:
            logger.warning("Choose Screens: %s", exc)
            return {**answer, "reason": self._said(
                exc.key if isinstance(exc, PortalError) else UNREACHABLE)}
        finally:
            bus.close()
        keep(opened.restore, self.desktop, self._kept_at)
        shared = sum(1 for one in outputs if matched(one, opened.streams))
        logger.info("Choose Screens: %s shares %s of %s screens%s", self.desktop, shared,
                    len(outputs), "" if opened.restore else ", and keeps no choice")
        return {**answer, "kept": bool(opened.restore), "shared": shared,
                "reason": None if opened.restore else self._said(NOT_KEPT)}


def start_choosing(adapter: Any) -> jobs.Job:
    """Choose Screens as a job. Raises where the desktop never asks, or a table runs."""
    if not isinstance(adapter, PortalAdapter):
        raise service_errors.UnavailableError(t(NOTHING_TO_CHOOSE))
    if launch_state.current().launching:
        raise service_errors.BlockedError(t("error.launch.already_launching"))

    def work(job: jobs.Job) -> dict[str, Any]:
        job.progress(0, 1, t(CHOOSING, desktop=adapter.desktop))
        return adapter.choose()

    return jobs.submit(jobs.KIND_MEDIA_CAPTURE, work)

"""macOS: who has the keyboard, and giving it to a program.

AppKit and Quartz through pyobjc, which VPinFE already ships on macOS, and nothing the
person has to allow: window titles are the one thing the system keeps back without Screen
Recording, so `titles_readable` says when they are blank and the watch goes by the
table's first window instead. Only imported on macOS.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from common.failures import why
from common.host.front import Window

# The system's own windows: the menu bar, the Dock, notifications. None of them is a
# program the person is playing in, and the keyboard is never theirs.
_SYSTEM_OWNERS = frozenset({"Window Server", "Dock", "SystemUIServer", "Control Center",
                            "Notification Center", "Spotlight", "loginwindow", "MenuBarAgent"})
_SMALLEST = 64
_OPEN = "/usr/bin/open"
_OPEN_SECONDS = 5
# Asks closer together than this are one round; the next round starts from the polite ask.
_ROUND_SECONDS = 10.0

# "Move focus to next window" in the system's keyboard shortcuts, and what it is when the
# person has never changed it: Command and the key above Tab.
_HOTKEYS = ("AppleSymbolicHotKeys", "com.apple.symbolichotkeys")
_NEXT_WINDOW = "27"
_NO_KEY = 65535
_DEFAULT_SHORTCUT = (50, 0x100000)
Area = tuple[float, float, float, float]

_LSAPPINFO = "/usr/bin/lsappinfo"
_LSAPPINFO_SECONDS = 1
# `holder` reuses the program Launch Services named for this long; `fresh_holder` never does.
_FRONT_REUSE_SECONDS = 1.0
# Between the move, the press and the release of the click. A click posted with no gap
# between its events does not reach VPX.
_CLICK_SETTLE_SECONDS = 0.05


def _front_pid() -> int | None:
    """The process id of the program in front, asked of Launch Services, which stays
    current in a process with no Cocoa run loop. None where it does not answer."""
    try:
        asn = subprocess.run([_LSAPPINFO, "front"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=_LSAPPINFO_SECONDS,
                             check=False).stdout.strip()
        if not asn:
            return None
        out = subprocess.run([_LSAPPINFO, "info", "-only", "pid", asn], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=_LSAPPINFO_SECONDS, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"pid\s*=\s*(\d+)", out)
    return int(found.group(1)) if found else None


def _read_hotkeys() -> Any:
    from CoreFoundation import CFPreferencesCopyAppValue
    return CFPreferencesCopyAppValue(*_HOTKEYS)


def playfield_area_of(ini_config: Any) -> Callable[[], Area | None]:
    """Where the playfield display is, as the person set it for VPinFE, in the same
    top-left points the window list uses."""
    def area() -> Area | None:
        from common.config.config_access import DisplayConfig
        from common.host.display_service import get_display_monitors
        monitors = get_display_monitors()
        number = DisplayConfig.from_config(ini_config).playfield_screen_id
        if not monitors:
            return None
        one = monitors[number] if number < len(monitors) else monitors[0]
        return float(one.x), float(one.y), float(one.width), float(one.height)
    return area


class MacBackend:
    name = "macOS"
    # Activation is the program's, not a window's: activating the table's program cannot
    # choose which of its windows has the keyboard, so any of them ends a hand-over.
    any_table_window_will_do = True

    def __init__(self, quartz: Any = None, appkit: Any = None,
                 clock: Callable[[], float] = time.monotonic,
                 hotkeys: Callable[[], Any] = _read_hotkeys,
                 playfield_area: Callable[[], Area | None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if sys.platform != "darwin" and (quartz is None or appkit is None):
            raise RuntimeError("the macOS backend only runs on macOS")
        if quartz is None:
            import Quartz
            quartz = Quartz
        if appkit is None:
            import AppKit
            appkit = AppKit
        self._quartz = quartz
        self._appkit = appkit
        self._clock = clock
        self._hotkeys = hotkeys
        self._playfield_area = playfield_area
        self._sleep = sleep
        self._front: tuple[int, float] | None = None
        self._asked: dict[int, tuple[int, float]] = {}

    @property
    def titles_readable(self) -> bool:
        """Whether the system hands over other programs' window titles. Without Screen
        Recording it gives each program only its own."""
        preflight = getattr(self._quartz, "CGPreflightScreenCaptureAccess", None)
        try:
            return bool(preflight()) if preflight is not None else False
        except Exception:  # noqa: BLE001 - an unanswered question means withheld, not a stopped watch
            return False

    def windows(self) -> list[Window]:
        """Visible windows, front to back. A program that fills the screen sits above the
        ordinary level, so size and owner decide, not the level."""
        quartz = self._quartz
        options = (quartz.kCGWindowListOptionOnScreenOnly
                   | quartz.kCGWindowListExcludeDesktopElements)
        found: list[Window] = []
        for info in quartz.CGWindowListCopyWindowInfo(options, quartz.kCGNullWindowID) or ():
            if info.get(quartz.kCGWindowOwnerName) in _SYSTEM_OWNERS:
                continue
            if float(info.get(quartz.kCGWindowAlpha, 1.0)) <= 0:
                continue
            bounds = info.get(quartz.kCGWindowBounds) or {}
            if min(float(bounds.get("Width", 0)), float(bounds.get("Height", 0))) < _SMALLEST:
                continue
            found.append(Window(handle=int(info[quartz.kCGWindowNumber]),
                                pid=int(info[quartz.kCGWindowOwnerPID]),
                                title=str(info.get(quartz.kCGWindowName) or ""),
                                bounds=(float(bounds.get("X", 0)), float(bounds.get("Y", 0)),
                                        float(bounds.get("Width", 0)),
                                        float(bounds.get("Height", 0)))))
        return found

    def holder(self) -> Window | None:
        """The front program's own window: a side window of the table sits above whoever
        is in front, so the first window in the list is not it. Launch Services names the
        program, and its answer is reused for `_FRONT_REUSE_SECONDS`. A program with no
        window of its own answers a window with no bounds. Raises where Launch Services
        does not answer."""
        return self._holder_of(self._program_in_front(fresh=False))

    def fresh_holder(self) -> Window | None:
        """`holder` with a new answer from Launch Services, for what is about to act on it."""
        return self._holder_of(self._program_in_front(fresh=True))

    def _program_in_front(self, *, fresh: bool) -> int:
        now = self._clock()
        if not fresh and self._front is not None and now - self._front[1] <= _FRONT_REUSE_SECONDS:
            return self._front[0]
        self._front = None
        pid = _front_pid()
        if pid is None:
            raise RuntimeError("Launch Services did not say which program is in front")
        self._front = (pid, now)
        return pid

    def _holder_of(self, pid: int) -> Window:
        for window in self.windows():
            if window.pid == pid:
                return window
        return Window(handle=f"app:{pid}", pid=pid, title="")

    def hand_over(self, window: Window) -> str:
        """Activation is the program's, not the window's. Each ask for the same program
        goes one step further than the last, because from macOS 14 a program that is not
        in front is only activated when the one in front lets it: first the polite ask
        that names the program in front, then the older forceful one, then Launch
        Services, which brings forward what a double click would."""
        appkit = self._appkit
        app = appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        if app is None:
            return "the program is no longer running"
        now = self._clock()
        step, last = self._asked.get(window.pid, (0, now))
        if now - last > _ROUND_SECONDS:
            step = 0
        self._asked[window.pid] = (step + 1, now)
        if app.isHidden():
            app.unhide()
        try:
            in_front = self.fresh_holder()
        except RuntimeError:
            in_front = None
        front = None if in_front is None else \
            appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(in_front.pid)
        if step == 0 and front is not None and front.processIdentifier() != window.pid \
                and hasattr(app, "activateFromApplication_options_"):
            return "" if app.activateFromApplication_options_(front, 0) \
                else "macOS did not accept the request"
        if step <= 1:
            forced = app.activateWithOptions_(appkit.NSApplicationActivateIgnoringOtherApps)
            return "" if forced else "macOS did not accept the request"
        return self._open(app)

    def _open(self, app: Any) -> str:
        identifier = app.bundleIdentifier()
        if not identifier:
            return "the program is not an application bundle, so it cannot be brought forward"
        try:
            done = subprocess.run([_OPEN, "-b", str(identifier)], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=_OPEN_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return why(exc)
        return "" if done.returncode == 0 else (done.stderr.strip() or "open failed")

    # -- bringing the keyboard back to the playfield from outside ----------------------

    def next_window_shortcut(self) -> tuple[int, int] | None:
        """Key code and modifier flags of "Move focus to next window", None where the
        person turned it off or gave it no key."""
        try:
            entry = (self._hotkeys() or {}).get(_NEXT_WINDOW)
            if entry is None:
                return _DEFAULT_SHORTCUT
            if not entry.get("enabled"):
                return None
            parameters: Sequence[Any] = (entry.get("value") or {}).get("parameters") or []
            if len(parameters) < 3 or int(parameters[1]) == _NO_KEY:
                return None
            return int(parameters[1]), int(parameters[2])
        except Exception:  # noqa: BLE001 - an entry that cannot be read is read as the one nobody changed
            return _DEFAULT_SHORTCUT

    def press_next_window(self) -> str:
        """Posts the shortcut through the system event tap. "" where it was posted, else
        why not. It moves the keyboard to the next window of the program in front, which
        is the program's own affair, so it only means anything while the table is in front."""
        shortcut = self.next_window_shortcut()
        if shortcut is None:
            return "the shortcut for the next window is turned off"
        refused = self._may_post()
        if refused:
            return refused
        quartz = self._quartz
        key, flags = shortcut
        for down in (True, False):
            event = quartz.CGEventCreateKeyboardEvent(None, key, down)
            quartz.CGEventSetFlags(event, flags)
            quartz.CGEventPost(quartz.kCGHIDEventTap, event)
        return ""

    def click_playfield(self, table: Sequence[Window]) -> str:
        """A left click in the middle of the table's window on the playfield display, with
        the pointer put back where it was. "" where it was posted, else why not."""
        refused = self._may_post()
        if refused:
            return refused
        target = self._playfield_window(table)
        if target is None or target.bounds is None:
            return "none of the table's windows is on the playfield screen"
        left, top, width, height = target.bounds
        center = (left + width / 2, top + height / 2)
        quartz = self._quartz
        before = quartz.CGEventGetLocation(quartz.CGEventCreate(None))
        try:
            for kind in (quartz.kCGEventMouseMoved, quartz.kCGEventLeftMouseDown,
                         quartz.kCGEventLeftMouseUp):
                event = quartz.CGEventCreateMouseEvent(None, kind, center,
                                                       quartz.kCGMouseButtonLeft)
                quartz.CGEventPost(quartz.kCGHIDEventTap, event)
                self._sleep(_CLICK_SETTLE_SECONDS)
        finally:
            quartz.CGWarpMouseCursorPosition(before)
        return ""

    def _may_post(self) -> str:
        """Without the permission to post events the system drops them and says nothing."""
        preflight = getattr(self._quartz, "CGPreflightPostEventAccess", None)
        try:
            allowed = bool(preflight()) if preflight is not None else False
        except Exception:  # noqa: BLE001 - an unanswered question means not allowed
            allowed = False
        return "" if allowed else ("macOS has not allowed VPinFE to post key presses and "
                                   "clicks, so nothing was sent")

    def _playfield_window(self, table: Sequence[Window]) -> Window | None:
        """The largest of the table's windows whose middle is on the playfield display."""
        area = self._playfield_area() if self._playfield_area is not None else None
        if area is None:
            return None
        left, top, width, height = area
        on_it = []
        for window in table:
            if window.bounds is None:
                continue
            x, y, w, h = window.bounds
            if left <= x + w / 2 < left + width and top <= y + h / 2 < top + height:
                on_it.append(window)
        return max(on_it, key=lambda one: one.bounds[2] * one.bounds[3] if one.bounds else 0,
                   default=None)

"""macOS: who has the keyboard, and giving it to a program.

AppKit and Quartz through pyobjc, which VPinFE already ships on macOS, and nothing the
person has to allow: window titles are the one thing the system keeps back without Screen
Recording, so `titles_readable` says when they are blank and the watch goes by the
table's first window instead. Only imported on macOS.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from common.failures import why
from common.host.front import Window

# The system's own windows: the menu bar, the Dock, notifications. None of them is a
# program the person is playing in, and the keyboard is never theirs.
_SYSTEM_OWNERS = frozenset({"Window Server", "Dock", "SystemUIServer", "Control Center",
                            "Notification Center", "Spotlight", "loginwindow"})
_SMALLEST = 64
_OPEN = "/usr/bin/open"
_OPEN_SECONDS = 5
# Asks closer together than this are one round; the next round starts from the polite ask.
_ROUND_SECONDS = 10.0


class MacBackend:
    name = "macOS"

    def __init__(self, quartz: Any = None, appkit: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
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
                                title=str(info.get(quartz.kCGWindowName) or "")))
        return found

    def holder(self) -> Window | None:
        """The window at the front of the screen. The window list answers for itself, where
        `frontmostApplication` goes stale in a process that runs no Cocoa run loop, which
        this one does not. Nothing in front, the desktop showing, answers None."""
        return next(iter(self.windows()), None)

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
        in_front = self.holder()
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

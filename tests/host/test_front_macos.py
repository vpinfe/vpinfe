"""Protects: (e) the macOS hand-over reads windows and the front program the way the system
answers them, and asks for activation in the order that works on each macOS version.

The system is a fake; no window is touched, and nothing here runs on a real macOS.
"""

# ruff: noqa: N802
from __future__ import annotations

import types
import unittest
from unittest import mock

from common.host import front_macos
from common.host.front import Window


def _info(number: int, pid: int, *, name: str | None = "", owner: str = "App",
          width: int = 800, height: int = 600, alpha: float = 1.0) -> dict:
    info = {"kCGWindowNumber": number, "kCGWindowOwnerPID": pid, "kCGWindowOwnerName": owner,
            "kCGWindowAlpha": alpha, "kCGWindowBounds": {"Width": width, "Height": height}}
    if name is not None:
        info["kCGWindowName"] = name
    return info


def _quartz(infos: list[dict], *, preflight: bool | None = True) -> types.SimpleNamespace:
    quartz = types.SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1, kCGWindowListExcludeDesktopElements=16,
        kCGNullWindowID=0, kCGWindowNumber="kCGWindowNumber",
        kCGWindowOwnerPID="kCGWindowOwnerPID", kCGWindowOwnerName="kCGWindowOwnerName",
        kCGWindowName="kCGWindowName", kCGWindowAlpha="kCGWindowAlpha",
        kCGWindowBounds="kCGWindowBounds",
        CGWindowListCopyWindowInfo=lambda _options, _above: infos)
    if preflight is not None:
        quartz.CGPreflightScreenCaptureAccess = lambda: preflight
    return quartz


class _App:
    def __init__(self, pid: int, *, accepts: bool = True, hidden: bool = False,
                 bundle: str | None = "com.example.app", polite: bool = True) -> None:
        self.pid = pid
        self.accepts = accepts
        self.hidden = hidden
        self.bundle = bundle
        self.calls: list[str] = []
        if polite:
            self.activateFromApplication_options_ = self._polite

    def processIdentifier(self) -> int:
        return self.pid

    def isHidden(self) -> bool:
        return self.hidden

    def unhide(self) -> None:
        self.calls.append("unhide")

    def bundleIdentifier(self) -> str | None:
        return self.bundle

    def _polite(self, source: _App, _options: int) -> bool:
        self.calls.append(f"polite from {source.pid}")
        return self.accepts

    def activateWithOptions_(self, options: int) -> bool:
        self.calls.append(f"forceful {options}")
        return self.accepts


def _appkit(front: _App | None, apps: dict[int, _App]) -> types.SimpleNamespace:
    workspace = types.SimpleNamespace(frontmostApplication=lambda: front)
    return types.SimpleNamespace(
        NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: workspace),
        NSRunningApplication=types.SimpleNamespace(
            runningApplicationWithProcessIdentifier_=apps.get),
        NSApplicationActivateIgnoringOtherApps=2)


class WindowsTests(unittest.TestCase):
    def _windows(self, infos: list[dict]) -> list[Window]:
        return front_macos.MacBackend(_quartz(infos), _appkit(None, {})).windows()

    def test_windows_come_back_front_to_back_with_their_titles(self) -> None:
        found = self._windows([_info(7, 100, name="Visual Pinball Player"),
                               _info(8, 101, name="Backglass")])

        self.assertEqual(found, [Window(7, 100, "Visual Pinball Player"),
                                 Window(8, 101, "Backglass")])

    def test_a_window_whose_title_is_withheld_has_a_blank_one(self) -> None:
        self.assertEqual(self._windows([_info(7, 100, name=None)]), [Window(7, 100, "")])

    def test_the_menu_bar_the_dock_and_slivers_are_not_windows_of_a_program(self) -> None:
        found = self._windows([_info(1, 50, owner="Window Server"),
                               _info(2, 51, owner="Dock"),
                               _info(3, 52, width=40, height=22),
                               _info(4, 53, alpha=0.0),
                               _info(5, 100, name="Table")])

        self.assertEqual([one.handle for one in found], [5])

    def test_titles_are_readable_only_where_the_system_allows_screen_recording(self) -> None:
        for allowed in (True, False):
            with self.subTest(allowed=allowed):
                backend = front_macos.MacBackend(_quartz([], preflight=allowed),
                                                 _appkit(None, {}))
                self.assertEqual(backend.titles_readable, allowed)

    def test_a_system_that_cannot_say_is_treated_as_withholding_titles(self) -> None:
        backend = front_macos.MacBackend(_quartz([], preflight=None), _appkit(None, {}))

        self.assertFalse(backend.titles_readable)


class HolderTests(unittest.TestCase):
    def _holder(self, infos: list[dict]) -> Window | None:
        return front_macos.MacBackend(_quartz(infos), _appkit(None, {})).holder()

    def test_the_holder_is_the_window_at_the_front_of_the_screen(self) -> None:
        held = self._holder([_info(7, 100, name="Player"), _info(9, 100, name="B"),
                             _info(8, 200, name="Other")])

        self.assertEqual(held, Window(7, 100, "Player"))

    def test_a_menu_bar_or_dock_in_front_does_not_hold_the_keyboard(self) -> None:
        held = self._holder([_info(1, 50, owner="Dock"), _info(7, 100, name="Player")])

        self.assertEqual(held, Window(7, 100, "Player"))

    def test_an_empty_screen_is_nobody(self) -> None:
        self.assertIsNone(self._holder([]))


class HandOverTests(unittest.TestCase):
    def _backend(self, *, front: _App | None, target: _App) -> front_macos.MacBackend:
        apps = {target.pid: target} if front is None else {target.pid: target, front.pid: front}
        screen = [] if front is None else [_info(99, front.pid, name="Front")]
        return front_macos.MacBackend(_quartz(screen), _appkit(front, apps),
                                      clock=lambda: self.now)

    now = 0.0

    def test_the_first_ask_names_the_program_in_front(self) -> None:
        kiosk, table = _App(1), _App(100)

        said = self._backend(front=kiosk, target=table).hand_over(Window(7, 100, ""))

        self.assertEqual(said, "")
        self.assertEqual(table.calls, ["polite from 1"])

    def test_the_second_ask_is_the_forceful_one_and_the_third_goes_through_open(self) -> None:
        kiosk, table = _App(1), _App(100)
        backend = self._backend(front=kiosk, target=table)

        with mock.patch.object(front_macos.subprocess, "run",
                               return_value=types.SimpleNamespace(returncode=0,
                                                                  stderr="")) as run:
            said = [backend.hand_over(Window(7, 100, "")) for _ in range(3)]

        self.assertEqual(said, ["", "", ""])
        self.assertEqual(table.calls, ["polite from 1", "forceful 2"])
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], [front_macos._OPEN, "-b", "com.example.app"])

    def test_a_later_round_starts_again_from_the_polite_ask(self) -> None:
        kiosk, table = _App(1), _App(100)
        backend = self._backend(front=kiosk, target=table)

        backend.hand_over(Window(7, 100, ""))
        backend.hand_over(Window(7, 100, ""))
        self.now = 60.0
        backend.hand_over(Window(7, 100, ""))

        self.assertEqual(table.calls, ["polite from 1", "forceful 2", "polite from 1"])

    def test_each_program_is_counted_on_its_own(self) -> None:
        kiosk, table, other = _App(1), _App(100), _App(200)
        backend = front_macos.MacBackend(
            _quartz([_info(99, kiosk.pid, name="Front")]),
            _appkit(kiosk, {1: kiosk, 100: table, 200: other}), clock=lambda: 0.0)

        backend.hand_over(Window(7, 100, ""))
        backend.hand_over(Window(8, 200, ""))

        self.assertEqual((table.calls, other.calls), (["polite from 1"], ["polite from 1"]))

    def test_a_refusal_is_reported_in_words(self) -> None:
        said = self._backend(front=_App(1), target=_App(100, accepts=False)).hand_over(
            Window(7, 100, ""))

        self.assertEqual(said, "macOS did not accept the request")

    def test_a_system_without_the_polite_ask_goes_straight_to_the_forceful_one(self) -> None:
        table = _App(100, polite=False)

        self._backend(front=_App(1), target=table).hand_over(Window(7, 100, ""))

        self.assertEqual(table.calls, ["forceful 2"])

    def test_a_program_that_is_gone_is_said_to_be(self) -> None:
        backend = front_macos.MacBackend(_quartz([]), _appkit(_App(1), {}))

        self.assertEqual(backend.hand_over(Window(7, 100, "")),
                         "the program is no longer running")

    def test_a_hidden_program_is_shown_before_it_is_asked(self) -> None:
        table = _App(100, hidden=True)

        self._backend(front=_App(1), target=table).hand_over(Window(7, 100, ""))

        self.assertEqual(table.calls[0], "unhide")

    def test_open_without_a_bundle_says_so(self) -> None:
        table = _App(100, bundle=None)
        backend = self._backend(front=_App(1), target=table)

        said = [backend.hand_over(Window(7, 100, "")) for _ in range(3)]

        self.assertIn("not an application bundle", said[2])


if __name__ == "__main__":
    unittest.main()

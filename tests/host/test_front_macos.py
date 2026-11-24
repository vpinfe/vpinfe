"""Protects: (e) the macOS hand-over reads windows and the front program the way the system
answers them, and asks for activation in the order that works on each macOS version.

The system is a fake; no window is touched, and nothing here runs on a real macOS.
"""

# ruff: noqa: N802
from __future__ import annotations

import subprocess
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
                               _info(6, 54, owner="MenuBarAgent"),
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
    def setUp(self) -> None:
        self.now = 0.0
        self.front_pid: int | None = 100
        patched = mock.patch.object(front_macos, "_front_pid",
                                    side_effect=lambda: self.front_pid)
        self.asked = patched.start()
        self.addCleanup(patched.stop)

    def _backend(self, infos: list[dict]) -> front_macos.MacBackend:
        return front_macos.MacBackend(_quartz(infos), _appkit(None, {}), clock=lambda: self.now)

    def _holder(self, infos: list[dict]) -> Window | None:
        return self._backend(infos).holder()

    def test_the_holder_is_the_window_of_the_program_launch_services_names(self) -> None:
        held = self._holder([_info(7, 100, name="Player"), _info(9, 100, name="B"),
                             _info(8, 200, name="Other")])

        self.assertEqual(held, Window(7, 100, "Player"))

    def test_a_side_window_first_in_the_list_is_not_the_holder(self) -> None:
        """A side window of the table is first in the list while another program is in front."""
        self.front_pid = 200

        held = self._holder([_info(7, 100, name="Side"), _info(8, 200, name="Settings")])

        self.assertEqual(held, Window(8, 200, "Settings"))

    def test_a_menu_bar_or_dock_in_front_does_not_hold_the_keyboard(self) -> None:
        held = self._holder([_info(1, 50, owner="Dock"), _info(8, 51, owner="MenuBarAgent"),
                             _info(7, 100, name="Player")])

        self.assertEqual(held, Window(7, 100, "Player"))

    def test_a_front_program_with_no_window_is_still_the_holder(self) -> None:
        self.front_pid = 300

        held = self._holder([_info(7, 100, name="Side")])

        self.assertEqual(held, Window("app:300", 300, ""))

    def test_launch_services_not_answering_is_an_error_not_the_first_window(self) -> None:
        self.front_pid = None

        with self.assertRaises(RuntimeError):
            self._holder([_info(7, 100, name="Side")])

    def test_an_answer_is_reused_for_a_second_and_asked_again_after_it(self) -> None:
        backend = self._backend([_info(7, 100, name="Player"), _info(8, 200, name="Other")])

        backend.holder()
        self.now = 1.0
        backend.holder()
        self.assertEqual(self.asked.call_count, 1)

        self.front_pid = 200
        self.now = 1.5
        self.assertEqual(backend.holder(), Window(8, 200, "Other"))
        self.assertEqual(self.asked.call_count, 2)

    def test_a_fresh_holder_always_asks_and_the_next_plain_one_reuses_it(self) -> None:
        backend = self._backend([_info(7, 100, name="Player"), _info(8, 200, name="Other")])

        backend.holder()
        self.front_pid = 200
        self.assertEqual(backend.fresh_holder(), Window(8, 200, "Other"))
        self.assertEqual(self.asked.call_count, 2)

        self.front_pid = 100
        self.assertEqual(backend.holder(), Window(8, 200, "Other"))
        self.assertEqual(self.asked.call_count, 2)

    def test_an_answer_that_failed_is_not_kept(self) -> None:
        backend = self._backend([_info(7, 100, name="Player")])
        backend.holder()
        self.front_pid = None
        self.now = 5.0
        with self.assertRaises(RuntimeError):
            backend.holder()

        self.front_pid = 100
        self.now = 5.1

        self.assertEqual(backend.holder(), Window(7, 100, "Player"))

    def test_an_empty_screen_names_the_program_with_no_window(self) -> None:
        self.assertEqual(self._holder([]), Window("app:100", 100, ""))


class FrontPidTests(unittest.TestCase):
    def _answers(self, *replies: str | Exception) -> mock.Mock:
        calls = [reply if isinstance(reply, Exception) else
                 types.SimpleNamespace(stdout=reply) for reply in replies]
        return mock.Mock(side_effect=calls)

    def test_the_process_id_is_read_from_the_program_launch_services_names(self) -> None:
        run = self._answers("ASN:0x0-0x1b01b:\n",
                            "[ NULL ]  [ NULL ]\n    pid = 4242 !cgsConnection !signalled\n")
        with mock.patch.object(front_macos.subprocess, "run", run):
            found = front_macos._front_pid()

        self.assertEqual(found, 4242)
        self.assertEqual(run.call_args_list[1].args[0][-1], "ASN:0x0-0x1b01b:")

    def test_launch_services_failing_is_no_answer(self) -> None:
        for reply in (OSError("no such file"), subprocess.TimeoutExpired("lsappinfo", 2)):
            with self.subTest(reply=type(reply).__name__), \
                    mock.patch.object(front_macos.subprocess, "run", self._answers(reply)):
                self.assertIsNone(front_macos._front_pid())

    def test_nothing_in_front_or_a_reply_without_a_pid_is_no_answer(self) -> None:
        with mock.patch.object(front_macos.subprocess, "run", self._answers("\n")):
            self.assertIsNone(front_macos._front_pid())
        with mock.patch.object(front_macos.subprocess, "run", self._answers("ASN:1:", "")):
            self.assertIsNone(front_macos._front_pid())


class HandOverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.front_pid: int | None = None
        patched = mock.patch.object(front_macos, "_front_pid",
                                    side_effect=lambda: self.front_pid)
        patched.start()
        self.addCleanup(patched.stop)

    def _backend(self, *, front: _App | None, target: _App) -> front_macos.MacBackend:
        apps = {target.pid: target} if front is None else {target.pid: target, front.pid: front}
        screen = [] if front is None else [_info(99, front.pid, name="Front")]
        self.front_pid = None if front is None else front.pid
        return front_macos.MacBackend(_quartz(screen), _appkit(front, apps),
                                      clock=lambda: self.now)

    now = 0.0

    def test_the_first_ask_names_the_program_in_front(self) -> None:
        kiosk, table = _App(1), _App(100)

        said = self._backend(front=kiosk, target=table).hand_over(Window(7, 100, ""))

        self.assertEqual(said, "")
        self.assertEqual(table.calls, ["polite from 1"])

    def test_a_hand_over_goes_on_when_launch_services_does_not_answer(self) -> None:
        table = _App(100)
        backend = front_macos.MacBackend(_quartz([]), _appkit(None, {100: table}),
                                         clock=lambda: self.now)

        said = backend.hand_over(Window(7, 100, ""))

        self.assertEqual(said, "")
        self.assertEqual(table.calls, ["forceful 2"])

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
        self.front_pid = kiosk.pid
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


class _Tap:
    """The event tap: what is posted, and where the pointer is."""

    def __init__(self, *, allowed: bool = True, pointer: tuple[float, float] = (10.0, 20.0)
                 ) -> None:
        self.posted: list[tuple] = []
        self.pauses: list[float] = []
        self.pointer = pointer
        self.quartz = types.SimpleNamespace(
            kCGHIDEventTap=0, kCGEventMouseMoved=5, kCGEventLeftMouseDown=1,
            kCGEventLeftMouseUp=2, kCGMouseButtonLeft=0,
            CGPreflightPostEventAccess=lambda: allowed,
            CGEventCreateKeyboardEvent=lambda _s, key, down: ["key", key, down, 0],
            CGEventSetFlags=lambda event, flags: event.__setitem__(3, flags),
            CGEventCreateMouseEvent=lambda _s, kind, at, _b: ["mouse", kind, at],
            CGEventCreate=lambda _s: "now",
            CGEventGetLocation=lambda _event: self.pointer,
            CGEventPost=lambda _tap, event: self.posted.append(tuple(event)),
            CGWarpMouseCursorPosition=lambda at: self.posted.append(("warp", at)))


class OutsideTests(unittest.TestCase):
    PLAYFIELD = (0.0, 0.0, 1080.0, 1920.0)

    def _backend(self, tap: _Tap, hotkeys: object = None) -> front_macos.MacBackend:
        return front_macos.MacBackend(tap.quartz, _appkit(None, {}),
                                      hotkeys=lambda: hotkeys,
                                      playfield_area=lambda: self.PLAYFIELD,
                                      sleep=tap.pauses.append)

    def test_the_shortcut_is_command_and_the_key_above_tab_until_changed(self) -> None:
        self.assertEqual(self._backend(_Tap()).next_window_shortcut(), (50, 0x100000))
        self.assertEqual(self._backend(_Tap(), {"15": {}}).next_window_shortcut(), (50, 0x100000))

    def test_a_shortcut_the_person_changed_is_the_one_pressed(self) -> None:
        changed = {"27": {"enabled": True, "value": {"parameters": [65535, 10, 0x180000]}}}

        self.assertEqual(self._backend(_Tap(), changed).next_window_shortcut(), (10, 0x180000))

    def test_an_entry_that_cannot_be_read_is_the_one_nobody_changed(self) -> None:
        broken = {"27": {"enabled": True, "value": {"parameters": ["a", "b", "c"]}}}

        self.assertEqual(self._backend(_Tap(), broken).next_window_shortcut(), (50, 0x100000))

    def test_a_shortcut_that_is_off_or_has_no_key_is_none(self) -> None:
        off = {"27": {"enabled": False, "value": {"parameters": [96, 50, 0x100000]}}}
        none = {"27": {"enabled": True, "value": {"parameters": [65535, 65535, 0x100000]}}}

        self.assertIsNone(self._backend(_Tap(), off).next_window_shortcut())
        self.assertIsNone(self._backend(_Tap(), none).next_window_shortcut())

    def test_a_setting_that_cannot_be_read_is_the_default(self) -> None:
        def broken() -> object:
            raise OSError("no preferences")

        backend = front_macos.MacBackend(_Tap().quartz, _appkit(None, {}), hotkeys=broken)

        self.assertEqual(backend.next_window_shortcut(), (50, 0x100000))

    def test_the_press_is_the_key_down_and_up_with_its_modifier(self) -> None:
        tap = _Tap()

        said = self._backend(tap).press_next_window()

        self.assertEqual(said, "")
        self.assertEqual(tap.posted, [("key", 50, True, 0x100000), ("key", 50, False, 0x100000)])

    def test_nothing_is_posted_without_the_permission_and_it_says_so(self) -> None:
        tap = _Tap(allowed=False)
        backend = self._backend(tap)

        self.assertIn("has not allowed", backend.press_next_window())
        table = [Window(1, 100, "", self.PLAYFIELD)]
        self.assertIn("has not allowed", backend.click_playfield(table))
        self.assertEqual(tap.posted, [])

    def test_a_press_for_a_shortcut_that_is_off_posts_nothing(self) -> None:
        tap = _Tap()
        off = {"27": {"enabled": 0, "value": {"parameters": [96, 50, 0x100000]}}}

        self.assertIn("turned off", self._backend(tap, off).press_next_window())
        self.assertEqual(tap.posted, [])

    def test_the_click_is_in_the_middle_of_the_tables_window_on_the_playfield_screen(self) -> None:
        tap = _Tap()
        backglass = Window(2, 100, "", (1080.0, 0.0, 1920.0, 1080.0))
        small = Window(3, 100, "", (0.0, 0.0, 200.0, 200.0))
        playfield = Window(1, 100, "", (0.0, 0.0, 1080.0, 1920.0))

        said = self._backend(tap).click_playfield([backglass, small, playfield])

        self.assertEqual(said, "")
        center = (540.0, 960.0)
        self.assertEqual(tap.posted, [("mouse", 5, center), ("mouse", 1, center),
                                      ("mouse", 2, center), ("warp", (10.0, 20.0))])

    def test_the_click_waits_between_the_move_the_press_and_the_release(self) -> None:
        tap = _Tap()
        order: list[str] = []
        tap.quartz.CGEventPost = lambda _tap, event: order.append(f"post {event[1]}")
        backend = front_macos.MacBackend(tap.quartz, _appkit(None, {}),
                                         playfield_area=lambda: self.PLAYFIELD,
                                         sleep=lambda _seconds: order.append("wait"))

        backend.click_playfield([Window(1, 100, "", self.PLAYFIELD)])

        self.assertEqual(order, ["post 5", "wait", "post 1", "wait", "post 2", "wait"])

    def test_the_pointer_goes_back_even_when_a_post_fails(self) -> None:
        tap = _Tap()

        def failing(_tap: int, event: list) -> None:
            if event[1] == 1:
                raise OSError("tap closed")
            tap.posted.append(tuple(event))

        tap.quartz.CGEventPost = failing
        with self.assertRaises(OSError):
            self._backend(tap).click_playfield([Window(1, 100, "", self.PLAYFIELD)])

        self.assertEqual(tap.posted[-1], ("warp", (10.0, 20.0)))

    def test_no_click_when_none_of_the_tables_windows_is_on_the_playfield_screen(self) -> None:
        tap = _Tap()
        elsewhere = Window(2, 100, "", (1080.0, 0.0, 1920.0, 1080.0))

        said = self._backend(tap).click_playfield([elsewhere, Window(5, 100, "")])

        self.assertIn("none of the table's windows is on the playfield screen", said)
        self.assertEqual(tap.posted, [])

    def test_windows_say_where_they_are(self) -> None:
        info = _info(7, 100, name="P")
        info["kCGWindowBounds"] = {"X": 10, "Y": 20, "Width": 800, "Height": 600}

        found = front_macos.MacBackend(_quartz([info]), _appkit(None, {})).windows()

        self.assertEqual(found[0].bounds, (10.0, 20.0, 800.0, 600.0))


if __name__ == "__main__":
    unittest.main()

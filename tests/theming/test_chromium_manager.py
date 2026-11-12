from __future__ import annotations

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from common.config.config_store import ConfigStore
from frontend import chromium_manager, theme_windows
from frontend.chromium_manager import ChromiumManager


class ChromiumManagerTests(unittest.TestCase):
    def test_windows_get_chromium_path_prefers_bundled_when_present(self) -> None:
        bundled = r"C:\vpinfe\chromium\windows\chrome-win\chrome.exe"

        with mock.patch("frontend.chromium_manager.platform.system", return_value="Windows"), \
            mock.patch("frontend.chromium_manager.resource_path", return_value=bundled), \
            mock.patch("frontend.chromium_manager.os.path.expandvars") as expandvars, \
            mock.patch("frontend.chromium_manager.os.path.isfile", return_value=True):
            self.assertEqual(
                chromium_manager.get_chromium_path(),
                chromium_manager.ChromiumPath(bundled, False),
            )
            expandvars.assert_not_called()

    def test_windows_get_chromium_path_uses_system_browser_for_slim_build(self) -> None:
        bundled = r"C:\vpinfe\chromium\windows\chrome-win\chrome.exe"
        chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

        def exists(path: str) -> bool:
            return path == chrome

        def expandvars(value: str) -> str:
            return chrome if "Google\\Chrome" in value else value

        with (
            mock.patch("frontend.chromium_manager.platform.system", return_value="Windows"),
            mock.patch("frontend.chromium_manager.resource_path", return_value=bundled),
            mock.patch("frontend.chromium_manager.os.path.expandvars",
                       side_effect=expandvars),
            mock.patch("frontend.chromium_manager.os.path.isfile", side_effect=exists),
        ):
            self.assertEqual(
                chromium_manager.get_chromium_path(),
                chromium_manager.ChromiumPath(chrome, True),
            )

    def test_windows_get_chromium_path_does_not_use_edge_for_slim_build(self) -> None:
        bundled = r"C:\vpinfe\chromium\windows\chrome-win\chrome.exe"
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

        def exists(path: str) -> bool:
            return path == edge

        def expandvars(value: str) -> str:
            return edge if "Microsoft\\Edge" in value else value

        with (
            mock.patch("frontend.chromium_manager.platform.system", return_value="Windows"),
            mock.patch("frontend.chromium_manager.resource_path", return_value=bundled),
            mock.patch("frontend.chromium_manager.os.path.expandvars",
                       side_effect=expandvars),
            mock.patch("frontend.chromium_manager.os.path.isfile", side_effect=exists),
        ):
            self.assertEqual(
                chromium_manager.get_chromium_path(),
                chromium_manager.ChromiumPath(bundled, False),
            )

    def test_linux_get_chromium_path_finds_google_chrome_stable(self) -> None:
        chrome = "/usr/bin/google-chrome-stable"
        bundled = "/opt/vpinfe/chromium/linux/chrome/chrome"

        def which(binary_name: str) -> str | None:
            return chrome if binary_name == "google-chrome-stable" else None

        with mock.patch("frontend.chromium_manager.platform.system", return_value="Linux"), \
            mock.patch("frontend.chromium_manager.which", side_effect=which), \
            mock.patch("frontend.chromium_manager.resource_path", return_value=bundled):
            self.assertEqual(
                chromium_manager.get_chromium_path(),
                chromium_manager.ChromiumPath(chrome, True),
            )

    def test_browser_program_wins_over_the_bundled_copy(self) -> None:
        chosen = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        bundled = r"C:\vpinfe\chromium\windows\chrome-win\chrome.exe"

        with mock.patch("frontend.chromium_manager.platform.system", return_value="Windows"), \
            mock.patch("frontend.chromium_manager.resource_path", return_value=bundled), \
            mock.patch("frontend.chromium_manager.os.path.isfile", return_value=True):
            self.assertEqual(chromium_manager.get_chromium_path(chosen),
                             chromium_manager.ChromiumPath(chosen, True))

    def test_browser_program_that_is_missing_is_still_the_answer(self) -> None:
        # Quietly falling back to another browser would hide that the setting is wrong.
        with mock.patch("frontend.chromium_manager.os.path.isfile", return_value=False):
            self.assertEqual(chromium_manager.get_chromium_path("/nowhere/chrome"),
                             chromium_manager.ChromiumPath(str(Path("/nowhere/chrome")), True))

    def test_blank_browser_program_finds_one_as_before(self) -> None:
        chrome = "/usr/bin/google-chrome-stable"

        def which(binary_name: str) -> str | None:
            return chrome if binary_name == "google-chrome-stable" else None

        with mock.patch("frontend.chromium_manager.platform.system", return_value="Linux"), \
            mock.patch("frontend.chromium_manager.which", side_effect=which):
            self.assertEqual(chromium_manager.get_chromium_path("  "),
                             chromium_manager.ChromiumPath(chrome, True))

    def test_browser_program_is_read_from_the_config_when_not_given(self) -> None:
        with mock.patch("frontend.chromium_manager.configured_browser_path",
                        return_value="/opt/chrome/chrome"):
            self.assertEqual(chromium_manager.get_chromium_path(),
                             chromium_manager.ChromiumPath(str(Path("/opt/chrome/chrome")), True))

    def test_a_mac_app_bundle_resolves_to_the_program_inside(self) -> None:
        app = "/Applications/Google Chrome.app"
        with mock.patch("common.launcher_path.sys.platform", "darwin"):
            self.assertEqual(chromium_manager.get_chromium_path(app).path,
                             str(Path(app, "Contents", "MacOS", "Google Chrome")))

    def test_google_chrome_path_finds_it_on_linux_and_answers_none_without_it(self) -> None:
        chrome = "/usr/bin/google-chrome"
        with mock.patch("frontend.chromium_manager.platform.system", return_value="Linux"), \
            mock.patch("frontend.chromium_manager.which",
                       side_effect=lambda name: chrome if name == "google-chrome" else None), \
            mock.patch("frontend.chromium_manager.os.path.isfile", return_value=True):
            self.assertEqual(chromium_manager.google_chrome_path(), chrome)
        with mock.patch("frontend.chromium_manager.platform.system", return_value="Linux"), \
            mock.patch("frontend.chromium_manager.which", return_value=None):
            self.assertIsNone(chromium_manager.google_chrome_path())

    def test_google_chrome_path_on_windows_never_offers_chromium_or_edge(self) -> None:
        chromium = r"C:\Program Files\Chromium\Application\chrome.exe"

        def expandvars(value: str) -> str:
            return chromium if "Chromium" in value else value

        with mock.patch("frontend.chromium_manager.platform.system", return_value="Windows"), \
            mock.patch("frontend.chromium_manager.os.path.expandvars", side_effect=expandvars), \
            mock.patch("frontend.chromium_manager.os.path.isfile",
                       side_effect=lambda path: path == chromium):
            self.assertIsNone(chromium_manager.google_chrome_path())

    def test_linux_and_mac_keep_passwords_out_of_the_keyring_and_the_keychain(self) -> None:
        """Protects: (d) a first launch stopped on a dialog nobody is there to answer."""
        import importlib.util
        import platform

        def flags_on(system: str) -> list[str]:
            spec = importlib.util.spec_from_file_location(
                "chromium_manager_probe", chromium_manager.__file__)
            assert spec is not None and spec.loader is not None
            probe = importlib.util.module_from_spec(spec)
            with mock.patch.object(platform, "system", return_value=system):
                spec.loader.exec_module(probe)
            return list(probe.CHROMIUM_BASE_FLAGS)

        self.assertIn("--password-store=basic", flags_on("Linux"))
        self.assertNotIn("--use-mock-keychain", flags_on("Linux"))
        for flag in ("--use-mock-keychain", "--password-store=basic"):
            self.assertIn(flag, flags_on("Darwin"))
        for flag in ("--use-mock-keychain", "--password-store=basic"):
            self.assertNotIn(flag, flags_on("Windows"))

    def test_parse_additional_chromium_options_supports_multiple_flags(self) -> None:
        options = chromium_manager.parse_additional_chromium_options(
            '--disable-accelerated-video-decode\n'
            '--ozone-platform=x11 --user-agent="VPinFE Test"'
        )

        self.assertEqual(
            options,
            [
                "--disable-accelerated-video-decode",
                "--ozone-platform=x11",
                "--user-agent=VPinFE Test",
            ],
        )

    def test_launch_window_appends_additional_chromium_options(self) -> None:
        manager = ChromiumManager()
        proc = types.SimpleNamespace()
        monitor = types.SimpleNamespace(x=10, y=20, width=800, height=600)

        chromium = chromium_manager.ChromiumPath("/usr/bin/chromium", True)
        with (
            mock.patch("frontend.chromium_manager.get_chromium_path", return_value=chromium),
            mock.patch("frontend.chromium_manager.os.path.exists", return_value=True),
            mock.patch("frontend.chromium_manager.tempfile.mkdtemp",
                       return_value="/tmp/vpinfe-profile"),
            mock.patch("frontend.chromium_manager.subprocess.Popen", return_value=proc) as popen,
        ):
            manager.launch_window(
                "table",
                "http://127.0.0.1:8001/app/table",
                monitor,
                0,
                additional_options="--disable-accelerated-video-decode\n--ozone-platform=x11",
            )

        args = popen.call_args.args[0]
        self.assertIn("--disable-accelerated-video-decode", args)
        self.assertIn("--ozone-platform=x11", args)

    def _library_path_a_window_gets(self, local_install: bool) -> str | None:
        chromium = chromium_manager.ChromiumPath("/usr/bin/chromium", local_install)
        monitor = types.SimpleNamespace(x=10, y=20, width=800, height=600)
        with (
            mock.patch("frontend.chromium_manager.get_chromium_path", return_value=chromium),
            mock.patch("frontend.chromium_manager.os.path.exists", return_value=True),
            mock.patch("frontend.chromium_manager.tempfile.mkdtemp",
                       return_value="/tmp/vpinfe-profile"),
            mock.patch("frontend.chromium_manager.subprocess.Popen",
                       return_value=types.SimpleNamespace()) as popen,
            mock.patch("sys.frozen", True, create=True),
            mock.patch("platform.system", return_value="Linux"),
            mock.patch.dict("os.environ", {"LD_LIBRARY_PATH": "/opt/vpinfe/_internal"}),
        ):
            ChromiumManager().launch_window("table", "http://127.0.0.1:8001/app/table",
                                            monitor, 0)
        return popen.call_args.kwargs["env"].get("LD_LIBRARY_PATH")

    def test_a_local_chromium_does_not_get_the_bundled_library_path(self) -> None:
        self.assertIsNone(self._library_path_a_window_gets(local_install=True))

    def test_the_built_in_chromium_keeps_the_bundled_library_path(self) -> None:
        self.assertEqual(self._library_path_a_window_gets(local_install=False),
                         "/opt/vpinfe/_internal")

    def test_an_added_disable_features_is_folded_into_the_one_a_window_gets(self) -> None:
        manager = ChromiumManager()
        monitor = types.SimpleNamespace(x=10, y=20, width=800, height=600)

        chromium = chromium_manager.ChromiumPath("/usr/bin/chromium", True)
        with (
            mock.patch("frontend.chromium_manager.get_chromium_path", return_value=chromium),
            mock.patch("frontend.chromium_manager.os.path.exists", return_value=True),
            mock.patch("frontend.chromium_manager.tempfile.mkdtemp",
                       return_value="/tmp/vpinfe-profile"),
            mock.patch("frontend.chromium_manager.subprocess.Popen",
                       return_value=types.SimpleNamespace()) as popen,
        ):
            manager.launch_window(
                "table", "http://127.0.0.1:8001/app/table", monitor, 0,
                additional_options="--disable-features=HardwareMediaKeyHandling",
            )

        args = popen.call_args.args[0]
        features = [arg for arg in args if arg.startswith("--disable-features=")]
        self.assertEqual(len(features), 1)
        self.assertTrue(features[0].endswith(",HardwareMediaKeyHandling"))
        self.assertIn("CalculateNativeWindowOcclusion", features[0])

    def test_a_stray_quote_in_the_exclusions_does_not_stop_the_launch(self) -> None:
        """A typo in a text field cost the whole frontend. `chrome_options` was
        guarded and `chrome_options_exclude` was not, though both are the same
        setting shape parsed by the same function - so an unbalanced quote in the
        second raised out of the args list and every window went with it."""
        manager = ChromiumManager()
        proc = types.SimpleNamespace()
        monitor = types.SimpleNamespace(x=10, y=20, width=800, height=600)

        chromium = chromium_manager.ChromiumPath("/usr/bin/chromium", True)
        with (
            mock.patch("frontend.chromium_manager.get_chromium_path", return_value=chromium),
            mock.patch("frontend.chromium_manager.os.path.exists", return_value=True),
            mock.patch("frontend.chromium_manager.tempfile.mkdtemp",
                       return_value="/tmp/vpinfe-profile"),
            mock.patch("frontend.chromium_manager.subprocess.Popen", return_value=proc) as popen,
        ):
            manager.launch_window(
                "table",
                "http://127.0.0.1:8001/app/table",
                monitor,
                0,
                mute_audio=True,
                exclude_options='--mute-audio "',
            )

        args = popen.call_args.args[0]
        self.assertTrue(args, "the window still launches")
        # The exclusion is ignored rather than half-applied: nothing was excluded,
        # so the default it named is still there.
        self.assertIn("--mute-audio", args)

    def test_a_stray_quote_in_the_options_does_not_stop_the_launch(self) -> None:
        manager = ChromiumManager()
        proc = types.SimpleNamespace()
        monitor = types.SimpleNamespace(x=10, y=20, width=800, height=600)

        chromium = chromium_manager.ChromiumPath("/usr/bin/chromium", True)
        with (
            mock.patch("frontend.chromium_manager.get_chromium_path", return_value=chromium),
            mock.patch("frontend.chromium_manager.os.path.exists", return_value=True),
            mock.patch("frontend.chromium_manager.tempfile.mkdtemp",
                       return_value="/tmp/vpinfe-profile"),
            mock.patch("frontend.chromium_manager.subprocess.Popen", return_value=proc) as popen,
        ):
            manager.launch_window(
                "table",
                "http://127.0.0.1:8001/app/table",
                monitor,
                0,
                additional_options='--ozone-platform=x11 "',
            )

        self.assertTrue(popen.call_args.args[0])

    def test_launch_window_can_disable_default_chromium_options(self) -> None:
        manager = ChromiumManager()
        proc = types.SimpleNamespace()
        monitor = types.SimpleNamespace(x=10, y=20, width=800, height=600)

        chromium = chromium_manager.ChromiumPath("/usr/bin/chromium", True)
        with (
            mock.patch("frontend.chromium_manager.get_chromium_path", return_value=chromium),
            mock.patch("frontend.chromium_manager.os.path.exists", return_value=True),
            mock.patch("frontend.chromium_manager.tempfile.mkdtemp",
                       return_value="/tmp/vpinfe-profile"),
            mock.patch("frontend.chromium_manager.subprocess.Popen", return_value=proc) as popen,
        ):
            manager.launch_window(
                "table",
                "http://127.0.0.1:8001/app/table",
                monitor,
                0,
                include_default_options=False,
            )

        args = popen.call_args.args[0]
        self.assertIn("--app=http://127.0.0.1:8001/app/table", args)
        self.assertIn("--window-size=800,600", args)
        self.assertNotIn("--kiosk", args)
        self.assertNotIn("--disable-background-networking", args)

    def test_every_window_carries_a_class_named_for_it(self) -> None:
        for window_name in ("table", "bg", "dmd"):
            options = chromium_manager.get_builtin_chromium_options(
                window_name, include_default_options=False)
            self.assertIn(f"--class=vpinfe-{window_name}", options)
            self.assertIn(f"--window-name=vpinfe-{window_name}", options)

    def test_wait_ignores_exited_launcher_while_window_connected(self) -> None:
        manager = ChromiumManager()
        proc = types.SimpleNamespace(poll=mock.Mock(return_value=0), returncode=0)
        manager._processes = [("table", proc, None, None)]  # type: ignore[list-item]
        connected_states = iter([True, False])
        manager.terminate_all = mock.Mock(  # type: ignore[method-assign]
            side_effect=lambda: manager._exit_event.set())

        manager.wait_for_exit(
            is_window_connected=lambda window_name: next(connected_states, False)
        )

        proc.poll.assert_called()
        manager.terminate_all.assert_called_once()

    def test_request_exit_unblocks_a_wait_without_closing_the_windows(self) -> None:
        manager = ChromiumManager()
        proc = types.SimpleNamespace(poll=mock.Mock(return_value=None), returncode=None)
        manager._processes = [("table", proc, None, None)]  # type: ignore[list-item]
        threading.Timer(0.1, manager.request_exit).start()

        manager.wait_for_exit()

        self.assertEqual(manager._processes, [("table", proc, None, None)])

    def test_terminate_all_is_a_no_op_once_the_windows_are_gone(self) -> None:
        manager = ChromiumManager()

        manager.terminate_all()

        self.assertTrue(manager._exit_event.is_set())


class WindowRoleTests(unittest.TestCase):
    """What each window gets at launch, whichever contract named it."""

    OVERRIDES = {"backglass": "10,20,300,400", "scoreview": "1,2,3,4"}

    def _launch(self, contract: int) -> tuple[ChromiumManager, list[Any]]:
        ini = Path(self.enterContext(tempfile.TemporaryDirectory())) / "vpinfe.ini"
        ini.write_text("[windows.playfield]\nscreen_id = 0\n"
                       "[windows.backglass]\nscreen_id = 1\n"
                       f"override = {self.OVERRIDES['backglass']}\n"
                       "[windows.scoreview]\nscreen_id = 2\n"
                       f"override = {self.OVERRIDES['scoreview']}\n", encoding="utf-8")
        config = ConfigStore(str(ini))
        screens = [types.SimpleNamespace(x=i * 100, y=0, width=100, height=100)
                   for i in range(3)]
        events: list[Any] = []

        def launch(manager: ChromiumManager, name: str, url: str, monitor: Any,
                   index: int, **kwargs: Any) -> None:
            override = parse_qs(urlsplit(url).query).get("override", [""])[0]
            events.append((name, override, kwargs["mute_audio"]))
            manager._processes.append((name, mock.Mock(pid=100 + index), "", monitor))

        # `time.sleep` is the process's, so a thread another test left running would
        # write its pauses into `events` too; only this thread's are the launch's.
        launching, really_sleep = threading.get_ident(), chromium_manager.time.sleep

        def sleep(seconds: float) -> None:
            if threading.get_ident() == launching:
                events.append("pause")
            else:
                really_sleep(seconds)

        with (
            mock.patch.object(theme_windows, "active",
                              return_value=theme_windows.DEFAULT_WINDOWS[contract]),
            mock.patch.object(sys, "platform", "darwin"),
            mock.patch.object(chromium_manager, "get_mac_screens", return_value=screens),
            mock.patch.object(ChromiumManager, "launch_window", launch),
            mock.patch.object(ChromiumManager, "_focus_game_window_mac"),
            mock.patch.object(chromium_manager, "sweep_stale_profiles", return_value=0),
            mock.patch.object(chromium_manager.time, "sleep", side_effect=sleep),
        ):
            manager = ChromiumManager()
            manager.launch_all_windows(config)
        return manager, events

    def test_the_controller_plays_sound_opens_last_and_the_others_get_their_override(
            self) -> None:
        for contract, (controller, backglass, scoreview) in (
                theme_windows.DEFAULT_WINDOWS.items()):
            with self.subTest(contract=contract):
                _manager, events = self._launch(contract)

                self.assertEqual(events, [
                    (scoreview, self.OVERRIDES["scoreview"], True),
                    (backglass, self.OVERRIDES["backglass"], True),
                    "pause",
                    (controller, "", False),
                ])

    def _focused_on_macos(self, contract: int) -> list[int]:
        """The pids macOS was asked to bring forward, after a launch."""
        manager, _events = self._launch(contract)
        activated: list[int] = []
        apps = [types.SimpleNamespace(
                    processIdentifier=lambda pid=pid: pid,
                    activateWithOptions_=lambda _how, pid=pid: activated.append(pid))
                for pid in (100, 101, 102)]
        workspace = types.SimpleNamespace(runningApplications=lambda: apps)
        appkit = types.SimpleNamespace(
            NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda: workspace),
            NSApplicationActivateIgnoringOtherApps=1)

        with (mock.patch.dict("sys.modules", {"AppKit": appkit}),
              mock.patch.object(chromium_manager.time, "sleep")):
            manager._focus_game_window_mac()
        return activated

    def _restored_on_windows(self, contract: int) -> list[tuple[str, int]]:
        """What Windows was told, with the controller minimized first."""
        manager, _events = self._launch(contract)
        manager._minimized_hwnds = [(name, hwnd) for hwnd, name
                                    in enumerate(theme_windows.DEFAULT_WINDOWS[contract])]
        calls: list[tuple[str, int]] = []
        user32 = types.SimpleNamespace(
            ShowWindow=lambda hwnd, _how: calls.append(("show", hwnd)),
            SetForegroundWindow=lambda hwnd: calls.append(("front", hwnd)))

        with (mock.patch.object(sys, "platform", "win32"),
              mock.patch("ctypes.WinDLL", create=True, return_value=user32)):
            manager.restore_all_windows()
        return calls

    def test_macos_focuses_the_controller(self) -> None:
        for contract in theme_windows.DEFAULT_WINDOWS:
            with self.subTest(contract=contract):
                self.assertEqual(self._focused_on_macos(contract), [100])

    def test_windows_restores_the_controller_last_and_brings_it_forward(self) -> None:
        for contract in theme_windows.DEFAULT_WINDOWS:
            with self.subTest(contract=contract):
                self.assertEqual(self._restored_on_windows(contract),
                                 [("show", 1), ("show", 2), ("show", 0), ("front", 0)])


class NoBrowserTests(unittest.TestCase):
    """A browser that is not there is the same fact for every window."""

    def _launch(self) -> mock.Mock:
        ini = Path(self.enterContext(tempfile.TemporaryDirectory())) / "vpinfe.ini"
        ini.write_text("[windows.playfield]\nscreen_id = 0\n"
                       "[windows.backglass]\nscreen_id = 1\n", encoding="utf-8")
        config = ConfigStore(str(ini))
        screens = [types.SimpleNamespace(x=i * 100, y=0, width=100, height=100)
                   for i in range(2)]
        launch = mock.Mock(side_effect=chromium_manager.BrowserNotFoundError("no chrome"))
        with (
            mock.patch.object(theme_windows, "active",
                              return_value=theme_windows.DEFAULT_WINDOWS[2]),
            mock.patch.object(sys, "platform", "linux"),
            mock.patch("screeninfo.get_monitors", return_value=screens),
            mock.patch.object(ChromiumManager, "launch_window", launch),
            mock.patch.object(chromium_manager.time, "sleep"),
        ):
            ChromiumManager().launch_all_windows(config)
        return launch

    def test_one_plain_line_says_so_and_no_other_window_is_tried(self) -> None:
        with self.assertLogs(chromium_manager.logger, "INFO") as logged:
            launch = self._launch()

        warnings = [record for record in logged.records if record.levelname != "INFO"]
        self.assertEqual([record.levelname for record in warnings], ["WARNING"])
        self.assertIsNone(warnings[0].exc_info)
        launch.assert_called_once()

    def test_a_path_with_nothing_there_raises_the_error_that_says_so(self) -> None:
        monitor = types.SimpleNamespace(x=0, y=0, width=800, height=600)
        gone = chromium_manager.ChromiumPath("/nowhere/chrome", True)
        with mock.patch("frontend.chromium_manager.get_chromium_path", return_value=gone), \
                self.assertRaises(chromium_manager.BrowserNotFoundError):
            ChromiumManager().launch_window("table", "http://127.0.0.1:8001/", monitor, 0)

    def test_a_window_that_fails_for_another_reason_still_leaves_the_rest(self) -> None:
        launch = mock.Mock(side_effect=[RuntimeError("no"), None])
        ini = Path(self.enterContext(tempfile.TemporaryDirectory())) / "vpinfe.ini"
        ini.write_text("[windows.playfield]\nscreen_id = 0\n"
                       "[windows.backglass]\nscreen_id = 1\n", encoding="utf-8")
        screens = [types.SimpleNamespace(x=i * 100, y=0, width=100, height=100)
                   for i in range(2)]
        with (
            mock.patch.object(theme_windows, "active",
                              return_value=theme_windows.DEFAULT_WINDOWS[2]),
            mock.patch.object(sys, "platform", "linux"),
            mock.patch("screeninfo.get_monitors", return_value=screens),
            mock.patch.object(ChromiumManager, "launch_window", launch),
            mock.patch.object(chromium_manager.time, "sleep"),
            self.assertLogs(chromium_manager.logger, "ERROR"),
        ):
            ChromiumManager().launch_all_windows(ConfigStore(str(ini)))

        self.assertEqual(launch.call_count, 2)


class ScreenWaitTests(unittest.TestCase):
    """Three windows on screens 0, 1 and 2, and a screen list that is short at first."""

    def _launch(self, platform: str, answers: list[int]) -> tuple[list[str], int]:
        ini = Path(self.enterContext(tempfile.TemporaryDirectory())) / "vpinfe.ini"
        ini.write_text("[windows.playfield]\nscreen_id = 0\n"
                       "[windows.backglass]\nscreen_id = 1\n"
                       "[windows.scoreview]\nscreen_id = 2\n", encoding="utf-8")
        config = ConfigStore(str(ini))
        counts = iter(answers)
        asked = 0
        clock = [0.0]

        def screens() -> list[Any]:
            nonlocal asked
            asked += 1
            count = next(counts, answers[-1])
            return [types.SimpleNamespace(x=i * 100, y=0, width=100, height=100)
                    for i in range(count)]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        placed: list[str] = []

        def launch(manager: ChromiumManager, name: str, url: str, monitor: Any,
                   index: int, **kwargs: Any) -> None:
            placed.append(name)

        with (
            mock.patch.object(theme_windows, "active",
                              return_value=theme_windows.DEFAULT_WINDOWS[2]),
            mock.patch.object(sys, "platform", platform),
            mock.patch("screeninfo.get_monitors", screens),
            mock.patch.object(chromium_manager, "get_mac_screens", screens),
            mock.patch.object(ChromiumManager, "launch_window", launch),
            mock.patch.object(ChromiumManager, "_focus_game_window_mac"),
            mock.patch.object(chromium_manager.time, "sleep", sleep),
            mock.patch.object(chromium_manager.time, "monotonic", lambda: clock[0]),
        ):
            ChromiumManager().launch_all_windows(config)
        return sorted(placed), asked

    def test_a_screen_list_that_fills_in_late_places_every_window(self) -> None:
        placed, asked = self._launch("linux", [1, 1, 3])

        self.assertEqual(placed, ["backglass", "playfield", "scoreview"])
        self.assertEqual(asked, 3)

    def test_screens_that_never_arrive_end_the_wait_and_the_rest_still_open(self) -> None:
        placed, asked = self._launch("linux", [1])

        self.assertEqual(placed, ["playfield"])
        self.assertEqual(asked, 1 + round(chromium_manager.SCREEN_WAIT_S
                                          / chromium_manager.SCREEN_POLL_S))

    def test_macos_takes_the_first_answer(self) -> None:
        placed, asked = self._launch("darwin", [1, 3])

        self.assertEqual(placed, ["playfield"])
        self.assertEqual(asked, 1)


class LibraryEndpointTests(unittest.TestCase):
    """Reading `network.library_url` into the three values a window url carries."""

    @staticmethod
    def _network(library_url: str = "", http_port: int = 8001):
        return types.SimpleNamespace(library_url=library_url, http_port=http_port)

    def _resolve(self, *args, **kwargs):
        return chromium_manager._library_endpoint(self._network(*args, **kwargs))

    def test_no_library_set_and_both_ports_are_this_install(self) -> None:
        for value in ("", "   "):
            with self.subTest(value=value):
                self.assertEqual(self._resolve(value), ("", 8001, 8001))

    def test_a_library_url_yields_its_host_and_port(self) -> None:
        self.assertEqual(self._resolve("http://cab.local:8001"), ("cab.local", 8001, 8001))

    def test_the_library_port_and_this_machine_s_port_are_answered_separately(self) -> None:
        """The one that would misdial in both directions if a single number were sent."""
        self.assertEqual(self._resolve("https://library.example:9000"),
                         ("library.example", 9000, 8001))

    def test_a_url_with_no_port_falls_back_to_this_install_s(self) -> None:
        self.assertEqual(self._resolve("http://cab.local", 8005), ("cab.local", 8005, 8005))


class WindowUrlTests(unittest.TestCase):
    """A window has to be told where the services are: it cannot ask, because asking
    needs the bridge and finding the bridge needs a port. A port missing here is a
    frontend dialling the wrong one forever, which is why every form is checked."""

    def _url(self, system: str, *, splash: bool = False, library_host: str = "",
             http_port: int = 9001, device_port: int = 9001) -> str:
        with mock.patch("frontend.chromium_manager.platform.system", return_value=system):
            return chromium_manager._build_window_url(
                base_url="http://127.0.0.1",
                theme_assets_port=9000,
                theme_name="Some Theme",
                window_name="playfield",
                splash_enabled=splash,
                ws_port=9002,
                http_port=http_port,
                library_host=library_host,
                device_port=device_port,
            )

    def test_every_window_url_carries_every_port(self) -> None:
        for system, splash, label in (("Linux", False, "the /app/ bootstrap"),
                                      ("Darwin", True, "the splash page"),
                                      ("Darwin", False, "a theme page")):
            with self.subTest(label):
                url = self._url(system, splash=splash)

                self.assertIn("wsPort=9002", url)
                self.assertIn("themeAssetsPort=9000", url)
                self.assertIn("libraryPort=9001", url)

    def test_no_library_host_leaves_the_url_as_it_was(self) -> None:
        """Every single-machine install. The page keeps assuming loopback for everything,
        so a setting nobody set cannot change what a window opens with."""
        for system, splash, label in (("Linux", False, "the /app/ bootstrap"),
                                      ("Darwin", True, "the splash page"),
                                      ("Darwin", False, "a theme page")):
            with self.subTest(label):
                url = self._url(system, splash=splash)

                self.assertNotIn("libraryHost", url)
                self.assertNotIn("devicePort", url)

    def test_a_remote_library_travels_in_every_window_url(self) -> None:
        """A page cannot ask where its library is for the same reason it cannot ask for a
        port, so the host takes the same route."""
        for system, splash, label in (("Linux", False, "the /app/ bootstrap"),
                                      ("Darwin", True, "the splash page"),
                                      ("Darwin", False, "a theme page")):
            with self.subTest(label):
                url = self._url(system, splash=splash, library_host="library.example")

                self.assertIn("libraryHost=library.example", url)

    def test_the_library_port_and_this_machine_s_port_travel_separately(self) -> None:
        """A library on 9000 is not this machine on 9000. Sending one number would make an
        install dial its own api at the library's port, or the library's at its own."""
        url = self._url("Darwin", library_host="library.example", http_port=9000,
                        device_port=8001)

        self.assertIn("libraryPort=9000", url)
        self.assertIn("devicePort=8001", url)

    def test_a_host_that_needs_encoding_is_encoded(self) -> None:
        url = self._url("Darwin", library_host="library name")

        self.assertIn("libraryHost=library%20name", url)

    def test_the_ports_are_query_parameters_of_the_page(self) -> None:
        """Appended to whatever the form already asks for, not replacing it."""
        url = self._url("Darwin")

        self.assertIn("index_playfield.html?window=playfield&", url)

    def test_a_given_page_replaces_the_theme_s_own(self) -> None:
        with mock.patch("frontend.chromium_manager.platform.system", return_value="Darwin"):
            url = chromium_manager._build_window_url(
                base_url="http://127.0.0.1", theme_assets_port=9000,
                theme_name="Some Theme", window_name="topper", splash_enabled=False,
                page="core/index_topper.html")

        self.assertTrue(url.startswith(
            "http://127.0.0.1:9000/core/index_topper.html?window=topper&wsPort="), url)


def _ns_screen(x: float, y: float, width: float, height: float) -> types.SimpleNamespace:
    """An NSScreen as AppKit hands it over: a frame whose origin is its bottom-left corner,
    measured up from the bottom of the primary screen."""
    frame = types.SimpleNamespace(origin=types.SimpleNamespace(x=x, y=y),
                                  size=types.SimpleNamespace(width=width, height=height))
    return types.SimpleNamespace(frame=lambda: frame)


class MacScreenTests(unittest.TestCase):
    def _placed(self, *screens: types.SimpleNamespace) -> list[tuple[int, int, int, int]]:
        appkit = types.SimpleNamespace(
            NSScreen=types.SimpleNamespace(screens=lambda: list(screens)))
        with mock.patch.dict("sys.modules", {"AppKit": appkit}):
            return [tuple(one) for one in chromium_manager.get_mac_screens()]

    def test_a_screen_above_the_primary_is_placed_above_its_top(self) -> None:
        self.assertEqual(self._placed(_ns_screen(0, 0, 1728, 1117),
                                      _ns_screen(0, 1117, 2560, 1440)),
                         [(0, 0, 1728, 1117), (0, -1440, 2560, 1440)])

    def test_a_screen_beside_or_below_the_primary_is_placed_from_its_top(self) -> None:
        self.assertEqual(self._placed(_ns_screen(0, 0, 1728, 1117),
                                      _ns_screen(1728, 37, 1920, 1080),
                                      _ns_screen(0, -1080, 1920, 1080)),
                         [(0, 0, 1728, 1117), (1728, 0, 1920, 1080), (0, 1117, 1920, 1080)])

    def test_the_primary_is_where_positions_start_whatever_else_is_connected(self) -> None:
        self.assertEqual(self._placed(_ns_screen(0, 0, 1728, 1117),
                                      _ns_screen(0, 1117, 2560, 1440),
                                      _ns_screen(1728, 37, 1920, 1080),
                                      _ns_screen(0, -1080, 1920, 1080)),
                         [(0, 0, 1728, 1117), (0, -1440, 2560, 1440),
                          (1728, 0, 1920, 1080), (0, 1117, 1920, 1080)])


def _process_list_that_fails(*_args: Any, **_kwargs: Any) -> Any:
    raise SystemError("<built-in function proc_cmdline> returned a result with an "
                      "exception set")
    yield


class StaleProfileSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.leftover = Path(tmp.name) / f"{chromium_manager.PROFILE_PREFIX}leftover"
        self.leftover.mkdir()
        patcher = mock.patch("frontend.chromium_manager.tempfile.gettempdir",
                             return_value=tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_process_list_that_cannot_be_read_sweeps_nothing(self) -> None:
        with mock.patch("psutil.process_iter", side_effect=_process_list_that_fails):
            ChromiumManager()

        self.assertTrue(self.leftover.is_dir())

    def test_a_profile_no_process_holds_is_swept(self) -> None:
        with mock.patch("psutil.process_iter", return_value=iter(())):
            ChromiumManager()

        self.assertFalse(self.leftover.exists())


if __name__ == "__main__":
    unittest.main()

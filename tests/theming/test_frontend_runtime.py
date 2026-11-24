"""A kill signal takes the same way out as a user closing a window."""

from __future__ import annotations

import configparser
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from common.runtime import lifecycle, shutdown
from frontend import runtime
from frontend.chromium_manager import ChromiumManager

# `install_setup.pending()` reads this as false: no `device.setup_pending` option means
# `cfg_bool`'s own fallback answers.
_NOT_PENDING = mock.Mock(config=configparser.ConfigParser())


def _browser_that_waits():
    """A stand-in for ChromiumManager: blocks until request_exit is called."""
    exited = threading.Event()
    browser = mock.Mock()
    browser.request_exit.side_effect = exited.set

    def _wait_for_exit(**_kwargs):
        deadline = time.monotonic() + 10.0
        while not exited.wait(0.05):
            if time.monotonic() > deadline:
                raise AssertionError("the signal never reached request_exit")

    browser.wait_for_exit.side_effect = _wait_for_exit
    return browser


# Windows defines SIGTERM but does not deliver it: os.kill falls through to
# TerminateProcess and the whole run dies, with no failure and no summary. Same guard,
# same reason, as tests/host/test_shutdown.py.
@unittest.skipIf(sys.platform.startswith("win"),
                 "Windows terminates the process instead of delivering SIGTERM")
@unittest.skipIf(not hasattr(signal, "SIGTERM"), "no SIGTERM on this platform")
class TerminationSignalTests(unittest.TestCase):
    def setUp(self):
        for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                self.addCleanup(signal.signal, sig, signal.getsignal(sig))
        # Absorb the signal here first. If run_frontend_loop ever stops installing its
        # own handler, these tests have to fail - not kill the whole run, which is what
        # SIGTERM does by default to the process sending it to itself.
        signal.signal(signal.SIGTERM, lambda *_args: None)
        shutdown.reset_for_tests()
        self.addCleanup(shutdown.reset_for_tests)

    def _send_sigterm_shortly(self):
        killer = threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM))
        killer.start()
        self.addCleanup(killer.cancel)

    def test_sigterm_closes_the_windows_and_returns_for_shutdown(self):
        browser = _browser_that_waits()
        self._send_sigterm_shortly()

        with mock.patch.object(runtime, "get_display_monitors", return_value=[]):
            runtime.run_frontend_loop(
                False,
                _NOT_PENDING,
                browser,
                threading.Event(),
                mock.Mock(),
            )

        browser.terminate_all.assert_called_once_with()

    def test_sigterm_ends_the_headless_loop(self):
        shutdown_event = threading.Event()
        self._send_sigterm_shortly()
        failsafe = threading.Timer(10.0, shutdown_event.set)
        failsafe.start()
        self.addCleanup(failsafe.cancel)
        started = time.monotonic()

        runtime.run_frontend_loop(True, mock.Mock(), mock.Mock(), shutdown_event, mock.Mock())

        self.assertLess(time.monotonic() - started, 5.0, "the signal never ended the loop")

    def test_a_headless_run_closes_windows_opened_since_it_started_when_it_ends(self):
        shutdown_event = threading.Event()
        shutdown_event.set()
        browser = mock.Mock()

        runtime.run_frontend_loop(True, mock.Mock(), browser, shutdown_event, mock.Mock())

        browser.terminate_all.assert_called_once_with()

    def test_a_closed_window_still_ends_the_loop_without_a_signal(self):
        browser = mock.Mock()
        browser.watch.side_effect = lambda *, on_lost, **_kwargs: on_lost()

        with mock.patch.object(runtime, "get_display_monitors", return_value=[]):
            runtime.run_frontend_loop(
                False,
                _NOT_PENDING,
                browser,
                threading.Event(),
                mock.Mock(),
            )

        browser.launch_all_windows.assert_called_once()
        browser.terminate_all.assert_called_once_with()

    def test_no_window_opening_at_all_still_ends_the_run(self):
        browser = mock.Mock(has_windows=False)

        with mock.patch.object(runtime, "get_display_monitors", return_value=[]):
            runtime.run_frontend_loop(False, _NOT_PENDING, browser, threading.Event(),
                                      mock.Mock())

        browser.terminate_all.assert_called_once_with()

    def test_a_signal_during_startup_stops_the_frontend_opening_at_all(self):
        browser = _browser_that_waits()
        shutdown.watch_during_startup()
        os.kill(os.getpid(), signal.SIGTERM)

        with mock.patch.object(runtime, "get_display_monitors", return_value=[]):
            runtime.run_frontend_loop(
                False,
                _NOT_PENDING,
                browser,
                threading.Event(),
                mock.Mock(),
            )

        browser.launch_all_windows.assert_not_called()
        browser.watch.assert_not_called()


class SetupPendingTests(unittest.TestCase):
    """While Setup is pending the one window is Setup itself, and the real ones wait."""

    def test_the_window_opens_setup_and_says_it_is_the_frontends_own(self):
        browser = _browser_that_waits()
        threading.Timer(0.05, browser.request_exit).start()

        with mock.patch.object(runtime, "get_display_monitors",
                               return_value=[mock.Mock()]), \
             mock.patch.object(runtime, "install_setup") as fake_pending, \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
            fake_pending.pending.return_value = True
            runtime.run_frontend_loop(False, mock.Mock(), browser, threading.Event(),
                                      mock.Mock())

        url = browser.launch_window.call_args.kwargs["url"]
        self.assertTrue(url.startswith("http://localhost:"), url)
        self.assertTrue(url.endswith("/?setup=window"), url)

    def test_closing_the_window_while_still_pending_quits(self):
        browser = _browser_that_waits()
        threading.Timer(0.05, browser.request_exit).start()

        with mock.patch.object(runtime, "get_display_monitors",
                               return_value=[mock.Mock()]), \
             mock.patch.object(runtime, "install_setup") as fake_pending, \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
            fake_pending.pending.return_value = True
            runtime.run_frontend_loop(False, mock.Mock(), browser, threading.Event(),
                                      mock.Mock())

        browser.launch_all_windows.assert_not_called()

    def test_setup_finishing_under_the_window_opens_the_real_ones(self):
        browser = _browser_that_waits()
        shutdown_event = threading.Event()
        browser.launch_all_windows.side_effect = lambda _config: shutdown_event.set()
        seen = {"n": 0}

        def _still_pending(_config) -> bool:
            seen["n"] += 1
            return seen["n"] < 3

        with mock.patch.object(runtime, "get_display_monitors",
                               return_value=[mock.Mock()]), \
             mock.patch.object(runtime, "SETUP_POLL_S", 0.01), \
             mock.patch.object(runtime, "install_setup") as fake_pending, \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
            fake_pending.pending.side_effect = _still_pending
            runtime.run_frontend_loop(False, mock.Mock(), browser, shutdown_event,
                                      mock.Mock())

        browser.launch_all_windows.assert_called_once()

    def test_a_signal_while_setup_closes_does_not_open_the_real_windows(self):
        self.addCleanup(shutdown.reset_for_tests)
        browser = mock.Mock()
        browser.wait_for_exit.side_effect = lambda **_kwargs: None
        browser.terminate_all.side_effect = shutdown._requested.set

        with mock.patch.object(runtime, "get_display_monitors",
                               return_value=[mock.Mock()]), \
             mock.patch.object(runtime, "install_setup") as fake_setup, \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
            fake_setup.pending.side_effect = [True, False, False]
            runtime.run_frontend_loop(False, mock.Mock(), browser, threading.Event(),
                                      mock.Mock())

        browser.launch_all_windows.assert_not_called()

    def test_the_real_windows_wait_after_the_setup_window_has_closed(self):
        """The real manager's exit flag is set by closing the Setup window and stays set,
        so a wait that opened the real windows next returned at once and took them down."""
        with mock.patch("frontend.chromium_manager.sweep_stale_profiles"):
            manager = ChromiumManager()
        setup_finished = threading.Event()
        real_windows_open = threading.Event()
        loop_returned = threading.Event()
        shutdown_event = threading.Event()
        opened: list[str] = []

        def _open_a_window(name: str) -> None:
            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            profile = tempfile.mkdtemp(prefix="vpinfe-test-profile-")
            manager._processes.append((name, process, profile, None))
            opened.append(name)

        self.addCleanup(lambda: [p.kill() for _n, p, _d, _m in manager._processes])
        browser = mock.Mock(wraps=manager)
        browser.launch_window.side_effect = lambda **kwargs: (
            _open_a_window("setup"), setup_finished.set())
        browser.launch_all_windows.side_effect = lambda _config: (
            _open_a_window("real"), real_windows_open.set())

        def _run() -> None:
            with mock.patch.object(runtime, "get_display_monitors",
                                   return_value=[mock.Mock()]), \
                 mock.patch.object(runtime, "SETUP_POLL_S", 0.01), \
                 mock.patch.object(runtime, "install_setup") as fake_setup, \
                 mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
                fake_setup.pending.side_effect = lambda _config: not setup_finished.is_set()
                runtime.run_frontend_loop(False, mock.Mock(), browser, shutdown_event,
                                          mock.Mock())
            loop_returned.set()

        self.addCleanup(shutdown_event.set)
        self.addCleanup(manager.terminate_all)
        threading.Thread(target=_run, daemon=True).start()

        self.assertTrue(real_windows_open.wait(10), "the real windows never opened")
        self.assertFalse(loop_returned.wait(0.5),
                         "the real windows' wait returned the moment they opened")
        self.assertEqual(opened, ["setup", "real"])
        self.assertTrue(manager.is_running)

        shutdown_event.set()
        self.assertTrue(loop_returned.wait(10), "asking to exit did not end the wait")


def _launch(manager: ChromiumManager, name: str) -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    manager._processes.append((name, process, tempfile.mkdtemp(prefix="vpinfe-test-"), None))
    return process


class WindowsComeAndGoTests(unittest.TestCase):
    """Closing the windows on request is not the run ending; closing one by hand is."""

    def setUp(self) -> None:
        with mock.patch("frontend.chromium_manager.sweep_stale_profiles"):
            self.manager = ChromiumManager()
        self.addCleanup(self.manager.terminate_all)

    def test_closing_the_windows_does_not_end_the_wait(self) -> None:
        process = _launch(self.manager, "table")

        self.assertTrue(self.manager.close_windows())

        self.assertIsNotNone(process.poll())
        self.assertFalse(self.manager.is_running)
        self.assertFalse(self.manager._exit_event.is_set())

    def test_closing_none_says_so(self) -> None:
        self.assertFalse(self.manager.close_windows())

    def test_terminating_ends_the_wait(self) -> None:
        _launch(self.manager, "table")

        self.manager.terminate_all()

        self.assertTrue(self.manager._exit_event.is_set())

    def test_a_monitor_of_closed_windows_stays_quiet_when_they_go(self) -> None:
        """The close moves the generation on before it kills anything, so a monitor that
        sees a window gone in between does not take it for a hand close."""
        process = _launch(self.manager, "table")
        lost = threading.Event()
        self.manager.watch(on_lost=lost.set)

        with self.manager._lock:
            self.manager._generation += 1
        process.kill()

        self.assertFalse(lost.wait(2), "a close nobody did by hand read as one")
        self.assertFalse(self.manager._exit_event.is_set())

    def _state(self, *, pending: bool, frontend: bool = True) -> str:
        with mock.patch.object(runtime, "install_setup") as setup, \
             mock.patch("common.devices.install_identity.has_feature", return_value=frontend), \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
            setup.pending.return_value = pending
            return runtime.windows_state(self.manager)

    def test_the_windows_are_closed_until_one_is_launched_and_open_after(self) -> None:
        self.assertEqual(self._state(pending=False), lifecycle.WINDOWS_CLOSED)

        _launch(self.manager, "table")

        self.assertEqual(self._state(pending=False), lifecycle.WINDOWS_OPEN)

        self.manager.close_windows()

        self.assertEqual(self._state(pending=False), lifecycle.WINDOWS_CLOSED)

    def test_a_reopen_in_between_closing_and_opening_still_reads_as_open(self) -> None:
        with runtime._reopening:
            self.assertEqual(self._state(pending=False), lifecycle.WINDOWS_OPEN)

    def test_an_install_without_the_frontend_feature_is_off_whatever_the_windows_do(self) -> None:
        _launch(self.manager, "table")

        self.assertEqual(self._state(pending=False, frontend=False), lifecycle.WINDOWS_OFF)
        self.assertEqual(self._state(pending=True, frontend=False), lifecycle.WINDOWS_OFF)

    def test_setup_open_is_its_own_state_whatever_the_windows_do(self) -> None:
        self.assertEqual(self._state(pending=True), lifecycle.WINDOWS_SETUP)

    def test_a_launcher_that_has_exited_still_counts_as_a_window(self) -> None:
        process = _launch(self.manager, "table")
        process.kill()
        process.wait()

        self.assertTrue(self.manager.has_windows)
        self.assertFalse(self.manager.is_running)

    def test_a_window_closed_by_hand_is_reported_and_takes_the_rest_down(self) -> None:
        first = _launch(self.manager, "table")
        second = _launch(self.manager, "bg")
        lost = threading.Event()
        self.manager.watch(on_lost=lost.set)

        first.kill()

        self.assertTrue(lost.wait(10), "a window closed by hand was not noticed")
        self.assertIsNotNone(second.wait(10))

    def test_windows_opened_after_a_close_are_watched_again(self) -> None:
        _launch(self.manager, "table")
        self.manager.watch(on_lost=lambda: None)
        self.manager.close_windows()
        process = _launch(self.manager, "table")
        lost = threading.Event()
        self.manager.watch(on_lost=lost.set)

        process.kill()

        self.assertTrue(lost.wait(10), "the reopened windows were not watched")


class ReopenWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(shutdown.reset_for_tests)
        shutdown.reset_for_tests()
        self.browser = mock.Mock()
        self.config = mock.Mock()
        self.bridge = mock.Mock()
        self.api = mock.Mock()
        self.bridge.apis.return_value = [self.api]
        self.event = threading.Event()
        self.order: list[str] = []
        self.browser.close_windows.side_effect = lambda: self.order.append("close")
        self.config.reread.side_effect = lambda: self.order.append("reread")
        self.browser.launch_all_windows.side_effect = lambda _c: self.order.append("open")

    def _served(self, *args) -> None:
        self.order.append("serve")
        self.served_with = args

    def _reopen(self, *, pending: bool = False, media_changed: bool = False,
                keep_running: bool = False) -> None:
        with mock.patch.object(runtime, "get_display_monitors", return_value=[]), \
             mock.patch.object(runtime, "install_setup") as setup, \
             mock.patch.object(runtime, "register_window_apis",
                               side_effect=self._served), \
             mock.patch.object(runtime, "_apply_theme_media_sets",
                               side_effect=lambda *_a: self.order.append("sets")), \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()), \
             mock.patch("common.games.game_repository.reread_media_settings",
                        return_value=media_changed):
            setup.pending.return_value = pending
            runtime.reopen_windows(self.config, self.browser, self.bridge, self.event,
                                   mock.Mock(), keep_running=keep_running)

    def test_the_settings_are_read_again_before_the_windows_open(self) -> None:
        self._reopen()

        self.assertEqual(self.order, ["close", "reread", "sets", "serve", "open"])
        self.browser.watch.assert_called_once()

    def test_a_window_the_settings_newly_serve_gets_its_api_before_it_opens(self) -> None:
        self._reopen()

        self.assertIs(self.served_with[1], self.bridge)
        self.assertIs(self.served_with[2], self.browser)

    def test_a_lost_window_ends_the_run_through_the_event(self) -> None:
        self._reopen()

        self.browser.watch.call_args.kwargs["on_lost"]()

        self.assertTrue(self.event.is_set())

    def test_a_run_that_keeps_going_clears_a_stale_exit_before_it_opens(self) -> None:
        self.browser.reset_exit.side_effect = lambda: self.order.append("reset")

        self._reopen(keep_running=True)

        self.assertEqual(self.order[-2:], ["reset", "open"])

    def test_a_lost_window_on_a_run_that_keeps_going_closes_the_windows_only(self) -> None:
        self._reopen(keep_running=True)
        self.browser.has_windows = False
        self.browser.reset_exit.reset_mock()

        with mock.patch.object(runtime.settings_waiting, "windows_closed") as closed:
            self.browser.watch.call_args.kwargs["on_lost"]()

        self.assertFalse(self.event.is_set())
        self.browser.reset_exit.assert_called_once_with()
        closed.assert_called_once_with()

    def test_a_library_resolved_under_other_media_is_marked_stale(self) -> None:
        self._reopen(media_changed=True)

        self.api.library.mark_stale.assert_called_once_with()

    def test_an_unchanged_library_is_left_alone(self) -> None:
        self._reopen(media_changed=False)

        self.api.library.mark_stale.assert_not_called()

    def test_nothing_reopens_while_setup_is_pending(self) -> None:
        self._reopen(pending=True)

        self.assertEqual(self.order, [])

    def test_nothing_reopens_once_the_run_is_ending(self) -> None:
        self.event.set()

        self._reopen()

        self.assertEqual(self.order, [])

    def test_a_quit_during_the_reread_opens_nothing(self) -> None:
        self.config.reread.side_effect = self.event.set

        self._reopen()

        self.browser.launch_all_windows.assert_not_called()

    def test_closing_leaves_the_run_going(self) -> None:
        with mock.patch.object(runtime, "install_setup") as setup, \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
            setup.pending.return_value = False
            runtime.close_windows(self.browser, mock.Mock())

        self.browser.close_windows.assert_called_once_with()
        self.browser.terminate_all.assert_not_called()

    def test_closing_while_setup_is_pending_closes_nothing(self) -> None:
        with mock.patch.object(runtime, "install_setup") as setup, \
             mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
            setup.pending.return_value = True
            runtime.close_windows(self.browser, mock.Mock())

        self.browser.close_windows.assert_not_called()

    def test_the_end_of_the_run_waits_for_a_reopen_in_progress(self) -> None:
        released = threading.Event()

        def _hold() -> None:
            with runtime._reopening:
                released.wait(5)

        holder = threading.Thread(target=_hold, daemon=True)
        holder.start()
        self.addCleanup(released.set)
        time.sleep(0.1)
        self.event.set()
        done = threading.Event()

        def _run() -> None:
            with mock.patch.object(runtime, "get_display_monitors", return_value=[]), \
                 mock.patch.object(runtime, "install_setup") as setup, \
                 mock.patch("common.config.paths.get_ini_config", return_value=mock.Mock()):
                setup.pending.return_value = False
                runtime.run_frontend_loop(False, mock.Mock(), self.browser, self.event,
                                          mock.Mock())
            done.set()

        threading.Thread(target=_run, daemon=True).start()

        self.assertFalse(done.wait(0.5), "the run ended under a reopen still launching")
        released.set()
        self.assertTrue(done.wait(5))
        self.browser.terminate_all.assert_called_once_with()

    def test_no_window_opening_keeps_the_run_going(self) -> None:
        self.browser.has_windows = False

        self._reopen()

        self.assertFalse(self.event.is_set())


class RegisterWindowApisTests(unittest.TestCase):
    """A window is refused at the socket until it has an API, and Setup and the Manager
    assign screens after the process has booted."""

    def _register(self, bridge, screens: dict[str, str], windows=("table", "bg", "dmd")):
        displays = mock.Mock()
        displays.window_screen_id.side_effect = lambda key: screens.get(key, "")
        with mock.patch.object(runtime, "DisplayConfig") as config, \
             mock.patch.object(runtime, "window_configs",
                               return_value=[(w, w) for w in windows]), \
             mock.patch.object(runtime, "API") as api:
            config.from_config.return_value = displays
            runtime.register_window_apis(mock.Mock(), bridge, mock.Mock(), mock.Mock())
        return api

    def _bridge(self, *registered: str):
        bridge = mock.Mock()
        bridge.registered_windows.return_value = frozenset(registered)
        bridge.apis.return_value = [mock.Mock()]
        return bridge

    def test_windows_that_gained_a_screen_are_registered(self) -> None:
        bridge = self._bridge("table")

        self._register(bridge, {"table": "0", "bg": "1", "dmd": "2"})

        self.assertEqual([c.args[0] for c in bridge.register_api.call_args_list],
                         ["bg", "dmd"])

    def test_a_window_with_no_screen_is_left_unregistered(self) -> None:
        bridge = self._bridge()

        self._register(bridge, {"table": "0", "bg": " "})

        self.assertEqual([c.args[0] for c in bridge.register_api.call_args_list], ["table"])

    def test_a_registered_window_keeps_the_api_it_has(self) -> None:
        bridge = self._bridge("table", "bg", "dmd")

        api = self._register(bridge, {"table": "0", "bg": "1", "dmd": "2"})

        bridge.register_api.assert_not_called()
        api.assert_not_called()

    def test_a_new_window_shares_the_library_the_others_show(self) -> None:
        bridge = self._bridge("table")

        api = self._register(bridge, {"table": "0", "bg": "1"})

        self.assertIs(api.call_args.kwargs["library"], bridge.apis.return_value[0].library)


class ThemeMediaSetTests(unittest.TestCase):
    def _apply(self, theme: dict) -> mock.Mock:
        with mock.patch("common.media.media_specs.set_media_set_override") as override, \
             mock.patch("frontend.theme_api.get_theme_config", return_value=theme):
            runtime._apply_theme_media_sets(mock.Mock(), mock.Mock())
        return override

    def test_the_theme_choice_is_applied(self) -> None:
        self._apply({"wheelSet": "Neon"}).assert_called_once_with("wheel", "Neon")

    def test_a_theme_that_names_none_clears_the_last_one(self) -> None:
        self._apply({}).assert_called_once_with("wheel", None)


if __name__ == "__main__":
    unittest.main()

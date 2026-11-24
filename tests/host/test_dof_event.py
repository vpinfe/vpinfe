"""Turning a DOF event on says why when it cannot, and never starts DOF under a table."""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any, cast
from unittest import mock

from common.host import dof_service, launch_state
from common.i18n import t

_INI: Any = "ini"


class _Helper:
    """The DOF helper, answering what it is told to."""

    def __init__(self, **replies: object) -> None:
        self.replies = {"start": {"ok": True, "started": True, "detail": "x"},
                        "send_event_token": {"ok": True, "sent": True},
                        "clear_event": {"ok": True, "cleared": True}, **replies}
        self.asked: list[tuple[str, dict]] = []

    def request(self, command: str, timeout: float = 0.0, **fields: object) -> object:
        self.asked.append((command, fields))
        return self.replies[command]

    def is_alive(self) -> bool:
        return True


class EventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _Helper()
        self.running = False
        self.playing = False
        self.enabled = True
        for patch in (
                mock.patch.object(dof_service, "_is_enabled", lambda _ini: self.enabled),
                mock.patch.object(dof_service, "_get_helper", lambda: self.helper),
                mock.patch.object(dof_service, "is_running", lambda: self.running),
                mock.patch.object(dof_service, "_HELPER", None),
                mock.patch.object(dof_service, "_CURRENT_EVENT", None),
                mock.patch.object(dof_service.dof_config, "base_path", lambda: None),
                mock.patch.object(launch_state, "current",
                                  lambda: launch_state.LaunchState(launching=self.playing))):
            self.enterContext(patch)

    def _asked(self, command: str) -> list[dict]:
        return [fields for asked, fields in self.helper.asked if asked == command]

    def test_it_goes_out_and_reads_as_a_yes(self) -> None:
        outcome = dof_service.send_dof_event_token(None, "e905")

        self.assertTrue(outcome)
        self.assertEqual(self._asked("send_event_token"), [{"event_token": "E905"}])

    def test_off_says_so_and_asks_the_helper_nothing(self) -> None:
        self.enabled = False

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertFalse(outcome)
        self.assertEqual(outcome.reason, dof_service.OFF)
        self.assertEqual(outcome.words(), t("dof.event.off"))
        self.assertEqual(self.helper.asked, [])

    def test_a_table_playing_stops_it_before_the_helper_starts(self) -> None:
        self.playing = True

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.PLAYING)
        self.assertEqual(outcome.words(), t("dof.event.playing"))
        self.assertEqual(self.helper.asked, [])

    def test_the_event_already_on_is_not_sent_again(self) -> None:
        dof_service.send_dof_event_token(None, "E905")
        self.running = True

        again = dof_service.send_dof_event_token(None, "E905")

        self.assertFalse(again)
        self.assertEqual(len(self._asked("send_event_token")), 1)

    def test_a_test_fires_the_event_already_on_again(self) -> None:
        dof_service.send_dof_event_token(None, "E905")
        self.running = True

        outcome = dof_service.test_event(None, "E905")

        self.assertTrue(outcome)
        self.assertEqual([asked for asked, _ in self.helper.asked][-2:],
                         ["clear_event", "send_event_token"])
        self.assertEqual(len(self._asked("send_event_token")), 2)

    def test_a_runner_that_is_not_there_says_so(self) -> None:
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "runner_unavailable"}

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.NO_RUNNER)
        self.assertEqual(self._asked("send_event_token"), [])

    def test_a_runner_that_will_not_load_is_not_called_not_installed(self) -> None:
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "runner_unloadable"}

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.UNLOADABLE)
        self.assertEqual(outcome.words(), t("dof.event.unloadable"))
        self.assertNotEqual(outcome.words(), t("dof.event.no_runner"))
        self.assertEqual(self._asked("send_event_token"), [])

    def test_no_config_says_so_and_sends_nothing(self) -> None:
        with mock.patch.object(dof_service.dof_config, "present", lambda: False):
            outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.NO_CONFIG)
        self.assertEqual(outcome.words(), t("dof.event.no_config"))
        self.assertEqual(self._asked("send_event_token"), [])

    def test_a_missing_runner_is_named_before_a_missing_config(self) -> None:
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "runner_unavailable"}

        with mock.patch.object(dof_service.dof_config, "present", lambda: False):
            outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.NO_RUNNER)

    def test_a_library_that_will_not_load_is_named_while_the_helper_is_up(self) -> None:
        self.running = True
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "runner_unloadable"}

        with mock.patch.object(dof_service.dof_config, "present", lambda: False):
            outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.UNLOADABLE)
        self.assertEqual(self._asked("send_event_token"), [])

    def test_a_dead_runner_is_named_when_the_send_finds_it(self) -> None:
        self.running = True
        self.helper.replies["send_event_token"] = {"ok": False, "error": "runner_not_running"}
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "runner_unloadable"}

        with mock.patch.object(dof_service.dof_config, "present", lambda: True):
            outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.UNLOADABLE)

    def test_no_config_is_named_once_the_runner_is_up(self) -> None:
        self.running = True
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "already_running"}

        with mock.patch.object(dof_service.dof_config, "present", lambda: False):
            outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.NO_CONFIG)

    def test_a_runner_that_would_not_start_gives_its_own_words(self) -> None:
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "start_failed:Could not open the boards"}

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.FAILED)
        self.assertEqual(outcome.detail, "Could not open the boards")
        self.assertIn("Could not open the boards", outcome.words())

    def test_a_code_is_for_the_log_and_not_for_a_person(self) -> None:
        self.running = True
        self.helper.replies["send_event_token"] = {"ok": False, "error": "no such board"}

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.FAILED)
        self.assertEqual(outcome.detail, "")
        self.assertEqual(outcome.words(), t("dof.event.failed"))

    def test_a_helper_already_up_without_a_runner_still_says_so(self) -> None:
        self.running = True
        self.helper.replies["send_event_token"] = {"ok": False, "error": "runner_not_running"}
        self.helper.replies["start"] = {"ok": True, "started": False,
                                        "detail": "runner_unavailable"}

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.NO_RUNNER)

    def test_a_runner_that_comes_up_on_the_second_ask_sends(self) -> None:
        self.running = True
        answers = iter([{"ok": False, "error": "runner_not_running"}, {"ok": True}])
        cast(Any, self.helper).request = lambda command, timeout=0.0, **fields: (
            next(answers) if command == "send_event_token"
            else {"ok": True, "started": True, "detail": "x"})

        self.assertTrue(dof_service.send_dof_event_token(None, "E905"))

    def test_the_helper_does_not_start_under_a_table_by_any_road(self) -> None:
        self.playing = True

        self.assertFalse(dof_service.start_dof_service_if_enabled(None))
        self.assertFalse(dof_service.restart_dof_service_if_enabled(None))
        self.assertEqual(self._asked("start"), [])

    def test_the_event_a_test_finds_already_on_is_not_an_error(self) -> None:
        with mock.patch.object(dof_service, "test_event",
                               lambda _ini, _event: dof_service.Outcome(False, dof_service.SAME)):
            self.assertEqual(dof_service.turn_on(None, "e905"), "E905")

    def test_a_helper_that_does_not_answer_says_so(self) -> None:
        self.running = True
        self.helper.replies["send_event_token"] = None

        outcome = dof_service.send_dof_event_token(None, "E905")

        self.assertEqual(outcome.reason, dof_service.NO_HELPER)

    def test_a_token_that_is_not_one_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            dof_service.send_dof_event_token(None, "905")


class ReloadConfigTests(unittest.TestCase):
    """A pressed download starts the helper over so it reads the new files, and nothing else
    does."""

    def setUp(self) -> None:
        self.running = True
        self.playing = False
        self.restarted: list[object] = []
        for patch in (
                mock.patch.object(dof_service, "is_running", lambda: self.running),
                mock.patch.object(dof_service, "restart_dof_service_if_enabled",
                                  self._restart),
                mock.patch.object(launch_state, "current",
                                  lambda: launch_state.LaunchState(launching=self.playing))):
            self.enterContext(patch)

    def _restart(self, ini: object) -> bool:
        self.restarted.append(ini)
        return True

    def test_a_running_helper_is_started_over(self) -> None:
        self.assertTrue(dof_service.reload_config(_INI))
        self.assertEqual(self.restarted, ["ini"])

    def test_a_helper_that_is_not_running_is_left_alone(self) -> None:
        self.running = False

        self.assertFalse(dof_service.reload_config(_INI))
        self.assertEqual(self.restarted, [])

    def test_a_table_playing_leaves_it_alone(self) -> None:
        self.playing = True

        self.assertFalse(dof_service.reload_config(_INI))
        self.assertEqual(self.restarted, [])


class BlockedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.playing = False
        self.enabled = True
        for patch in (
                mock.patch.object(dof_service, "_is_enabled", lambda _ini: self.enabled),
                mock.patch.object(launch_state, "current",
                                  lambda: launch_state.LaunchState(launching=self.playing))):
            self.enterContext(patch)

    def test_nothing_blocks_an_enabled_device_with_no_table_playing(self) -> None:
        self.assertTrue(dof_service.blocked(None))

    def test_off_and_playing_each_say_why(self) -> None:
        self.enabled = False
        self.assertEqual(dof_service.blocked(None).reason, dof_service.OFF)

        self.enabled, self.playing = True, True
        self.assertEqual(dof_service.blocked(None).reason, dof_service.PLAYING)


class BoardsTests(unittest.TestCase):
    """Boards are read from the helper's log, since libdof's `init()` answers nothing."""

    def setUp(self) -> None:
        self.helper = dof_service._DofHelperProcess()
        self.helper._runner_started_at = time.monotonic()
        self.enterContext(mock.patch.object(dof_service, "_HELPER", self.helper))
        self.enterContext(mock.patch.object(
            dof_service._DofHelperProcess, "is_alive", lambda _self: True))
        self.enterContext(mock.patch.object(dof_service, "BOARD_WAIT", 0.0))

    def _said(self, message: str) -> None:
        self.helper._handle_log({"type": "log", "level": "INFO", "message": message})

    def test_a_board_that_reported_in_is_a_board(self) -> None:
        self._said("PacLed64 device 1 initialized")

        self.assertFalse(dof_service.no_boards())

    def test_a_board_that_was_not_found_is_no_board(self) -> None:
        self._said("PacLed64 device 1 not found")
        self._said("Could not initialize FT245RBitbangController 3. No SerialNumber.")
        self._said("PinscapePico 2 could not connect to device")

        self.assertTrue(dof_service.no_boards())

    def test_a_ledwiz_that_could_not_connect_is_no_board_though_it_logs_initialized(self) -> None:
        self._said("Initializing LedWiz Nr. 08")
        self._said("LedWiz LedWiz 08 could not connect to device")
        self._said("LedWiz Nr. 08 initialized and updater thread initialized.")

        self.assertTrue(dof_service.no_boards())

    def test_a_board_beside_a_ledwiz_that_could_not_connect_is_a_board(self) -> None:
        self._said("LedWiz LedWiz 08 could not connect to device")
        self._said("LedWiz Nr. 08 initialized and updater thread initialized.")
        self._said("PacLed64 device 1 initialized at index 3")

        self.assertFalse(dof_service.no_boards())

    def test_a_ledwiz_that_connected_is_a_board_beside_one_that_did_not(self) -> None:
        self._said("LedWiz LedWiz 08 could not connect to device")
        self._said("LedWiz Nr. 08 initialized and updater thread initialized.")
        self._said("LedWiz Nr. 09 initialized and updater thread initialized.")

        self.assertFalse(dof_service.no_boards())

    def test_each_controller_type_is_seen_by_the_line_it_logs(self) -> None:
        for line in (
            "LedWiz Nr. 08 initialized and updater thread initialized.",
            "PacDrive initialized and updater thread started.",
            "PacUIO Id:1 initialized and updater thread started.",
            "FT245RBitbangController 3 with serial number A1 has been initialized.",
            "Detected and added Pinscape Controller (KL25Z) #1 with name Cab",
            "Found Pinscape device: Cab (path=/dev/hidraw1)",
            "PinscapePico 2 connected successfully",
            "Pinscape Pico discovery found unit #2 (Cab), 24 output ports",
            "DudesCab Controller Informations : Device [1,RID:2] Name [Cab], v1.2.3, unit #1",
        ):
            with self.subTest(line=line):
                self.helper._boards_up = 0
                self._said(line)

                self.assertFalse(dof_service.no_boards())

    def test_the_lines_that_come_before_a_board_are_not_one(self) -> None:
        for line in (
            "Initializing LedWiz Nr. 08",
            "PacLed64 auto-configuration starting",
            "Pinscape auto-configuration starting",
            "Global configuration found at: /tmp/x",
        ):
            with self.subTest(line=line):
                self._said(line)

                self.assertTrue(dof_service.no_boards())

    def test_a_helper_that_logged_nothing_of_boards_has_none(self) -> None:
        self._said("Frontend DOF event sent")

        self.assertTrue(dof_service.no_boards())

    def test_a_board_still_starting_is_waited_for(self) -> None:
        late = threading.Timer(0.05, self._said, ["PacLed64 device 1 initialized"])
        with mock.patch.object(dof_service, "BOARD_WAIT", 5.0):
            late.start()
            began = time.monotonic()

            self.assertFalse(dof_service.no_boards())

        late.join()
        self.assertLess(time.monotonic() - began, 2.0)

    def test_no_helper_and_a_runner_never_started_say_nothing_either_way(self) -> None:
        self.helper._runner_started_at = None
        self.assertFalse(dof_service.no_boards())

        with mock.patch.object(dof_service, "_HELPER", None):
            self.assertFalse(dof_service.no_boards())

    def test_starting_the_runner_counts_afresh(self) -> None:
        self._said("PacLed64 device 1 initialized")

        was = self.helper._boards_started()
        self.helper._boards_settled(was, started=True)

        self.assertTrue(dof_service.no_boards())

    def _start(self, answer: dict[str, object] | None) -> None:
        stdin = mock.Mock()

        def reply(_encoded: str) -> None:
            if answer is not None:
                for request_id, pending in self.helper._pending.items():
                    pending["response"] = {"type": "response", "id": request_id, **answer}

        stdin.write.side_effect = reply
        self.helper._proc = mock.Mock(stdin=stdin)
        self.enterContext(mock.patch.object(
            dof_service._DofHelperProcess, "_ensure_started", lambda _self: True))
        self.helper.request("start", timeout=0.05)

    def test_a_start_request_counts_afresh_and_a_refused_one_keeps_the_count(self) -> None:
        self._said("PacLed64 device 1 initialized")

        self._start({"started": False})
        self.assertFalse(dof_service.no_boards())

        self._start({"started": True})
        self.assertTrue(dof_service.no_boards())

    def test_a_start_that_never_answered_keeps_the_count(self) -> None:
        self._said("PacLed64 device 1 initialized")

        self._start(None)

        self.assertFalse(dof_service.no_boards())

    def test_a_start_that_found_it_running_keeps_what_was_counted(self) -> None:
        self._said("PacLed64 device 1 initialized")

        was = self.helper._boards_started()
        self.helper._boards_settled(was, started=False)

        self.assertFalse(dof_service.no_boards())

    def test_a_ledwiz_down_before_a_refused_start_is_still_down_after_it(self) -> None:
        self._said("LedWiz LedWiz 08 could not connect to device")

        self._start({"started": False})
        self._said("LedWiz Nr. 8 initialized and updater thread initialized.")

        self.assertTrue(dof_service.no_boards())

    def test_a_ledwiz_down_before_a_start_that_took_is_counted_if_it_connects_this_time(
            self) -> None:
        self._said("LedWiz LedWiz 08 could not connect to device")

        self._start({"started": True})
        self._said("LedWiz Nr. 08 initialized and updater thread initialized.")

        self.assertFalse(dof_service.no_boards())


if __name__ == "__main__":
    unittest.main()

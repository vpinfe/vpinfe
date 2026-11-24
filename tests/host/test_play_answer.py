"""Protects: (d) Take Picture and Back reach a running table when the page is asleep.

While a table runs, a press of either that the host hears or is sent is answered by the
host's picture code, never relayed to a window that may not be running its page.
"""

from __future__ import annotations

import unittest
from unittest import mock

from common import events
from common.capture import freeze
from common.host import input_actions, key_reader, launch_state
from frontend import input_events


class _Bridge:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_event_all_with_iframe(self, message: dict) -> None:
        self.sent.append(message)


class AnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = _Bridge()
        input_events.reset_for_tests()
        input_events.register(self.bridge)  # type: ignore[arg-type]
        self.addCleanup(input_events.reset_for_tests)
        self.addCleanup(events.unsubscribe, events.INPUT_ACTION, input_events.on_input_action)
        self.heard: list[dict] = []
        events.subscribe(events.INPUT_ACTION, self._note)
        self.addCleanup(events.unsubscribe, events.INPUT_ACTION, self._note)
        playing = launch_state.LaunchState(launching=True, source="frontend")
        patch = mock.patch.object(launch_state, "current", return_value=playing)
        patch.start()
        self.addCleanup(patch.stop)
        self.picture = mock.patch.object(freeze, "take_picture",
                                         return_value={"state": "frozen"})
        self.back = mock.patch.object(freeze, "resume", return_value={"state": "resumed"})
        self.picture_mock = self.picture.start()
        self.back_mock = self.back.start()
        self.addCleanup(self.picture.stop)
        self.addCleanup(self.back.stop)
        self.addCleanup(input_actions.settle)
        self.addCleanup(input_actions._answered.clear)

    def _note(self, **payload: object) -> None:
        self.heard.append(payload)

    def _act(self, action: str, phase: str = "tap") -> None:
        input_actions.act(action, phase, source="remote")
        input_actions.settle()

    def test_take_picture_from_the_remote_is_answered_without_a_window(self) -> None:
        self._act("take_picture")

        self.picture_mock.assert_called_once_with()
        self.assertEqual([m["type"] for m in self.bridge.sent], ["PlayTone"])
        self.assertEqual(self.bridge.sent[0]["state"], "frozen")

    def test_back_is_answered_by_resuming(self) -> None:
        self._act("back")

        self.back_mock.assert_called_once_with()
        self.picture_mock.assert_not_called()

    def test_extensions_hear_it_once_as_answered(self) -> None:
        self._act("take_picture")

        self.assertEqual([(h["action"], h["phase"], h["answered"]) for h in self.heard],
                         [("take_picture", "press", True)])

    def test_a_held_press_that_is_renewed_is_one_picture(self) -> None:
        self._act("take_picture", "press")
        self._act("take_picture", "press")
        self._act("take_picture", "press")

        self.assertEqual(self.picture_mock.call_count, 1)

    def test_a_press_after_the_release_is_a_new_one(self) -> None:
        self._act("take_picture", "press")
        self._act("take_picture", "release")
        self._act("take_picture", "press")

        self.assertEqual(self.picture_mock.call_count, 2)

    def test_a_key_heard_during_play_is_answered_by_the_host(self) -> None:
        reader = key_reader.Reader(key_reader.Hearing(key_reader.PYNPUT),
                                   {"KeyP": ["take_picture"]})
        reader.hear("KeyP")
        input_actions.settle()

        self.picture_mock.assert_called_once_with()
        self.assertNotIn("InputAction", [m["type"] for m in self.bridge.sent])

    def test_a_failure_in_the_picture_code_does_not_stop_the_next_press(self) -> None:
        self.picture_mock.side_effect = [RuntimeError("no"), {"state": "taken"}]

        with self.assertLogs("vpinfe.common.host.input_actions", level="ERROR"):
            self._act("take_picture")
        self._act("take_picture")

        self.assertEqual(self.bridge.sent[-1]["state"], "taken")


    def test_a_release_after_the_table_has_exited_still_lets_go(self) -> None:
        self._act("take_picture", "press")
        idle = launch_state.LaunchState(launching=False)
        with mock.patch.object(launch_state, "current", return_value=idle):
            self._act("take_picture", "release")
        self._act("take_picture", "press")

        self.assertEqual(self.picture_mock.call_count, 2)

    def test_no_release_follows_an_answered_press(self) -> None:
        self._act("take_picture")

        self.assertEqual([h["phase"] for h in self.heard], ["press"])


class NotPlayingTests(unittest.TestCase):
    def test_back_with_no_table_running_reaches_the_windows(self) -> None:
        bridge = _Bridge()
        input_events.reset_for_tests()
        input_events.register(bridge)  # type: ignore[arg-type]
        self.addCleanup(input_events.reset_for_tests)
        self.addCleanup(events.unsubscribe, events.INPUT_ACTION, input_events.on_input_action)
        idle = launch_state.LaunchState(launching=False)
        with mock.patch.object(launch_state, "current", return_value=idle):
            input_actions.act("back", "tap", source="remote")
        self.addCleanup(input_actions.release_all)

        self.assertEqual({m["type"] for m in bridge.sent}, {"InputAction"})

    def test_with_no_table_running_the_press_goes_to_the_windows(self) -> None:
        idle = launch_state.LaunchState(launching=False)
        with mock.patch.object(launch_state, "current", return_value=idle):
            self.assertFalse(input_actions.answers_here("take_picture"))

    def test_another_action_is_never_answered_here(self) -> None:
        playing = launch_state.LaunchState(launching=True, source="frontend")
        with mock.patch.object(launch_state, "current", return_value=playing):
            self.assertFalse(input_actions.answers_here("select"))

    def test_a_recording_run_is_not_a_play(self) -> None:
        recording = launch_state.LaunchState(launching=True,
                                             source=launch_state.SOURCE_CAPTURE)
        with mock.patch.object(launch_state, "current", return_value=recording):
            self.assertFalse(input_actions.answers_here("take_picture"))


if __name__ == "__main__":
    unittest.main()

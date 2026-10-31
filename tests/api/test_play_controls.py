"""Pressing a control of the table that is running, from somewhere else.

What a caller may say is an id, and nothing else: the key is the app's and is read from the
launcher's settings file at the press.
"""

from __future__ import annotations

import logging
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import httpapi
from apps.vpx.controls import VPXControls
from common.apps.contract import Control
from common.host import key_simulator, launch_state, table_controls
from httpapi import auth, scopes

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]


class _Process:
    def poll(self) -> None:
        return None


class _Keys:
    """Stands in for the keyboard: what was sent, and as a tap or a hold."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, float | str]] = []

    def press_code(self, code: str, *, via=None) -> bool:
        self.sent.append((code, 0))
        return True

    def hold_code(self, code: str, seconds: float = 0.1, *, via=None) -> bool:
        self.sent.append((code, seconds))
        return True

    def down_code(self, code: str, *, via=None) -> bool:
        self.sent.append((code, "down"))
        return True

    def up_code(self, code: str, *, via=None) -> bool:
        self.sent.append((code, "up"))
        return True


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class PlayControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.ini = Path(held.name) / "VPinballX.ini"
        self.ini.write_text("[Input]\nMapping.ExtraBall = Joy;3\nMapping.Credit1 = Key;4\n",
                            encoding="utf-8")
        self.keys = _Keys()
        self.sender = key_simulator.Sender(key_simulator.WTYPE)
        self.can_hold = True
        for patched in (
                mock.patch.object(key_simulator, "down_code", self.keys.down_code),
                mock.patch.object(key_simulator, "up_code", self.keys.up_code),
                mock.patch.object(key_simulator, "can_hold", lambda via=None: self.can_hold),
                mock.patch.object(key_simulator, "press_code", self.keys.press_code),
                mock.patch.object(key_simulator, "hold_code", self.keys.hold_code),
                mock.patch.object(key_simulator, "sender", lambda: self.sender)):
            patched.start()
            self.addCleanup(patched.stop)
        self.addCleanup(launch_state.clear)
        self.addCleanup(table_controls.release_all)
        self.addCleanup(auth.set_policy, auth.SignInPolicy())
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _play(self, *, source: str = launch_state.SOURCE_REMOTE, declared=True) -> None:
        controls = VPXControls()
        settings = {"ini_path": str(self.ini)}
        running = launch_state.RunningControls(
            app="vpx", offered=controls.offered(),
            key=lambda one: controls.key(one, settings),
            why_not=lambda one: controls.why_not(one, settings)) if declared else None
        launch_state.set_launching("Example", source=source, game_id="g1", controls=running)
        launch_state.attach(_Process())  # type: ignore[arg-type]

    def _grant(self, *granted: str) -> None:
        class Policy(auth.SignInPolicy):
            name = "test"

            def identify(self, request) -> auth.Identity:
                return auth.Identity(name="test", scopes=frozenset(granted))

        auth.set_policy(Policy())

    def _press(self, control_id: str, **body):
        return self.client.post(f"/play/controls/{control_id}", json=body or None)

    def _listed(self) -> dict:
        return {one["id"]: one for one in self.client.get("/play/controls").json()["controls"]}

    # --- what is offered

    def test_nothing_playing_offers_nothing(self) -> None:
        body = self.client.get("/play/controls").json()

        self.assertIsNone(body["table"])
        self.assertEqual(body["controls"], [])
        self.assertEqual(body["notes"], [])
        self.assertTrue(body["press"]["available"])

    def test_a_running_table_offers_its_apps_controls_in_the_installs_words(self) -> None:
        self._play()

        body = self.client.get("/play/controls").json()

        self.assertEqual(body["table"], {"game_id": "g1", "game_name": "Example", "app": "vpx"})
        self.assertEqual([one["id"] for one in body["controls"]],
                         [one.id for one in VPXControls().offered()])
        credit = self._listed()["credit_1"]
        self.assertEqual((credit["group"], credit["label"], credit["available"], credit["reason"]),
                         ("coins", "Credit 1", True, ""))

    def test_a_hand_control_the_settings_map_to_a_controller_is_left_out_not_dimmed(self) -> None:
        self.ini.write_text("[Input]\nMapping.LeftFlipper = Joy;1\nMapping.CenterNudge = Joy;2\n",
                            encoding="utf-8")
        self._play()

        body = self.client.get("/play/controls").json()
        listed = self._listed()
        named = " ".join(note["text"] for note in body["notes"])

        self.assertNotIn("left_flipper", listed)
        self.assertNotIn("nudge_center", listed)
        self.assertTrue({"right_flipper", "launch_ball", "nudge_left", "nudge_right"}
                        <= set(listed))
        self.assertNotIn("Left Flipper", named)
        self.assertNotIn("Nudge Center", named)
        self.assertEqual(self._press("left_flipper").status_code, 409)
        self.assertEqual(self.keys.sent, [])

    def test_a_flipper_is_held_for_a_moment_so_the_table_sees_it(self) -> None:
        self._play()

        self.assertEqual(self._press("right_flipper").status_code, 200)

        self.assertEqual(self.keys.sent, [("ShiftRight", 0.1)])

    def test_the_hand_controls_say_they_can_be_held_and_the_rest_do_not(self) -> None:
        self._play()

        listed = self._listed()

        self.assertTrue(all(listed[one]["holdable"]
                            for one in ("left_flipper", "right_flipper", "launch_ball",
                                        "left_magna", "right_magna")))
        self.assertFalse(listed["start"]["holdable"])
        self.assertFalse(listed["nudge_left"]["holdable"])
        self.assertEqual({one: listed[one]["side"] for one in
                          ("left_flipper", "left_magna", "launch_ball", "right_flipper",
                           "right_magna")},
                         {"left_flipper": "left", "left_magna": "left", "launch_ball": "",
                          "right_flipper": "right", "right_magna": "right"})

    def test_a_press_leaves_the_key_down_and_a_release_lets_it_go(self) -> None:
        self._play()

        self.assertEqual(self._press("left_flipper", phase="press").json()["pressed"], True)
        self.assertEqual(self._press("left_flipper", phase="release").json()["pressed"], True)

        self.assertEqual(self.keys.sent, [("ShiftLeft", "down"), ("ShiftLeft", "up")])

    def test_a_renewed_press_does_not_press_the_key_again(self) -> None:
        self._play()

        for _ in range(3):
            self._press("launch_ball", phase="press")
        self._press("launch_ball", phase="release")

        self.assertEqual(self.keys.sent, [("Enter", "down"), ("Enter", "up")])

    def test_a_press_with_no_release_lets_go_when_the_renewals_stop(self) -> None:
        self._play()

        self._press("right_flipper", phase="press", ttl_ms=200)
        time.sleep(0.6)

        self.assertEqual(self.keys.sent, [("ShiftRight", "down"), ("ShiftRight", "up")])

    def test_a_release_with_nothing_down_sends_nothing_and_says_so(self) -> None:
        self._play()

        response = self._press("left_flipper", phase="release")

        self.assertEqual(response.json(), {"control_id": "left_flipper", "pressed": False})
        self.assertEqual(self.keys.sent, [])

    def test_a_control_that_is_a_single_press_cannot_be_held(self) -> None:
        self._play()

        self.assertEqual(self._press("start", phase="press").status_code, 409)
        self.assertEqual(self.keys.sent, [])

    def test_where_a_key_cannot_be_left_down_a_press_is_one_tap_and_a_release_is_nothing(
            self) -> None:
        self._play()
        self.can_hold = False

        self._press("left_flipper", phase="press")
        self._press("left_flipper", phase="release")

        self.assertEqual(self.keys.sent, [("ShiftLeft", 0.1)])

    def test_where_a_key_cannot_be_left_down_the_renewals_are_not_more_taps(self) -> None:
        self._play()
        self.can_hold = False

        for _ in range(4):
            self._press("left_flipper", phase="press")

        self.assertEqual(self.keys.sent, [("ShiftLeft", 0.1)])

    def test_a_release_still_lets_go_after_the_table_has_ended(self) -> None:
        self._play()
        self._press("right_flipper", phase="press")
        launch_state.clear()

        response = self._press("right_flipper", phase="release")

        self.assertEqual(response.json()["pressed"], True)
        self.assertEqual(self.keys.sent, [("ShiftRight", "down"), ("ShiftRight", "up")])

    def test_an_expiry_that_lost_the_race_to_a_renewal_does_not_let_go(self) -> None:
        self._play()
        self._press("left_flipper", phase="press")
        stale = table_controls._down["left_flipper"].timer
        self._press("left_flipper", phase="press")

        table_controls._expired("left_flipper", "test", stale)

        self.assertEqual(self.keys.sent, [("ShiftLeft", "down")])

    def test_going_away_lets_go_of_every_key_still_down(self) -> None:
        self._play()
        self._press("left_flipper", phase="press")
        self._press("right_flipper", phase="press")

        table_controls.release_all()

        self.assertEqual(sorted(self.keys.sent), [("ShiftLeft", "down"), ("ShiftLeft", "up"),
                                                   ("ShiftRight", "down"),
                                                   ("ShiftRight", "up")])

    def test_a_phase_nobody_defined_is_refused(self) -> None:
        self._play()

        self.assertEqual(self._press("left_flipper", phase="wiggle").status_code, 422)

    def test_a_control_mapped_to_a_controller_button_is_unavailable_and_says_why(self) -> None:
        self._play()

        extra = self._listed()["extra_ball"]

        self.assertFalse(extra["available"])
        self.assertIn("Extra Ball", extra["reason"])
        self.assertNotIn("Joy", extra["reason"])

    def test_controls_that_cannot_be_pressed_for_one_cause_share_one_note_naming_them(self) -> None:
        self._play()

        body = self.client.get("/play/controls").json()
        listed = self._listed()

        off = sorted(key for key, one in listed.items()
                     if not one["available"] and key != "pause")
        self.assertGreater(len(off), 2)
        self.assertEqual(sorted(key for note in body["notes"] for key in note["controls"]), off)
        for note in body["notes"]:
            for key in note["controls"]:
                self.assertEqual(note["text"].count(listed[key]["label"]), 1, note)

    def test_no_note_names_pause_which_has_a_key_and_a_reason_of_its_own(self) -> None:
        offered = [Control("pause", "table"), Control("credit_1", "table"),
                   Control("credit_2", "table")]
        held = mock.Mock(offered=offered, app="vpx")
        held.key.return_value = ""
        held.why_not.return_value = "unmapped"

        notes = table_controls._notes(held)

        self.assertEqual([note["controls"] for note in notes], [["credit_1", "credit_2"]])

    def test_a_control_on_a_key_that_cannot_be_sent_is_unavailable_and_says_why(self) -> None:
        self.ini.write_text("[Input]\nMapping.Credit1 = Key;93\n", encoding="utf-8")
        self._play()

        credit = self._listed()["credit_1"]
        response = self._press("credit_1")

        self.assertFalse(credit["available"])
        self.assertIn("cannot press", credit["reason"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.keys.sent, [])

    def test_the_answer_carries_no_key(self) -> None:
        self._play()

        body = self.client.get("/play/controls").json()

        for one in body["controls"]:
            self.assertEqual(set(one), {"id", "group", "label", "hold_ms", "holdable", "side",
                                        "available", "reason"})
            self.assertNotRegex(one["reason"], r"Key;|Joy|Digit|Key[A-Z]\b")

    def test_a_recording_offers_nothing(self) -> None:
        self._play(source=launch_state.SOURCE_CAPTURE)

        body = self.client.get("/play/controls").json()

        self.assertIsNone(body["table"])
        self.assertEqual(body["controls"], [])

    def test_an_app_that_declares_none_offers_none(self) -> None:
        self._play(declared=False)

        body = self.client.get("/play/controls").json()

        self.assertIsNone(body["table"])
        self.assertEqual(body["controls"], [])

    def test_where_keys_cannot_be_sent_the_answer_says_so(self) -> None:
        self.sender = key_simulator.Sender("", reason={
            "key": key_simulator.NO_SESSION, "params": {}, "fix": "none", "remedy": None})
        self._play()

        press = self.client.get("/play/controls").json()["press"]

        self.assertFalse(press["available"])
        self.assertEqual(press["reason"]["key"], key_simulator.NO_SESSION)

    # --- pressing

    def test_a_press_sends_the_controls_key(self) -> None:
        self._play()

        response = self._press("credit_1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"control_id": "credit_1", "pressed": True})
        self.assertEqual(self.keys.sent, [("KeyA", 0)])

    def test_a_held_control_is_held_by_the_install(self) -> None:
        self._play()

        self._press("start")

        self.assertEqual(self.keys.sent, [("Digit1", 0.1)])

    def test_the_key_is_read_at_the_press(self) -> None:
        self._play()
        self._press("credit_1")
        self.ini.write_text("[Input]\nMapping.Credit1 = Key;5\n", encoding="utf-8")

        self._press("credit_1")

        self.assertEqual([code for code, _ in self.keys.sent], ["KeyA", "KeyB"])

    def test_pressed_is_false_when_the_key_could_not_be_sent(self) -> None:
        self._play()
        with mock.patch.object(key_simulator, "press_code", return_value=False):
            response = self._press("credit_1")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["pressed"])

    def test_nothing_playing_is_refused(self) -> None:
        response = self._press("credit_1")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.keys.sent, [])

    def test_a_recording_is_refused(self) -> None:
        self._play(source=launch_state.SOURCE_CAPTURE)

        response = self._press("credit_1")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.keys.sent, [])

    def test_an_id_the_app_does_not_declare_is_not_found_and_echoes_only_the_id(self) -> None:
        self._play()

        response = self._press("tilt")

        self.assertEqual(response.status_code, 404)
        error = response.json()["error"]
        self.assertEqual(error["code"], "not_found")
        self.assertEqual(error["details"], {"control_id": "tilt"})
        self.assertEqual(self.keys.sent, [])

    def test_an_app_with_no_controls_declares_no_id(self) -> None:
        self._play(declared=False)

        self.assertEqual(self._press("credit_1").status_code, 404)

    def test_a_key_code_is_not_an_id(self) -> None:
        self._play()

        for spelled in ("KeyA", "Digit5", "Key;4", "..%2Fstate"):
            with self.subTest(spelled=spelled):
                self.assertEqual(self._press(spelled).status_code, 404)
        self.assertEqual(self.keys.sent, [])

    def test_a_control_with_no_key_is_refused_with_its_reason(self) -> None:
        self._play()

        response = self._press("extra_ball")

        self.assertEqual(response.status_code, 409)
        self.assertIn("Extra Ball", response.json()["error"]["message"])
        self.assertEqual(self.keys.sent, [])

    def test_where_keys_cannot_be_sent_it_is_unavailable_with_the_devices_reason(self) -> None:
        self.sender = key_simulator.Sender("", reason={
            "key": key_simulator.NO_SESSION, "params": {}, "fix": "none", "remedy": None})
        self._play()

        response = self._press("credit_1")

        self.assertEqual(response.status_code, 503)
        error = response.json()["error"]
        self.assertEqual(error["code"], "unavailable")
        self.assertEqual(error["message"], key_simulator.words(self.sender.reason))
        self.assertEqual(self.keys.sent, [])

    def test_a_body_that_carries_a_key_is_ignored(self) -> None:
        self._play()

        self._press("credit_1", source="remote", key="Escape", code="Escape")

        self.assertEqual(self.keys.sent, [("KeyA", 0)])

    def test_the_log_names_the_control_and_who_pressed_it(self) -> None:
        self._play()

        with self.assertLogs("vpinfe.common.host.table_controls", logging.INFO) as logged:
            self._press("credit_1", source="remote")

        self.assertIn("credit_1", logged.output[0])
        self.assertIn("remote", logged.output[0])

    # --- who may

    def test_pressing_needs_input_act(self) -> None:
        self._play()
        self._grant(scopes.PLAY_READ)

        response = self._press("credit_1")

        self.assertEqual(response.status_code, 403)
        self.assertIn(scopes.INPUT_ACT, response.json()["error"]["message"])
        self.assertEqual(self.keys.sent, [])

    def test_reading_needs_play_read(self) -> None:
        self._grant(scopes.INPUT_ACT)

        response = self.client.get("/play/controls")

        self.assertEqual(response.status_code, 403)
        self.assertIn(scopes.PLAY_READ, response.json()["error"]["message"])

    def test_the_play_tier_may_do_both(self) -> None:
        self._play()
        self._grant(*scopes.PLAY)

        self.assertEqual(self.client.get("/play/controls").status_code, 200)
        self.assertEqual(self._press("credit_1").status_code, 200)


if __name__ == "__main__":
    unittest.main()

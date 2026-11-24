"""What Visual Pinball lets a Remote press on a running table: the controls, and the key behind
each from the launcher's own settings file."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.vpx import VPX
from apps.vpx.controls import VPXControls
from common.apps.contract import (
    CONTROLS_COINS,
    CONTROLS_DEBUG,
    CONTROLS_GAME,
    CONTROLS_MENU,
    CONTROLS_NUDGE,
    CONTROLS_PLAY,
    CONTROLS_SERVICE,
    CONTROLS_SOUND,
    NOT_A_KEY,
    UNMAPPED,
    Controls,
)
from common.i18n import t


class VPXControlsTests(unittest.TestCase):
    def _ini(self, body: str | bytes) -> dict[str, str]:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        path = Path(held.name) / "VPinballX.ini"
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
        return {"ini_path": str(path)}

    def test_the_app_declares_them(self) -> None:
        self.assertIsInstance(VPX.controls, Controls)

    def test_each_is_declared_once_under_a_known_group(self) -> None:
        offered = VPXControls().offered()

        self.assertEqual(len({one.id for one in offered}), len(offered))
        self.assertLessEqual({one.group for one in offered},
                             {CONTROLS_PLAY, CONTROLS_GAME, CONTROLS_COINS, CONTROLS_SOUND,
                              CONTROLS_NUDGE, CONTROLS_MENU, CONTROLS_SERVICE,
                              CONTROLS_DEBUG})

    def test_the_groups_come_in_the_order_a_person_meets_them(self) -> None:
        groups = [one.group for one in VPXControls().offered()]
        first_seen = list(dict.fromkeys(groups))

        self.assertEqual(first_seen, [CONTROLS_PLAY, CONTROLS_GAME, CONTROLS_COINS,
                                      CONTROLS_SOUND, CONTROLS_NUDGE, CONTROLS_MENU,
                                      CONTROLS_SERVICE, CONTROLS_DEBUG])
        self.assertEqual(groups, sorted(groups, key=first_seen.index),
                         "a group's controls sit together")

    def test_the_service_menu_leads_with_the_coin_door_then_its_four_keys(self) -> None:
        menu = [one.id for one in VPXControls().offered() if one.group == CONTROLS_MENU]

        self.assertEqual(menu, ["coin_door", "menu_up", "menu_down", "menu_enter",
                                "menu_cancel"])

    def test_the_hands_lead_with_flippers_and_plunger_and_nudge_comes_in_threes(self) -> None:
        offered = VPXControls().offered()

        self.assertEqual([one.id for one in offered if one.group == CONTROLS_PLAY],
                         ["left_flipper", "right_flipper", "launch_ball", "left_magna",
                          "right_magna"])
        self.assertEqual([one.id for one in offered if one.group == CONTROLS_NUDGE],
                         ["nudge_left", "nudge_center", "nudge_right"])

    def test_only_what_the_settings_map_to_a_key_is_offered_of_the_hands(self) -> None:
        hands = {one.id for one in VPXControls().offered()
                 if one.group in (CONTROLS_PLAY, CONTROLS_NUDGE)}

        self.assertEqual({one.id for one in VPXControls().offered() if one.when_mapped}, hands)

    def test_the_hands_have_the_keys_visual_pinball_ships(self) -> None:
        settings = self._ini("[Input]\n")

        self.assertEqual({one: VPXControls().key(one, settings) for one in (
            "left_flipper", "right_flipper", "launch_ball", "left_magna", "right_magna",
            "nudge_left", "nudge_center", "nudge_right")},
            {"left_flipper": "ShiftLeft", "right_flipper": "ShiftRight",
                              "launch_ball": "Enter", "left_magna": "ControlLeft",
                              "right_magna": "ControlRight", "nudge_left": "KeyZ",
                              "nudge_center": "Space", "nudge_right": "Slash"})

    def test_every_one_has_a_label_a_person_reads(self) -> None:
        for one in VPXControls().offered():
            with self.subTest(control=one.id):
                label = t(f"controls.{one.id}")
                self.assertNotEqual(label, f"controls.{one.id}")
                self.assertTrue(label.strip())

    def test_every_group_has_a_label(self) -> None:
        for group in {one.group for one in VPXControls().offered()}:
            with self.subTest(group=group):
                self.assertNotEqual(t(f"controls.group.{group}"), f"controls.group.{group}")

    def test_the_key_is_the_one_its_settings_file_maps(self) -> None:
        settings = self._ini("[Input]\nMapping.ExtraBall = Key;72\nMapping.Credit1 = Key;4\n")

        self.assertEqual(VPXControls().key("extra_ball", settings), "Pause")
        self.assertEqual(VPXControls().key("credit_1", settings), "KeyA")

    def test_vpxs_own_key_where_the_file_maps_nothing(self) -> None:
        settings = self._ini("[Input]\nMapping.Start = \nMapping.Lockbar = Key;4\n")

        self.assertEqual(VPXControls().key("start", settings), "Digit1")
        self.assertEqual(VPXControls().key("fire", settings), "KeyA")
        self.assertEqual(VPXControls().key("credit_1", settings), "Digit5")

    def test_no_file_at_all_is_vpxs_own_keys(self) -> None:
        self.assertEqual(VPXControls().key("volume_up", {"ini_path": "/no/such/file.ini"}),
                         "Equal")

    def test_a_control_with_no_default_and_no_mapping_has_no_key(self) -> None:
        settings = self._ini("[Input]\nMapping.ShowRules = \n")

        self.assertEqual(VPXControls().key("show_rules", settings), "")
        self.assertEqual(VPXControls().why_not("show_rules", settings), UNMAPPED)

    def test_a_controller_button_is_not_a_key(self) -> None:
        settings = self._ini("[Input]\nMapping.ExtraBall = Joy;3\n")

        self.assertEqual(VPXControls().key("extra_ball", settings), "")
        self.assertEqual(VPXControls().why_not("extra_ball", settings), NOT_A_KEY)

    def test_a_key_combination_is_not_one_key(self) -> None:
        settings = self._ini("[Input]\nMapping.Start = Key;225 & Key;30\n")

        self.assertEqual(VPXControls().key("start", settings), "")
        self.assertEqual(VPXControls().why_not("start", settings), NOT_A_KEY)

    def test_a_key_beside_a_controller_button_is_the_key(self) -> None:
        settings = self._ini("[Input]\nMapping.ExtraBall = Joy;3 | Key;4\n")

        self.assertEqual(VPXControls().key("extra_ball", settings), "KeyA")
        self.assertEqual(VPXControls().why_not("extra_ball", settings), "")

    def test_a_file_that_cannot_be_read_is_vpxs_own_keys(self) -> None:
        settings = self._ini(b"[Input]\nMapping.ExtraBall = Key;72\nMapping.Note = caf\xe9\n")

        with self.assertLogs("vpinfe.apps.vpx.controls", level="WARNING") as caught:
            key = VPXControls().key("extra_ball", settings)

        self.assertEqual(key, "KeyB")
        self.assertIn("VPinballX.ini", caught.output[0])

    def test_the_service_menu_keys_do_not_follow_the_file(self) -> None:
        settings = self._ini("[Input]\nMapping.UIUp = Key;4\n")
        controls = VPXControls()

        self.assertEqual([controls.key(name, settings) for name in
                          ("menu_up", "menu_down", "menu_enter", "menu_cancel")],
                         ["Digit9", "Digit8", "Digit0", "Digit7"])

    def test_pause_is_the_key_its_pause_is(self) -> None:
        settings = self._ini("[Input]\nMapping.Pause = Key;72\n")

        self.assertEqual(VPXControls().key("pause", settings), "Pause")
        self.assertEqual(VPXControls().key("pause", self._ini("")), "KeyP")

    def test_start_the_menu_and_the_hands_are_held_a_moment_and_the_rest_are_tapped(self) -> None:
        held = {one.id: one.hold_ms for one in VPXControls().offered() if one.hold_ms}

        self.assertEqual(held, {"start": 100, "menu_up": 100, "menu_down": 100,
                                "menu_enter": 100, "menu_cancel": 100,
                                "left_flipper": 100, "right_flipper": 100, "launch_ball": 100,
                                "left_magna": 100, "right_magna": 100,
                                "nudge_left": 100, "nudge_center": 100, "nudge_right": 100})

    def test_only_the_flippers_the_magna_saves_and_the_plunger_follow_a_finger(self) -> None:
        holdable = {one.id for one in VPXControls().offered() if one.holdable}

        self.assertEqual(holdable, {"left_flipper", "right_flipper", "launch_ball",
                                    "left_magna", "right_magna"})

    def test_each_hand_control_says_which_side_it_is_on(self) -> None:
        sides = {one.id: one.side for one in VPXControls().offered() if one.side}

        self.assertEqual(sides, {"left_flipper": "left", "left_magna": "left",
                                 "right_flipper": "right", "right_magna": "right"})

    def test_a_magna_save_mapped_to_a_controller_is_left_out_of_the_listing(self) -> None:
        settings = self._ini("[Input]\nMapping.LeftMagna = Joy;3\n")

        self.assertEqual(VPXControls().key("left_magna", settings), "")
        self.assertEqual(VPXControls().why_not("left_magna", settings), NOT_A_KEY)

    def test_an_id_it_does_not_declare_has_no_key(self) -> None:
        self.assertEqual(VPXControls().key("tilt", self._ini("")), "")
        self.assertEqual(VPXControls().why_not("tilt", self._ini("")), UNMAPPED)

    def test_tilt_and_exit_are_not_offered(self) -> None:
        offered = {one.id for one in VPXControls().offered()}

        self.assertFalse(offered & {"tilt", "exit"})


if __name__ == "__main__":
    unittest.main()

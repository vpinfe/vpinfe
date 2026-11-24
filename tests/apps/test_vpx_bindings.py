"""The keys a `Mapping.<Name>` value names, read and written the way the program does."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.vpx import areas, bindings
from apps.vpx.config import VPXConfig
from common.apps.contract import SCOPE_LAUNCHER

LEFT = "Input.Mapping.LeftFlipper"
GEN = "Input.Mapping.GenTournament"


class ParseTests(unittest.TestCase):
    def test_a_blank_value_is_the_default_s_keys(self) -> None:
        self.assertEqual(bindings.parse("", "Key;225").keys, (("ShiftLeft",),))

    def test_a_chord_is_its_keys_in_one_settled_order(self) -> None:
        self.assertEqual(bindings.parse("Key;30 & Key;226").keys, (("AltLeft", "Digit1"),))
        self.assertEqual(bindings.parse("", "Key;226 & Key;30").keys, (("AltLeft", "Digit1"),))

    def test_an_alternative_that_is_not_a_key_is_kept_as_written(self) -> None:
        held = bindings.parse("Key;225 | SDLJoy_0300_1;4")

        self.assertEqual(held.keys, (("ShiftLeft",),))
        self.assertEqual(held.kept, ("SDLJoy_0300_1;4",))

    def test_a_key_with_no_name_here_is_kept_not_dropped(self) -> None:
        self.assertEqual(bindings.parse("Key;600").kept, ("Key;600",))

    def test_a_chord_with_one_part_unnamed_is_kept_whole(self) -> None:
        held = bindings.parse("Key;225 & Key;600")

        self.assertEqual((held.keys, held.kept), ((), ("Key;225 & Key;600",)))


class WriteTests(unittest.TestCase):
    def test_a_new_key_takes_the_place_of_the_old_one(self) -> None:
        self.assertEqual(bindings.value_of([("KeyA",)], "Key;225", "Key;225"), "Key;4")

    def test_two_keys_are_two_alternatives(self) -> None:
        self.assertEqual(bindings.value_of([("KeyA",), ("KeyB",)], "", "Key;225"),
                         "Key;4 | Key;5")

    def test_a_chord_is_written_with_its_parts_together(self) -> None:
        self.assertEqual(bindings.value_of([("AltLeft", "Digit1")], "", ""),
                         "Key;226 & Key;30")

    def test_a_controller_button_stays_where_it_was(self) -> None:
        self.assertEqual(
            bindings.value_of([("KeyA",)], "Key;225 | SDLJoy_0300_1;4", "Key;225"),
            "Key;4 | SDLJoy_0300_1;4")
        self.assertEqual(
            bindings.value_of([("KeyA",)], "SDLJoy_0300_1;4 | Key;225", "Key;225"),
            "SDLJoy_0300_1;4 | Key;4")

    def test_the_default_is_written_blank(self) -> None:
        self.assertEqual(bindings.value_of([("ShiftLeft",)], "Key;4", "Key;225"), "")

    def test_the_default_is_not_blank_beside_a_button_that_is_kept(self) -> None:
        self.assertEqual(bindings.value_of([("ShiftLeft",)], "Key;4 | SDLJoy_0300_1;4",
                                           "Key;225"),
                         "Key;225 | SDLJoy_0300_1;4")

    def test_an_action_with_no_default_and_no_key_is_blank(self) -> None:
        self.assertEqual(bindings.value_of([], "Key;4", ""), "")

    def test_a_key_the_program_has_no_number_for_is_not_writable(self) -> None:
        self.assertFalse(bindings.writable(("IntlRo",)))
        self.assertTrue(bindings.writable(("KeyA",)))
        self.assertFalse(bindings.writable(()))


class TheActionsTests(unittest.TestCase):
    def test_every_default_is_keys_the_table_can_name(self) -> None:
        for one in bindings.ACTIONS:
            with self.subTest(action=one.name):
                self.assertEqual(bindings.parse("", one.default).kept, ())

    def test_every_action_is_under_a_heading_the_area_draws(self) -> None:
        self.assertEqual({one.heading for one in bindings.ACTIONS}, set(bindings.HEADINGS))
        self.assertEqual([one.key for one in areas.CURATED[areas.INPUT]],
                         list(bindings.HEADINGS))

    def test_the_actions_are_on_the_input_page_and_no_other(self) -> None:
        for one in bindings.ACTIONS:
            with self.subTest(action=one.name):
                self.assertEqual(areas.area_of(one.qualified), areas.INPUT)

    def test_no_action_is_named_twice(self) -> None:
        names = [one.qualified for one in bindings.ACTIONS]

        self.assertEqual(len(names), len(set(names)))

    def test_a_mapping_of_an_action_this_does_not_know_is_not_one(self) -> None:
        self.assertTrue(bindings.is_mapping("Input.Mapping.NotAnAction"))
        self.assertFalse(bindings.is_action("Input.Mapping.NotAnAction"))


class TheFileTests(unittest.TestCase):
    """A key mapped here changes that key's line in the launcher's file and no other."""

    INI = ("[Player]\nPlaySound = 1\n\n[Input]\nMapping.LeftFlipper = \n"
           "Mapping.RightFlipper = Key;229\nMapping.GenTournament = \nJoyCustom1 = 3\n")

    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.path = Path(held.name) / "VPinballX.ini"
        self.path.write_text(self.INI, encoding="utf-8")
        self.settings = {"ini_path": str(self.path)}
        self.config = VPXConfig()

    def test_the_actions_are_offered_with_the_program_s_default(self) -> None:
        offered = {f.key: f for g in self.config.groups(self.settings)
                   if g.key == areas.INPUT for f in g.settings}

        self.assertEqual(offered[LEFT].default, "Key;225")
        self.assertEqual(offered[GEN].default, "Key;226 & Key;30")
        self.assertEqual(len(offered), len(bindings.ACTIONS))

    def test_the_settings_beside_the_mappings_are_not_taken_into_the_page(self) -> None:
        every = {f.key for g in self.config.groups(self.settings) for f in g.settings}

        self.assertIn("Input.JoyCustom1", every)
        self.assertNotIn("Input.JoyCustom1",
                         {f.key for g in self.config.groups(self.settings)
                          if g.key == areas.INPUT for f in g.settings})

    def test_one_key_written_is_one_line_changed(self) -> None:
        self.config.write(SCOPE_LAUNCHER, "", {LEFT: "Key;4"}, self.settings)

        was, now = self.INI.splitlines(), self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual([(a, b) for a, b in zip(was, now, strict=True) if a != b],
                         [("Mapping.LeftFlipper = ", "Mapping.LeftFlipper = Key;4")])

    def test_the_file_is_copied_aside_before_it_is_written(self) -> None:
        self.config.write(SCOPE_LAUNCHER, "", {LEFT: "Key;4"}, self.settings)

        [aside] = [one for one in self.path.parent.iterdir() if one.name != self.path.name]
        self.assertEqual(aside.read_text(encoding="utf-8"), self.INI)


PAD = "SDLJoy_030000007e0500000920000000000000_1"


class ControllerTests(unittest.TestCase):
    def test_a_button_is_a_member_and_comes_back_the_way_it_was_written(self) -> None:
        held = bindings.parse(f"Key;225 | {PAD};4", controllers=True)

        self.assertEqual(held.keys, (("ShiftLeft",), (f"vpx:{PAD};4",)))
        self.assertEqual(held.kept, ())
        self.assertEqual(
            bindings.value_of([*held.keys], f"Key;225 | {PAD};4", controllers=True),
            f"Key;225 | {PAD};4")

    def test_without_asking_for_them_a_controller_is_kept_as_written(self) -> None:
        held = bindings.parse(f"{PAD};4")

        self.assertEqual((held.keys, held.kept), ((), (f"{PAD};4",)))

    def test_an_axis_side_keeps_its_direction_and_threshold(self) -> None:
        held = bindings.parse(f"{PAD};514;x;-0.500000 | {PAD};516;o;0.500000", controllers=True)

        self.assertEqual(held.keys, ((f"vpx:{PAD};514;x;-0.5",), (f"vpx:{PAD};516;o;0.5",)))
        self.assertEqual(bindings.controller_part(held.keys[0][0]), (PAD, 514, -0.5, True))
        self.assertEqual(
            bindings.value_of([*held.keys], "", controllers=True),
            f"{PAD};514;x;-0.500000 | {PAD};516;o;0.500000")

    def test_a_key_and_a_button_together_are_one_chord(self) -> None:
        held = bindings.parse(f"Key;226 & {PAD};3", controllers=True)

        self.assertEqual(held.keys, (("AltLeft", f"vpx:{PAD};3"),))
        self.assertEqual(
            bindings.value_of([*held.keys], "", controllers=True), f"Key;226 & {PAD};3")

    def test_a_press_is_built_from_a_device_an_element_and_a_side(self) -> None:
        self.assertEqual(bindings.controller_of(PAD, 4), f"vpx:{PAD};4")
        self.assertEqual(bindings.controller_of(PAD, 513, 0.5), f"vpx:{PAD};513;o;0.5")
        self.assertEqual(bindings.controller_of(PAD, 513, -0.5, True), f"vpx:{PAD};513;x;-0.5")

    def test_a_part_that_is_not_a_joystick_is_not_a_member(self) -> None:
        self.assertEqual(bindings.controller_member("Mouse;1"), "")
        self.assertEqual(bindings.controller_member("OpenPinDev;3"), "")

    def test_the_default_is_written_blank_with_a_controller_in_the_value(self) -> None:
        held = bindings.parse(f"Key;225 | {PAD};4", "Key;225", controllers=True)

        self.assertEqual(
            bindings.value_of([("ShiftLeft",)], f"Key;225 | {PAD};4", "Key;225",
                              controllers=True), "")
        self.assertEqual(held.kept, ())


class DeviceRecordTests(unittest.TestCase):
    INFO = {PAD: bindings.DeviceInfo("Pro Controller #1", ((4, False, "Button A"),
                                                           (514, True, "Left Stick X")))}

    def writes(self, devices: str = "") -> dict[str, str]:
        return bindings.device_writes([(f"vpx:{PAD};4",)], devices, self.INFO)

    def test_a_joystick_the_program_does_not_list_is_listed_with_what_it_has(self) -> None:
        self.assertEqual(self.writes(), {
            f"Input.Device.{PAD}.NoAutoLayout": "1",
            f"Input.Device.{PAD}.Name": "Pro Controller #1",
            f"Input.Device.{PAD}.Type": "2",
            f"Input.Device.{PAD}.Element0": "4;B;Button A",
            f"Input.Device.{PAD}.Element1": "514;A;Left Stick X",
            "Input.Devices": PAD})

    def test_a_joystick_already_listed_only_has_its_automatic_layout_turned_off(self) -> None:
        self.assertEqual(self.writes(f"Other;{PAD}"),
                         {f"Input.Device.{PAD}.NoAutoLayout": "1"})

    def test_the_new_joystick_is_added_after_those_already_listed(self) -> None:
        self.assertEqual(self.writes("SDLJoy_aa_1")["Input.Devices"], f"SDLJoy_aa_1;{PAD}")

    def test_a_joystick_not_plugged_in_is_not_recorded_half_way(self) -> None:
        writes = bindings.device_writes([("vpx:SDLJoy_ab_1;4",)], "", self.INFO)

        self.assertEqual(writes, {"Input.Device.SDLJoy_ab_1.NoAutoLayout": "1"})

    def test_a_trigger_at_zero_is_the_same_member_written_either_way(self) -> None:
        self.assertEqual(bindings.controller_member(f"{PAD};516;o;0.000000"), f"vpx:{PAD};516")
        self.assertEqual(bindings.controller_member(f"{PAD};516;x;0.000000"),
                         f"vpx:{PAD};516;x;0")

    def test_keys_alone_write_nothing_to_the_record(self) -> None:
        self.assertEqual(bindings.device_writes([("KeyA",)], "", self.INFO), {})

    def test_the_record_is_written_into_the_file_under_input(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        path = Path(held.name) / "VPinballX.ini"
        path.write_text("[Input]\nMapping.Custom1 = \n", encoding="utf-8")
        settings = {"ini_path": str(path)}

        VPXConfig().write(SCOPE_LAUNCHER, "", {
            "Input.Mapping.Custom1": f"{PAD};4", **self.writes()}, settings)

        text = path.read_text(encoding="utf-8")
        self.assertIn(f"Mapping.Custom1 = {PAD};4", text)
        self.assertIn(f"Devices = {PAD}", text)
        self.assertIn(f"Device.{PAD}.NoAutoLayout = 1", text)
        self.assertIn(f"Device.{PAD}.Element1 = 514;A;Left Stick X", text)


if __name__ == "__main__":
    unittest.main()

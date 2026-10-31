"""Visual Pinball's half of the screens answer, as ini keys."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from apps.vpx import areas
from apps.vpx import screens as vs
from common.apps.contract import TableOrder


@dataclass(frozen=True)
class Monitor:
    number: int
    name: str
    width: int
    height: int
    x: int
    y: int


WIDE = Monitor(1, "SAMSUNG", 3840, 2160, 0, 0)
BACK = Monitor(2, "DELL P2419H", 1920, 1080, 3840, 0)
STRIP = Monitor(3, "", 1280, 390, 5760, 0)
TALL = Monitor(1, "SAMSUNG", 2160, 3840, 0, 0)
CAB = Monitor(1, "LG", 1080, 1920, 0, 0)
LAND = Monitor(1, "LG", 1920, 1080, 0, 0)
SQUARE = Monitor(1, "SAMSUNG", 1080, 1080, 0, 0)
SCREENS = [WIDE, BACK, STRIP]
LOGGED = ["Samsung Electric Company HDMI-A-1-SAMSUNG [0, 0]", "CVT DVI [5760, 0]"]


class NamingAScreenTests(unittest.TestCase):
    def test_the_name_vpx_logged_at_the_position_wins(self) -> None:
        self.assertEqual(vs.display_id(WIDE, LOGGED),
                         "Samsung Electric Company HDMI-A-1-SAMSUNG [0, 0]")

    def test_a_screen_vpx_never_logged_is_called_by_its_model(self) -> None:
        self.assertEqual(vs.display_id(BACK, LOGGED), "DELL P2419H [3840, 0]")

    def test_a_screen_with_neither_has_no_name(self) -> None:
        self.assertEqual(vs.display_id(STRIP), "")

    def test_a_logged_name_at_another_position_is_not_borrowed(self) -> None:
        self.assertEqual(vs.display_id(BACK, ["CVT DVI [0, 0]"]), "DELL P2419H [3840, 0]")

    def test_a_stored_id_names_the_screen_at_its_position(self) -> None:
        self.assertIs(vs.screen_for("anything at all [3840, 0]", SCREENS), BACK)

    def test_a_position_nothing_sits_at_names_no_screen(self) -> None:
        self.assertIsNone(vs.screen_for("SAMSUNG [10, 10]", SCREENS))

    def test_a_name_alone_names_the_one_screen_it_holds_the_model_of(self) -> None:
        self.assertIs(vs.screen_for("Samsung Electric Company SAMSUNG", SCREENS), WIDE)

    def test_a_blank_names_no_screen(self) -> None:
        self.assertIsNone(vs.screen_for("  ", SCREENS))

    def test_a_name_two_screens_share_names_neither(self) -> None:
        twin = Monitor(4, "SAMSUNG", 1920, 1080, 7000, 0)

        self.assertIsNone(vs.screen_for("SAMSUNG", [WIDE, twin]))


class WhereVpxHasItsPartsTests(unittest.TestCase):
    def test_a_floating_window_is_on_the_screen_its_display_names(self) -> None:
        held = vs.placed({
            "Player.PlayfieldDisplay": "SAMSUNG [0, 0]",
            "Backglass.BackglassDisplay": "DELL P2419H [3840, 0]",
            "Backglass.BackglassOutput": "1",
            "ScoreView.ScoreViewDisplay": "CVT DVI [5760, 0]",
            "ScoreView.ScoreViewOutput": "1"}, SCREENS)

        self.assertEqual(held, {"playfield": 1, "backglass": 2, "scoreview": 3,
                                "topper": None})

    def test_a_window_that_is_not_floating_is_on_no_screen(self) -> None:
        held = vs.placed({"Backglass.BackglassDisplay": "DELL P2419H [3840, 0]",
                          "Backglass.BackglassOutput": "0"}, SCREENS)

        self.assertIsNone(held["backglass"])

    def test_an_embedded_window_is_inside_the_playfield(self) -> None:
        held = vs.placed({"Topper.TopperOutput": "2"}, SCREENS)

        self.assertEqual(held["topper"], vs.INSIDE)

    def test_a_display_no_screen_answers_to_is_on_no_screen(self) -> None:
        held = vs.placed({"Backglass.BackglassDisplay": "Gone [1, 1]",
                          "Backglass.BackglassOutput": "1"}, SCREENS)

        self.assertIsNone(held["backglass"])

    def test_a_blank_ini_has_nothing_anywhere(self) -> None:
        self.assertEqual(set(vs.placed({}, SCREENS).values()), {None})


class PuttingPartsOnScreensTests(unittest.TestCase):
    def test_a_part_on_a_screen_gets_its_display_and_floats(self) -> None:
        written = vs.windows({"backglass": 2}, SCREENS)

        self.assertEqual(written.keys, {"Backglass.BackglassDisplay": "DELL P2419H [3840, 0]",
                                        "Backglass.BackglassOutput": "1"})

    def test_the_playfield_has_a_display_and_no_output(self) -> None:
        written = vs.windows({"playfield": 1}, SCREENS, LOGGED)

        self.assertEqual(written.keys, {
            "Player.PlayfieldDisplay": "Samsung Electric Company HDMI-A-1-SAMSUNG [0, 0]"})

    def test_a_part_with_no_screen_is_disabled(self) -> None:
        self.assertEqual(vs.windows({"scoreview": None}, SCREENS).keys,
                         {"ScoreView.ScoreViewOutput": "0"})

    def test_a_playfield_with_no_screen_blanks_its_display(self) -> None:
        self.assertEqual(vs.windows({"playfield": None}, SCREENS).keys,
                         {"Player.PlayfieldDisplay": ""})

    def test_a_part_in_the_playfield_is_embedded_in_a_region_of_its_window(self) -> None:
        written = vs.windows({"scoreview": vs.INSIDE}, [CAB], playfield=CAB)

        self.assertEqual(written.keys, {
            "ScoreView.ScoreViewOutput": "2", "ScoreView.ScoreViewWndX": "54",
            "ScoreView.ScoreViewWndY": "1623", "ScoreView.ScoreViewWidth": "972",
            "ScoreView.ScoreViewHeight": "243"})

    def test_the_region_lies_inside_the_window_whatever_its_shape(self) -> None:
        for window in (CAB, WIDE, BACK, TALL, Monitor(1, "", 1366, 768, 0, 0)):
            with self.subTest(window=window):
                x, y, width, height = vs.embedded_region(window)

                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + width, window.width)
                self.assertLessEqual(y + height, window.height)
                self.assertEqual(width // 4, height)

    def test_the_region_is_a_strip_along_the_bottom_edge_centered(self) -> None:
        self.assertEqual(vs.embedded_region(CAB), (54, 1623, 972, 243))
        self.assertEqual(vs.embedded_region(BACK), (474, 783, 972, 243))

    def test_the_region_is_never_the_size_of_a_floating_window(self) -> None:
        x, y, width, height = vs.embedded_region(BACK)

        self.assertLess(width, BACK.width)
        self.assertLess(height, BACK.height // 2)

    def test_a_part_in_the_playfield_with_no_playfield_screen_is_left_out_and_says_so(self) -> None:
        written = vs.windows({"topper": vs.INSIDE, "backglass": 2}, SCREENS)

        self.assertEqual(written.unsized, ("topper",))
        self.assertNotIn("Topper.TopperOutput", written.keys)
        self.assertNotIn("Topper.TopperWidth", written.keys)
        self.assertIn("Backglass.BackglassOutput", written.keys)

    def test_the_playfield_itself_is_never_embedded(self) -> None:
        written = vs.windows({"playfield": vs.INSIDE}, SCREENS, playfield=WIDE)

        self.assertEqual((written.keys, written.unsized), ({}, ()))

    def test_a_screen_with_no_name_leaves_its_part_out_and_says_so(self) -> None:
        written = vs.windows({"scoreview": 3, "backglass": 2}, SCREENS)

        self.assertEqual(written.unnamed, ("scoreview",))
        self.assertNotIn("ScoreView.ScoreViewOutput", written.keys)
        self.assertNotIn("ScoreView.ScoreViewDisplay", written.keys)
        self.assertIn("Backglass.BackglassOutput", written.keys)

    def test_a_part_the_ini_has_no_window_for_is_left_out(self) -> None:
        self.assertEqual(vs.windows({"cabinet": 1}, SCREENS).keys, {})


class AgreeingWithVpinfeTests(unittest.TestCase):
    ON = {"playfield": 1, "backglass": 2, "scoreview": 3}

    def test_parts_in_the_same_place_do_not_differ(self) -> None:
        self.assertEqual(vs.differs({**self.ON, "topper": 4}, {**self.ON, "topper": 4}), [])

    def test_a_topper_on_another_screen_than_vpinfe_s_differs(self) -> None:
        self.assertEqual(vs.differs({**self.ON, "topper": 4}, {**self.ON, "topper": 2}),
                         ["topper"])
        self.assertEqual(vs.differs({**self.ON, "topper": 4}, {**self.ON, "topper": None}),
                         ["topper"])

    def test_a_topper_with_no_screen_in_vpinfe_is_as_the_person_set_it(self) -> None:
        self.assertEqual(vs.differs(self.ON, {**self.ON, "topper": 2}), [])

    def test_a_part_somewhere_else_differs(self) -> None:
        self.assertEqual(vs.differs(self.ON, {**self.ON, "scoreview": None}), ["scoreview"])

    def test_a_part_vpinfe_leaves_off_and_vpx_has_differs(self) -> None:
        self.assertEqual(vs.differs({"playfield": 1}, {"playfield": 1, "backglass": 2}),
                         ["backglass"])

    def test_a_move_reaches_the_parts_vpx_still_has_where_vpinfe_had_them(self) -> None:
        got = vs.followed(self.ON, {"backglass": 3, "scoreview": 2},
                          {**self.ON, "scoreview": None})

        self.assertEqual(got, {"backglass": 3})

    def test_a_topper_follows_where_vpx_had_it_with_vpinfe(self) -> None:
        self.assertEqual(vs.followed({"topper": 4}, {"topper": 2}, {"topper": 4}),
                         {"topper": 2})
        self.assertEqual(vs.followed({}, {"topper": 2}, {}), {"topper": 2})

    def test_a_topper_vpx_has_set_apart_stays(self) -> None:
        self.assertEqual(vs.followed({"topper": 4}, {"topper": 2}, {"topper": 3}), {})

    def test_a_topper_vpinfe_stops_having_stays_where_the_person_has_it(self) -> None:
        self.assertEqual(vs.followed({"topper": 4}, {"topper": None}, {"topper": 4}), {})

    def test_a_topper_on_the_screen_a_followed_part_moves_to_is_cleared(self) -> None:
        got = vs.followed({"backglass": 2, "topper": 4}, {"backglass": 4, "topper": None},
                          {"backglass": 2, "topper": 4})

        self.assertEqual(got, {"backglass": 4, "topper": None})
        self.assertEqual(vs.followed({"backglass": 2}, {"backglass": 4},
                                     {"backglass": 2, "topper": 4}),
                         {"backglass": 4, "topper": None})

    def test_a_part_vpx_holds_nothing_for_follows_the_move(self) -> None:
        got = vs.followed({"backglass": 2}, {"backglass": 4}, {}, blank={"backglass"})

        self.assertEqual(got, {"backglass": 4})

    def test_a_part_vpx_holds_nothing_for_is_not_followed_to_nowhere(self) -> None:
        got = vs.followed({"backglass": 2}, {"backglass": None}, {}, blank={"backglass"})

        self.assertEqual(got, {})

    def test_a_part_vpx_has_somewhere_else_stays_though_another_is_blank(self) -> None:
        got = vs.followed({"backglass": 2, "scoreview": 3}, {"backglass": 4, "scoreview": 4},
                          {"backglass": 1}, blank={"scoreview"})

        self.assertEqual(got, {"scoreview": 4})

    def test_a_key_that_is_missing_or_blank_holds_nothing_and_an_output_of_0_holds_something(
            self) -> None:
        got = vs.holds_nothing({"Player.PlayfieldDisplay": " ", "Backglass.BackglassOutput": "0",
                                "Topper.TopperOutput": ""})

        self.assertEqual(got, {"playfield", "scoreview", "topper"})

    def test_a_screen_the_file_names_for_a_part_is_a_value_though_its_output_is_blank(
            self) -> None:
        got = vs.holds_nothing({"Backglass.BackglassDisplay": "DELL [0, 0]"})

        self.assertNotIn("backglass", got)


class TheTurnTests(unittest.TestCase):
    PORTRAIT_BY_ROTATION = {"0": "0.0", "90": "90.0", "180": "180.0", "270": "270.0"}
    LANDSCAPE_BY_ROTATION = {"0": "90.0", "90": "180.0", "180": "270.0", "270": "0.0"}

    def test_the_flipper_end_goes_to_the_edge_that_faces_the_player(self) -> None:
        """VPinFE's rotation 0 faces the output's bottom edge, 90 the left, 180 the top and
        270 the right. VPX puts the flipper end on the bottom of a portrait output at 0 and
        on the right of a landscape one at 0, a clockwise turn at a time."""
        for rotation, expected in self.PORTRAIT_BY_ROTATION.items():
            with self.subTest(shape="portrait", rotation=rotation):
                self.assertEqual(vs.view_cab_rotation(CAB, rotation), expected)
        for rotation, expected in self.LANDSCAPE_BY_ROTATION.items():
            with self.subTest(shape="landscape", rotation=rotation):
                self.assertEqual(vs.view_cab_rotation(WIDE, rotation), expected)

    def test_a_playfield_the_os_turns_upright_is_zero(self) -> None:
        self.assertEqual(vs.view_cab_rotation(CAB, "0"), "0.0")

    def test_a_blank_rotation_counts_as_zero(self) -> None:
        self.assertEqual(vs.view_cab_rotation(CAB, ""), "0.0")
        self.assertEqual(vs.view_cab_rotation(WIDE, ""), "90.0")

    def test_a_landscape_panel_that_wants_zero_or_180_faces_left_or_right(self) -> None:
        self.assertEqual(vs.view_cab_rotation(WIDE, "270"), "0.0")
        self.assertEqual(vs.view_cab_rotation(WIDE, "90"), "180.0")

    def test_the_value_is_written_as_the_float_vpx_declares(self) -> None:
        for rotation in ("0", "90", "180", "270"):
            self.assertRegex(vs.view_cab_rotation(CAB, rotation) or "", r"^\d+\.0$")

    def test_a_turn_of_none_of_the_four_says_nothing(self) -> None:
        self.assertIsNone(vs.view_cab_rotation(CAB, "45"))
        self.assertIsNone(vs.view_cab_rotation(CAB, "sideways"))

    def test_a_square_output_says_nothing(self) -> None:
        self.assertIsNone(vs.view_cab_rotation(SQUARE, "0"))

    def test_no_screen_says_nothing(self) -> None:
        self.assertIsNone(vs.view_cab_rotation(None, "0"))

    def test_the_value_goes_back_to_the_rotation_that_made_it(self) -> None:
        for window in (CAB, WIDE):
            for rotation in ("0", "90", "180", "270"):
                with self.subTest(window=window.width, rotation=rotation):
                    value = vs.view_cab_rotation(window, rotation)

                    self.assertEqual(vs.vpinfe_rotation(window, value or ""), rotation)

    def test_a_blank_vpx_turn_is_its_own_zero_on_either_shape(self) -> None:
        self.assertEqual(vs.vpinfe_rotation(CAB, ""), "0")
        self.assertEqual(vs.vpinfe_rotation(WIDE, ""), "270")

    def test_a_vpx_turn_between_the_four_matches_no_rotation(self) -> None:
        self.assertIsNone(vs.vpinfe_rotation(CAB, "45"))
        self.assertIsNone(vs.vpinfe_rotation(SQUARE, "0"))
        self.assertIsNone(vs.vpinfe_rotation(None, "0"))

    def test_a_blank_is_vpxs_own_zero(self) -> None:
        self.assertEqual(vs.turn_of(""), 0.0)
        self.assertEqual(vs.turn_of("270"), 270.0)
        self.assertIsNone(vs.turn_of("sideways"))

    def test_vpx_differs_only_where_the_answer_gives_a_turn(self) -> None:
        self.assertFalse(vs.rotation_differs("0.0", ""))
        self.assertFalse(vs.rotation_differs("0.0", "0"))
        self.assertTrue(vs.rotation_differs("0.0", "270"))
        self.assertFalse(vs.rotation_differs(None, "270"))

    def test_a_new_answer_moves_vpx_while_it_holds_the_old_one(self) -> None:
        self.assertTrue(vs.rotation_follows("0.0", "0"))
        self.assertTrue(vs.rotation_follows(None, ""))
        self.assertFalse(vs.rotation_follows("0.0", "270"))
        self.assertFalse(vs.rotation_follows(None, "90"))


class WhatTheArrangementImpliesTests(unittest.TestCase):
    def test_a_score_screen_gets_the_priorities_and_full_dmd(self) -> None:
        got = vs.derived({"playfield": 1, "backglass": 2, "scoreview": 3}, {})

        self.assertEqual(got, {**vs.PRIORITIES, **vs.FULL_DMD})

    def test_the_priorities_stack_pup_over_the_backglass_dmd_over_the_score(self) -> None:
        got = vs.PRIORITIES

        self.assertLess(got["ScoreView.Priority.ScoreView"], got["ScoreView.Priority.B2SLegacyDMD"])
        self.assertLess(got["ScoreView.Priority.B2SLegacyDMD"], got["ScoreView.Priority.PUP"])

    def test_one_screen_gets_the_priorities_and_no_full_dmd(self) -> None:
        got = vs.derived({"playfield": 1}, {})

        self.assertEqual(got, vs.PRIORITIES)

    def test_two_screens_with_no_dmd_screen_get_nothing(self) -> None:
        self.assertEqual(vs.derived({"playfield": 1, "backglass": 2}, {}), {})

    def test_two_screens_with_the_dmd_embedded_get_nothing_either(self) -> None:
        self.assertEqual(vs.derived({"playfield": 1, "backglass": 2, "scoreview": "inside"},
                                    {}), {})

    def test_two_screens_where_one_is_the_dmd_get_everything(self) -> None:
        got = vs.derived({"playfield": 1, "scoreview": 2}, {})

        self.assertEqual(got, {**vs.PRIORITIES, **vs.FULL_DMD})

    def test_two_parts_on_one_screen_are_one_screen(self) -> None:
        self.assertEqual(vs.derived({"playfield": 1, "backglass": 1}, {}), vs.PRIORITIES)

    def test_a_value_vpx_holds_is_left_alone(self) -> None:
        got = vs.derived({"playfield": 1, "scoreview": 2},
                         {"ScoreView.Priority.PUP": "5", "Plugin.B2SLegacy.B2SHideGrill": "0"})

        self.assertNotIn("ScoreView.Priority.PUP", got)
        self.assertNotIn("Plugin.B2SLegacy.B2SHideGrill", got)
        self.assertIn("ScoreView.Priority.ScoreView", got)

    def test_a_blank_value_is_nothing_held(self) -> None:
        got = vs.derived({"playfield": 1}, {"ScoreView.Priority.PUP": "  "})

        self.assertEqual(got["ScoreView.Priority.PUP"], "3")

    def test_full_dmd_is_never_turned_off(self) -> None:
        self.assertEqual(vs.full_dmd({"playfield": 1, "backglass": 2}), {})
        self.assertEqual(vs.full_dmd({}), {})


class ByProgramTests(unittest.TestCase):
    """The same arrangement answered for the plugins a program has."""

    ON = {"playfield": 1, "backglass": 2, "scoreview": 3}
    NIXIE_ERA = {"plugins": ["B2S", "Nixie", "PUP"], "build": 6300,
                 "defaults": {"Plugin.B2S.Enable": "1", "Plugin.B2SLegacy.Enable": "0"}}
    LEGACY_ERA = {"plugins": ["B2S", "B2SLegacy", "ScoreView", "PUP"], "build": 6200,
                  "defaults": {"Plugin.B2S.Enable": "0", "Plugin.B2SLegacy.Enable": "1"}}
    SCOREVIEW_ERA = {"plugins": ["B2S", "ScoreView", "PUP"], "build": 6100,
                     "defaults": {"Plugin.B2S.Enable": "0", "Plugin.B2SLegacy.Enable": "1"}}

    def test_a_program_not_known_is_answered_as_before(self) -> None:
        self.assertEqual(vs.derived(self.ON, {}, None), {**vs.PRIORITIES, **vs.FULL_DMD})
        self.assertEqual(vs.derived(self.ON, {}, {"plugins": None}),
                         {**vs.PRIORITIES, **vs.FULL_DMD})

    def test_a_program_with_no_plugins_gets_nothing(self) -> None:
        self.assertEqual(vs.derived(self.ON, {}, {"plugins": [], "build": 6000}), {})

    def test_where_the_legacy_renderer_draws_the_answer_is_the_legacy_one(self) -> None:
        self.assertEqual(vs.derived(self.ON, {}, self.LEGACY_ERA),
                         {**vs.PRIORITIES, **vs.FULL_DMD})

    def test_the_legacy_renderer_off_leaves_b2s_to_draw(self) -> None:
        held = {"Plugin.B2SLegacy.Enable": "0", "Plugin.B2S.Enable": "1"}

        got = vs.derived(self.ON, held, self.LEGACY_ERA)

        self.assertEqual(got["Plugin.B2S.ScoreViewDMDOverlay"], "1")
        self.assertNotIn("Plugin.B2SLegacy.ScoreViewDMDOverlay", got)
        self.assertNotIn("ScoreView.Priority.B2SLegacyDMD", got)

    def test_nixie_ranks_between_pup_and_b2s_and_scoreview_is_not_named(self) -> None:
        got = vs.render_priorities(self.ON, self.NIXIE_ERA, {})

        self.assertEqual(got, {"ScoreView.Priority.PUP": "3", "ScoreView.Priority.Nixie": "2",
                               "ScoreView.Priority.B2S": "1"})

    def test_before_nixie_b2s_ranks_over_the_score_view(self) -> None:
        got = vs.render_priorities(self.ON, self.SCOREVIEW_ERA, {})

        self.assertEqual(got, {"ScoreView.Priority.PUP": "3", "ScoreView.Priority.B2S": "2",
                               "ScoreView.Priority.ScoreView": "1"})

    def test_only_the_plugins_installed_are_ranked(self) -> None:
        program = {**self.NIXIE_ERA, "plugins": ["B2S", "Nixie"]}

        self.assertEqual(vs.render_priorities(self.ON, program, {}),
                         {"ScoreView.Priority.Nixie": "2", "ScoreView.Priority.B2S": "1"})

    def test_two_screens_without_a_dmd_screen_get_nothing_whatever_the_program(self) -> None:
        for program in (None, self.NIXIE_ERA, self.LEGACY_ERA):
            with self.subTest(program=program):
                self.assertEqual(
                    vs.render_priorities({"playfield": 1, "backglass": 2}, program, {}), {})

    def test_full_dmd_is_b2s_s_own_keys_while_it_keeps_the_dmd_in_the_ini(self) -> None:
        got = vs.full_dmd(self.ON, {**self.SCOREVIEW_ERA, "build": 6200},
                          {"Plugin.B2S.Enable": "1"})

        self.assertEqual(got, vs.FULL_DMD_B2S)

    def test_b2s_left_off_by_its_build_draws_nothing_so_full_dmd_is_nothing(self) -> None:
        self.assertEqual(vs.full_dmd(self.ON, {**self.SCOREVIEW_ERA, "build": 6200}, {}), {})

    def test_full_dmd_is_nothing_once_the_backglass_file_keeps_the_dmd(self) -> None:
        self.assertEqual(vs.full_dmd(self.ON, self.NIXIE_ERA, {}), {})

    def test_nixie_ranks_over_b2s_on_both_sides_of_the_build_that_moves_the_dmd(self) -> None:
        for build in (6254, 6266, 6267, 6300):
            with self.subTest(build=build):
                got = vs.derived(self.ON, {}, {**self.NIXIE_ERA, "build": build})

                self.assertGreater(got["ScoreView.Priority.Nixie"],
                                   got["ScoreView.Priority.B2S"])
                self.assertGreater(got["ScoreView.Priority.PUP"],
                                   got["ScoreView.Priority.Nixie"])

    def test_full_dmd_writes_b2s_s_keys_until_6267_and_nothing_from_it(self) -> None:
        for build, keys in ((6266, vs.FULL_DMD_B2S), (6267, {}), (6300, {})):
            with self.subTest(build=build):
                got = vs.full_dmd(self.ON, {**self.NIXIE_ERA, "build": build}, {})

                self.assertEqual(got, keys)

    def test_full_dmd_is_nothing_with_no_plugin_drawing(self) -> None:
        program = {"plugins": ["PUP"], "build": 6200, "defaults": {}}

        self.assertEqual(vs.full_dmd(self.ON, program, {}), {})

    def test_a_value_the_ini_holds_is_still_left_alone(self) -> None:
        got = vs.derived(self.ON, {"ScoreView.Priority.Nixie": "9"}, self.NIXIE_ERA)

        self.assertNotIn("ScoreView.Priority.Nixie", got)
        self.assertEqual(got["ScoreView.Priority.PUP"], "3")


class ATablesOwnOrderTests(unittest.TestCase):
    """A table whose backglass file places a Score View DMD ranks B2S over Nixie."""

    PROGRAM: dict[str, Any] = {
        "plugins": ["B2S", "Nixie", "PUP"], "build": 6300, "dmd_in_file": True,
        "defaults": {"Plugin.B2S.Enable": "1", "Plugin.B2SLegacy.Enable": "0"}}
    LAUNCHER = {"ScoreView.Priority.PUP": "3", "ScoreView.Priority.Nixie": "2",
                "ScoreView.Priority.B2S": "1"}
    SWAPPED = {"ScoreView.Priority.Nixie": "1", "ScoreView.Priority.B2S": "2"}
    CLEARED = {"ScoreView.Priority.Nixie": "", "ScoreView.Priority.B2S": ""}
    NOTHING = TableOrder()

    def test_a_file_that_places_a_score_view_dmd_swaps_the_two_and_leaves_pup(self) -> None:
        got = vs.table_order(self.PROGRAM, self.LAUNCHER, {}, True)

        self.assertEqual(got, TableOrder(write=self.SWAPPED, ours=self.SWAPPED))

    def test_a_file_that_places_none_is_given_nothing(self) -> None:
        self.assertEqual(vs.table_order(self.PROGRAM, self.LAUNCHER, {}, False), self.NOTHING)

    def test_the_swap_it_recorded_is_taken_out_when_the_file_places_none(self) -> None:
        got = vs.table_order(self.PROGRAM, self.LAUNCHER, self.SWAPPED, False, self.SWAPPED)

        self.assertEqual(got, TableOrder(write=self.CLEARED, ours={}))

    def test_a_pair_the_table_holds_that_was_never_recorded_is_never_taken_out(self) -> None:
        nothing: tuple[dict[str, str] | None, ...] = (None, {})
        for written in nothing:
            with self.subTest(written=written):
                got = vs.table_order(self.PROGRAM, self.LAUNCHER, self.SWAPPED, False, written)

                self.assertEqual(got, self.NOTHING)

    def test_a_pair_that_is_not_the_recorded_one_is_never_taken_out_and_the_record_goes(
            self) -> None:
        written = {"ScoreView.Priority.Nixie": "3", "ScoreView.Priority.B2S": "4"}

        got = vs.table_order(self.PROGRAM, self.LAUNCHER, self.SWAPPED, False, written)

        self.assertEqual(got, TableOrder(ours={}))

    def test_a_table_that_holds_a_rank_of_its_own_is_left_alone_either_way(self) -> None:
        own = {"ScoreView.Priority.B2S": "5"}
        for in_file in (True, False):
            with self.subTest(in_file=in_file):
                self.assertEqual(vs.table_order(self.PROGRAM, self.LAUNCHER, own, in_file),
                                 self.NOTHING)

    def test_a_pair_the_table_changed_since_is_no_longer_ours_and_is_forgotten(self) -> None:
        own = {"ScoreView.Priority.Nixie": "1", "ScoreView.Priority.B2S": "9"}

        got = vs.table_order(self.PROGRAM, self.LAUNCHER, own, True, self.SWAPPED)

        self.assertEqual(got, TableOrder(ours={}))

    def test_a_table_already_holding_its_recorded_swap_has_nothing_to_write(self) -> None:
        got = vs.table_order(self.PROGRAM, self.LAUNCHER, self.SWAPPED, True, self.SWAPPED)

        self.assertEqual(got, TableOrder(ours=self.SWAPPED))

    def test_the_numbers_are_the_launchers_own_swapped(self) -> None:
        launcher = {**self.LAUNCHER, "ScoreView.Priority.Nixie": "7",
                    "ScoreView.Priority.B2S": "4"}

        got = vs.table_order(self.PROGRAM, launcher, {}, True)

        self.assertEqual(got.write, {"ScoreView.Priority.Nixie": "4",
                                     "ScoreView.Priority.B2S": "7"})

    def test_a_recorded_pair_follows_the_launcher_when_its_numbers_change(self) -> None:
        launcher = {**self.LAUNCHER, "ScoreView.Priority.Nixie": "7",
                    "ScoreView.Priority.B2S": "4"}

        got = vs.table_order(self.PROGRAM, launcher, self.SWAPPED, True, self.SWAPPED)

        wanted = {"ScoreView.Priority.Nixie": "4", "ScoreView.Priority.B2S": "7"}
        self.assertEqual(got, TableOrder(write=wanted, ours=wanted))

    def test_a_recorded_pair_goes_when_the_launcher_no_longer_ranks_nixie_over_b2s(self) -> None:
        launcher = {**self.LAUNCHER, "ScoreView.Priority.Nixie": "1",
                    "ScoreView.Priority.B2S": "2"}

        got = vs.table_order(self.PROGRAM, launcher, self.SWAPPED, True, self.SWAPPED)

        self.assertEqual(got, TableOrder(write=self.CLEARED, ours={}))

    def test_builds_that_keep_the_dmd_in_the_ini_have_no_file_to_follow(self) -> None:
        program = {**self.PROGRAM, "build": 6266, "dmd_in_file": False}

        self.assertEqual(vs.table_order(program, self.LAUNCHER, {}, True), self.NOTHING)

    def test_a_launcher_that_does_not_rank_nixie_over_b2s_gives_nothing(self) -> None:
        for launcher in ({}, {"ScoreView.Priority.Nixie": "1", "ScoreView.Priority.B2S": "2"},
                         {"ScoreView.Priority.Nixie": "2"}):
            with self.subTest(launcher=launcher):
                self.assertEqual(vs.table_order(self.PROGRAM, launcher, {}, True), self.NOTHING)

    def test_legacy_drawing_or_b2s_off_or_no_nixie_gives_nothing(self) -> None:
        legacy = {**self.LAUNCHER, "Plugin.B2SLegacy.Enable": "1"}
        with_legacy = {**self.PROGRAM, "plugins": [*self.PROGRAM["plugins"], "B2SLegacy"]}
        b2s_off = {**self.LAUNCHER, "Plugin.B2S.Enable": "0"}
        no_nixie = {**self.PROGRAM, "plugins": ["B2S", "PUP"]}
        for program, launcher in ((with_legacy, legacy), (self.PROGRAM, b2s_off),
                                  (no_nixie, self.LAUNCHER), (None, self.LAUNCHER)):
            with self.subTest(program=program, launcher=launcher):
                self.assertEqual(vs.table_order(program, launcher, {}, True), self.NOTHING)


class TheWholeAnswerTests(unittest.TestCase):
    NOW = {"playfield": 1, "backglass": 2, "scoreview": 3}

    def test_it_is_the_windows_the_turn_and_what_follows(self) -> None:
        got = vs.answer(self.NOW, self.NOW, [TALL, BACK, STRIP], LOGGED, {},
                        turn=("portrait", "0"))

        self.assertEqual(got.keys["Backglass.BackglassOutput"], "1")
        self.assertEqual(got.keys["ScoreView.ScoreViewOutput"], "1")
        self.assertEqual(got.keys["TableOverride.ViewCabRotation"], "0.0")
        self.assertEqual(got.keys["ScoreView.Priority.PUP"], "3")
        self.assertEqual(got.unnamed, ())

    def test_a_landscape_playfield_is_turned_by_the_edge_that_faces_the_player(self) -> None:
        got = vs.answer(self.NOW, self.NOW, SCREENS, LOGGED, {}, turn=("landscape", "270"))

        self.assertEqual(got.keys["TableOverride.ViewCabRotation"], "0.0")

    def test_the_turn_is_worked_out_for_the_screen_the_playfield_is_on(self) -> None:
        moved = {"playfield": 1}

        tall = vs.answer(moved, moved, [TALL], LOGGED, {}, turn=("portrait", "0"))
        wide = vs.answer(moved, moved, [WIDE], LOGGED, {}, turn=("landscape", "0"))

        self.assertEqual(tall.keys["TableOverride.ViewCabRotation"], "0.0")
        self.assertEqual(wide.keys["TableOverride.ViewCabRotation"], "90.0")

    def test_the_turn_of_a_playfield_on_no_screen_is_not_written(self) -> None:
        got = vs.answer({"playfield": None}, {"playfield": None}, SCREENS, LOGGED, {},
                        turn=("portrait", "0"))

        self.assertNotIn("TableOverride.ViewCabRotation", got.keys)

    def test_a_part_put_in_the_playfield_takes_a_region_of_that_playfield(self) -> None:
        now: dict[str, vs.Placement] = {"playfield": 1, "scoreview": "inside"}

        got = vs.answer({"scoreview": "inside"}, now, [CAB], LOGGED, {})

        self.assertEqual(got.keys["ScoreView.ScoreViewOutput"], "2")
        self.assertEqual(got.keys["ScoreView.ScoreViewWidth"], "972")
        self.assertEqual(got.unsized, ())

    def test_a_part_put_in_a_playfield_with_no_screen_is_reported(self) -> None:
        now = {"playfield": None, "scoreview": "inside"}

        got = vs.answer({"scoreview": "inside"}, now, [CAB], LOGGED, {})

        self.assertEqual(got.unsized, ("scoreview",))
        self.assertNotIn("ScoreView.ScoreViewOutput", got.keys)

    REGION_HELD = {"ScoreView.ScoreViewOutput": "2", "ScoreView.ScoreViewWndX": "54",
                   "ScoreView.ScoreViewWndY": "1623", "ScoreView.ScoreViewWidth": "972",
                   "ScoreView.ScoreViewHeight": "243"}

    def test_a_part_leaving_the_playfield_for_a_screen_floats_with_its_region_blanked(self) -> None:
        now = {"playfield": 1, "scoreview": 3}

        got = vs.answer({"scoreview": 3}, now, SCREENS, LOGGED, self.REGION_HELD)

        self.assertEqual(got.keys["ScoreView.ScoreViewOutput"], "1")
        self.assertEqual({key: got.keys[key] for key in vs.REGION["scoreview"]},
                         dict.fromkeys(vs.REGION["scoreview"], ""))

    def test_a_part_leaving_the_playfield_for_no_screen_is_disabled_with_its_region_blanked(
            self) -> None:
        now = {"playfield": 1, "scoreview": None}

        got = vs.answer({"scoreview": None}, now, SCREENS, LOGGED, self.REGION_HELD)

        self.assertEqual(got.keys["ScoreView.ScoreViewOutput"], "0")
        self.assertEqual({key: got.keys[key] for key in vs.REGION["scoreview"]},
                         dict.fromkeys(vs.REGION["scoreview"], ""))

    def test_a_floating_part_that_moves_keeps_the_geometry_of_its_window(self) -> None:
        held = {"ScoreView.ScoreViewOutput": "1", "ScoreView.ScoreViewWndX": "40"}

        for where in (3, None):
            with self.subTest(where=where):
                got = vs.answer({"scoreview": where}, {"playfield": 1, "scoreview": where},
                                SCREENS, LOGGED, held)

                self.assertFalse(set(vs.REGION["scoreview"]) & set(got.keys))

    def test_a_part_that_stays_inside_keeps_its_region_as_it_is(self) -> None:
        now: dict[str, vs.Placement] = {"playfield": 1, "scoreview": "inside"}

        got = vs.answer({"backglass": None}, {**now, "backglass": None}, [CAB], LOGGED,
                        self.REGION_HELD)

        self.assertFalse(set(vs.REGION["scoreview"]) & set(got.keys))

    def test_a_part_that_cannot_be_named_does_not_have_its_region_blanked(self) -> None:
        got = vs.answer({"scoreview": 3}, {"playfield": 1, "scoreview": 3}, SCREENS, [],
                        self.REGION_HELD)

        self.assertEqual(got.unnamed, ("scoreview",))
        self.assertFalse(set(vs.REGION["scoreview"]) & set(got.keys))

    def test_a_part_inside_follows_the_playfield_to_a_window_of_another_shape(self) -> None:
        now: dict[str, vs.Placement] = {"playfield": 1, "scoreview": "inside"}

        got = vs.answer({"playfield": 1}, now, [LAND], LOGGED, self.REGION_HELD)

        self.assertEqual(got.keys["ScoreView.ScoreViewOutput"], "2")
        x, y, width, height = vs.embedded_region(LAND)
        self.assertEqual([got.keys[key] for key in vs.REGION["scoreview"]],
                         [str(x), str(y), str(width), str(height)])
        self.assertLessEqual(x + width, LAND.width)
        self.assertLessEqual(y + height, LAND.height)
        self.assertEqual(got.unsized, ())

    def test_a_playfield_with_no_screen_leaves_the_parts_inside_it_alone(self) -> None:
        now = {"playfield": None, "scoreview": "inside"}

        got = vs.answer({"playfield": None}, now, [CAB], LOGGED, self.REGION_HELD)

        self.assertEqual(got.unsized, ())
        self.assertFalse(set(vs.REGION["scoreview"]) & set(got.keys))

    def test_a_part_put_inside_along_with_the_playfield_takes_the_new_window_too(self) -> None:
        now: dict[str, vs.Placement] = {
            "playfield": 1, "scoreview": "inside", "backglass": "inside"}

        got = vs.answer({"playfield": 1, "backglass": "inside"}, now, [LAND], LOGGED, {})

        for part in ("scoreview", "backglass"):
            self.assertEqual(got.keys[vs.OUTPUT[part]], "2")
            self.assertEqual([got.keys[key] for key in vs.REGION[part]],
                             [str(one) for one in vs.embedded_region(LAND)])

    def test_no_turn_given_leaves_the_turn_alone(self) -> None:
        got = vs.answer({"backglass": 2}, self.NOW, [TALL, BACK, STRIP], LOGGED, {})

        self.assertNotIn("TableOverride.ViewCabRotation", got.keys)

    def test_two_screens_with_no_dmd_write_no_priorities(self) -> None:
        now = {"playfield": 1, "backglass": 2}

        got = vs.answer(now, now, SCREENS, LOGGED, {})

        self.assertEqual(set(got.keys), {
            "Player.PlayfieldDisplay", "Backglass.BackglassDisplay",
            "Backglass.BackglassOutput"})

    def test_the_part_that_has_no_name_is_reported_and_the_rest_written(self) -> None:
        got = vs.answer({"scoreview": 3}, self.NOW, SCREENS, [], {})

        self.assertEqual(got.unnamed, ("scoreview",))
        self.assertNotIn("ScoreView.ScoreViewOutput", got.keys)


class ThePanelTests(unittest.TestCase):
    def test_a_kind_turns_the_plugin_and_that_kind_on(self) -> None:
        got = vs.dmd_util("ZeDMD", platform="linux")

        self.assertEqual(got, {"Plugin.DMDUtil.Enable": "1", "Plugin.DMDUtil.ZeDMD": "1"})

    def test_it_is_written_on_macos_too(self) -> None:
        self.assertEqual(vs.dmd_util("Pixelcade", platform="darwin"),
                         {"Plugin.DMDUtil.Enable": "1", "Plugin.DMDUtil.Pixelcade": "1"})

    def test_windows_gets_nothing(self) -> None:
        self.assertEqual(vs.dmd_util("ZeDMD", platform="win32"), {})

    def test_only_the_plugin_and_the_kind_are_written(self) -> None:
        for kind in vs.DMD_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(set(vs.dmd_util(kind, platform="linux")),
                                 {"Plugin.DMDUtil.Enable", f"Plugin.DMDUtil.{kind}"})

    def test_no_kind_writes_nothing(self) -> None:
        self.assertEqual(vs.dmd_util(None, platform="linux"), {})
        self.assertEqual(vs.dmd_util("Other", platform="linux"), {})

    def test_the_kind_vpx_has_on_is_read_back(self) -> None:
        self.assertEqual(vs.dmd_kind({"Plugin.DMDUtil.PIN2DMD": "1"}), "PIN2DMD")
        self.assertEqual(vs.dmd_kind({}), "")


class TheHeadingsTheManagerKnowsTests(unittest.TestCase):
    """The Manager tells a part's rows by the headings the displays area declares."""

    def headings(self) -> dict:
        return {heading.key: heading for heading in areas.CURATED[areas.DISPLAYS]}

    def test_each_window_heading_holds_its_display_and_output(self) -> None:
        found = self.headings()

        for key, part in vs.WINDOW_HEADINGS.items():
            with self.subTest(heading=key):
                self.assertIn(vs.DISPLAY[part], found[key].keys)
                if part in vs.OUTPUT:
                    self.assertIn(vs.OUTPUT[part], found[key].keys)

    def test_the_cabinet_heading_holds_the_screen_size(self) -> None:
        for key in vs.SCREEN_SIZE:
            self.assertIn(key, self.headings()[vs.CABINET_HEADING].keys)


if __name__ == "__main__":
    unittest.main()

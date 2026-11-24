"""What an upgrade of the program leaves in the settings file, and what each would become."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from apps.vpx import build, leftovers
from apps.vpx import ini as vini
from apps.vpx.setting_types import Span
from common.apps.contract import MOVE, NAME, PLACE, TURN_ON, Leftover

NEW = frozenset({"Nixie", "B2S"})


def _found(text: str, present: frozenset[str] | None = NEW, rev: int | None = None,
           in_file: bool = True) -> dict[str, Leftover]:
    return {one.key: one for one in leftovers.find(vini.parse(text), present, rev, in_file)}


class PluginsDecide(unittest.TestCase):
    def test_a_program_whose_plugins_cannot_be_read_leaves_nothing_behind(self) -> None:
        text = "[Plugin.ScoreView]\nEnable = 0\n[Plugin.B2SLegacy]\nB2SDualMode = 2\n"
        self.assertEqual(_found(text, None), {})

    def test_a_setting_stays_while_the_plugin_that_wrote_it_is_installed(self) -> None:
        text = "[Plugin.ScoreView]\nEnable = 0\n[Plugin.B2SLegacy]\nB2SDualMode = 2\n"
        self.assertEqual(_found(text, frozenset({"Nixie", "ScoreView", "B2S", "B2SLegacy"})), {})

    def test_the_new_plugin_has_to_be_there_to_take_it(self) -> None:
        text = "[Plugin.ScoreView]\nEnable = 0\n[Plugin.B2SLegacy]\nB2SDualMode = 2\n"
        self.assertEqual(_found(text, frozenset()), {})


class Moves(unittest.TestCase):
    def test_score_view_off_moves_to_nixie(self) -> None:
        one = _found("[Plugin.ScoreView]\nEnable = 0\n")["Plugin.ScoreView.Enable"]

        self.assertEqual((one.action, one.to, one.new_value, one.drops),
                         (MOVE, "Plugin.Nixie.Enable", "0", (("Plugin.ScoreView.Enable", "0"),)))

    def test_score_view_on_has_nothing_to_move(self) -> None:
        self.assertEqual(_found("[Plugin.ScoreView]\nEnable = 1\n"), {})

    def test_the_layout_folder_moves(self) -> None:
        one = _found("[Plugin.ScoreView]\nLayoutFolder = /layouts\n")[
            "Plugin.ScoreView.LayoutFolder"]

        self.assertEqual((one.to, one.new_value), ("Plugin.Nixie.LayoutFolder", "/layouts"))

    def test_a_value_already_at_the_new_name_is_the_one_in_force(self) -> None:
        text = ("[Plugin.ScoreView]\nEnable = 0\nLayoutFolder = /old\n"
                "[Plugin.Nixie]\nEnable = 1\nLayoutFolder = /new\n")

        self.assertEqual(_found(text), {})

    def test_a_blank_new_name_does_not_hold_a_value(self) -> None:
        text = "[Plugin.ScoreView]\nEnable = 0\n[Plugin.Nixie]\nEnable =\n"

        self.assertIn("Plugin.ScoreView.Enable", _found(text))

    def test_fantasy_moves_to_b2s(self) -> None:
        one = _found("[Plugin.B2SLegacy]\nB2SDualMode = 2\n")["Plugin.B2SLegacy.B2SDualMode"]

        self.assertEqual((one.to, one.new_value), ("Plugin.B2S.DualMode", "2"))

    def test_authentic_is_what_b2s_does_anyway(self) -> None:
        self.assertEqual(_found("[Plugin.B2SLegacy]\nB2SDualMode = 1\n"), {})

    def test_dual_mode_is_not_offered_to_a_build_that_dropped_it(self) -> None:
        text = "[Plugin.B2SLegacy]\nB2SDualMode = 2\n"
        spans = {**build.SPANS, "Plugin.B2S.DualMode": Span(0, 6300)}

        with patch.object(build, "SPANS", spans):
            self.assertIn("Plugin.B2SLegacy.B2SDualMode", _found(text, rev=6200))
            self.assertEqual(_found(text, rev=6300), {})

    def test_off_is_read_as_the_settings_read_it(self) -> None:
        self.assertIn("Plugin.ScoreView.Enable", _found("[Plugin.ScoreView]\nEnable = false\n"))
        self.assertIn("Plugin.B2S.Enable", _found("[Plugin.B2S]\nEnable = FALSE\n"))

    def test_hide_grill_moves_to_show_grill_inverted(self) -> None:
        on = _found("[Plugin.B2SLegacy]\nB2SHideGrill = 1\n")["Plugin.B2SLegacy.B2SHideGrill"]
        off = _found("[Plugin.B2SLegacy]\nB2SHideGrill = 0\n")["Plugin.B2SLegacy.B2SHideGrill"]

        self.assertEqual((on.to, on.new_value), ("Plugin.B2S.ShowGrill", "0"))
        self.assertEqual(off.new_value, "1")


class Boxes(unittest.TestCase):
    BOX = ("[Plugin.B2S]\nBackglassDMDOverlay = 1\nBackglassDMDAutoPos = 0\n"
           "BackglassDMDX = 10\nBackglassDMDY = 20\nBackglassDMDW = 300\nBackglassDMDH = 80\n")

    def test_a_box_is_carried_in_the_pixels_the_settings_count(self) -> None:
        one = _found(self.BOX)["Plugin.B2S.BackglassDMD"]

        self.assertEqual((one.action, one.target, one.box, one.frame),
                         (PLACE, "Backglass", (10, 20, 300, 80), None))
        self.assertEqual(one.word, "dmd_backglass")

    def test_every_key_it_holds_is_blanked(self) -> None:
        one = _found(self.BOX)["Plugin.B2S.BackglassDMD"]

        self.assertEqual({key for key, _ in one.drops},
                         {"Plugin.B2S.BackglassDMD" + part
                          for part in ("Overlay", "AutoPos", "X", "Y", "W", "H")})

    def test_the_score_view_window_has_a_word_of_its_own(self) -> None:
        text = self.BOX.replace("Backglass", "ScoreView")

        self.assertEqual(_found(text)["Plugin.B2S.ScoreViewDMD"].word, "dmd_scoreview")

    def test_a_box_left_to_the_program_has_nothing_to_carry(self) -> None:
        for text in (self.BOX.replace("Overlay = 1", "Overlay = 0"),
                     self.BOX.replace("W = 300", "W = 0")):
            with self.subTest(text=text):
                self.assertEqual(_found(text), {})

    def test_a_build_that_keeps_the_dmd_in_the_settings_leaves_the_box_where_it_is(self) -> None:
        self.assertEqual(_found(self.BOX, in_file=False), {})

    def test_legacy_counts_in_its_own_frame(self) -> None:
        text = self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]")

        one = _found(text)["Plugin.B2SLegacy.BackglassDMD"]

        self.assertEqual(one.frame, (1024, 768))

    def test_a_legacy_box_is_counted_by_legacy_s_own_grill_setting(self) -> None:
        text = self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]")

        self.assertEqual(_found(text)["Plugin.B2SLegacy.BackglassDMD"].cut_if,
                         "Plugin.B2SLegacy.B2SHideGrill")
        self.assertEqual(_found(self.BOX)["Plugin.B2S.BackglassDMD"].cut_if, "")

    def test_a_b2s_box_goes_first_and_the_legacy_one_is_only_named(self) -> None:
        text = self.BOX + self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]")

        found = _found(text)

        self.assertEqual(found["Plugin.B2S.BackglassDMD"].action, PLACE)
        self.assertEqual(found["Plugin.B2SLegacy.BackglassDMD"].action, NAME)
        self.assertEqual(found["Plugin.B2SLegacy.BackglassDMD"].word, "dmd_backglass_replaced")

    def test_a_window_only_legacy_holds_is_still_offered(self) -> None:
        text = (self.BOX + self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]")
                .replace("Backglass", "ScoreView"))

        found = _found(text)

        self.assertEqual(found["Plugin.B2SLegacy.ScoreViewDMD"].action, PLACE)

    def test_a_box_placed_automatically_is_named_and_carries_nothing(self) -> None:
        for plugin, window, word in (("B2S", "Backglass", "dmd_backglass_auto"),
                                     ("B2S", "ScoreView", "dmd_scoreview_auto")):
            text = (self.BOX.replace("AutoPos = 0", "AutoPos = 1")
                    .replace("Backglass", window))
            with self.subTest(window=window):
                one = _found(text)[f"Plugin.{plugin}.{window}DMD"]

                self.assertEqual((one.action, one.word, one.drops), (NAME, word, ()))

    def test_an_automatic_box_needs_no_size_to_be_named(self) -> None:
        text = "[Plugin.B2S]\nBackglassDMDOverlay = 1\nBackglassDMDAutoPos = 1\n"

        self.assertEqual(_found(text)["Plugin.B2S.BackglassDMD"].action, NAME)

    def test_an_automatic_legacy_box_is_named_once_legacy_is_gone(self) -> None:
        text = self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]").replace("AutoPos = 0",
                                                                           "AutoPos = 1")

        one = _found(text)["Plugin.B2SLegacy.BackglassDMD"]

        self.assertEqual((one.action, one.word), (NAME, "dmd_backglass_auto"))

    def test_a_live_b2s_box_for_the_window_keeps_legacy_s_box_from_replacing_it(self) -> None:
        text = self.BOX + self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]")

        self.assertEqual(_found(text)["Plugin.B2SLegacy.BackglassDMD"].action, NAME)

    def test_a_b2s_box_that_is_off_or_unsized_does_not_shadow_legacy_s(self) -> None:
        legacy = ("[Plugin.B2SLegacy]\nBackglassDMDOverlay = 1\nBackglassDMDAutoPos = 0\n"
                  "BackglassDMDX = 1\nBackglassDMDY = 1\nBackglassDMDW = 5\nBackglassDMDH = 5\n")
        for b2s in ("[Plugin.B2S]\nBackglassDMDOverlay = 0\n",
                    "[Plugin.B2S]\nBackglassDMDOverlay = 1\nBackglassDMDW = 0\n",
                    "[Plugin.B2S]\nBackglassDMDAutoPos = 1\n"):
            with self.subTest(b2s=b2s):
                self.assertEqual(_found(b2s + legacy)["Plugin.B2SLegacy.BackglassDMD"].action,
                                 PLACE)

    def test_an_automatic_b2s_box_with_a_size_does_not_shadow_legacy_s_manual_one(self) -> None:
        b2s = ("[Plugin.B2S]\nBackglassDMDOverlay = 1\nBackglassDMDAutoPos = 1\n"
               "BackglassDMDW = 300\nBackglassDMDH = 100\n")
        legacy = ("[Plugin.B2SLegacy]\nBackglassDMDOverlay = 1\nBackglassDMDAutoPos = 0\n"
                  "BackglassDMDX = 1\nBackglassDMDY = 1\nBackglassDMDW = 5\nBackglassDMDH = 5\n")

        found = _found(b2s + legacy)

        self.assertEqual((found["Plugin.B2S.BackglassDMD"].action,
                          found["Plugin.B2S.BackglassDMD"].word),
                         (NAME, "dmd_backglass_auto"))
        self.assertEqual(found["Plugin.B2SLegacy.BackglassDMD"].action, PLACE)

    def test_a_size_that_is_not_a_finite_number_is_no_size(self) -> None:
        legacy = ("[Plugin.B2SLegacy]\nBackglassDMDOverlay = 1\nBackglassDMDAutoPos = 0\n"
                  "BackglassDMDX = 1\nBackglassDMDY = 1\nBackglassDMDW = 5\nBackglassDMDH = 5\n")
        for wide in ("inf", "1e999", "nan"):
            b2s = (f"[Plugin.B2S]\nBackglassDMDOverlay = 1\nBackglassDMDAutoPos = 0\n"
                   f"BackglassDMDW = {wide}\nBackglassDMDH = 10\n")
            with self.subTest(wide=wide):
                self.assertEqual(_found(b2s + legacy)["Plugin.B2SLegacy.BackglassDMD"].action,
                                 PLACE)
                self.assertNotIn("Plugin.B2S.BackglassDMD", _found(b2s))

    def test_an_automatic_legacy_box_under_a_live_b2s_one_is_said_to_be_replaced(self) -> None:
        legacy = self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]").replace("AutoPos = 0",
                                                                            "AutoPos = 1")

        self.assertEqual(_found(self.BOX + legacy)["Plugin.B2SLegacy.BackglassDMD"].word,
                         "dmd_backglass_replaced")

    def test_legacy_boxes_wait_while_legacy_is_installed(self) -> None:
        text = self.BOX.replace("Plugin.B2S]", "Plugin.B2SLegacy]")

        self.assertEqual(_found(text, frozenset({"B2S", "B2SLegacy"})), {})


class Named(unittest.TestCase):
    def test_the_hide_dmd_keys_are_named_and_nothing_replaces_them(self) -> None:
        found = _found("[Plugin.B2SLegacy]\nB2SHideB2SDMD = 1\nB2SHideDMD = true\n")

        self.assertEqual({one.action for one in found.values()}, {NAME})
        self.assertEqual(sorted(found), ["Plugin.B2SLegacy.B2SHideB2SDMD",
                                         "Plugin.B2SLegacy.B2SHideDMD"])

    def test_hide_dmd_set_to_off_is_not_named(self) -> None:
        self.assertEqual(_found("[Plugin.B2SLegacy]\nB2SHideDMD = 0\n"), {})

    def test_b2s_switched_off_is_named_with_turning_it_on(self) -> None:
        one = _found("[Plugin.B2S]\nEnable = 0\n")["Plugin.B2S.Enable"]

        self.assertEqual((one.action, one.to, one.new_value), (TURN_ON, "Plugin.B2S.Enable", "1"))

    def test_b2s_on_is_not_named(self) -> None:
        self.assertEqual(_found("[Plugin.B2S]\nEnable = 1\n"), {})


if __name__ == "__main__":
    unittest.main()

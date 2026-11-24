"""The comparisons: where VPinFE's view, the desktop's and Visual Pinball's settings disagree.
Each rule fires on the facts it has, and a missing fact never makes a finding."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import replace

from apps.vpx import screens as vpx_screens
from common.capture import geometry
from common.support.collect.displays import compare
from common.support.collect.displays import desktop as desk
from common.support.collect.displays.model import ScreenFact, Seen

LANDSCAPE = Seen(0, "Monitor A", 0, 0, 1920, 1080)
PORTRAIT = Seen(1, "Monitor B", 1920, 0, 1080, 1920)


def facts(seen: Sequence[Seen] = (LANDSCAPE, PORTRAIT), **fields: object) -> compare.Facts:
    base: dict[str, object] = {
        "here": desk.Desktop(desk.LINUX, desk.X11, desk.OTHER), "seen": tuple(seen),
        "windows": (("Playfield", "0"), ("Backglass", "1"))}
    return compare.Facts(**{**base, **fields})  # type: ignore[arg-type]


def ids(found: Sequence[object]) -> list[str]:
    return [one.id for one in found]  # type: ignore[attr-defined]


def device(connector: str, seen: Seen, **fields: object) -> ScreenFact:
    base: dict[str, object] = {
        "connector": connector, "x": seen.x, "y": seen.y, "width": seen.width,
        "height": seen.height, "turn": geometry.NONE, "source": "test"}
    return ScreenFact(**{**base, **fields})  # type: ignore[arg-type]


class Matching(unittest.TestCase):
    def test_the_same_place_and_size_is_the_same_screen(self) -> None:
        a, b = device("A", LANDSCAPE), device("B", PORTRAIT)
        self.assertEqual(compare.match((LANDSCAPE, PORTRAIT), (b, a)), {0: a, 1: b})

    def test_a_screen_whose_size_differs_is_matched_by_its_place(self) -> None:
        scaled = device("A", LANDSCAPE, width=1536, height=864)
        self.assertEqual(compare.match((LANDSCAPE,), (scaled,)), {0: scaled})

    def test_a_screen_in_another_place_is_matched_by_its_size_and_each_is_taken_once(self) -> None:
        moved = device("A", LANDSCAPE, x=500)
        found = compare.match((LANDSCAPE, replace(LANDSCAPE, number=1, x=3000)), (moved,))
        self.assertEqual(list(found), [0])

    def test_a_screen_the_desktop_does_not_place_is_never_matched(self) -> None:
        self.assertEqual(compare.match((LANDSCAPE,), (ScreenFact(connector="A"),)), {})


class Rules(unittest.TestCase):
    def test_nothing_known_is_nothing_found(self) -> None:
        self.assertEqual(compare.findings(facts(windows=())), [])

    def test_r1_a_window_set_to_a_screen_that_is_not_there_is_a_problem(self) -> None:
        found = compare.findings(facts((LANDSCAPE,), windows=(("Backglass", "1"),)))
        self.assertEqual(ids(found), ["R1"])
        self.assertTrue(found[0].problem)
        self.assertIn("screen 1", found[0].text)
        self.assertIn("only 1 screen was found", found[0].text)

    def test_r1_an_empty_setting_is_not_a_missing_screen(self) -> None:
        self.assertEqual(compare.findings(facts((LANDSCAPE,), windows=(("Backglass", ""),))), [])

    def test_r2_a_different_count_is_a_note(self) -> None:
        device_screens = (device("A", LANDSCAPE),)
        found = compare.findings(facts(device=device_screens))
        self.assertEqual(ids(found), ["R2"])
        self.assertFalse(found[0].problem)

    def test_r3_a_scaled_size_is_a_note_and_names_xwayland_on_wayland(self) -> None:
        scaled = device("A", LANDSCAPE, width=1536, height=864, scale=1.25)
        here = desk.Desktop(desk.LINUX, desk.WAYLAND, desk.GNOME)
        found = compare.findings(facts((LANDSCAPE, PORTRAIT), here=here,
                                       device=(scaled, device("B", PORTRAIT))))
        self.assertEqual(ids(found), ["R3"])
        self.assertIn("XWayland", found[0].text)
        self.assertIn("scale 1.25", found[0].text)

    def test_r4_two_turns_is_a_note_that_claims_no_total(self) -> None:
        turned = device("B", PORTRAIT, turn=geometry.Turn(ccw=90), said="left")
        found = compare.findings(facts(
            windows=(("Playfield", "1"),), device=(device("A", LANDSCAPE), turned),
            rotation=90, orientation="portrait"))
        self.assertIn("R4", ids(found))
        text = next(one.text for one in found if one.id == "R4")
        self.assertNotIn("180", text)
        self.assertIn("both", text)

    def test_r5_a_picture_that_is_not_turned_to_fit_a_portrait_screen_is_a_problem(self) -> None:
        found = compare.findings(facts(
            windows=(("Playfield", "1"),), orientation="landscape", rotation=0))
        self.assertEqual(ids(found), ["R5"])
        self.assertTrue(found[0].problem)

    def test_r5_does_not_fire_once_rotation_is_set_or_the_shape_agrees(self) -> None:
        self.assertEqual(ids(compare.findings(facts(
            windows=(("Playfield", "1"),), orientation="landscape", rotation=90))), [])
        self.assertEqual(ids(compare.findings(facts(
            windows=(("Playfield", "1"),), orientation="portrait", rotation=0))), [])

    def test_r6_visual_pinballs_turn_that_differs_from_what_vpinfe_gives_is_a_problem(self) -> None:
        base = facts(windows=(("Playfield", "1"),), orientation="portrait", rotation=0)
        expected = compare.expected_cab_rotation(base)
        assert expected is not None
        different = str(float(expected) + 90)
        found = compare.findings(replace(base, vp=compare.VpFacts(different)))
        self.assertEqual(ids(found), ["R6"])
        self.assertEqual(compare.findings(replace(base, vp=compare.VpFacts(expected))), [])

    def test_r7_a_visual_pinball_display_id_that_matches_no_screen_is_a_problem(self) -> None:
        base = facts()
        vp = compare.VpFacts(compare.expected_cab_rotation(base), (
            compare.VpDisplay("backglass", "Gone [9, 9]", vpx_screens.FLOATING),
            compare.VpDisplay("scoreview", "Gone [9, 9]", vpx_screens.DISABLED)))
        found = compare.findings(replace(base, vp=vp))
        self.assertEqual(ids(found), ["R7"])
        self.assertIn("Backglass.BackglassDisplay", found[0].text)

    def test_r8_two_screens_over_the_same_area_is_duplication(self) -> None:
        twin = replace(LANDSCAPE, number=1)
        self.assertEqual(ids(compare.findings(facts((LANDSCAPE, twin), windows=()))), ["R8"])

    def test_r9_a_window_set_to_a_screen_that_is_asleep_or_off(self) -> None:
        asleep = device("A", LANDSCAPE, asleep=True)
        off = device("B", PORTRAIT, enabled=False)
        found = compare.findings(facts(device=(asleep, off)))
        self.assertEqual(ids(found), ["R9", "R9"])
        self.assertIn("asleep", found[0].text)
        self.assertIn("off", found[1].text)

    def test_r10_a_compositor_without_the_rules_line_is_a_problem_except_on_vpinos(self) -> None:
        sway = desk.Desktop(desk.LINUX, desk.WAYLAND, desk.SWAY)
        self.assertEqual(ids(compare.findings(facts(here=sway, rules_added=False))), ["R10"])
        self.assertEqual(compare.findings(facts(here=sway, rules_added=True)), [])
        self.assertEqual(compare.findings(facts(here=sway, rules_added=None)), [])
        appliance = replace(sway, distro="vpinos")
        self.assertEqual(compare.findings(facts(here=appliance, rules_added=False)), [])

    def test_r11_game_mode_with_more_than_one_window_is_a_note(self) -> None:
        game = desk.Desktop(desk.LINUX, desk.WAYLAND, desk.GAMESCOPE)
        found = compare.findings(facts(here=game))
        self.assertEqual(ids(found), ["R11"])
        self.assertFalse(found[0].problem)
        self.assertIn("playfield and backglass", found[0].text)
        one_window = facts(here=game, windows=(("Playfield", "0"),))
        self.assertEqual(compare.findings(one_window), [])


class WhenNothingIsKnown(unittest.TestCase):
    def test_no_detected_screens_means_no_missing_screen_findings(self) -> None:
        vp = compare.VpFacts("0", (compare.VpDisplay("backglass", "Gone [9, 9]",
                                                     vpx_screens.FLOATING),))
        found = compare.findings(facts((), vp=vp, windows=(("Backglass", "1"),)))
        self.assertEqual(ids(found), [])

    def test_a_rotation_that_is_not_a_number_is_not_compared(self) -> None:
        self.assertEqual(ids(compare.findings(facts(vp=compare.VpFacts("oops")))), [])

    def test_an_output_with_no_area_is_never_a_vpinfe_screen(self) -> None:
        """sway and Hyprland list a switched-off output with a 0x0 area at the origin."""
        empty = device("B", LANDSCAPE, width=0, height=0)
        self.assertEqual(compare.match((LANDSCAPE,), (empty,)), {})

    def test_a_mirror_alone_is_not_a_turn(self) -> None:
        mirrored = device("A", LANDSCAPE, turn=geometry.Turn(True, 0))
        self.assertFalse(mirrored.is_turned)
        found = compare.findings(facts(
            (LANDSCAPE,), windows=(("Playfield", "0"),), rotation=90, device=(mirrored,)))
        self.assertNotIn("R4", ids(found))
        self.assertEqual(compare.device_rotation(mirrored),
                         "Not rotated, mirrored (landscape, 1920x1080)")


class Describing(unittest.TestCase):
    def test_not_reported_where_the_desktop_says_nothing_about_a_turn(self) -> None:
        self.assertEqual(compare.device_rotation(None), "Not reported")
        self.assertEqual(compare.device_rotation(ScreenFact()), "Not reported")

    def test_a_screen_that_is_not_turned_says_so_with_its_shape(self) -> None:
        self.assertEqual(compare.device_rotation(device("A", LANDSCAPE)),
                         "Not rotated (landscape, 1920x1080)")

    def test_a_turned_screen_prints_the_operating_systems_word_beside_the_degrees(self) -> None:
        turned = device("B", PORTRAIT, turn=geometry.Turn(ccw=90), said="left", source="xrandr")
        said = compare.device_rotation(turned)
        self.assertIn("90° (xrandr says left)", said)
        self.assertIn("now portrait", said)

    def test_what_that_makes_names_both_turns_and_never_judges(self) -> None:
        turned = device("B", PORTRAIT, turn=geometry.Turn(ccw=90), said="left")
        both = compare.what_that_makes(facts(rotation=90), turned, PORTRAIT)
        self.assertIn("as well", both)
        none = compare.what_that_makes(facts(rotation=0), device("A", LANDSCAPE), LANDSCAPE)
        self.assertEqual(none, "Neither VPinFE nor the device turns this screen.")
        self.assertEqual(compare.what_that_makes(facts(rotation=90), None, PORTRAIT),
                         "VPinFE turns the picture 90°.")


if __name__ == "__main__":
    unittest.main()

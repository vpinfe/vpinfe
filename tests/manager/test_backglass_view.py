"""What the backglass preview draws, read from the settings in effect."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from common.games import backglass_ops
from common.games.formats import dmd_frame
from manager.library import backglass_view as view

LEGACY, B2S = backglass_ops.LEGACY, backglass_ops.B2S
FLOATING = {"Backglass.BackglassOutput": "1"}


def _glass(*, size=(1000, 800), grill=200, scoreview=None) -> dict:
    return {"size": size, "grill": grill, "scoreview_art": scoreview}


def _backglass(held: dict, **glass):
    return view.screens(held, _glass(**glass))[0]


class TheGrillCut(unittest.TestCase):
    def test_legacy_cuts_it_when_hide_grill_is_on(self) -> None:
        screen = _backglass({f"{LEGACY}B2SHideGrill": "1"})

        self.assertAlmostEqual(100 * 800 / 600, screen.scale)
        self.assertAlmostEqual(1000 / 600, screen.ratio)

    def test_legacy_keeps_it_when_hide_grill_is_off(self) -> None:
        screen = _backglass({f"{LEGACY}B2SHideGrill": "0"})

        self.assertEqual((100.0, 1000 / 800), (screen.scale, screen.ratio))

    def test_b2s_keeps_it_only_when_show_grill_is_on(self) -> None:
        off = {f"{LEGACY}Enable": "0"}
        on = {f"{LEGACY}Enable": "0", f"{B2S}ShowGrill": "1"}

        self.assertAlmostEqual(800 / 600 * 100, _backglass(off).scale)
        self.assertEqual(100.0, _backglass(on).scale)

    def test_a_file_with_no_grill_is_not_cut(self) -> None:
        self.assertEqual(100.0, _backglass({f"{LEGACY}B2SHideGrill": "1"}, grill=0).scale)


class TheWindow(unittest.TestCase):
    def test_a_floating_window_has_its_own_shape(self) -> None:
        held = {**FLOATING, "Backglass.BackglassWidth": "1920",
                "Backglass.BackglassHeight": "540"}

        self.assertAlmostEqual(1920 / 540, _backglass(held).ratio)

    def test_full_screen_takes_the_full_screen_size(self) -> None:
        held = {**FLOATING, "Backglass.BackglassFullScreen": "1",
                "Backglass.BackglassFSWidth": "1280", "Backglass.BackglassFSHeight": "1024",
                "Backglass.BackglassWidth": "1920", "Backglass.BackglassHeight": "540"}

        self.assertAlmostEqual(1280 / 1024, _backglass(held).ratio)

    def test_an_embedded_window_takes_the_arts_shape(self) -> None:
        held = {"Backglass.BackglassOutput": "2", "Backglass.BackglassWidth": "1920",
                "Backglass.BackglassHeight": "540"}

        self.assertAlmostEqual(1000 / 800, _backglass(held).ratio)

    def test_a_turned_window_without_a_size_swaps_the_arts_sides(self) -> None:
        screen = _backglass({"Backglass.BackglassRotation": "1"})

        self.assertEqual((1, 800 / 1000), (screen.turns, screen.ratio))

    def test_turns_wrap_at_four(self) -> None:
        self.assertEqual(1, _backglass({"Backglass.BackglassRotation": "5"}).turns)

    def test_an_output_of_zero_or_nothing_is_off(self) -> None:
        self.assertEqual((True, True, False),
                         (_backglass({}).off, _backglass({"Backglass.BackglassOutput": "0"}).off,
                          _backglass(FLOATING).off))


class TheDmd(unittest.TestCase):
    def _box(self, **keys: str) -> view.Box | None:
        held = {f"{LEGACY}BackglassDMD{name}": value for name, value in keys.items()}
        return _backglass({f"{LEGACY}B2SHideGrill": "1", **held}).dmd

    def test_it_is_not_drawn_with_the_overlay_off(self) -> None:
        self.assertIsNone(self._box(Overlay="0", X="1", Y="1", W="9", H="9"))

    def test_automatic_has_no_box(self) -> None:
        self.assertEqual(view.Box(auto=True), self._box(Overlay="1", AutoPos="1"))

    def test_a_legacy_box_is_in_percent_of_1024_by_768_counted_up_from_the_bottom(self) -> None:
        box = self._box(Overlay="1", X="256", Y="192", W="512", H="192")
        assert box is not None

        self.assertEqual((25.0, 50.0, 50.0, 25.0),
                         (box.left, box.top, box.width, box.height))

    def test_a_b2s_box_is_in_percent_of_the_art_with_the_grill_cut_counted_up_from_the_bottom(
            self) -> None:
        held = {f"{LEGACY}Enable": "0", f"{B2S}ShowGrill": "0", f"{B2S}BackglassDMDOverlay": "1",
                f"{B2S}BackglassDMDX": "100", f"{B2S}BackglassDMDY": "50",
                f"{B2S}BackglassDMDW": "500", f"{B2S}BackglassDMDH": "150"}

        box = _backglass(held).dmd

        self.assertEqual((10.0, 66.666667, 50.0, 25.0),
                         tuple(round(v, 6) for v in (box.left, box.top, box.width, box.height)))

    def test_a_box_with_no_size_is_not_drawn(self) -> None:
        self.assertIsNone(self._box(Overlay="1", X="1", Y="1", W="0", H="0"))

    def test_the_renderer_in_effect_names_the_keys(self) -> None:
        held = {f"{LEGACY}Enable": "0", f"{B2S}BackglassDMDOverlay": "1",
                f"{B2S}BackglassDMDAutoPos": "1", f"{LEGACY}BackglassDMDOverlay": "0"}

        self.assertEqual(view.Box(auto=True), _backglass(held).dmd)


class TheScoreView(unittest.TestCase):
    ON = {"ScoreView.ScoreViewOutput": "1", "ScoreView.ScoreViewWidth": "1000",
          "ScoreView.ScoreViewHeight": "250"}

    def _second(self, held: dict, art=(400, 100)):
        return view.screens(held, _glass(scoreview=art))[1]

    def test_it_is_drawn_when_the_file_has_art_and_the_window_is_on(self) -> None:
        self.assertAlmostEqual(4.0, self._second(self.ON).ratio)

    def test_it_is_not_drawn_when_its_window_is_off(self) -> None:
        self.assertIsNone(self._second({}))

    def test_it_is_not_drawn_when_the_file_has_no_art(self) -> None:
        self.assertIsNone(self._second(self.ON, art=None))

    def test_a_b2s_dmd_is_in_the_pixels_of_its_own_art_counted_up_from_the_bottom(self) -> None:
        held = {**self.ON, f"{LEGACY}Enable": "0", f"{B2S}ScoreViewDMDOverlay": "1",
                f"{B2S}ScoreViewDMDX": "40", f"{B2S}ScoreViewDMDY": "10",
                f"{B2S}ScoreViewDMDW": "200", f"{B2S}ScoreViewDMDH": "50"}

        box = self._second(held).dmd

        self.assertEqual((10.0, 40.0, 50.0, 50.0),
                         (box.left, box.top, box.width, box.height))


class TheBoxWritten(unittest.TestCase):
    def test_a_legacy_box_is_written_in_1024_by_768_with_automatic_off(self) -> None:
        screen = _backglass({f"{LEGACY}B2SHideGrill": "1"})

        values = view.box_values(LEGACY, screen, view.Box(25.0, 50.0, 50.0, 25.0))

        self.assertEqual({f"{LEGACY}BackglassDMDAutoPos": "0", f"{LEGACY}BackglassDMDX": "256",
                          f"{LEGACY}BackglassDMDY": "192", f"{LEGACY}BackglassDMDW": "512",
                          f"{LEGACY}BackglassDMDH": "192"}, values)

    def test_a_b2s_box_is_written_in_the_pixels_of_the_cut_art(self) -> None:
        held = {f"{LEGACY}Enable": "0"}
        screen = _backglass(held)

        values = view.box_values(B2S, screen, view.Box(10.0, 200 / 3, 50.0, 25.0))

        self.assertEqual(("100", "50", "500", "150"),
                         tuple(values[f"{B2S}BackglassDMD{name}"] for name in "XYWH"))

    def test_the_scoreview_has_its_own_keys_and_art(self) -> None:
        held = {**TheScoreView.ON, f"{LEGACY}Enable": "0"}
        screen = view.screens(held, _glass(scoreview=(400, 100)))[1]
        assert screen is not None

        values = view.box_values(B2S, screen, view.Box(10.0, 40.0, 50.0, 50.0))

        self.assertEqual(("40", "10", "200", "50"),
                         tuple(values[f"{B2S}ScoreViewDMD{name}"] for name in "XYWH"))

    def test_what_is_written_draws_the_same_box_again(self) -> None:
        for plugin in (LEGACY, B2S):
            with self.subTest(plugin=plugin):
                base = {f"{LEGACY}Enable": "1" if plugin == LEGACY else "0",
                        f"{LEGACY}B2SHideGrill": "1", f"{plugin}BackglassDMDOverlay": "1"}
                box = view.Box(12.5, 30.0, 40.0, 20.0)

                values = view.box_values(plugin, _backglass(base), box)
                again = _backglass({**base, **values}).dmd

                for want, got in zip((box.left, box.top, box.width, box.height),
                                     (again.left, again.top, again.width, again.height),
                                     strict=True):
                    self.assertAlmostEqual(want, got, delta=0.2)

    def test_a_box_cannot_be_written_outside_the_window_or_empty(self) -> None:
        screen = _backglass({})

        values = view.box_values(LEGACY, screen, view.Box(0.0, 100.0, 0.0, 0.0))

        self.assertEqual(("0", "0", "1", "1"),
                         tuple(values[f"{LEGACY}BackglassDMD{name}"] for name in "XYWH"))


class TheFrameFound(unittest.TestCase):
    def test_a_frame_counted_up_from_the_bottom_is_a_box_counted_down_from_the_top(self) -> None:
        box = view.frame_box(dmd_frame.Frame(100, 50, 500, 150), (1000, 600))

        self.assertEqual((10.0, 66.666667, 50.0, 25.0),
                         tuple(round(v, 6) for v in (box.left, box.top, box.width, box.height)))


class TheWindowFitted(unittest.TestCase):
    def test_the_backglass_is_fitted_on_the_picture_with_the_grill_cut_when_it_is_cut(self) -> None:
        fitted = view.fit_target({f"{LEGACY}B2SHideGrill": "1"}, _glass(), "Backglass")
        assert fitted is not None
        screen, cut = fitted

        self.assertEqual(("Backglass", True), (screen.part, cut))

    def test_the_backglass_is_fitted_on_the_whole_picture_when_the_grill_stays(self) -> None:
        fitted = view.fit_target({}, _glass(), "Backglass")
        assert fitted is not None
        screen, cut = fitted

        self.assertEqual(("Backglass", False), (screen.part, cut))

    def test_the_scoreview_is_fitted_even_while_its_window_is_off(self) -> None:
        fitted = view.fit_target({}, _glass(scoreview=(400, 100)), "ScoreView")
        assert fitted is not None
        screen, cut = fitted

        self.assertEqual(("ScoreView", (400, 100), False), (screen.part, screen.space, cut))

    def test_a_file_with_no_scoreview_art_has_nothing_to_fit_it_to(self) -> None:
        self.assertIsNone(view.fit_target({}, _glass(), "ScoreView"))


class TheWindowsStacked(unittest.TestCase):
    def test_two_windows_go_side_by_side_where_the_stage_is_wider_than_both_shapes_stack(
            self) -> None:
        rule = view._beside(1.5, 6.0)

        self.assertIn("(min-aspect-ratio: 3.0000)", rule)
        self.assertIn("flex-direction: row", rule)

    def test_the_readout_counts_in_the_units_the_fields_do(self) -> None:
        screen = view.Screen(1.0, 0, 100.0, None, part=view.SCORE_PART, space=(400, 100))

        self.assertEqual((1024, 768), view._counted_in(LEGACY, screen.space))
        self.assertEqual((400, 100), view._counted_in(B2S, screen.space))


class WhichEdition(unittest.TestCase):
    def test_dual_mode_two_is_fantasy_under_legacy(self) -> None:
        self.assertEqual("fantasy", view.edition_in_effect({f"{LEGACY}B2SDualMode": "2"}))

    def test_dual_mode_one_or_unset_is_authentic(self) -> None:
        self.assertEqual(("authentic", "authentic"),
                         (view.edition_in_effect({f"{LEGACY}B2SDualMode": "1"}),
                          view.edition_in_effect({})))

    def test_the_legacy_renderer_s_dual_mode_does_not_reach_b2s(self) -> None:
        held = {f"{LEGACY}Enable": "0", f"{LEGACY}B2SDualMode": "2"}

        self.assertEqual("authentic", view.edition_in_effect(held))

    def test_b2s_has_a_dual_mode_of_its_own(self) -> None:
        held = {f"{LEGACY}Enable": "0", f"{B2S}DualMode": "2"}

        self.assertEqual("fantasy", view.edition_in_effect(held))

    def test_the_program_decides_which_dual_mode_is_read(self) -> None:
        held = {f"{LEGACY}B2SDualMode": "2", f"{B2S}DualMode": "1"}
        legacy = {"plugins": ["B2S", "B2SLegacy"], "defaults": {}, "build": 6200}
        nixie = {"plugins": ["B2S", "Nixie"], "defaults": {}, "build": 6300}

        self.assertEqual(("fantasy", "authentic"),
                         (view.edition_in_effect(held, legacy),
                          view.edition_in_effect(held, nixie)))


class ByProgram(unittest.TestCase):
    NIXIE = {"plugins": ["B2S", "Nixie"], "build": 6300,
             "defaults": {"Plugin.B2S.Enable": "1", "Plugin.B2SLegacy.Enable": "0"}}
    NONE = {"plugins": ["PUP"], "build": 6300, "defaults": {}}

    def test_without_the_legacy_renderer_installed_b2s_draws_with_its_own_grill(self) -> None:
        held = {f"{LEGACY}B2SHideGrill": "0"}

        plain = view.screens(held, _glass())[0]
        nixie = view.screens(held, _glass(), self.NIXIE)[0]

        self.assertEqual(100.0, plain.scale)
        self.assertAlmostEqual(800 / 600 * 100, nixie.scale)

    def test_a_box_left_in_the_settings_is_dead_once_the_file_places_the_dmd(self) -> None:
        held = {f"{B2S}BackglassDMDOverlay": "1", f"{B2S}BackglassDMDX": "100",
                f"{B2S}BackglassDMDY": "50", f"{B2S}BackglassDMDW": "500",
                f"{B2S}BackglassDMDH": "150", "ScoreView.ScoreViewOutput": "1"}
        before = {**self.NIXIE, "build": 6266, "dmd_in_file": False}
        after = {**self.NIXIE, "build": 6267, "dmd_in_file": True}
        glass = _glass(scoreview=(400, 100))

        self.assertIsNotNone(view.screens(held, glass, before)[0].dmd)
        for program in (after, {**after, "build": 6400}):
            with self.subTest(build=program["build"]):
                glass_screen, score_screen = view.screens(held, glass, program)
                self.assertIsNone(glass_screen.dmd)
                assert score_screen is not None
                self.assertIsNone(score_screen.dmd)

    def test_the_legacy_renderer_keeps_its_box_whatever_the_build(self) -> None:
        held = {f"{LEGACY}BackglassDMDOverlay": "1", f"{LEGACY}BackglassDMDX": "100",
                f"{LEGACY}BackglassDMDY": "50", f"{LEGACY}BackglassDMDW": "500",
                f"{LEGACY}BackglassDMDH": "150"}
        program = {"plugins": ["B2S", "B2SLegacy"], "build": 6267, "dmd_in_file": True,
                   "defaults": {"Plugin.B2SLegacy.Enable": "1"}}

        self.assertIsNotNone(view.screens(held, _glass(), program)[0].dmd)

    def test_with_no_renderer_installed_no_box_is_drawn(self) -> None:
        held = {f"{B2S}BackglassDMDOverlay": "1", f"{B2S}BackglassDMDX": "1",
                f"{B2S}BackglassDMDY": "1", f"{B2S}BackglassDMDW": "9",
                f"{B2S}BackglassDMDH": "9"}

        self.assertIsNone(view.screens(held, _glass(), self.NONE)[0].dmd)


class TheDmdInTheFile(unittest.TestCase):
    PROGRAM = {"plugins": ["B2S"], "build": 6267, "dmd_in_file": True,
               "defaults": {"Plugin.B2S.Enable": "1"}}
    HELD = {"ScoreView.ScoreViewOutput": "1"}

    def glass(self, *displays: dict, **more) -> dict:
        return {**_glass(**more), "displays": list(displays)}

    def test_the_box_is_the_files_own_counted_from_the_top_left(self) -> None:
        glass = self.glass({"target": "Backglass", "x": 100, "y": 80, "w": 500, "h": 160},
                           grill=0)

        screen = view.screens(self.HELD, glass, self.PROGRAM)[0]

        assert screen.dmd is not None
        self.assertTrue(screen.in_file)
        self.assertEqual((10.0, 10.0, 50.0, 20.0),
                         (screen.dmd.left, screen.dmd.top, screen.dmd.width, screen.dmd.height))

    def test_a_cut_grill_counts_down_the_art_that_is_left(self) -> None:
        glass = self.glass({"target": "Backglass", "x": 0, "y": 300, "w": 100, "h": 60})
        held = {f"{B2S}ShowGrill": "0"}

        screen = view.screens(held, glass, self.PROGRAM)[0]

        assert screen.dmd is not None
        self.assertAlmostEqual(50.0, screen.dmd.top)
        self.assertAlmostEqual(10.0, screen.dmd.height)

    def test_a_file_with_no_dmd_has_no_box(self) -> None:
        self.assertIsNone(view.screens(self.HELD, self.glass(), self.PROGRAM)[0].dmd)

    def test_a_dmd_with_no_size_draws_nothing(self) -> None:
        glass = self.glass({"target": "Backglass", "x": 1, "y": 1, "w": 0, "h": 0})

        self.assertIsNone(view.screens(self.HELD, glass, self.PROGRAM)[0].dmd)

    def test_each_window_reads_its_own_dmd(self) -> None:
        glass = self.glass({"target": "Backglass", "x": 10, "y": 10, "w": 100, "h": 40},
                           {"target": "ScoreView", "x": 40, "y": 10, "w": 200, "h": 50},
                           scoreview=(400, 100))

        glass_screen, score_screen = view.screens(self.HELD, glass, self.PROGRAM)

        assert score_screen is not None and glass_screen.dmd and score_screen.dmd
        self.assertAlmostEqual(1.0, glass_screen.dmd.left)
        self.assertAlmostEqual(50.0, score_screen.dmd.width)
        self.assertTrue(score_screen.in_file)

    def test_the_settings_build_is_not_in_the_file(self) -> None:
        program = {**self.PROGRAM, "build": 6266, "dmd_in_file": False}

        self.assertFalse(view.screens(self.HELD, self.glass(), program)[0].in_file)
        self.assertFalse(view.in_file(self.HELD, program))
        self.assertTrue(view.in_file(self.HELD, self.PROGRAM))

    def test_a_dragged_box_is_written_in_pixels_from_the_top(self) -> None:
        screen = view.screens(self.HELD, self.glass(grill=0), self.PROGRAM)[0]

        self.assertEqual((100, 80, 500, 160),
                         view.file_values(screen, view.Box(10.0, 10.0, 50.0, 20.0)))

    def test_a_box_dragged_past_the_edge_stays_on_the_art(self) -> None:
        screen = view.screens(self.HELD, self.glass(grill=0), self.PROGRAM)[0]

        self.assertEqual((0, 0, 1, 1), view.file_values(screen, view.Box(-5.0, -1.0, 0.0, 0.0)))

    def test_what_is_written_is_what_is_drawn(self) -> None:
        glass = self.glass({"target": "Backglass", "x": 123, "y": 77, "w": 456, "h": 99},
                           grill=0)
        screen = view.screens(self.HELD, glass, self.PROGRAM)[0]

        assert screen.dmd is not None
        self.assertEqual((123, 77, 456, 99), view.file_values(screen, screen.dmd))

    def test_both_windows_can_be_fitted(self) -> None:
        glass = self.glass(scoreview=(400, 100))

        for part in (view.GLASS_PART, view.SCORE_PART):
            with self.subTest(part=part):
                target = view.fit_target(self.HELD, glass, part, self.PROGRAM)
                assert target is not None
                self.assertTrue(target[0].in_file)

    def test_a_score_view_with_its_window_off_is_still_fitted_in_the_file(self) -> None:
        target = view.fit_target({}, self.glass(scoreview=(400, 100)), view.SCORE_PART,
                                 self.PROGRAM)

        assert target is not None
        self.assertTrue(target[0].in_file)


class TheFrameAsTheFileCountsIt(unittest.TestCase):
    def found(self, framed):
        with patch.object(view, "_framed", return_value=framed):
            return view.found_in_file("g", "f", look="on", edition="authentic", cut=False,
                                      scoreview=False)

    def test_the_row_is_counted_from_the_top(self) -> None:
        # Frame.y counts up from the bottom row, so a frame 500 up and 100 tall has its top
        # row at 800 - 1 - 500 - 100 = 199 from the top.
        frame = dmd_frame.Frame(30, 500, 200, 100)

        self.assertEqual((30, 199, 200, 100), self.found((frame, (1000, 800))))

    def test_art_with_no_window_finds_nothing(self) -> None:
        self.assertIsNone(self.found(None))

    def test_a_window_drawn_on_a_picture_is_found_where_it_was_drawn(self) -> None:
        picture = Image.new("RGB", (960, 540), (255, 255, 255))
        ImageDraw.Draw(picture).rectangle((240, 100, 719, 219), fill=(0, 0, 0))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "glass.png"
            picture.save(path)
            with patch.object(view.backglass_ops, "picture_file",
                              return_value=SimpleNamespace(path=path)):
                found = view.found_in_file("g", "f", look="on", edition="authentic",
                                           cut=False, scoreview=False)

        # The search pads the window by one pixel on each side before it fits the shape.
        assert found is not None
        self.assertEqual((101, 118), (found[1], found[3]))


class TheLauncherConfigShape(unittest.TestCase):
    def test_a_value_is_read_out_of_its_entry(self) -> None:
        self.assertEqual({"a": "1", "b": "", "c": "x"},
                         view.flat({"a": {"value": "1"}, "b": None, "c": "x"}))


if __name__ == "__main__":
    unittest.main()

"""Visual Pinball's settings at the scope somebody is editing them.

Two layers, not three, and the table layer is one file whose name is decided by which
of two spellings exists. They do not stack, and the shadowing that follows is what the
`in_effect` flag exists to report.
"""

from __future__ import annotations

import re
import threading
import time
import unittest
from collections import Counter
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from apps.vpx import areas, bindings, build, displays
from apps.vpx.config import VPXConfig, own_file, own_log, settings_file
from apps.vpx.setting_types import LABELS, TYPES, Span
from common import apps
from common.apps.contract import SCOPE_ENTRY, SCOPE_FOLDER, SCOPE_LAUNCHER, Standing
from tests import REAL_MACHINE_FOLDERS

APP_INI = """\
[Backglass]
; Output Mode: Where it goes [Default: 'Disabled', 0='Disabled', 1='Floating']
BackglassOutput = 1

; Grill Height: How tall [Default: 180]
GrillHeight = 180

[DMD]
; Legacy Renderer: Use the legacy renderer [Default: 1]
Profile1Legacy = 1

[Version]
; VPX Version: what wrote this [Default: '10815353']
VPinball = 10815353
"""

KEY = "Backglass.BackglassOutput"
# Not contextual, where `KEY` is: the program keeps a contextual table value even when
# it matches the application's, so a rule about matching values needs an ordinary one.
PLAIN = "DMD.Profile1Legacy"
GRILL = "Backglass.GrillHeight"


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app_ini = self.root / "VPinballX.ini"
        self.app_ini.write_text(APP_INI)
        self.settings = {"ini_path": str(self.app_ini)}
        # A folder named for the game rather than the table, so the two spellings of the
        # table layer are genuinely different files.
        self.game = self.root / "Medieval Madness"
        self.game.mkdir()
        self.table = self.game / "MM (VPW 1.2).vpx"
        self.table.write_text("")
        self.other = self.game / "MM (VR).vpx"
        self.other.write_text("")
        self.config = VPXConfig()

    def at(self, scope: str, table=None, key: str = KEY):
        return self.config.read(scope, str(table or self.table), self.settings)[key]

    def folder_file(self, text: str) -> None:
        (self.game / "Medieval Madness.ini").write_text(text)

    def table_file(self, text: str) -> None:
        (self.game / "MM (VPW 1.2).ini").write_text(text)


class LayerTests(_Case):
    def test_with_nothing_beside_it_the_launcher_answers(self) -> None:
        found = self.at(SCOPE_ENTRY)

        self.assertEqual((found.value, found.scope), ("1", SCOPE_LAUNCHER))
        self.assertFalse(found.set_here)
        self.assertTrue(found.in_effect)

    def test_a_folder_file_reaches_the_tables_in_it(self) -> None:
        self.folder_file("[Backglass]\nBackglassOutput = 0\n")

        found = self.at(SCOPE_ENTRY)
        self.assertEqual((found.value, found.scope), ("0", SCOPE_FOLDER))
        self.assertFalse(found.set_here, "it is not set at the table")

    def test_at_its_own_scope_a_folder_value_is_set_here(self) -> None:
        self.folder_file("[Backglass]\nBackglassOutput = 0\n")

        found = self.at(SCOPE_FOLDER)
        self.assertTrue(found.set_here)
        self.assertTrue(found.in_effect)

    def test_a_table_file_stops_the_folder_reaching_that_table(self) -> None:
        """The trap. The two files do not stack: a table with its own file falls through
        to the application for everything that file does not carry, not to the folder."""
        self.folder_file("[Backglass]\nBackglassOutput = 0\n")
        self.table_file("[Backglass]\nGrillHeight = 200\n")

        found = self.at(SCOPE_ENTRY)
        self.assertEqual((found.value, found.scope), ("1", SCOPE_LAUNCHER))

    def test_the_shadowed_folder_value_reports_itself_as_not_in_effect(self) -> None:
        """Invisible otherwise, and it is the bug report we would get."""
        self.folder_file("[Backglass]\nBackglassOutput = 0\n")
        self.table_file("[Backglass]\nGrillHeight = 200\n")

        found = self.at(SCOPE_FOLDER)
        self.assertTrue(found.set_here)
        self.assertFalse(found.in_effect)

    def test_the_other_table_in_the_folder_is_unaffected(self) -> None:
        self.folder_file("[Backglass]\nBackglassOutput = 0\n")
        self.table_file("[Backglass]\nGrillHeight = 200\n")

        self.assertEqual(self.at(SCOPE_ENTRY, self.other).scope, SCOPE_FOLDER)

    def test_a_launcher_value_with_no_table_in_play_is_in_force(self) -> None:
        """Nothing sits above the application layer, so a value set there is the one
        that wins - and a row saying otherwise marks every setting as shadowed."""
        found = self.config.read(SCOPE_LAUNCHER, "", self.settings)[KEY]

        self.assertTrue(found.set_here)
        self.assertTrue(found.in_effect)

    def test_a_launcher_value_a_table_overrides_is_still_in_force_at_its_own_scope(self) -> None:
        """The launcher is not shadowed by a table: it still answers for every other
        table. Only the layer that lost to another at the *same* scope is."""
        self.table_file("[Backglass]\nBackglassOutput = 0\n")

        found = self.config.read(SCOPE_LAUNCHER, str(self.table), self.settings)[KEY]
        self.assertTrue(found.set_here)

    def test_a_value_a_table_s_own_file_sets_is_in_force_at_the_table(self) -> None:
        self.folder_file("[Backglass]\nBackglassOutput = 0\nGrillHeight = 150\n")
        self.table_file("[Backglass]\nBackglassOutput = 1\nGrillHeight = 200\n"
                        "[DMD]\nProfile1Legacy = 0\n")

        for key in (KEY, GRILL, PLAIN):
            with self.subTest(key=key):
                self.assertIn(SCOPE_ENTRY, self.config.scopes_for(key))
                found = self.at(SCOPE_ENTRY, key=key)
                self.assertTrue(found.set_here)
                self.assertTrue(found.in_effect)

    def test_a_folder_named_for_its_table_makes_one_file_of_the_two(self) -> None:
        """The ordinary case, and the two scopes coincide rather than shadowing."""
        solo = self.root / "Attack from Mars"
        solo.mkdir()
        table = solo / "Attack from Mars.vpx"
        table.write_text("")
        (solo / "Attack from Mars.ini").write_text("[Backglass]\nBackglassOutput = 0\n")

        for scope in (SCOPE_FOLDER, SCOPE_ENTRY):
            with self.subTest(scope=scope):
                found = self.config.read(scope, str(table), self.settings)[KEY]
                self.assertTrue(found.in_effect)
                self.assertTrue(found.set_here)


SENSORS = "Input.NudgeSensorCount"
FPS = "Player.ShowFPS"


class AllTablesOnlyTests(_Case):
    def setUp(self) -> None:
        super().setUp()
        with self.app_ini.open("a") as ini:
            ini.write("\n[Input]\n; Nudge Sensors: How many [Default: 0]\n"
                      "NudgeSensorCount = 2\n"
                      "\n[Player]\n; Show FPS: Draw the frame rate [Default: 0]\n"
                      "ShowFPS = 0\n")

    def test_only_the_launcher_offers_it(self) -> None:
        self.assertEqual(self.config.scopes_for(SENSORS), (SCOPE_LAUNCHER,))
        self.assertEqual(self.config.scopes_for(FPS), (SCOPE_LAUNCHER,))
        self.assertEqual(self.config.scopes_for(KEY), self.config.scopes())

    def test_the_input_section_is_the_rule_not_a_list_of_its_keys(self) -> None:
        self.assertEqual(self.config.scopes_for("Input.SomethingLater"), (SCOPE_LAUNCHER,))

    def test_a_table_s_value_is_not_the_one_in_force(self) -> None:
        self.table_file("[Player]\nShowFPS = 1\n")

        found = self.at(SCOPE_ENTRY, key=FPS)
        self.assertEqual((found.value, found.scope), ("0", SCOPE_LAUNCHER))
        self.assertTrue(found.set_here)
        self.assertFalse(found.in_effect)
        self.assertEqual((found.fallback, found.fallback_scope), ("0", SCOPE_LAUNCHER))

    def test_nor_is_a_folder_s(self) -> None:
        self.folder_file("[Input]\nNudgeSensorCount = 4\n")

        found = self.at(SCOPE_FOLDER, key=SENSORS)
        self.assertEqual((found.value, found.scope), ("2", SCOPE_LAUNCHER))
        self.assertFalse(found.in_effect)
        self.assertEqual(self.at(SCOPE_ENTRY, key=SENSORS).scope, SCOPE_LAUNCHER)

    def test_a_folder_named_for_its_table_does_not_make_it_count(self) -> None:
        solo = self.root / "Attack from Mars"
        solo.mkdir()
        table = solo / "Attack from Mars.vpx"
        table.write_text("")
        (solo / "Attack from Mars.ini").write_text("[Player]\nShowFPS = 1\n")

        for scope in (SCOPE_FOLDER, SCOPE_ENTRY):
            with self.subTest(scope=scope):
                found = self.config.read(scope, str(table), self.settings)[FPS]
                self.assertFalse(found.in_effect)

    def test_at_the_launcher_it_is_what_it_always_was(self) -> None:
        self.table_file("[Player]\nShowFPS = 1\n")

        found = self.config.read(SCOPE_LAUNCHER, str(self.table), self.settings)[FPS]
        self.assertEqual((found.value, found.scope), ("0", SCOPE_LAUNCHER))
        self.assertTrue(found.set_here)
        self.assertTrue(found.in_effect)


BALL_CONTROL = frozenset({"Editor.ThrowBallSize", "Editor.ThrowBallMass",
                          "Editor.ThrowBallsAlwaysOn", "Editor.BallControlAlwaysOn"})


class EditorTests(_Case):
    def test_the_editor_s_are_offered_for_all_tables_only(self) -> None:
        editor = [key for key in TYPES if key.startswith("Editor.")]
        self.assertTrue(editor)

        for key in [*editor, "Editor.SomethingLater"]:
            if key in BALL_CONTROL:
                continue
            with self.subTest(key=key):
                self.assertEqual(self.config.scopes_for(key), (SCOPE_LAUNCHER,))

    def test_but_ball_control_is_read_at_the_table(self) -> None:
        for key in sorted(BALL_CONTROL):
            with self.subTest(key=key):
                self.assertIn(key, TYPES)
                self.assertEqual(self.config.scopes_for(key), self.config.scopes())


class StereoTests(_Case):
    def test_every_stereo_and_anaglyph_setting_is_offered_for_a_table(self) -> None:
        stereo = [key for key in TYPES if key.startswith(("Player.Stereo3D", "Player.Anaglyph"))]
        self.assertTrue(stereo)

        for key in stereo:
            with self.subTest(key=key):
                self.assertEqual(self.config.scopes_for(key), self.config.scopes())

    def test_a_table_s_value_is_the_one_in_force(self) -> None:
        with self.app_ini.open("a") as ini:
            ini.write("\n[Player]\n; Stereo rendering: Stereo rendering mode [Default: 0]\n"
                      "Stereo3D = 0\n")
        self.table_file("[Player]\nStereo3D = 5\n")

        found = self.at(SCOPE_ENTRY, key="Player.Stereo3D")
        self.assertEqual((found.value, found.scope), ("5", SCOPE_ENTRY))
        self.assertTrue(found.in_effect)


class ReadAtTableStartTests(_Case):
    """Kept for all tables, and still read through a table's settings when it starts."""

    def setUp(self) -> None:
        super().setUp()
        with self.app_ini.open("a") as ini:
            ini.write("\n[Player]\n; Anti-Aliasing: Supersampling [Default: 1]\n"
                      "AAFactor = 1\n; Full Screen: [Default: 0]\nPlayfieldFullScreen = 0\n"
                      "\n[ScoreView]\n; Full Screen: [Default: 0]\nScoreViewFullScreen = 0\n")

    def test_it_is_still_offered_only_for_all_tables(self) -> None:
        self.assertEqual(self.config.scopes_for(AA), (SCOPE_LAUNCHER,))

    def test_a_table_s_value_is_the_one_in_force(self) -> None:
        self.table_file("[Player]\nAAFactor = 2\n[ScoreView]\nScoreViewFullScreen = 1\n")

        for key, value in ((AA, "2"), ("ScoreView.ScoreViewFullScreen", "1")):
            with self.subTest(key=key):
                found = self.at(SCOPE_ENTRY, key=key)
                self.assertEqual((found.value, found.scope), (value, SCOPE_ENTRY))
                self.assertTrue(found.set_here)
                self.assertTrue(found.in_effect)
                self.assertEqual((found.fallback, found.fallback_scope),
                                 ("0" if key != AA else "1", SCOPE_LAUNCHER))

    def test_the_playfield_window_s_is_not(self) -> None:
        self.table_file("[Player]\nPlayfieldFullScreen = 1\n")

        found = self.at(SCOPE_ENTRY, key="Player.PlayfieldFullScreen")
        self.assertEqual((found.value, found.scope), ("0", SCOPE_LAUNCHER))
        self.assertFalse(found.in_effect)

    def test_it_counts_among_the_table_s_own(self) -> None:
        self.table_file("[Player]\nAAFactor = 2\nPlayfieldFullScreen = 1\n")

        self.assertEqual(self.config.held_for_table(str(self.table))["keys"], [AA])


AA = "Player.AAFactor"


CAMERA = "[TableOverride]\nViewCabMode = 1\nViewCabFOV = 55\nViewCabLayback = 0\n"


class HeldForTableTests(_Case):
    def held(self, table=None) -> dict:
        return self.config.held_for_table(str(table or self.table))

    def test_a_table_with_no_file_holds_nothing(self) -> None:
        self.assertEqual(self.held(), {"scope": "", "keys": [], "point_of_view": False})

    def test_its_own_file_is_counted_at_the_table(self) -> None:
        self.table_file("[Backglass]\nBackglassOutput = 0\nGrillHeight = 200\n")

        self.assertEqual(self.held(),
                         {"scope": SCOPE_ENTRY,
                          "keys": ["Backglass.BackglassOutput", "Backglass.GrillHeight"],
                          "point_of_view": False})

    def test_a_blank_key_sets_nothing(self) -> None:
        self.table_file("[Backglass]\nBackglassOutput = 0\nGrillHeight =\n")

        self.assertEqual(self.held()["keys"], ["Backglass.BackglassOutput"])

    def test_the_camera_is_one_thing_not_its_numbers(self) -> None:
        self.table_file(CAMERA)

        self.assertEqual(self.held(),
                         {"scope": SCOPE_ENTRY, "keys": [], "point_of_view": True})

    def test_a_setting_read_for_all_tables_only_is_not_counted(self) -> None:
        self.table_file("[Player]\nShowFPS = 1\n[Input]\nNudgeSensorCount = 4\n")

        self.assertEqual(self.held()["keys"], [])

    def test_a_folder_file_reaching_the_table_is_the_folder_s(self) -> None:
        self.folder_file("[Backglass]\nBackglassOutput = 0\n" + CAMERA)

        self.assertEqual(self.held(),
                         {"scope": SCOPE_FOLDER,
                          "keys": ["Backglass.BackglassOutput"], "point_of_view": True})


AREAS_INI = """\
[Player]
; Enable Playfield: Mechanical sounds [Default: 1]
PlaySound = 1
; Show FPS: Performance overlay [Default: 0]
ShowFPS = 0
; Screen Width: Physical width [Default: 95.9]
ScreenWidth = 95.9
; Mass: Flipper mass [Default: 1]
FlipperPhysicsMass0 = 1
; Day/Night: Ambient light level [Default: 1]
EmissionScale = 1

[Topper]
; Output Mode: Where it goes [Default: 'Disabled', 0='Disabled', 1='Floating']
TopperOutput = 0

[TableOverride]
; Difficulty: Overall difficulty [Default: 1]
Difficulty = 1
; FOV: Field of view [Default: 45]
ViewCabFOV = 45

[Plugin.PinMAME]
Enable = 1
PinMAMEPath =
Cheat = 0

[Plugin.FlexDMD]
Enable = 0

[Plugin.HelloWorld]
Enable = 0

[DefaultProps\\Bumper]
; Radius: How big [Default: 45]
Radius = 45

[CVEdit]
; Keyword Color: Keywords [Default: 0]
Keyword = 0
"""


class AreaTests(_Case):
    """The groups are the pages of the program's own settings menu."""

    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(AREAS_INI)
        self.groups = {g.key: g for g in self.config.groups(self.settings)}

    def members(self, area: str) -> set[str]:
        return {f.key for f in self.groups[area].settings}

    def test_the_areas_come_in_the_order_of_the_program_s_menu(self) -> None:
        self.assertEqual(list(self.groups), [areas.DISPLAYS, areas.INPUT, areas.SOUND,
                                             areas.GRAPHICS, areas.PLUGINS,
                                             areas.POINT_OF_VIEW, areas.REST])

    def test_each_setting_is_on_its_page(self) -> None:
        self.assertEqual(self.members(areas.DISPLAYS),
                         {"Player.ScreenWidth", "Topper.TopperOutput"})
        self.assertEqual(self.members(areas.SOUND), {"Player.PlaySound"})
        self.assertEqual(self.members(areas.GRAPHICS), {"Player.ShowFPS"})

    def test_the_table_editor_s_settings_are_left_out(self) -> None:
        offered = {f.key for g in self.groups.values() for f in g.settings}

        for key in ("DefaultProps\\Bumper.Radius", "CVEdit.Keyword",
                    "Player.FlipperPhysicsMass0"):
            with self.subTest(key=key):
                self.assertNotIn(key, offered)

    def test_a_plugin_for_writing_plugins_is_in_the_rest(self) -> None:
        self.assertIn("Plugin.HelloWorld.Enable", self.members(areas.REST))
        self.assertNotIn("Plugin.HelloWorld.Enable", self.members(areas.PLUGINS))

    def test_each_plugin_is_a_heading_with_its_switch_first(self) -> None:
        headings = self.groups[areas.PLUGINS].curated

        self.assertEqual([h.key for h in headings], ["FlexDMD", "PinMAME"])
        pinmame = headings[1]
        self.assertEqual(pinmame.keys, ("Plugin.PinMAME.Enable", "Plugin.PinMAME.Sound",
                                        "Plugin.PinMAME.PinMAMEPath"))
        self.assertEqual(pinmame.enabled_by, "Plugin.PinMAME.Enable")
        self.assertEqual(pinmame.rivals, ())

    def test_each_backglass_renderer_names_the_other_as_its_rival(self) -> None:
        headings = {h.key: h for h in areas.plugin_headings(
            {"Plugin.B2S.Enable", "Plugin.B2SLegacy.Enable", "Plugin.B2SLegacy.B2SHideDMD"})}

        self.assertEqual(headings["B2S"].rivals, ("Plugin.B2SLegacy.Enable",))
        self.assertEqual(headings["B2SLegacy"].rivals, ("Plugin.B2S.Enable",))

    def test_a_heading_with_no_switch_has_no_rival(self) -> None:
        heading, = areas.plugin_headings({"Plugin.B2S.ShowGrill"})

        self.assertEqual((heading.enabled_by, heading.rivals), ("", ()))

    def test_a_backglass_renderer_is_about_the_backglass_and_pairs_each_dmd_box(self) -> None:
        backglass, scoreview = "Plugin.B2SLegacy.BackglassDMD", "Plugin.B2SLegacy.ScoreViewDMD"
        box = [f"{art}{part}" for art in (backglass, scoreview) for part in "XYWH"]
        heading, = areas.plugin_headings({"Plugin.B2SLegacy.Enable", *box})

        self.assertEqual(heading.kinds, ("backglass",))
        self.assertEqual([(pair.key, pair.keys) for pair in heading.pairs],
                         [("backglass_dmd_position", (f"{backglass}X", f"{backglass}Y")),
                          ("backglass_dmd_size", (f"{backglass}W", f"{backglass}H")),
                          ("scoreview_dmd_position", (f"{scoreview}X", f"{scoreview}Y")),
                          ("scoreview_dmd_size", (f"{scoreview}W", f"{scoreview}H"))])
        self.assertTrue(all(areas.is_curated(key) for key in box))

    def test_the_version_pick_is_a_file_s_row_and_not_the_table_s(self) -> None:
        dual = "Plugin.B2SLegacy.B2SDualMode"
        heading, = areas.plugin_headings({"Plugin.B2SLegacy.Enable", dual})

        self.assertEqual((heading.versions, dual in heading.keys), ((dual,), False))
        self.assertEqual(areas.plugin_headings({"Plugin.B2SLegacy.Enable"})[0].versions, ())

    def test_a_dmd_pair_the_file_holds_half_of_is_left_out(self) -> None:
        heading, = areas.plugin_headings({"Plugin.B2S.Enable", "Plugin.B2S.BackglassDMDX",
                                          "Plugin.B2S.BackglassDMDY",
                                          "Plugin.B2S.BackglassDMDW"})

        self.assertEqual([pair.key for pair in heading.pairs], ["backglass_dmd_position"])

    def test_the_dmd_overlay_s_rows_are_switched_by_the_overlay(self) -> None:
        prefix = "Plugin.B2SLegacy."
        heading, = areas.plugin_headings({f"{prefix}Enable", f"{prefix}BackglassDMDOverlay",
                                          f"{prefix}BackglassDMDAutoPos",
                                          f"{prefix}BackglassDMDX",
                                          f"{prefix}ScoreViewDMDAutoPos"})

        self.assertEqual([(one.enabled_by, one.keys) for one in heading.switched if one.on],
                         [(f"{prefix}BackglassDMDOverlay", (f"{prefix}BackglassDMDAutoPos",
                                                            f"{prefix}BackglassDMDX"))])

    def test_the_dmd_box_is_drawn_only_while_automatic_position_is_off(self) -> None:
        arts = ("Plugin.B2S.BackglassDMD", "Plugin.B2S.ScoreViewDMD")
        heading, = areas.plugin_headings({"Plugin.B2S.Enable", *(
            f"{art}{part}" for art in arts for part in ("Overlay", "AutoPos", "X", "Y"))})

        self.assertEqual([(one.enabled_by, one.keys, one.on) for one in heading.switched],
                         [row for art in arts for row in (
                             (f"{art}Overlay", (f"{art}AutoPos", f"{art}X", f"{art}Y"), True),
                             (f"{art}AutoPos", (f"{art}X", f"{art}Y"), False))])
        self.assertEqual([heading.keys.count(f"{art}X") for art in arts], [1, 1])

    def test_any_other_plugin_is_about_no_file(self) -> None:
        heading, = areas.plugin_headings({"Plugin.PinMAME.Enable"})

        self.assertEqual((heading.kinds, heading.pairs), ((), ()))

    def test_the_plugins_rows_follow_their_headings_each_in_the_file_s_order(self) -> None:
        self.assertEqual([f.key for f in self.groups[areas.PLUGINS].settings],
                         ["Plugin.FlexDMD.Enable", "Plugin.PinMAME.Enable",
                          "Plugin.PinMAME.PinMAMEPath", "Plugin.PinMAME.Cheat",
                          "Plugin.PinMAME.Sound"])

    def test_a_curated_row_the_file_lacks_is_left_out(self) -> None:
        playfield = next(h for h in self.groups[areas.SOUND].curated
                         if h.key == "playfield")

        self.assertEqual(playfield.keys, ("Player.PlaySound",))
        self.assertNotIn("backglass", [h.key for h in self.groups[areas.SOUND].curated])

    def test_a_window_s_video_mode_position_and_size_are_each_a_pair(self) -> None:
        topper = next(h for h in areas.CURATED[areas.DISPLAYS] if h.key == "topper")

        self.assertEqual([(pair.key, pair.keys) for pair in topper.pairs],
                         [("video_mode", ("Topper.TopperFSWidth", "Topper.TopperFSHeight")),
                          ("position", ("Topper.TopperWndX", "Topper.TopperWndY")),
                          ("size", ("Topper.TopperWidth", "Topper.TopperHeight"))])

    def test_a_window_s_video_mode_follows_fullscreen_and_is_drawn_after_it(self) -> None:
        for window in (h for h in areas.CURATED[areas.DISPLAYS] if h.key != "cabinet"):
            fullscreen = next(key for key in window.keys if key.endswith("FullScreen"))
            video_mode = next(pair.keys for pair in window.pairs if pair.key == "video_mode")
            with self.subTest(window.key):
                self.assertEqual([(one.enabled_by, one.keys) for one in window.switched
                                  if one.on], [(fullscreen, video_mode)])
                self.assertEqual(window.keys[window.keys.index(fullscreen) + 1:][:2],
                                 video_mode)

    def test_a_window_s_position_and_size_are_drawn_only_while_it_is_windowed(self) -> None:
        for window in (h for h in areas.CURATED[areas.DISPLAYS] if h.key != "cabinet"):
            fullscreen = next(key for key in window.keys if key.endswith("FullScreen"))
            windowed = tuple(key for pair in window.pairs if pair.key in ("position", "size")
                             for key in pair.keys)
            with self.subTest(window.key):
                self.assertEqual([(one.enabled_by, one.keys) for one in window.switched
                                  if not one.on], [(fullscreen, windowed)])

    def test_a_window_s_video_mode_is_not_switched_where_fullscreen_is_not_offered(self) -> None:
        self.app_ini.write_text("[Topper]\nTopperFSWidth = 1920\nTopperFSHeight = 1080\n")
        displays = {g.key: g for g in self.config.groups(self.settings)}[areas.DISPLAYS]
        topper = next(h for h in displays.curated if h.key == "topper")

        self.assertEqual(([pair.key for pair in topper.pairs], topper.switched),
                         (["video_mode"], ()))

    def test_a_pair_the_file_holds_half_of_is_left_out(self) -> None:
        self.app_ini.write_text("[Topper]\nTopperWndX = 0\nTopperWndY = 0\n"
                                "TopperWidth = 0\n")
        displays = {g.key: g for g in self.config.groups(self.settings)}[areas.DISPLAYS]
        topper = next(h for h in displays.curated if h.key == "topper")

        self.assertEqual([pair.key for pair in topper.pairs], ["position"])
        self.assertIn("Topper.TopperWidth", topper.keys)

    def test_the_vr_preview_pairs_its_rows_in_the_rest_and_none_is_common_at_a_table(
            self) -> None:
        parts = ("Display", "FullScreen", "FSWidth", "FSHeight", "WndX", "WndY", "Width",
                 "Height")
        self.app_ini.write_text("[PlayerVR]\n" + "".join(f"Preview{part} = 0\n"
                                                         for part in parts))
        rest = {g.key: g for g in self.config.groups(self.settings)}[areas.REST]
        preview = next(h for h in rest.curated if h.key == "vr_preview")

        self.assertEqual(
            [(pair.key, pair.keys) for pair in preview.pairs],
            [("video_mode", ("PlayerVR.PreviewFSWidth", "PlayerVR.PreviewFSHeight")),
             ("position", ("PlayerVR.PreviewWndX", "PlayerVR.PreviewWndY")),
             ("size", ("PlayerVR.PreviewWidth", "PlayerVR.PreviewHeight"))])
        self.assertEqual([f.key for f in rest.settings if f.per_table], [])

    def test_the_point_of_view_is_summarized(self) -> None:
        self.assertTrue(self.groups[areas.POINT_OF_VIEW].summarized)
        self.assertEqual(self.members(areas.POINT_OF_VIEW), {"TableOverride.ViewCabFOV"})

    def test_a_curated_row_a_table_can_hold_is_offered_first_there(self) -> None:
        fields = {f.key: f for g in self.groups.values() for f in g.settings}

        self.assertTrue(fields["Player.PlaySound"].per_table)
        self.assertTrue(fields["TableOverride.Difficulty"].per_table)
        self.assertFalse(fields["Player.ShowFPS"].per_table)
        self.assertFalse(fields["Plugin.PinMAME.Cheat"].per_table)


INSTALLED_INI = """\
[Plugin.vpx]
Enable = 1

[Plugin.Serum]
Enable = 1
SerumPath =

[Plugin.PinMAME]
Enable = 1

[Plugin.UpscaleDMD]
Enable = 0

[Plugin.FlexDMD]
Enable = 1
"""

MANIFESTS = {
    "serum": ("Serum", "Serum", "Serum DMD Colorization"),
    "pinmame": ("PinMAME", "PinMAME", "PinMAME"),
    "upscaledmd": ("UpscaleDMD", "DMD Upscaler", "Upscale DMD output"),
}


class InstalledPluginTests(_Case):
    """The program's plugins, as the `plugin.cfg` each one ships with says them."""

    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(INSTALLED_INI)
        bundle = self.root / "VPinballX_BGFX.app" / "Contents"
        for folder, (plugin, name, description) in MANIFESTS.items():
            (bundle / "PlugIns" / folder).mkdir(parents=True)
            (bundle / "PlugIns" / folder / "plugin.cfg").write_text(
                f'[configuration]\nid = "{plugin}"\nname = "{name}"\n'
                f'description = "{description}"\n')
        self.settings["bin_path"] = str(bundle / "MacOS" / "VPinballX_BGFX")
        self.groups = {g.key: g for g in self.config.groups(self.settings)}
        self.headings = {h.key: h for h in self.groups[areas.PLUGINS].curated}

    def test_a_plugin_is_named_and_described_as_the_program_says(self) -> None:
        upscaler = self.headings["UpscaleDMD"]

        self.assertEqual((upscaler.label, upscaler.description),
                         ("DMD Upscaler", "Upscale DMD output"))

    def test_a_name_that_is_only_the_id_leaves_the_words_to_the_catalog(self) -> None:
        self.assertEqual(self.headings["Serum"].label, "")
        self.assertEqual(self.headings["Serum"].description, "Serum DMD Colorization")

    def test_a_description_that_is_only_the_name_says_nothing(self) -> None:
        self.assertEqual(self.headings["PinMAME"].description, "")

    def test_the_plugins_sort_by_that_name(self) -> None:
        self.assertEqual(list(self.headings), ["UpscaleDMD", "PinMAME", "Serum"])
        self.assertEqual([plugin for plugin, _ in groupby(
                             areas.plugin_of(f.key) for f in self.groups[areas.PLUGINS].settings)],
                         ["UpscaleDMD", "PinMAME", "Serum"])

    def test_a_plugin_the_program_does_not_have_is_in_the_rest(self) -> None:
        self.assertIn("Plugin.FlexDMD.Enable",
                      {f.key for f in self.groups[areas.REST].settings})
        self.assertNotIn("FlexDMD", self.headings)

    def test_a_plugin_s_folder_is_declared_one_so_it_can_be_browsed_for(self) -> None:
        fields = {f.key: f for f in self.groups[areas.PLUGINS].settings}

        self.assertEqual(fields["Plugin.PinMAME.PinMAMEPath"].path, "dir")
        self.assertEqual(fields["Plugin.Serum.SerumPath"].path, "dir")
        self.assertEqual(fields["Plugin.PinMAME.Sound"].path, "")

    def test_every_folder_setting_is_one_a_plugin_has(self) -> None:
        self.assertEqual({areas.plugin_of(key) for key in areas.FOLDERS},
                         {"ScoreView", "Nixie", "PinMAME", "AltSound", "Serum", "VNI", "PUP"})

    def test_an_image_setting_is_declared_a_picture_file_so_it_can_be_browsed_for(self) -> None:
        self.app_ini.write_text(INSTALLED_INI + "\n[Player]\nBallImage = \nDecalImage = \n"
                                "OverwriteBallImage = 0\n")
        groups = self.config.groups(self.settings)
        fields = {f.key: f for g in groups for f in g.settings}

        for key in ("Player.BallImage", "Player.DecalImage"):
            self.assertEqual(fields[key].path, "file", key)
            self.assertIn(".png", fields[key].suffixes, key)
        self.assertNotIn(".vpx", fields["Player.BallImage"].suffixes)
        self.assertEqual(fields["Player.OverwriteBallImage"].path, "")

    def test_every_image_setting_is_one_the_program_has(self) -> None:
        self.assertLessEqual(areas.IMAGES, set(TYPES))

    def test_the_program_s_own_entry_is_not_offered(self) -> None:
        """Turning it off stops the program, which is why the program never offers it."""
        offered = {f.key for g in self.groups.values() for f in g.settings}

        self.assertNotIn("Plugin.vpx.Enable", offered)

    def test_without_the_program_the_file_decides_alone(self) -> None:
        self.settings["bin_path"] = str(self.root / "elsewhere" / "VPinballX_BGFX")
        groups = {g.key: g for g in self.config.groups(self.settings)}

        self.assertEqual([h.key for h in groups[areas.PLUGINS].curated],
                         ["FlexDMD", "PinMAME", "Serum", "UpscaleDMD"])
        self.assertEqual({h.label for h in groups[areas.PLUGINS].curated}, {""})


class ByProgramTests(_Case):
    """What the plugins folder and the Rev in the ini say a program does, and what shows."""

    def program(self, rev: int | None, *ids: str) -> None:
        """A macOS bundle with a plugin.cfg for each id, and an ini written by `rev`."""
        version = "" if rev is None else f"\n[Version]\nVPinball = 1081{rev}\n"
        self.app_ini.write_text(
            "".join(f"[Plugin.{one}]\nEnable =\n\n" for one in ids) + version)
        bundle = self.root / "VPinballX_BGFX.app" / "Contents"
        (bundle / "PlugIns").mkdir(parents=True, exist_ok=True)
        for one in ids:
            (bundle / "PlugIns" / one.lower()).mkdir(exist_ok=True)
            (bundle / "PlugIns" / one.lower() / "plugin.cfg").write_text(
                f'[configuration]\nid = "{one}"\nname = "{one}"\n')
        self.settings["bin_path"] = str(bundle / "MacOS" / "VPinballX_BGFX")

    def fields(self) -> dict:
        return {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

    def headings(self) -> dict:
        groups = {g.key: g for g in self.config.groups(self.settings)}
        return {h.key: h for h in groups[areas.PLUGINS].curated}

    def test_no_program_is_no_plugins_and_a_rev_from_the_file(self) -> None:
        self.app_ini.write_text("[Version]\nVPinball = 10816100\n")
        self.settings.pop("bin_path", None)

        found = self.config.program(self.settings)

        self.assertIsNone(found.plugins)
        self.assertEqual(found.build, 6100)

    def test_a_program_with_no_plugins_folder_is_unknown_unless_its_build_predates_them(
            self) -> None:
        program = self.root / "VPinballX_GL"
        program.write_text("")
        self.settings["bin_path"] = str(program)
        for version, expected in (("", None), ("10815353", None), ("10800000", frozenset())):
            with self.subTest(version=version):
                self.app_ini.write_text(f"[Version]\nVPinball = {version}\n")

                self.assertEqual(self.config.program(self.settings).plugins, expected)

    def test_a_link_to_the_program_finds_the_plugins_beside_the_program(self) -> None:
        self.program(6300, "B2S", "Nixie")
        real = Path(self.settings["bin_path"])
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text("", encoding="utf-8")
        link = self.root / "bin" / "vpx"
        link.parent.mkdir()
        link.symlink_to(real)
        self.settings["bin_path"] = str(link)

        self.assertEqual(self.config.program(self.settings).plugins,
                         frozenset({"B2S", "Nixie"}))

    def test_a_program_whose_plugins_are_not_beside_it_is_unknown_not_empty(self) -> None:
        self.program(6300, "B2S", "Nixie")
        wrapper = self.root / "bin" / "vpx"
        wrapper.parent.mkdir()
        wrapper.write_text("#!/bin/sh\n")
        self.settings["bin_path"] = str(wrapper)

        self.assertIsNone(self.config.program(self.settings).plugins)

    def test_the_dmd_is_in_the_file_from_the_build_that_took_it_out_of_the_settings(self) -> None:
        for rev, expected in ((6266, False), (6267, True)):
            with self.subTest(rev=rev):
                self.program(rev, "B2S")

                self.assertIs(self.config.program(self.settings).dmd_in_file, expected)

    def test_the_plugins_folder_wins_over_a_rev_the_upgrade_left_behind(self) -> None:
        self.program(6100, "B2S", "Nixie", "PUP")

        found = self.config.program(self.settings)

        self.assertEqual(found.plugins, frozenset({"B2S", "Nixie", "PUP"}))
        self.assertGreaterEqual(found.build or 0, 6293)
        self.assertEqual(found.defaults,
                         {"Plugin.B2S.Enable": "1", "Plugin.B2SLegacy.Enable": "0"})

    def test_before_the_switch_b2s_is_off_and_the_legacy_renderer_on(self) -> None:
        self.program(6200, "B2S", "B2SLegacy")

        found = self.config.program(self.settings)

        self.assertEqual(found.defaults,
                         {"Plugin.B2S.Enable": "0", "Plugin.B2SLegacy.Enable": "1"})

    def test_a_windows_build_is_the_exe_whatever_host_runs_it(self) -> None:
        self.program(6200, "B2S", "B2SLegacy")
        windows = self.root / "VPX" / "VPinballX64.exe"
        for one in ("B2S", "B2SLegacy"):
            (windows.parent / "plugins" / one.lower()).mkdir(parents=True)
            (windows.parent / "plugins" / one.lower() / "plugin.cfg").write_text(
                f'[configuration]\nid = "{one}"\n')
        windows.write_text("")
        self.settings["bin_path"] = str(windows)

        with mock.patch("apps.vpx.config.sys.platform", "darwin"):
            found = self.config.program(self.settings)

        self.assertEqual(found.defaults["Plugin.B2SLegacy.Enable"], "0")

    def test_an_unwritten_enable_shows_what_the_build_gives_it(self) -> None:
        self.program(6274, "B2S", "B2SLegacy", "Nixie")
        self.app_ini.write_text(self.app_ini.read_text().replace(
            "[Plugin.B2S]\nEnable =\n", "").replace("[Plugin.B2SLegacy]\nEnable =\n", ""))

        fields = self.fields()

        self.assertEqual(fields["Plugin.B2S.Enable"].default, "1")
        self.assertEqual(fields["Plugin.B2SLegacy.Enable"].default, "0")

    def test_a_value_the_file_states_is_the_one_read(self) -> None:
        self.program(6274, "B2S", "B2SLegacy")
        self.app_ini.write_text(self.app_ini.read_text().replace(
            "[Plugin.B2S]\nEnable =", "[Plugin.B2S]\nEnable = 0"))

        found = self.config.read(SCOPE_LAUNCHER, "", self.settings)

        self.assertEqual(found["Plugin.B2S.Enable"].value, "0")

    def test_nixie_has_a_heading_and_a_layout_folder_to_browse_for(self) -> None:
        self.program(6293, "B2S", "Nixie")

        fields = self.fields()

        self.assertIn("Nixie", self.headings())
        self.assertEqual(fields["Plugin.Nixie.LayoutFolder"].path, "dir")
        self.assertEqual(self.config.scopes_for("Plugin.Nixie.LayoutFolder"),
                         (SCOPE_LAUNCHER,))

    def test_the_layout_folders_are_each_a_plugin_s_own(self) -> None:
        self.program(6200, "B2S", "ScoreView")

        self.assertIn("Plugin.ScoreView.LayoutFolder", self.fields())
        self.assertNotIn("Plugin.Nixie.LayoutFolder", self.fields())

    def test_dual_mode_is_a_row_of_the_b2s_heading_from_the_build_that_has_it(self) -> None:
        self.program(6300, "B2S", "Nixie")
        self.app_ini.write_text(self.app_ini.read_text().replace(
            "[Plugin.B2S]\n", "[Plugin.B2S]\nDualMode = 1\n"))

        self.assertEqual(self.headings()["B2S"].versions, ("Plugin.B2S.DualMode",))

    def test_the_dmd_rows_are_the_ini_s_until_the_file_takes_them(self) -> None:
        for rev, expected in ((6266, True), (6267, False)):
            with self.subTest(rev=rev):
                self.program(rev, "B2S")
                self.app_ini.write_text(self.app_ini.read_text().replace(
                    "[Plugin.B2S]\n", "[Plugin.B2S]\nBackglassDMDX = 5\n"))

                self.assertEqual("Plugin.B2S.BackglassDMDX" in self.fields(), expected)
                self.assertEqual(self.headings()["B2S"].variant,
                                 "" if expected else "dmd_in_file")

    def test_a_build_with_no_rev_keeps_the_dmd_rows_and_the_plain_note(self) -> None:
        self.program(None, "B2S")
        self.app_ini.write_text(self.app_ini.read_text().replace(
            "[Plugin.B2S]\n", "[Plugin.B2S]\nBackglassDMDX = 5\n"))

        self.assertIn("Plugin.B2S.BackglassDMDX", self.fields())
        self.assertEqual(self.headings()["B2S"].variant, "")

    def test_priority_keys_for_every_renderer_are_read_back(self) -> None:
        self.program(6293, "B2S", "Nixie", "PUP")
        self.app_ini.write_text(self.app_ini.read_text() + (
            "\n[ScoreView]\nPriority.Nixie = 2\nPriority.B2S = 1\n"))

        found = self.config.read(SCOPE_LAUNCHER, "", self.settings)

        self.assertEqual({key: found[key].value for key in found if ".Priority." in key},
                         {"ScoreView.Priority.Nixie": "2", "ScoreView.Priority.B2S": "1"})


REGISTERED_INI = """\
[Plugin.B2S]
; Enable: Enable B2S plugin [Default: 0]
Enable = 1
ShowGrill =
"""


class RegisteredTests(_Case):
    """A plugin's settings as it registers them, which the file only holds once the
    program has run that plugin, and then bare."""

    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(REGISTERED_INI)
        bundle = self.root / "VPinballX_BGFX.app" / "Contents"
        for folder, plugin in (("b2s", "B2S"), ("upscaledmd", "UpscaleDMD")):
            (bundle / "PlugIns" / folder).mkdir(parents=True)
            (bundle / "PlugIns" / folder / "plugin.cfg").write_text(
                f'[configuration]\nid = "{plugin}"\n')
        self.settings["bin_path"] = str(bundle / "MacOS" / "VPinballX_BGFX")

    def fields(self) -> dict:
        return {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

    def test_a_setting_the_file_does_not_hold_yet_is_offered_with_its_default(self) -> None:
        dmd_x = self.fields()["Plugin.B2S.BackglassDMDX"]

        self.assertEqual((dmd_x.type, dmd_x.default, dmd_x.minimum, dmd_x.maximum),
                         ("int", "0", 0, 65535))
        self.assertEqual(dmd_x.label, "Backglass DMD X position")

    def test_a_setting_the_file_writes_bare_takes_its_registered_default(self) -> None:
        grill = self.fields()["Plugin.B2S.ShowGrill"]

        self.assertEqual((grill.type, grill.default), ("bool", "0"))

    def test_an_enumerated_one_offers_its_answers(self) -> None:
        mode = self.fields()["Plugin.UpscaleDMD.UpscaleMode"]

        self.assertEqual(mode.type, "choice")
        self.assertEqual(mode.default, "0")
        self.assertEqual(mode.choices[0], ("0", "Disabled"))

    def test_dual_mode_is_a_choice_between_its_two_stored_numbers(self) -> None:
        self.app_ini.write_text("[Plugin.B2SLegacy]\nB2SDualMode =\n")
        mode = self.fields()["Plugin.B2SLegacy.B2SDualMode"]

        self.assertEqual((mode.type, [value for value, _ in mode.choices]),
                         ("choice", ["1", "2"]))
        self.assertEqual(apps.choice_words("vpx", mode),
                         {"1": "Authentic", "2": "Fantasy"})

    def test_a_plugin_s_own_switch_is_off_until_it_is_turned_on(self) -> None:
        self.assertEqual(self.fields()["Plugin.UpscaleDMD.Enable"].default, "0")

    def test_what_the_file_says_is_left_as_it_says_it(self) -> None:
        self.app_ini.write_text("[Plugin.B2S]\n; Show Grill: Grill [Default: 1]\n"
                                "ShowGrill =\n")

        self.assertEqual(self.fields()["Plugin.B2S.ShowGrill"].default, "1")

    def test_a_plugin_the_program_does_not_have_offers_nothing_new(self) -> None:
        self.assertNotIn("Plugin.Serum.DisabledSize", self.fields())

    def test_without_the_program_the_file_s_plugins_are_the_ones_offered(self) -> None:
        self.settings["bin_path"] = str(self.root / "elsewhere" / "VPinballX_BGFX")
        fields = self.fields()

        self.assertIn("Plugin.B2S.BackglassDMDX", fields)
        self.assertNotIn("Plugin.UpscaleDMD.UpscaleMode", fields)

    def test_a_table_given_the_registered_default_holds_nothing(self) -> None:
        cleared = self.config.write(SCOPE_ENTRY, str(self.table),
                                    {"Plugin.B2S.ShowGrill": "0",
                                     "Plugin.B2S.BackglassDMDX": "0"}, self.settings)

        self.assertEqual(cleared, {"Plugin.B2S.ShowGrill", "Plugin.B2S.BackglassDMDX"})
        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())

    def test_a_table_given_another_value_keeps_it(self) -> None:
        self.config.write(SCOPE_ENTRY, str(self.table), {"Plugin.B2S.ShowGrill": "1"},
                          self.settings)

        self.assertTrue(self.at(SCOPE_ENTRY, key="Plugin.B2S.ShowGrill").set_here)


class DeclaredTests(_Case):
    """A file Visual Pinball did not write has no comments, and its blank settings still
    show what the program does with them."""

    def test_a_file_without_comments_shows_the_declared_default(self) -> None:
        self.app_ini.write_text(
            "[Global]\nEnableLog =\n[Player]\nBGSet =\nAAFactor =\n"
            "; Exposure: How bright [Default: 3.0 in 0.0 .. 5.0]\nHDRGlobalExposure =\n")
        fields = {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

        self.assertEqual(fields["Global.EnableLog"].default, "1")
        self.assertEqual(fields["Player.BGSet"].choices[0], ("0", "Desktop / Full Single Screen"))
        self.assertEqual(fields["Player.AAFactor"].default, "1")
        # The comment is the build's own and is kept over the source's.
        self.assertEqual(fields["Player.HDRGlobalExposure"].default, "3")

    def test_a_setting_the_table_supplies_has_no_default(self) -> None:
        self.app_ini.write_text(
            "[Player]\nEmissionScale =\n[TableOverride]\nDifficulty =\nExposure =\nToneMapper =\n")
        fields = {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

        for key in ("Player.EmissionScale", "TableOverride.Difficulty",
                    "TableOverride.Exposure", "TableOverride.ToneMapper"):
            with self.subTest(key=key):
                self.assertFalse(fields[key].default)

    def test_a_window_position_left_blank_declares_no_default(self) -> None:
        self.app_ini.write_text("[Player]\nPlayfieldWndX =\nPlayfieldWndY =\n")
        fields = {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

        self.assertEqual(fields["Player.PlayfieldWndX"].default, "")
        self.assertEqual(self.config.blank_words()["Player.PlayfieldWndY"], "from_the_screen")


class PluginFolderTests(unittest.TestCase):
    def test_on_macos_the_plugins_are_inside_the_bundle(self) -> None:
        from apps.vpx.plugins import folder

        for picked in ("/Applications/VPinballX_BGFX.app",
                       "/Applications/VPinballX_BGFX.app/Contents/MacOS/VPinballX_BGFX"):
            with self.subTest(picked=picked):
                self.assertEqual(folder(picked),
                                 Path("/Applications/VPinballX_BGFX.app/Contents/PlugIns"))

    def test_elsewhere_they_are_beside_the_program(self) -> None:
        from apps.vpx.plugins import folder

        self.assertEqual(folder("/opt/vpinball/VPinballX_GL"),
                         Path("/opt/vpinball/plugins"))
        self.assertIsNone(folder(""))

    def test_the_program_has_plugins_where_that_folder_is(self) -> None:
        from apps.vpx.capability import PLUGINS, VPXCapability

        with TemporaryDirectory() as tmp:
            program = Path(tmp) / "VPinballX_BGFX.app" / "Contents" / "MacOS" / "VPinballX"
            settings = {"bin_path": str(program), "ini_path": str(Path(tmp) / "none.ini")}
            before = VPXCapability().probe(settings)
            (program.parents[1] / "PlugIns").mkdir(parents=True)
            after = VPXCapability().probe(settings)

        self.assertFalse(before[PLUGINS].available)
        self.assertTrue(after[PLUGINS].available)


class TableOnlyTests(_Case):
    def test_what_the_program_keeps_per_table_is_not_offered_for_all(self) -> None:
        for key in ("TableOverride.Difficulty", "TableOverride.ViewCabFOV",
                    "TableOption.Anything", "Player.EmissionScale"):
            with self.subTest(key=key):
                self.assertEqual(self.config.scopes_for(key), (SCOPE_FOLDER, SCOPE_ENTRY))

    def test_a_plugin_s_switch_is_offered_everywhere_though_its_paths_are_not(self) -> None:
        self.assertEqual(self.config.scopes_for("Plugin.PinMAME.Enable"),
                         self.config.scopes())
        self.assertEqual(self.config.scopes_for("Plugin.PinMAME.PinMAMEPath"),
                         (SCOPE_LAUNCHER,))
        self.assertEqual(self.config.scopes_for("Plugin.DMDUtil.Enable"),
                         self.config.scopes())
        self.assertEqual(self.config.scopes_for("Plugin.DMDUtil.ZeDMD"), (SCOPE_LAUNCHER,))


class CuratedTests(unittest.TestCase):
    """The curated rows are named here, so each has to be one the program declares, and
    the words for each have to be in the catalog."""

    def setUp(self) -> None:
        import json

        self.words = json.loads((Path(areas.__file__).parent / "i18n" / "en.json")
                                .read_text(encoding="utf-8"))
        self.curated = [key for headings in areas.CURATED.values() for one in headings
                        for key in one.keys]
        self.plugin_rows = [key for keys in areas.PLUGIN_ROWS.values() for key in keys]

    def test_every_curated_row_is_one_the_program_declares(self) -> None:
        self.assertEqual([key for key in self.curated + self.plugin_rows
                          if key not in TYPES and not bindings.is_action(key)], [])

    def test_every_curated_row_is_in_its_own_area(self) -> None:
        for area, headings in areas.CURATED.items():
            for key in (key for one in headings for key in one.keys):
                with self.subTest(key=key):
                    self.assertEqual(areas.area_of(key), area)

    def test_a_plugin_row_has_words_from_its_plugin_or_the_catalog(self) -> None:
        self.assertEqual([key for key in self.plugin_rows if key not in LABELS
                          and f"field.{key}.label" not in self.words], [])

    def test_every_plugin_setting_has_words_from_its_plugin_or_the_catalog(self) -> None:
        self.assertEqual([key for key in TYPES if areas.plugin_of(key)
                          and not areas.is_plugin_switch(key) and key not in LABELS
                          and f"field.{key}.label" not in self.words], [])

    def test_the_catalog_does_not_name_a_setting_its_plugin_names(self) -> None:
        self.assertEqual([key for key in LABELS if f"field.{key}.label" in self.words], [])

    def test_every_heading_has_a_label(self) -> None:
        named = [f"group.{area}.heading.{one.key}.label"
                 for area, headings in areas.CURATED.items() for one in headings if one.key]
        named += [f"group.{areas.PLUGINS}.heading.{plugin}.label"
                  for plugin in areas.PLUGIN_ROWS]
        self.assertEqual([key for key in named if key not in self.words], [])

    def test_every_pair_is_two_rows_of_its_heading_with_a_label_and_a_joiner(self) -> None:
        pairs = [(area, one, pair) for area, headings in areas.CURATED.items()
                 for one in headings for pair in one.pairs]

        self.assertTrue(pairs)
        self.assertEqual([pair.keys for _, one, pair in pairs
                          if len(set(pair.keys)) != 2 or not set(pair.keys) <= set(one.keys)],
                         [])
        self.assertEqual([f"group.{area}.pair.{pair.key}.{word}" for area, _, pair in pairs
                          for word in ("label", "joiner")
                          if f"group.{area}.pair.{pair.key}.{word}" not in self.words], [])


class SharedWithGameTests(_Case):
    """A table named after its folder has one file for itself and for its game, so what
    it sets reaches the game's other tables."""

    def test_the_table_named_after_its_folder_shares_it(self) -> None:
        named = self.game / "Medieval Madness.vpx"
        named.write_text("")

        self.assertTrue(self.config.shared_with_game(str(named)))

    def test_any_other_table_has_a_file_of_its_own(self) -> None:
        self.assertFalse(self.config.shared_with_game(str(self.table)))


class FromGameTests(_Case):
    """What a game's own file sets that a table with a file of its own no longer reads."""

    def _from_game(self, table=None) -> dict[str, str]:
        return self.config.from_game(str(table or self.table), self.settings)

    def test_what_the_game_s_file_sets_and_the_table_s_does_not(self) -> None:
        self.folder_file("[Backglass]\nGrillHeight = 200\n\n[DMD]\nProfile1Legacy = 0\n")
        self.table_file("[DMD]\nProfile1Legacy = 1\n")

        self.assertEqual(self._from_game(), {"Backglass.GrillHeight": "200"})

    def test_nothing_where_the_game_s_file_reaches_the_table(self) -> None:
        self.folder_file("[Backglass]\nGrillHeight = 200\n")

        self.assertEqual(self._from_game(), {})

    def test_nothing_where_the_table_s_file_is_the_game_s(self) -> None:
        named = self.game / "Medieval Madness.vpx"
        named.write_text("")
        self.folder_file("[Backglass]\nGrillHeight = 200\n")

        self.assertEqual(self._from_game(named), {})

    def test_a_value_the_table_uses_already_is_left_out(self) -> None:
        self.folder_file("[Backglass]\nGrillHeight = 180\n")
        self.table_file("[DMD]\nProfile1Legacy = 0\n")

        self.assertEqual(self._from_game(), {})

    def test_so_is_one_only_all_tables_can_hold(self) -> None:
        self.folder_file("[Player]\nPlayfieldWidth = 800\n")
        self.table_file("[DMD]\nProfile1Legacy = 0\n")

        self.assertEqual(self._from_game(), {})

    def test_the_game_s_file_is_found_whatever_its_case(self) -> None:
        (self.game / "medieval madness.ini").write_text("[Backglass]\nGrillHeight = 200\n")
        self.table_file("[DMD]\nProfile1Legacy = 0\n")

        self.assertEqual(self._from_game(), {"Backglass.GrillHeight": "200"})


class FollowsGameTests(_Case):
    """What a game's own file gives a table that has no file of its own."""

    def _follows(self, table=None) -> dict[str, str]:
        return self.config.follows_game(str(table or self.table), self.settings)

    def test_what_the_game_s_file_sets_for_a_table_with_no_file(self) -> None:
        self.folder_file("[Backglass]\nGrillHeight = 200\n")

        self.assertEqual(self._follows(), {"Backglass.GrillHeight": "200"})

    def test_nothing_once_the_table_has_a_file_of_its_own(self) -> None:
        self.folder_file("[Backglass]\nGrillHeight = 200\n")
        self.table_file("[DMD]\nProfile1Legacy = 1\n")

        self.assertEqual(self._follows(), {})

    def test_nothing_where_the_table_s_file_is_the_game_s(self) -> None:
        named = self.game / "Medieval Madness.vpx"
        named.write_text("")
        self.folder_file("[Backglass]\nGrillHeight = 200\n")

        self.assertEqual(self._follows(named), {})

    def test_a_value_that_is_the_application_s_already_is_left_out(self) -> None:
        self.folder_file("[Backglass]\nGrillHeight = 180\n")

        self.assertEqual(self._follows(), {})

    def test_so_is_one_only_all_tables_can_hold(self) -> None:
        self.folder_file("[Player]\nPlayfieldWidth = 800\n")

        self.assertEqual(self._follows(), {})


SIZES_INI = """\
[Player]
; Width: Width of the window [Default: 16384]
PlayfieldWidth =

; Maximum texture dimension: The largest texture it loads [Default: 16384]
MaxTexDimension =
"""


class WindowSizeTests(_Case):
    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(SIZES_INI)

    def field(self, key: str):
        return next(f for g in self.config.groups(self.settings) for f in g.settings
                    if f.key == key)

    def test_a_window_size_worked_out_from_the_screen_declares_no_default(self) -> None:
        self.assertEqual(self.field("Player.PlayfieldWidth").default, "")
        self.assertEqual(self.config.blank_words().get("Player.PlayfieldWidth"),
                         "from_the_screen")

    def test_a_declared_default_it_uses_is_kept(self) -> None:
        self.assertEqual(self.field("Player.MaxTexDimension").default, "16384")
        self.assertNotIn("Player.MaxTexDimension", self.config.blank_words())

    def test_every_window_s_sizes_and_position_are_settings_vpx_declares(self) -> None:
        named = [key for key, word in self.config.blank_words().items()
                 if word == "from_the_screen"]

        self.assertEqual(len(named), 28)
        self.assertEqual([key for key in named if key not in TYPES], [])

    def test_a_named_value_belongs_to_a_number_vpx_declares(self) -> None:
        named = self.config.named_values()

        self.assertEqual(named["Player.MaxFramerate"],
                         (("-1", "match_the_display"), ("0", "no_limit")))
        self.assertEqual({TYPES.get(key) for key in named}, {"number"})


COLORS_INI = """\
[Alpha]
; Color: Color of lit segments [Default: 0X001523FF in 0X00000000 .. 0X00FFFFFF]
Profile4Color = 255
"""
COLOR = "Alpha.Profile4Color"


class ColorTests(_Case):
    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(COLORS_INI)

    def value(self) -> str:
        return self.config.read(SCOPE_LAUNCHER, "", self.settings)[COLOR].value

    def test_a_color_is_said_as_rrggbb_red_in_the_program_s_low_byte(self) -> None:
        field = next(f for g in self.config.groups(self.settings) for f in g.settings
                     if f.key == COLOR)

        self.assertEqual((field.type, field.default), ("color", "#FF2315"))
        self.assertEqual(self.value(), "#FF0000")

    def test_a_color_written_as_rrggbb_is_stored_as_the_program_s_number(self) -> None:
        self.config.write(SCOPE_LAUNCHER, "", {COLOR: "#00FF80"}, self.settings)

        self.assertIn("Profile4Color = 8453888", self.app_ini.read_text())
        self.assertEqual(self.value(), "#00FF80")

    def test_a_stored_color_is_read_as_the_program_reads_it(self) -> None:
        for stored, read in (("0X00FF0000", "#000000"), ("16711680 ", "#0000FF"),
                             ("red", "")):
            with self.subTest(stored=stored):
                self.app_ini.write_text(COLORS_INI.replace("= 255", f"= {stored}"))

                self.assertEqual(self.value(), read)


FLOATS_INI = """\
[Player]
; Screen Width: Physical width [Default: 95.889999 in 5.0 .. 200.0]
ScreenWidth =
; Camera Y: View point height offset [Default: 370.541931 in -1852.709595 .. 1852.709595]
CameraY =
; Tilt: How far [Default: 0.000000]
Tilt =
"""


class FloatTests(_Case):
    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(FLOATS_INI)

    def test_a_default_reads_as_the_fewest_digits_of_its_float32(self) -> None:
        fields = {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

        self.assertEqual({key: fields[f"Player.{key}"].default
                          for key in ("ScreenWidth", "CameraY", "Tilt")},
                         {"ScreenWidth": "95.89", "CameraY": "370.54193", "Tilt": "0"})

    def test_a_value_a_file_holds_reads_as_the_fewest_digits_too(self) -> None:
        self.app_ini.write_text(FLOATS_INI.replace("ScreenWidth =", "ScreenWidth = 95.889999")
                                .replace("CameraY =", "CameraY = 370.541931"))
        self.table_file("[Player]\nCameraY = 0.800000\n")

        width, camera = (self.at(SCOPE_ENTRY, key=f"Player.{key}")
                         for key in ("ScreenWidth", "CameraY"))

        self.assertEqual((width.value, camera.value, camera.fallback),
                         ("95.89", "0.8", "370.54193"))

    def test_a_table_given_that_float32_holds_nothing(self) -> None:
        cleared = self.config.write(SCOPE_ENTRY, str(self.table),
                                    {"Player.ScreenWidth": "95.89"}, self.settings)

        self.assertEqual(cleared, {"Player.ScreenWidth"})
        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())

    def test_a_table_given_another_float32_keeps_it(self) -> None:
        self.config.write(SCOPE_ENTRY, str(self.table), {"Player.ScreenWidth": "95.88999"},
                          self.settings)

        self.assertTrue(self.at(SCOPE_ENTRY, key="Player.ScreenWidth").set_here)


VIEWS_INI = """\
[Player]
; View Mode: Which camera setup to use [Default: 0, 0='Desktop', 1='Cabinet', 2='FSS']
BGSet = 0

[TableOverride]
; View mode: How the view projects [Default: 2, 0='Legacy', 1='Camera', 2='Window']
ViewCabMode =
; Field of view: How wide [Default: 55]
ViewCabFOV =
"""

DESKTOP_MODE = areas.view_mode("DT")
FSS_MODE = areas.view_mode("FSS")
CAB_MODE = areas.view_mode("Cab")


class ViewModeTests(_Case):
    def setUp(self) -> None:
        super().setUp()
        self.app_ini.write_text(VIEWS_INI)

    def rows(self, text: str = "") -> tuple[str, ...]:
        if text:
            self.table_file(text)
        values = self.config.read(SCOPE_ENTRY, str(self.table), self.settings)
        return self.config.summary_rows(areas.POINT_OF_VIEW, values)

    def test_a_cabinet_table_draws_the_cabinet_s(self) -> None:
        self.assertEqual(self.rows("[Player]\nBGSet = 1\n"), (CAB_MODE,))

    def test_one_left_to_the_program_draws_desktop_and_full_single_screen(self) -> None:
        self.assertEqual(self.rows(), (DESKTOP_MODE, FSS_MODE))

    def test_a_forced_desktop_table_draws_desktop_alone(self) -> None:
        self.assertEqual(self.rows("[Player]\nBGSet = 2\n"), (DESKTOP_MODE,))

    def test_forced_desktop_still_draws_a_full_single_screen_mode_the_table_sets(self) -> None:
        self.assertEqual(self.rows("[Player]\nBGSet = 2\n[TableOverride]\nViewFSSMode = 1\n"),
                         (DESKTOP_MODE, FSS_MODE))

    def test_another_view_s_mode_the_table_sets_follows(self) -> None:
        self.assertEqual(self.rows("[Player]\nBGSet = 1\n[TableOverride]\nViewDTMode = 0\n"),
                         (CAB_MODE, DESKTOP_MODE))

    def test_another_area_draws_none(self) -> None:
        self.assertEqual(self.config.summary_rows(areas.DISPLAYS, {}), ())

    def test_a_view_mode_s_default_is_the_table_s_own(self) -> None:
        field = next(f for g in self.config.groups(self.settings) for f in g.settings
                     if f.key == CAB_MODE)

        self.assertEqual(field.default, "")
        self.assertEqual(self.config.blank_words()[CAB_MODE], "from_the_table")

    def test_a_view_mode_is_offered_first_at_a_table(self) -> None:
        field = next(f for g in self.config.groups(self.settings) for f in g.settings
                     if f.key == CAB_MODE)

        self.assertTrue(field.per_table)

    def test_each_view_s_heading_leads_with_its_mode(self) -> None:
        headings = areas.view_headings({CAB_MODE, "TableOverride.ViewCabFOV",
                                        "TableOverride.ViewDTFOV"})

        self.assertEqual([(one.key, one.keys) for one in headings],
                         [("desktop", ("TableOverride.ViewDTFOV",)),
                          ("cabinet", (CAB_MODE, "TableOverride.ViewCabFOV"))])


class TableOptionTests(_Case):
    """Table options are declared by the table's script, so they are listed as the
    table's file holds them."""

    def test_a_table_s_options_are_a_group_of_their_own(self) -> None:
        self.table_file("[TableOption]\nBall_Speed = 2\nLights = 1\nBlank =\n")

        groups = self.config.held_groups(str(self.table))

        self.assertEqual([(g.key, g.read_only) for g in groups],
                         [(areas.TABLE_OPTIONS, True)])
        self.assertEqual([(f.key, f.label) for f in groups[0].settings],
                         [("TableOption.Ball_Speed", "Ball Speed"),
                          ("TableOption.Lights", "Lights")])

    def test_a_table_without_them_has_none(self) -> None:
        self.table_file("[Backglass]\nBackglassOutput = 0\n")

        self.assertEqual(self.config.held_groups(str(self.table)), ())


class WriteTests(_Case):
    def test_writing_at_a_scope_creates_its_file(self) -> None:
        self.config.write(SCOPE_ENTRY, str(self.table), {KEY: "0"}, self.settings)

        self.assertTrue((self.game / "MM (VPW 1.2).ini").is_file())
        self.assertEqual(self.at(SCOPE_ENTRY).value, "0")

    def test_writing_the_launcher_scope_keeps_the_comments(self) -> None:
        self.config.write(SCOPE_LAUNCHER, "", {KEY: "0"}, self.settings)
        text = self.app_ini.read_text()

        self.assertIn("BackglassOutput = 0", text)
        self.assertIn("; Output Mode: Where it goes", text)

    def test_a_value_set_and_cleared_at_a_table_gives_its_file_back(self) -> None:
        held = "[Backglass]\nBackglassOutput = 0\n"
        self.table_file(held)
        grill = "Plugin.B2SLegacy.B2SHideGrill"

        self.config.write(SCOPE_ENTRY, str(self.table), {grill: "1"}, self.settings)
        self.config.write(SCOPE_ENTRY, str(self.table), {grill: ""}, self.settings)

        self.assertEqual((self.game / "MM (VPW 1.2).ini").read_text(), held)

    def test_a_scope_with_nowhere_to_write_says_so(self) -> None:
        with self.assertRaises(ValueError):
            self.config.write(SCOPE_ENTRY, "", {KEY: "0"}, self.settings)

    def test_two_writes_at_once_both_land_and_the_file_keeps_the_rest(self) -> None:
        from apps.vpx import config as vpx_config

        real = vpx_config.vini.written
        both_started = threading.Barrier(2)

        def slowly(*args, **kwargs):
            time.sleep(0.1)
            return real(*args, **kwargs)

        def write(key: str, value: str) -> None:
            both_started.wait()
            self.config.write(SCOPE_LAUNCHER, "", {key: value}, self.settings)

        with mock.patch.object(vpx_config.vini, "written", slowly):
            writers = [threading.Thread(target=write, args=pair)
                       for pair in ((KEY, "0"), (PLAIN, "0"))]
            for one in writers:
                one.start()
            for one in writers:
                one.join()

        text = self.app_ini.read_text()
        self.assertIn("BackglassOutput = 0", text)
        self.assertIn("Profile1Legacy = 0", text)
        self.assertIn("GrillHeight = 180", text)

    def test_a_plugin_setting_goes_under_its_plugin_at_every_scope(self) -> None:
        from apps.vpx import ini as vini

        files = {SCOPE_LAUNCHER: (self.app_ini, "0"),
                 SCOPE_FOLDER: (self.game / "Medieval Madness.ini", "1"),
                 SCOPE_ENTRY: (self.game / "MM (VPW 1.2).ini", "1")}
        for scope, (path, value) in files.items():
            with self.subTest(scope=scope):
                self.config.write(scope, str(self.table),
                                  {"Plugin.B2SLegacy.B2SHideGrill": value}, self.settings)

                placed = {(one.section, one.key)
                          for one in vini.parse(path.read_text()).settings.values()}
                self.assertIn(("Plugin.B2SLegacy", "B2SHideGrill"), placed)


class SchemaTests(_Case):
    def test_a_section_the_areas_do_not_name_is_in_the_rest(self) -> None:
        keys = [g.key for g in self.config.groups(self.settings)]

        self.assertEqual(keys, [areas.DISPLAYS, areas.INPUT, areas.REST])

    def test_the_groups_come_from_the_file_rather_than_from_here(self) -> None:
        groups = {g.key: g for g in self.config.groups(self.settings)}

        keys = {f.key for f in groups[areas.DISPLAYS].settings}
        self.assertEqual(keys, {KEY, "Backglass.GrillHeight"})

    def test_a_setting_carries_what_the_comment_said(self) -> None:
        groups = {g.key: g for g in self.config.groups(self.settings)}
        one = next(f for f in groups[areas.DISPLAYS].settings if f.key == KEY)

        self.assertEqual(one.label, "Output Mode")
        self.assertEqual(one.type, "choice")
        self.assertEqual(one.choices, (("0", "Disabled"), ("1", "Floating")))

    def test_a_plugin_setting_written_bare_takes_the_label_its_plugin_declares(self) -> None:
        self.app_ini.write_text(APP_INI + "\n[Plugin.PinMAME]\nPinMAMEPath = \n"
                                "\n[Plugin.DMDUtil]\nZeDMD = 0\n")
        labels = {f.key: f.label for g in self.config.groups(self.settings)
                  for f in g.settings}

        self.assertEqual(labels["Plugin.PinMAME.PinMAMEPath"], "PinMAME Path")
        self.assertEqual(labels["Plugin.DMDUtil.ZeDMD"], "")

    def test_a_plugin_s_switch_says_nothing_its_plugin_s_name_does_not(self) -> None:
        self.app_ini.write_text(APP_INI + "\n[Plugin.PinMAME]\n"
                                "; Enable: Enable PinMAME plugin [Default: 0]\nEnable = 1\n")
        said = {f.key: f.description for g in self.config.groups(self.settings)
                for f in g.settings}

        self.assertEqual(said["Plugin.PinMAME.Enable"], "")
        self.assertEqual(said["DMD.Profile1Legacy"], "Use the legacy renderer")

    def test_a_plugin_s_switch_is_named_from_the_catalog(self) -> None:
        self.app_ini.write_text(APP_INI + "\n[Plugin.PinMAME]\n"
                                "; Enable: Enable PinMAME plugin [Default: 0]\nEnable = 1\n"
                                "\n[Plugin.Unheard]\nEnable = 1\n")
        fields = {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

        for key in ("Plugin.PinMAME.Enable", "Plugin.Unheard.Enable"):
            words = apps.field_words("vpx", fields[key])
            self.assertEqual((words["label"], words["label_key"]),
                             ("Enable", "app.vpx.enable"), key)

    def test_a_label_the_program_wrote_in_sentence_case_reads_in_title_case(self) -> None:
        self.app_ini.write_text(APP_INI + "\n[Plugin.B2SLegacy]\n"
                                "B2SDualMode = 0\nB2SHideB2SBackglass = 0\n")
        fields = {f.key: f for g in self.config.groups(self.settings) for f in g.settings}

        said = {key: apps.field_words("vpx", fields[key])["label"]
                for key in ("Plugin.B2SLegacy.B2SDualMode",
                            "Plugin.B2SLegacy.B2SHideB2SBackglass")}

        self.assertEqual(said, {"Plugin.B2SLegacy.B2SDualMode": "Dual Mode",
                                "Plugin.B2SLegacy.B2SHideB2SBackglass": "Hide B2S Backglass"})

    def test_a_setting_commonly_set_per_table_labeled_by_its_key_is_named_from_the_catalog(
            self) -> None:
        self.app_ini.write_text(APP_INI + "\n[TableOverride]\n"
                                "; Difficulty: Overall difficulty (affects slope, flipper size, "
                                "ball trajectories scattering,...) [Default: 1.0 in 0.0 .. 1.0]\n"
                                "Difficulty = \n")
        field = next(f for g in self.config.groups(self.settings) for f in g.settings
                     if f.key == "TableOverride.Difficulty")
        words = apps.field_words("vpx", field)

        self.assertTrue(field.per_table)
        self.assertEqual((words["label"], words["label_key"]),
                         ("Difficulty", "app.vpx.field.TableOverride.Difficulty.label"))

    def test_what_vpx_wrote_about_itself_is_not_offered_as_a_setting(self) -> None:
        offered = {f.key for g in self.config.groups(self.settings) for f in g.settings}

        self.assertNotIn("Version.VPinball", offered)
        self.assertNotIn("Version.VPinball", self.config.read(
            SCOPE_LAUNCHER, "", self.settings))


class BuildTests(_Case):
    """A setting the saving build does not have is left off the page."""

    STALE = "Editor.EnableLog"
    NEW = "Plugin.Nixie.LayoutFolder"

    def setUp(self) -> None:
        super().setUp()
        for patch in (mock.patch.object(build, "LINE", "1081"),
                      mock.patch.dict(build.SPANS, {self.STALE: Span(0, 5640),
                                                    self.NEW: Span(6254, None)}, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def offered(self, version: str | None = None) -> set[str]:
        ini = (APP_INI.replace("VPinball = 10815353", f"VPinball = {version}")
               if version else APP_INI.replace("VPinball = 10815353\n", ""))
        self.app_ini.write_text(
            ini + "\n[Editor]\n; Enable Log: Write a log [Default: 1]\nEnableLog = 1\n"
            "\n[Plugin.Nixie]\nLayoutFolder = \n")
        return {f.key for g in self.config.groups(self.settings) for f in g.settings}

    def install_nixie(self) -> None:
        bundle = self.root / "VPinballX_BGFX.app" / "Contents"
        (bundle / "PlugIns" / "nixie").mkdir(parents=True)
        (bundle / "PlugIns" / "nixie" / "plugin.cfg").write_text(
            '[configuration]\nid = "Nixie"\nname = "Nixie"\ndescription = "Nixie tubes"\n')
        self.settings["bin_path"] = str(bundle / "MacOS" / "VPinballX_BGFX")

    def test_a_build_that_has_replaced_a_setting_is_not_offered_the_old_one(self) -> None:
        self.assertNotIn(self.STALE, self.offered("10815985"))

    def test_a_build_that_predates_a_setting_is_not_offered_it(self) -> None:
        self.assertNotIn(self.NEW, self.offered("10815985"))

    def test_the_builds_that_do_have_a_setting_are_offered_it(self) -> None:
        self.assertIn(self.STALE, self.offered("10815353"))
        self.assertIn(self.NEW, self.offered("10816300"))

    def test_a_build_of_another_line_is_offered_everything(self) -> None:
        self.assertLessEqual({self.STALE, self.NEW}, self.offered("1080"))

    def test_an_ini_that_does_not_name_its_build_is_offered_everything(self) -> None:
        self.assertIn(self.STALE, self.offered())

    def test_a_plugin_the_program_has_is_offered_to_an_ini_an_older_build_saved(self) -> None:
        self.install_nixie()

        self.assertIn(self.NEW, self.offered("10815985"))

    def test_a_setting_a_build_dropped_stays_off_the_page_when_its_plugin_is_there(self) -> None:
        self.install_nixie()

        self.assertNotIn(self.STALE, self.offered("10815985"))

    def test_a_dropped_setting_a_game_sets_is_not_followed(self) -> None:
        self.folder_file("[Backglass]\nGrillHeight = 200\n")
        grill = {"Backglass.GrillHeight": Span(0, 5640)}
        with mock.patch.dict(build.SPANS, grill):
            held = self.config.follows_game(str(self.table), self.settings)
            self.app_ini.write_text(APP_INI.replace("VPinball = 10815353", "VPinball = 10815985"))
            gone = self.config.follows_game(str(self.table), self.settings)

        self.assertEqual((held, gone), ({"Backglass.GrillHeight": "200"}, {}))


SETS = ("DMD.Profile", "Alpha.Profile", "Player.Anaglyph", "DefaultCamera.")


def _as_the_set_calls_it(key: str) -> str:
    """A label every other setting in its set shares, as the program writes them."""
    return re.sub(r"^(?:Profile\d+|Anaglyph\d+|Desktop|FSS)", "", key.split(".", 1)[1])


class NumberedSetTests(_Case):
    def _labels(self, ini: str) -> dict[str, dict[str, str]]:
        self.app_ini.write_text(ini)
        return {f.key: apps.field_words("vpx", f)
                for g in self.config.groups(self.settings) for f in g.settings
                if not bindings.is_action(f.key)}

    def test_no_two_settings_of_a_set_read_the_same(self) -> None:
        sections: dict[str, list[str]] = {}
        for key in sorted(key for key in TYPES if key.startswith(SETS)):
            section, name = key.split(".", 1)
            sections.setdefault(section, []).append(
                f"; {_as_the_set_calls_it(key)}: What it does [Default: 0]\n{name} = \n")
        labels = [words["label"] for words in self._labels("".join(
            f"[{section}]\n{''.join(rows)}\n" for section, rows in sections.items())).values()]

        self.assertGreater(len(labels), 200)
        self.assertEqual([label for label, seen in Counter(labels).items() if seen > 1], [])

    def test_each_carries_its_set_s_name_as_the_program_gives_it(self) -> None:
        said = self._labels(
            "[DMD]\n; DMD Diffuse Glow: Glow of dots [Default: 0.1 in 0.0 .. 10.0]\n"
            "Profile2DiffuseGlow = \n"
            "\n[Alpha]\n; Color: Color of lit segments [Default: 0X00FFEF3F]\nProfile2Color = \n"
            "\n[Player]\n; Anaglyph Filter: Anaglyph filter applied to anaglyph profile #10 "
            "[Default: 'None', 0='None', 1='Dubois']\nAnaglyph10Filter = \n"
            "\n[DefaultCamera]\n; Camera X: View point width offset [Default: 0.0]\n"
            "FSSCamX = \n")

        self.assertEqual({key: (words["label"], words["label_key"])
                          for key, words in said.items()},
                         {"DMD.Profile2DiffuseGlow": ("Diffuse Glow (DMD: Neon Plasma)", ""),
                          "Alpha.Profile2Color": ("Color (Alpha: Blue VFD)", ""),
                          "Player.Anaglyph10Filter": ("Filter (Anaglyph Custom 4)", ""),
                          "DefaultCamera.FSSCamX":
                              ("Camera X (Default Camera: Full Single Screen)", "")})

    def test_a_set_member_whose_label_opens_otherwise_keeps_it(self) -> None:
        said = self._labels("[Player]\n; Anaglyph Left Contrast: Left eye [Default: 1.0]\n"
                            "Stereo3DLeftContrast = \n")

        self.assertEqual(said["Player.Stereo3DLeftContrast"]["label"], "Anaglyph Left Contrast")

    def test_a_member_past_the_set_s_names_keeps_its_whole_label(self) -> None:
        said = self._labels("[Player]\n; Anaglyph Filter: Filter [Default: 'None']\n"
                            "Anaglyph11Filter = \n")

        self.assertEqual(said["Player.Anaglyph11Filter"]["label"], "Anaglyph Filter")

    def test_a_label_that_is_the_key_run_together_is_worded_from_the_catalog(self) -> None:
        said = self._labels("[Player]\n; AutostartEnable: [Default: 0]\nasenable = \n")

        self.assertEqual(said["Player.asenable"]["label"], "Autostart")

    def test_a_label_s_own_aside_shares_the_set_s_parentheses(self) -> None:
        said = self._labels("[DefaultCamera]\n; Field Of View (overall scale): Global view "
                            "scale [Default: 50.0 in 25.0 .. 90.0]\nDesktopFov = \n")

        self.assertEqual(said["DefaultCamera.DesktopFov"]["label"],
                         "Field of View (Overall Scale, Default Camera: Desktop)")

    def test_a_profile_the_program_has_not_named_keeps_its_label(self) -> None:
        said = self._labels("[DMD]\n; Dot Tint: Color of lit dots [Default: 0]\n"
                            "Profile8DotTint = \n")

        self.assertEqual(said["DMD.Profile8DotTint"]["label"], "Dot Tint")


class TypeTests(_Case):
    def test_the_program_says_what_a_setting_is_and_the_file_cannot(self) -> None:
        """`Enable Log` and `ImageMngPosX` both default to a bare 0 or 1. Only the
        program's own declarations separate a switch from a number."""
        from apps.vpx.setting_types import TYPES

        self.assertEqual(TYPES.get("Editor.EnableLog"), "bool")
        self.assertEqual(TYPES.get("Editor.WindowLeft"), "int")

    def test_every_plugin_has_a_switch(self) -> None:
        """The host creates it so a plugin can be turned off, and no plugin declares
        it - so it is the one switch that would have had no type at all."""
        from apps.vpx.setting_types import TYPES

        for plugin in ("Plugin.B2S", "Plugin.PinMAME", "Plugin.FlexDMD"):
            with self.subTest(plugin=plugin):
                self.assertEqual(TYPES.get(f"{plugin}.Enable"), "bool")

    def test_a_setting_the_program_has_not_declared_keeps_what_the_file_implied(self) -> None:
        """A stale map degrades rather than breaks."""
        from apps.vpx import ini as vini
        from apps.vpx.config import _type_of

        one = vini.parse("[Nowhere]\n; A: b [Default: 3]\nNeverDeclared = 3\n")
        self.assertEqual(_type_of(one.settings["Nowhere.NeverDeclared"]), "int")

    def test_what_the_program_keeps_in_the_file_is_not_offered_as_a_setting(self) -> None:
        """Key bindings written per device, and the order plugins render in. State that
        happens to share the file, and 59 rows of it is noise to read past."""
        offered = {f.key for g in self.config.groups(self.settings) for f in g.settings}

        for key in ("Input.Mapping.NotAnAction", "Input.Device.Key.Type",
                    "Backglass.Priority.PUP"):
            with self.subTest(key=key):
                self.assertNotIn(key, offered)

    def test_a_binding_is_hidden_even_though_its_section_is_not(self) -> None:
        """`[Input]` holds both `Mapping.<Name>` and real settings, so the whole name
        decides rather than the heading above it. A mapping is offered only for an action
        the program has."""
        from apps.vpx.config import _offered

        self.assertFalse(_offered("Input.Mapping.NotAnAction"))
        self.assertTrue(_offered("Input.Mapping.LeftFlipper"))
        self.assertTrue(_offered("Input.JoyCustom1"))


class WritingLikeTheProgramTests(_Case):
    """A table layer is not written the way the application layer is, and the difference
    is the program's, not ours.

    `LayeredINIPropertyStore::Save` writes every key at the application layer, blank
    where it has no value. At a table it writes only real overrides: a key it has no
    value for is removed, and so is one whose value matches the application's - unless
    the property is contextual. Writing our own way leaves a file the program rewrites
    the first time it saves, and the parts it rewrote are the parts somebody set here.
    """

    def test_a_real_override_is_written(self) -> None:
        self.config.write("entry", str(self.table), {KEY: "0"}, self.settings)

        self.assertEqual(self.at("entry")[0] if isinstance(self.at("entry"), tuple)
                         else self.at("entry").value, "0")

    def test_clearing_at_a_table_removes_the_key(self) -> None:
        """Rather than blanking it. Both read the same on the way back in, and the
        program deletes a blank one the next time it saves."""
        self.config.write("entry", str(self.table), {KEY: "0", PLAIN: "0"}, self.settings)
        self.config.write("entry", str(self.table), {KEY: ""}, self.settings)

        written = (self.game / "MM (VPW 1.2).ini").read_text()
        self.assertNotIn("BackglassOutput", written)

    def test_clearing_at_the_application_blanks_it_instead(self) -> None:
        """Which is what the program does where there is no parent to fall through to."""
        self.config.write("launcher", str(self.table), {KEY: ""}, self.settings)

        self.assertIn("BackglassOutput =", self.app_ini.read_text())

    def test_the_application_s_own_value_clears_the_table_s(self) -> None:
        self.config.write(SCOPE_ENTRY, str(self.table), {PLAIN: "0"}, self.settings)

        cleared = self.config.write(SCOPE_ENTRY, str(self.table), {PLAIN: "1"},
                                    self.settings)

        self.assertEqual(cleared, {PLAIN})
        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())
        found = self.at(SCOPE_ENTRY, key=PLAIN)
        self.assertEqual((found.value, found.scope, found.set_here),
                         ("1", SCOPE_LAUNCHER, False))

    def test_a_key_the_application_leaves_blank_has_the_default_it_states(self) -> None:
        """How the program writes nearly every key of its own file."""
        self.app_ini.write_text(APP_INI.replace("Profile1Legacy = 1", "Profile1Legacy = "))
        self.config.write(SCOPE_ENTRY, str(self.table), {PLAIN: "0"}, self.settings)

        cleared = self.config.write(SCOPE_ENTRY, str(self.table), {PLAIN: "1"},
                                    self.settings)

        self.assertEqual(cleared, {PLAIN})
        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())

    def test_a_number_matches_however_it_is_spelled(self) -> None:
        self.app_ini.write_text(
            "[Player]\n; Exposure: How bright [Default: 1.0 in 0.0 .. 5.0]\n"
            "HDRGlobalExposure = 1.0\n")
        self.config.write(SCOPE_ENTRY, str(self.table),
                          {"Player.HDRGlobalExposure": "2.0"}, self.settings)

        cleared = self.config.write(SCOPE_ENTRY, str(self.table),
                                    {"Player.HDRGlobalExposure": "1"}, self.settings)

        self.assertEqual(cleared, {"Player.HDRGlobalExposure"})

    def test_a_text_setting_matches_only_as_written(self) -> None:
        self.app_ini.write_text("[Player]\n; Physics Set Name: What it is called "
                                "[Default: 'Set 1']\nPhysicsSetName0 = 1\n")

        cleared = self.config.write(SCOPE_ENTRY, str(self.table),
                                    {"Player.PhysicsSetName0": "1.0"}, self.settings)

        self.assertEqual(cleared, frozenset())

    def test_and_the_folder_s_at_the_folder(self) -> None:
        self.folder_file("[DMD]\nProfile1Legacy = 0\n")

        cleared = self.config.write(SCOPE_FOLDER, str(self.table), {PLAIN: "1"},
                                    self.settings)

        self.assertEqual(cleared, {PLAIN})
        self.assertFalse((self.game / "Medieval Madness.ini").exists())

    def test_with_nothing_to_clear_no_file_is_made(self) -> None:
        """A table file stops a folder file reaching the table, one made later included."""
        self.config.write(SCOPE_ENTRY, str(self.table), {PLAIN: "1"}, self.settings)

        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())

    def test_a_folder_value_in_the_way_still_answers(self) -> None:
        self.folder_file("[DMD]\nProfile1Legacy = 0\n")

        self.config.write(SCOPE_ENTRY, str(self.table), {PLAIN: "1"}, self.settings)

        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())
        found = self.at(SCOPE_ENTRY, key=PLAIN)
        self.assertEqual((found.value, found.scope), ("0", SCOPE_FOLDER))

    def test_clearing_a_table_s_last_key_gives_the_game_s_file_back(self) -> None:
        self.folder_file("[DMD]\nProfile1Legacy = 0\n")
        self.table_file("[Backglass]\nGrillHeight = 200\n")
        self.assertEqual(self.at(SCOPE_ENTRY, key=PLAIN).scope, SCOPE_LAUNCHER)

        self.config.write(SCOPE_ENTRY, str(self.table), {GRILL: ""}, self.settings)

        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())
        found = self.at(SCOPE_ENTRY, key=PLAIN)
        self.assertEqual((found.value, found.scope), ("0", SCOPE_FOLDER))

    def test_a_table_file_s_last_key_clears_back_to_the_game_s_value(self) -> None:
        self.folder_file("[DMD]\nProfile1Legacy = 0\n")
        self.table_file("[DMD]\nProfile1Legacy = 1\n")

        found = self.at(SCOPE_ENTRY, key=PLAIN)
        self.assertEqual((found.fallback, found.fallback_scope), ("0", SCOPE_FOLDER))

    def test_with_another_key_left_it_clears_back_to_the_launcher_s(self) -> None:
        self.folder_file("[DMD]\nProfile1Legacy = 0\n")
        self.table_file("[Backglass]\nGrillHeight = 200\n\n[DMD]\nProfile1Legacy = 1\n")

        found = self.at(SCOPE_ENTRY, key=PLAIN)
        self.assertEqual((found.fallback, found.fallback_scope), ("1", SCOPE_LAUNCHER))

    def test_a_file_with_another_key_left_is_written_not_removed(self) -> None:
        self.table_file("[Backglass]\nGrillHeight = 200\n\n[DMD]\nProfile1Legacy = 0\n")

        self.config.write(SCOPE_ENTRY, str(self.table), {GRILL: ""}, self.settings)

        written = (self.game / "MM (VPW 1.2).ini").read_text()
        self.assertIn("Profile1Legacy = 0", written)
        self.assertNotIn("GrillHeight", written)

    def test_an_emptied_file_is_removed_where_the_game_s_answers_instead(self) -> None:
        self.folder_file("[DMD]\nProfile1Legacy = 0\n")
        self.table_file("[DMD]\nProfile1Legacy = 0\n")

        self.config.write(SCOPE_ENTRY, str(self.table), {PLAIN: "1"}, self.settings)

        self.assertFalse((self.game / "MM (VPW 1.2).ini").exists())
        found = self.at(SCOPE_ENTRY, key=PLAIN)
        self.assertEqual((found.value, found.scope), ("0", SCOPE_FOLDER))

    def test_a_table_file_that_held_no_key_already_is_left(self) -> None:
        self.folder_file("[DMD]\nProfile1Legacy = 0\n")
        self.table_file("")

        self.config.write(SCOPE_ENTRY, str(self.table), {GRILL: ""}, self.settings)

        self.assertTrue((self.game / "MM (VPW 1.2).ini").is_file())

    def test_a_different_value_is_written_at_a_table(self) -> None:
        self.config.write("entry", str(self.table), {PLAIN: "0"}, self.settings)

        self.assertIn("Profile1Legacy = 0",
                      (self.game / "MM (VPW 1.2).ini").read_text())

    def test_a_contextual_setting_may_be_held_at_it(self) -> None:
        """The program keeps those even when they match, so we do too."""
        cleared = self.config.write(SCOPE_ENTRY, str(self.table), {KEY: "1"}, self.settings)

        self.assertEqual(cleared, frozenset())
        self.assertIn("BackglassOutput = 1", (self.game / "MM (VPW 1.2).ini").read_text())

    def test_so_is_every_plugin_s_switch(self) -> None:
        switches = ("Plugin.B2S.Enable", "Plugin.Unheard.Enable")
        self.app_ini.write_text(APP_INI + "\n[Plugin.B2S]\nEnable = 1\n\n"
                                "[Plugin.Unheard]\nEnable = 1\n")

        cleared = self.config.write(SCOPE_ENTRY, str(self.table),
                                    dict.fromkeys(switches, "1"), self.settings)

        self.assertEqual(cleared, frozenset())
        for key in switches:
            found = self.at(SCOPE_ENTRY, key=key)
            self.assertEqual((found.value, found.set_here), ("1", True), key)

    def test_the_application_layer_may_hold_any_value(self) -> None:
        """There is nothing above it to match, so nothing to be redundant against."""
        self.config.write("launcher", str(self.table), {KEY: "1"}, self.settings)

        self.assertIn("BackglassOutput = 1", self.app_ini.read_text())


class OwnFileTests(unittest.TestCase):
    """An empty Settings File is the one Visual Pinball reads when given none."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch("apps.vpx.config._machine_folders",
                             return_value=(self.root / "VPinballX", self.root / ".vpinball"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _file(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Player]\n", encoding="utf-8")
        return path

    def test_the_newest_version_folder_wins(self) -> None:
        self._file("VPinballX", "10.8", "VPinballX.ini")
        newest = self._file("VPinballX", "10.10", "VPinballX.ini")
        self._file("VPinballX", "VPinballX.ini")

        self.assertEqual(own_file(), newest)

    def test_one_beside_the_program_comes_before_the_old_layouts(self) -> None:
        beside = self._file("opt", "vpinball", "VPinballX.ini")
        self._file("VPinballX", "VPinballX.ini")

        self.assertEqual(own_file(str(self.root / "opt" / "vpinball" / "VPinballX_GL")),
                         beside)

    def test_the_old_layouts_are_still_found(self) -> None:
        standalone = self._file(".vpinball", "VPinballX.ini")

        self.assertEqual(own_file(), standalone)

    def test_none_where_there_is_none_yet(self) -> None:
        self.assertIsNone(own_file("/opt/vpinball/VPinballX_GL"))

    def test_an_empty_field_says_which_file_it_is(self) -> None:
        own = self._file("VPinballX", "10.8", "VPinballX.ini")

        self.assertEqual(VPXConfig().left_empty({"ini_path": ""}),
                         {"ini_path": Standing(str(own), True)})
        self.assertEqual(settings_file({"ini_path": ""}), own)

    def test_with_no_file_yet_it_is_where_the_newest_version_makes_one(self) -> None:
        made = self.root / "VPinballX" / "10.8" / "VPinballX.ini"

        self.assertEqual(VPXConfig().left_empty({"bin_path": ""}),
                         {"ini_path": Standing(str(made), False)})

    def test_with_no_preferences_folder_it_names_nothing(self) -> None:
        with mock.patch("apps.vpx.config.preferences_file", return_value=None):
            self.assertEqual(VPXConfig().left_empty({"bin_path": ""}),
                             {"ini_path": Standing("", False)})

    def test_the_log_is_the_newest_version_folder_s_whatever_settings_file_is_named(
            self) -> None:
        self._file("VPinballX", "10.8", "vpinball.log")
        newest = self._file("VPinballX", "10.10", "vpinball.log")
        self._file("elsewhere", "vpinball.log")

        self.assertEqual(own_log(), newest)


# As 10.8.1 writes them, a display named bare.
LOG_10_8_1 = """\
2026-07-22 10:01:49.339 INFO  [1] [VPX::Window::Window@86] No display configured. \
Using display "Built-in Display".
2026-09-05 12:09:42.540 INFO  [2] [VPX::Window::Window@290] Window #1 (960x540) was \
created on display External Display [3840x2160 60Hz SDL_PIXELFORMAT_ARGB8888]
"""
# As later builds write them, with its position in the name.
LOG_LATER = """\
2026-09-24 18:00:00.000 WARN  [3] [VPX::Window::Window@88] The selected display \
"External Display" is not available. Using display "Built-in Display [0, 0]" instead.
2026-09-24 18:00:00.100 INFO  [3] [VPX::Window::Window@307] Window #0 (1920x1080) was \
created on display Built-in Display [0, 0] [3456x2234 120Hz SDL_PIXELFORMAT_ARGB8888]
"""


class ReportedDisplayTests(unittest.TestCase):
    """A display is matched by its exact name, which differs by build."""

    def test_each_name_a_build_uses_is_read_as_it_wrote_it(self) -> None:
        self.assertEqual(displays.reported(LOG_10_8_1), ("External Display", "Built-in Display"))
        self.assertEqual(displays.reported(LOG_LATER), ("Built-in Display [0, 0]",))

    def test_one_it_last_said_is_not_there_is_not_offered(self) -> None:
        self.assertEqual(displays.reported(LOG_10_8_1 + LOG_LATER),
                         ("Built-in Display [0, 0]", "Built-in Display"))

    def test_every_display_setting_is_offered_them(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name, "vpinball.log")
        log.write_text(LOG_10_8_1, encoding="utf-8")

        with mock.patch("apps.vpx.config.own_log", return_value=log):
            said = VPXConfig().reported()

        self.assertEqual(set(said), {"Player.PlayfieldDisplay", "Backglass.BackglassDisplay",
                                     "ScoreView.ScoreViewDisplay", "Topper.TopperDisplay",
                                     "PlayerVR.PreviewDisplay"})
        self.assertEqual(said["Topper.TopperDisplay"], ("External Display", "Built-in Display"))

    def test_a_log_that_reports_none_offers_nothing(self) -> None:
        with mock.patch("apps.vpx.config.own_log", return_value=None):
            self.assertEqual(VPXConfig().reported(), {})

    def test_a_log_is_read_again_once_it_changes(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name, "vpinball.log")
        log.write_text(LOG_10_8_1, encoding="utf-8")
        displays.reported_in(log)

        log.write_text(LOG_10_8_1 + LOG_LATER, encoding="utf-8")

        self.assertEqual(displays.reported_in(log),
                         ("Built-in Display [0, 0]", "Built-in Display"))
        self.assertEqual(settings_file({"ini_path": "/cfg/other.ini"}),
                         Path("/cfg/other.ini"))


class SettingsFileCommandTests(unittest.TestCase):
    """The command that has the program write its own file."""

    def test_the_program_is_run_with_the_flag_that_saves_the_settings(self) -> None:
        command = VPXConfig().settings_file_command({"bin_path": "/opt/vpx/VPinballX_BGFX"})

        self.assertEqual(command[1:], ["-v"])
        self.assertTrue(command[0].endswith("VPinballX_BGFX"))

    def test_a_named_ini_is_passed_so_the_file_is_made_where_it_will_be_read(self) -> None:
        command = VPXConfig().settings_file_command(
            {"bin_path": "/opt/vpx/VPinballX_BGFX", "ini_path": "/cfg/own.ini"})

        self.assertEqual(command[1:], ["-ini", str(Path("/cfg/own.ini")), "-v"])

    def test_no_program_is_no_command(self) -> None:
        self.assertEqual(VPXConfig().settings_file_command({"bin_path": " "}), [])


class NoHomeFolderTests(unittest.TestCase):
    """An account with no Home folder still lists its launchers."""

    def _folders(self, platform: str, **env: str) -> tuple[Path | None, Path | None]:
        with mock.patch("apps.vpx.config.Path.home", side_effect=RuntimeError("no home")), \
                mock.patch("apps.vpx.config.sys.platform", platform), \
                mock.patch.dict("os.environ", {"XDG_DATA_HOME": "", **env}):
            return REAL_MACHINE_FOLDERS()

    def test_no_home_and_no_data_home_names_no_folder(self) -> None:
        self.assertEqual(self._folders("linux"), (None, None))
        self.assertEqual(self._folders("darwin"), (None, None))

    def test_a_data_home_stands_in_for_the_missing_home(self) -> None:
        self.assertEqual(self._folders("linux", XDG_DATA_HOME="/data"),
                         (Path("/data/VPinballX"), None))


if __name__ == "__main__":
    unittest.main()

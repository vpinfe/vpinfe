"""Reading Visual Pinball's settings file, and writing one back without losing it."""

from __future__ import annotations

import unittest

from apps.vpx import ini as vini

SAMPLE = """\
; #########################
; # A header nobody parses
; #########################

[Editor]
; Enable Log: Enable general logging to the vinball.log file [Default: 1]
EnableLog = 1

; WindowLeft: Main window left [Default: -1]
WindowLeft =

[Backglass]
; Output Mode: Disabled, floating, or embedded [Default: 'Disabled', 0='Disabled', \
1='Floating', 2='Embedded in playfield']
BackglassOutput = 1

; Brightness: How bright [Default: 1.0 in 0.0 .. 2.0]
Brightness = 1.5

; Tint: The color [Default: 0X000000 in 0X000000 .. 0XFFFFFF]
Tint = 0XFF0000

; Name: What to call it [Default: '']
Name = something
"""


def placed(text: str) -> set[tuple[str, str]]:
    """Each key with the heading it sits under."""
    return {(one.section, one.key) for one in vini.parse(text).settings.values()}


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ini = vini.parse(SAMPLE)

    def one(self, qualified: str) -> vini.Setting:
        return self.ini.settings[qualified]

    def test_a_key_is_addressed_by_its_section(self) -> None:
        """A key is only unique inside its section - `Width` is in eight of them."""
        self.assertIn("Editor.EnableLog", self.ini.settings)
        self.assertEqual(self.one("Editor.EnableLog").section, "Editor")

    def test_the_comment_gives_the_label_and_the_description(self) -> None:
        one = self.one("Editor.EnableLog")

        self.assertEqual(one.label, "Enable Log")
        self.assertEqual(one.description,
                         "Enable general logging to the vinball.log file")

    def test_a_closed_set_of_answers_comes_back_as_choices(self) -> None:
        one = self.one("Backglass.BackglassOutput")

        self.assertEqual(one.kind, vini.KIND_CHOICE)
        self.assertEqual(one.choices,
                         (("0", "Disabled"), ("1", "Floating"),
                          ("2", "Embedded in playfield")))

    def test_an_enumerated_default_is_stored_as_its_value_not_its_label(self) -> None:
        """The file states the default by label and stores the number."""
        self.assertEqual(self.one("Backglass.BackglassOutput").default, "0")

    def test_a_number_keeps_its_range(self) -> None:
        one = self.one("Backglass.Brightness")

        self.assertEqual(one.kind, vini.KIND_NUMBER)
        self.assertEqual((one.minimum, one.maximum), (0.0, 2.0))

    def test_a_color_is_not_read_as_a_number(self) -> None:
        """Its range is stated in hex, which a number field cannot use, so it is left
        unstated rather than reported wrong."""
        one = self.one("Backglass.Tint")

        self.assertEqual(one.kind, vini.KIND_COLOR)
        self.assertIsNone(one.minimum)

    def test_a_quoted_default_is_a_string(self) -> None:
        self.assertEqual(self.one("Backglass.Name").kind, vini.KIND_STRING)

    def test_a_key_written_blank_sets_nothing(self) -> None:
        """VPX writes every key it knows into the file and leaves 98% of them blank,
        because blank means "use the default". Reading blank as a value would make
        every setting in the program look like somebody had chosen it."""
        self.assertIsNone(self.ini.value("Editor.WindowLeft"))
        self.assertIsNone(self.ini.value("Editor.NeverHeardOf"))

    def test_but_the_file_still_carries_it(self) -> None:
        """Writing back needs the line it is already on; reading what is in force does
        not care that it is there."""
        self.assertTrue(self.ini.mentions("Editor.WindowLeft"))
        self.assertFalse(self.ini.mentions("Editor.NeverHeardOf"))

    def test_a_label_falls_back_to_the_key(self) -> None:
        self.assertEqual(vini.parse("[A]\nB = 1\n").settings["A.B"].label, "B")


SHAPES = """\
[Player]
; Synchronization:  [Default: 'Frame Pacing', 0='No Sync', 1='Vertical Sync', \
2='Adaptive Sync', 3='Frame Pacing']:
;   No Sync: nothing waits.
;   Vertical Sync: waits for the display.
SyncMode =

; Limit Framerate:  [Default: -1.0 in -1.0 .. 1000.0]:
;   -1 follows the display
;   0 sets no limit
MaxFramerate =

; Disable Motion Blur:  [Default: 0]:
;   Turns the blur off.
ForceMotionBlurOff =

[TableOverride]
; Viewport Rotation:  [Default: 0.0 in 0.0 .. 360.0 by 90.0 steps]
ViewCabRotation =

[Standalone]
; Folder: Where it looks [Default: 'C:\\[Tables]\\']
Folder =
"""


class CommentShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ini = vini.parse(SHAPES)

    def one(self, qualified: str) -> vini.Setting:
        return self.ini.settings[qualified]

    def test_choices_come_through_when_the_description_follows_the_block(self) -> None:
        one = self.one("Player.SyncMode")

        self.assertEqual(one.kind, vini.KIND_CHOICE)
        self.assertEqual(one.choices, (("0", "No Sync"), ("1", "Vertical Sync"),
                                       ("2", "Adaptive Sync"), ("3", "Frame Pacing")))
        self.assertEqual(one.default, "3")

    def test_the_description_is_the_lines_after_the_block(self) -> None:
        one = self.one("Player.SyncMode")

        self.assertEqual(one.label, "Synchronization")
        self.assertEqual(one.description,
                         "No Sync: nothing waits.\nVertical Sync: waits for the display.")

    def test_a_number_described_after_its_block_keeps_its_range(self) -> None:
        one = self.one("Player.MaxFramerate")

        self.assertEqual((one.kind, one.default), (vini.KIND_NUMBER, "-1.0"))
        self.assertEqual((one.minimum, one.maximum), (-1.0, 1000.0))
        self.assertEqual(one.description, "-1 follows the display\n0 sets no limit")

    def test_a_switch_described_after_its_block_keeps_its_default(self) -> None:
        self.assertEqual(self.one("Player.ForceMotionBlurOff").default, "0")

    def test_a_stepped_number_keeps_its_default(self) -> None:
        one = self.one("TableOverride.ViewCabRotation")

        self.assertEqual((one.kind, one.default), (vini.KIND_NUMBER, "0.0"))
        self.assertEqual((one.minimum, one.maximum), (0.0, 360.0))
        self.assertEqual(one.description, "")

    def test_a_bracket_inside_a_quoted_default_is_part_of_it(self) -> None:
        self.assertEqual(self.one("Standalone.Folder").default, "C:\\[Tables]\\")

    def test_no_description_carries_the_block(self) -> None:
        for qualified, one in {**self.ini.settings,
                               **vini.parse(SAMPLE).settings}.items():
            with self.subTest(qualified=qualified):
                self.assertNotIn("[Default:", one.description)


class WriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ini = vini.parse(SAMPLE)

    def test_writing_nothing_gives_the_file_back(self) -> None:
        self.assertEqual(vini.written(self.ini, {}), SAMPLE)

    def test_a_value_is_replaced_where_it_already_sits(self) -> None:
        """The comments are the only documentation these settings have, so the file
        keeps its own shape rather than being rewritten from the parse."""
        out = vini.written(self.ini, {"Backglass.BackglassOutput": "2"})

        self.assertIn("BackglassOutput = 2", out)
        self.assertIn("; Output Mode: Disabled, floating, or embedded", out)
        self.assertEqual(out.count("BackglassOutput"), 1)

    def test_a_key_the_file_lacks_is_added_under_its_section(self) -> None:
        out = vini.written(self.ini, {"Backglass.NewOne": "3"})
        again = vini.parse(out)

        self.assertEqual(again.settings["Backglass.NewOne"].section, "Backglass")
        self.assertEqual(again.value("Backglass.NewOne"), "3")

    def test_a_section_the_file_lacks_is_added(self) -> None:
        out = vini.written(self.ini, {"Plugin.New.Thing": "on"})

        self.assertIn(("Plugin.New", "Thing"), placed(out))

    def test_a_plugin_setting_goes_under_its_plugin(self) -> None:
        out = vini.written(self.ini, {"Plugin.B2SLegacy.B2SHideGrill": "1"})

        self.assertIn(("Plugin.B2SLegacy", "B2SHideGrill"), placed(out))
        self.assertNotIn("[Plugin]", out.splitlines())

    def test_a_plugin_setting_joins_its_plugin_where_the_file_has_it(self) -> None:
        held = vini.parse(f"{SAMPLE}\n[Plugin.B2SLegacy]\nB2SHideGrill = 0\n")

        out = vini.written(held, {"Plugin.B2SLegacy.B2SHideB2SDMD": "1"})

        self.assertIn(("Plugin.B2SLegacy", "B2SHideB2SDMD"), placed(out))
        self.assertEqual(out.count("[Plugin.B2SLegacy]"), 1)

    def test_a_new_file_starts_with_its_first_section(self) -> None:
        out = vini.written(vini.parse(""), {"Plugin.B2S.ShowGrill": "1"})

        self.assertEqual(out, "[Plugin.B2S]\nShowGrill = 1\n")

    def test_a_new_section_follows_one_blank_line(self) -> None:
        for held in ("[Player]\nFXAA = 1\n", "[Player]\nFXAA = 1\n\n"):
            with self.subTest(held=held):
                out = vini.written(vini.parse(held), {"Plugin.B2S.ShowGrill": "1"})

                self.assertEqual(out, "[Player]\nFXAA = 1\n\n[Plugin.B2S]\nShowGrill = 1\n")

    def test_a_key_with_dots_of_its_own_stays_in_its_section(self) -> None:
        out = vini.written(vini.parse(""), {"Backglass.Priority.PUP": "2"})

        self.assertEqual(placed(out), {("Backglass", "Priority.PUP")})

    def test_a_default_properties_section_keeps_its_name(self) -> None:
        out = vini.written(vini.parse(""), {"DefaultProps\\Ball.Mass": "1.5"})

        self.assertEqual(placed(out), {("DefaultProps\\Ball", "Mass")})

    def test_removing_a_section_s_last_key_takes_its_heading(self) -> None:
        held = vini.parse("[Player]\nBGSet = 1\n\n[Plugin.B2SLegacy]\nB2SHideGrill = 1\n")

        out = vini.written(held, {}, remove=["Plugin.B2SLegacy.B2SHideGrill"])

        self.assertEqual(out, "[Player]\nBGSet = 1\n")

    def test_a_key_added_then_removed_gives_the_file_back(self) -> None:
        for held, key in (("[Player]\nBGSet = 1\n", "Plugin.B2SLegacy.B2SHideGrill"),
                          ("[Player]\nBGSet = 1\n", "Player.FXAA"),
                          ("[Player]\nBGSet = 1\n\n[DMD]\nLegacy = 1\n", "Player.FXAA")):
            with self.subTest(held=held, key=key):
                added = vini.written(vini.parse(held), {key: "1"})

                self.assertEqual(vini.written(vini.parse(added), {}, remove=[key]), held)

    def test_a_key_added_to_a_section_goes_above_its_trailing_blank_lines(self) -> None:
        held = vini.parse("[Player]\nBGSet = 1\n\n\n[DMD]\nLegacy = 1\n")

        self.assertEqual(vini.written(held, {"Player.FXAA": "1"}),
                         "[Player]\nBGSet = 1\nFXAA = 1\n\n\n[DMD]\nLegacy = 1\n")

    def test_a_key_added_to_the_last_section_goes_above_the_file_s_blank_end(self) -> None:
        held = vini.parse("[Player]\nBGSet = 1\n\n")

        self.assertEqual(vini.written(held, {"Player.FXAA": "1"}),
                         "[Player]\nBGSet = 1\nFXAA = 1\n\n")

    def test_a_section_between_two_leaves_one_blank_line_between_them(self) -> None:
        held = vini.parse("[A]\nx = 1\n\n[B]\ny = 1\n\n[C]\nz = 1\n")

        self.assertEqual(vini.written(held, {}, remove=["B.y"]), "[A]\nx = 1\n\n[C]\nz = 1\n")

    def test_a_section_with_a_key_left_keeps_its_heading(self) -> None:
        held = vini.parse("[Plugin.B2SLegacy]\nB2SHideGrill = 1\nB2SHideDMD = 1\n")

        out = vini.written(held, {}, remove=["Plugin.B2SLegacy.B2SHideGrill"])

        self.assertEqual(out, "[Plugin.B2SLegacy]\nB2SHideDMD = 1\n")

    def test_a_section_a_key_is_added_to_keeps_its_heading(self) -> None:
        out = vini.written(vini.parse("[B]\ny = 1\n"), {"B.w": "3"}, remove=["B.y"])

        self.assertEqual(out, "[B]\nw = 3\n")

    def test_adding_one_key_and_removing_another_removes_the_one_named(self) -> None:
        held = vini.parse("[Player]\nBGSet = 1\n\n"
                          "[Plugin.B2SLegacy]\nB2SHideGrill = 1\nB2SHideDMD = 1\n")

        out = vini.written(held, {"Player.FXAA": "1"},
                           remove=["Plugin.B2SLegacy.B2SHideGrill"])

        self.assertEqual(placed(out), {("Player", "BGSet"), ("Player", "FXAA"),
                                       ("Plugin.B2SLegacy", "B2SHideDMD")})

    def test_nothing_else_moves(self) -> None:
        before = SAMPLE.splitlines()
        after = vini.written(self.ini, {"Editor.EnableLog": "0"}).splitlines()

        differing = [n for n, (a, b) in enumerate(zip(before, after, strict=True))
                     if a != b]
        self.assertEqual(len(differing), 1)


if __name__ == "__main__":
    unittest.main()

"""Visual Pinball launched to be recorded: through copies of its settings, never its own."""

import os
import unittest
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.vpx import ini as vini
from apps.vpx.capture import VPXCapture, connector
from common.apps.contract import VIEW_FSS, Entry

SETTINGS = """[Player]
; Synchronization:  [Default: 3, 0='No Sync', 1='Vertical Sync', 3='Frame Pacing']:
SyncMode = 3
MaxFramerate =
PlaySound = 1
BGSet = 1

[Backglass]
BackglassOutput = 1
"""


class CaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.folder = root / "capture"
        self.folder.mkdir()
        self.settings_file = root / "VPinballX.ini"
        self.settings_file.write_text(SETTINGS, encoding="utf-8")
        self.game = root / "Example"
        self.game.mkdir()
        self.table = self.game / "Example.vpx"

    def _command(self, sound: bool = False, table: str | None = None, view: str = "",
                 **settings: str) -> list[str]:
        settings = {"bin_path": "/opt/vpinball/VPinballX",
                    "ini_path": str(self.settings_file), **settings}
        return VPXCapture().command(Entry(table=str(self.table) if table is None else table),
                                    settings, sound=sound, view=view, folder=str(self.folder))

    def _passed(self, cmd: list[str], flag: str) -> vini.Ini:
        return vini.parse(Path(cmd[cmd.index(flag) + 1]).read_text(encoding="utf-8"))

    def test_the_settings_file_is_passed_as_a_copy_in_the_folder(self) -> None:
        cmd = self._command()

        self.assertEqual(cmd[:3], ["/opt/vpinball/VPinballX", "-ini",
                                   str(self.folder / "VPinballX.ini")])
        self.assertEqual(cmd[-2:], ["-play", str(self.table)])

    def test_the_copy_is_unsynced_with_the_frame_cap_at_the_display_s_refresh(self) -> None:
        copy = self._passed(self._command(), "-ini")

        self.assertEqual((copy.value("Player.SyncMode"), copy.value("Player.MaxFramerate")),
                         ("0", "-1"))

    def test_the_copy_keeps_everything_else_the_settings_file_says(self) -> None:
        copy = self._passed(self._command(sound=True), "-ini")

        self.assertEqual(copy.value("Backglass.BackglassOutput"), "1")
        self.assertEqual(copy.value("Player.BGSet"), "1")
        self.assertEqual(copy.value("Player.PlaySound"), "1")
        self.assertEqual(copy.settings["Player.SyncMode"].label, "Synchronization")

    def test_the_copy_is_muted_unless_the_sound_is_recorded(self) -> None:
        muted = self._passed(self._command(sound=False), "-ini")
        heard = self._passed(self._command(sound=True), "-ini")

        for key in ("Player.PlaySound", "Player.PlayMusic", "Player.SoundVolume",
                    "Player.MusicVolume"):
            self.assertEqual(muted.value(key), "0", key)
        self.assertIsNone(heard.value("Player.PlayMusic"))

    def test_the_view_the_table_is_built_for_is_asked_for_only_in_full_single_screen(self) -> None:
        own = self._passed(self._command(), "-ini")
        fss = self._passed(self._command(view=VIEW_FSS), "-ini")

        self.assertEqual((own.value("Player.BGSet"), fss.value("Player.BGSet")), ("1", "0"))

    def test_a_table_setting_its_view_is_given_a_copy_asking_for_full_single_screen(self) -> None:
        (self.game / "Example.ini").write_text("[Player]\nBGSet = 2\n", encoding="utf-8")

        cmd = self._command(view=VIEW_FSS)

        self.assertEqual(self._passed(cmd, "-tableini").value("Player.BGSet"), "0")

    def test_the_launcher_s_own_file_is_never_written(self) -> None:
        self._command()

        self.assertEqual(self.settings_file.read_text(encoding="utf-8"), SETTINGS)

    def test_with_no_settings_file_anywhere_the_copy_holds_the_recording_s_values(self) -> None:
        self.settings_file.unlink()

        copy = self._passed(self._command(), "-ini")

        self.assertEqual(copy.value("Player.SyncMode"), "0")

    def test_a_table_setting_none_of_them_is_given_no_table_copy(self) -> None:
        (self.game / "Example.ini").write_text("[Player]\nBGSet = 2\n", encoding="utf-8")

        cmd = self._command()

        self.assertNotIn("-tableini", cmd)

    def test_a_table_setting_one_of_them_is_given_a_copy_with_the_recording_s(self) -> None:
        """The table's own file wins over the Settings File, so its SyncMode 3 would
        undo the copy's 0."""
        own = "[Player]\nSyncMode = 3\nBGSet = 2\n"
        (self.game / "Example.ini").write_text(own, encoding="utf-8")

        cmd = self._command()

        table = self._passed(cmd, "-tableini")
        self.assertEqual((table.value("Player.SyncMode"), table.value("Player.BGSet")),
                         ("0", "2"))
        self.assertEqual(cmd[-2:], ["-play", str(self.table)])
        self.assertEqual(cmd.count("-ini"), 1)
        self.assertEqual((self.game / "Example.ini").read_text(encoding="utf-8"), own)

    def test_the_table_s_file_is_the_one_visual_pinball_would_read(self) -> None:
        """No `<table>.ini`, so the folder's."""
        (self.game / "example.INI").write_text("[Player]\nPlayMusic = 1\n", encoding="utf-8")

        table = self._passed(self._command(), "-tableini")

        self.assertEqual(table.value("Player.PlayMusic"), "0")

    def test_a_table_setting_sound_is_left_alone_when_the_sound_is_recorded(self) -> None:
        (self.game / "Example.ini").write_text("[Player]\nPlaySound = 1\n", encoding="utf-8")

        self.assertNotIn("-tableini", self._command(sound=True))

    def test_an_entry_with_no_file_has_no_table_copy(self) -> None:
        cmd = VPXCapture().command(Entry(key="mm"), {"bin_path": "/opt/vpinball/VPinballX"},
                                   sound=False, view="", folder=str(self.folder))

        self.assertNotIn("-tableini", cmd)
        self.assertIn("-ini", cmd)


# As VPX 10.8.1 BGFX wrote them under sway, three outputs and no topper.
SHOWN_ON_THREE = """[Player]
PlayfieldDisplay = Samsung Electric Company SAMSUNG 0x00000001 (DP-1 via HDMI)

[Backglass]
BackglassOutput = 1
BackglassDisplay = Samsung Electric Company SAMSUNG (HDMI-A-1)

[ScoreView]
ScoreViewOutput = 1
ScoreViewDisplay = CVT DVI 0x00000001 (DVI-D-1)

[Topper]
TopperOutput =
TopperDisplay =
"""


class OutputTests(unittest.TestCase):
    """Which output VPX's own settings show each window on."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.settings_file = root / "VPinballX.ini"
        self.game = root / "Example"
        self.game.mkdir()
        self.table = self.game / "Example.vpx"

    def _outputs(self, settings: str, table: str | None = None) -> dict[str, str]:
        self.settings_file.write_text(settings, encoding="utf-8")
        if table is not None:
            (self.game / "Example.ini").write_text(table, encoding="utf-8")
        return VPXCapture().outputs(Entry(table=str(self.table)),
                                    {"ini_path": str(self.settings_file)})

    def test_each_display_names_its_output_in_the_parentheses_it_ends_with(self) -> None:
        for display, expected in (
                ("Samsung Electric Company SAMSUNG 0x00000001 (DP-1 via HDMI)", "DP-1"),
                ("Samsung Electric Company SAMSUNG (HDMI-A-1)", "HDMI-A-1"),
                ("CVT DVI 0x00000001 (DVI-D-1)", "DVI-D-1"),
                ("Monitor (Rev 2) (HDMI-A-2)", "HDMI-A-2"),
                ("CVT DVI", ""),
                ("Samsung Electric Company HDMI-A-1-SAMSUNG", ""),
                ("Monitor (HDMI-A-1) left", ""),
                ("()", ""),
                ("", "")):
            with self.subTest(display):
                self.assertEqual(connector(display), expected)

    def test_three_screens_and_no_topper(self) -> None:
        self.assertEqual(self._outputs(SHOWN_ON_THREE),
                         {"playfield": "DP-1", "backglass": "HDMI-A-1",
                          "scoreview": "DVI-D-1", "topper": ""})

    def test_only_a_floating_output_is_a_screen_of_its_own(self) -> None:
        """0 is Disabled, and unset is its default; 2 draws it inside the playfield."""
        for mode, expected in (("0", ""), ("2", ""), ("", ""), ("1", "HDMI-A-1")):
            with self.subTest(mode=mode):
                said = self._outputs("[Backglass]\nBackglassOutput = " + mode
                                     + "\nBackglassDisplay = Samsung (HDMI-A-1)\n")

                self.assertEqual(said["backglass"], expected)

    def test_a_display_naming_no_output_says_nothing(self) -> None:
        said = self._outputs("[Player]\nPlayfieldDisplay = CVT DVI\n\n"
                             "[ScoreView]\nScoreViewOutput = 1\nScoreViewDisplay =\n")

        self.assertNotIn("playfield", said)
        self.assertNotIn("scoreview", said)

    def test_with_no_settings_file_only_the_playfield_is_shown(self) -> None:
        said = VPXCapture().outputs(Entry(), {"ini_path": str(self.settings_file)})

        self.assertEqual(said, {"backglass": "", "scoreview": "", "topper": ""})

    def test_the_table_s_own_file_is_read_where_vpx_reads_it(self) -> None:
        """VPX builds the playfield window from the Settings File alone."""
        said = self._outputs(SHOWN_ON_THREE,
                             "[Player]\nPlayfieldDisplay = Other (HDMI-A-2)\n\n"
                             "[ScoreView]\nScoreViewOutput = 0\n")

        self.assertEqual((said["playfield"], said["scoreview"]), ("DP-1", ""))

    def test_each_window_is_known_by_the_title_vpx_gives_it(self) -> None:
        capture = VPXCapture()
        for title, expected in (("Visual Pinball Player", "playfield"),
                                ("Visual Pinball Backglass", "backglass"),
                                ("Visual Pinball Score View", "scoreview"),
                                ("Visual Pinball Topper", "topper"),
                                ("VPinFE Table", ""), ("", "")):
            with self.subTest(title):
                self.assertEqual(capture.window("VPinballX_BGFX", title), expected)


class ViewsTests(unittest.TestCase):
    """Full Single Screen is offered for a table that enables it, and only then."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.table = Path(tmp.name) / "Example.vpx"

    def _views(self, enabled: bool | None) -> frozenset[str]:
        with patch("apps.vpx.capture.fss_enabled", return_value=enabled):
            return VPXCapture().views(Entry(table=str(self.table)), {})

    def test_a_table_that_enables_full_single_screen_offers_it(self) -> None:
        self.assertEqual(self._views(True), frozenset({VIEW_FSS}))

    def test_a_table_that_does_not_or_cannot_be_read_offers_nothing(self) -> None:
        self.assertEqual(self._views(False), frozenset())
        self.assertEqual(self._views(None), frozenset())

    def test_an_entry_with_no_file_offers_nothing(self) -> None:
        self.assertEqual(VPXCapture().views(Entry(key="mm"), {}), frozenset())


class PlacedByTests(unittest.TestCase):
    """What VPX reads to decide which windows it shows for a table."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.settings_file = root / "VPinballX.ini"
        self.settings_file.write_text(SHOWN_ON_THREE, encoding="utf-8")
        self.game = root / "Example"
        self.game.mkdir()
        self.table = self.game / "Mod.vpx"
        self.table.write_bytes(b"vpx")

    def _placed_by(self, table: Path | None = None) -> str:
        return VPXCapture().placed_by(Entry(table=str(table or self.table)),
                                      {"ini_path": str(self.settings_file)})

    def _changes(self, change) -> bool:
        before = self._placed_by()
        change()
        return self._placed_by() != before

    def test_the_same_until_something_it_reads_changes(self) -> None:
        self.assertEqual(self._placed_by(), self._placed_by())
        self.assertNotEqual(self._placed_by(self.game / "Other.vpx"), self._placed_by())

    def test_the_settings_file_by_what_it_holds(self) -> None:
        """VPX may write it back unchanged when it exits."""
        later = self.settings_file.stat().st_mtime_ns + 10**9
        self.assertFalse(self._changes(lambda: os.utime(self.settings_file,
                                                        ns=(later, later))))
        self.assertTrue(self._changes(lambda: self.settings_file.write_text(
            SHOWN_ON_THREE.replace("ScoreViewOutput = 1", "ScoreViewOutput = 0"),
            encoding="utf-8")))

    def test_a_file_beside_the_table_that_decides_a_window(self) -> None:
        for name in ("Mod.directb2s", "example.DIRECTB2S", "Mod.scv", "Example.scv",
                     "Mod.nix", "Example.nix", "Mod.ini", "example.ini"):
            with self.subTest(name):
                beside = self.game / name
                self.assertTrue(self._changes(partial(beside.write_bytes, b"x")))
                beside.unlink()

    def test_not_the_files_beside_it_that_decide_nothing(self) -> None:
        for name in ("Example.info", "Other.directb2s", "Mod.vbs"):
            with self.subTest(name):
                self.assertFalse(self._changes(partial((self.game / name).write_bytes, b"x")))


if __name__ == "__main__":
    unittest.main()

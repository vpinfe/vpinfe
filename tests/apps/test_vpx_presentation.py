"""Protects: (a, e) a person's Visual Pinball settings file is changed only on a Mac, and only
where nobody chose a Display Mode: a window they set up themselves stays as they left it.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from apps.vpx import VPX
from apps.vpx import ini as vini
from apps.vpx.presentation import VPXPrepare, unset_windows

BLANK = """[Player]
PlayfieldDisplay =
PlayfieldFullScreen =
PlayfieldWndX =
PlayfieldWndY =
PlayfieldWidth =
PlayfieldHeight =

[Backglass]
BackglassOutput = 1
BackglassDisplay = Second screen
BackglassFullScreen =
BackglassWndX =

[ScoreView]
ScoreViewOutput = 1
ScoreViewDisplay = Second screen
ScoreViewFullScreen =

[Topper]
TopperOutput = 1
TopperDisplay = Third screen
TopperFullScreen =
"""

PLAYFIELD = {"Player.PlayfieldFullScreen": "1"}
ALL = {**PLAYFIELD, "Backglass.BackglassFullScreen": "1",
       "ScoreView.ScoreViewFullScreen": "1", "Topper.TopperFullScreen": "1"}


def _values(path: Path) -> dict[str, str]:
    parsed = vini.parse(path.read_text(encoding="utf-8"))
    return {key: one.value for key, one in parsed.settings.items()}


class UnsetWindowsTests(unittest.TestCase):
    def test_every_window_nobody_set_is_full_screen(self) -> None:
        self.assertEqual(unset_windows(vini.parse(BLANK)), ALL)

    def test_a_file_with_no_window_keys_at_all_still_gets_the_playfield(self) -> None:
        self.assertEqual(unset_windows(vini.parse("[Player]\nPlayfieldDisplay =\n")), PLAYFIELD)

    def test_a_window_that_is_not_floating_is_left_alone(self) -> None:
        for output in ("", "0", "2"):
            with self.subTest(output=output):
                chosen = unset_windows(vini.parse(
                    BLANK.replace("BackglassOutput = 1", f"BackglassOutput = {output}")))

                self.assertNotIn("Backglass.BackglassFullScreen", chosen)

    def test_a_floating_window_with_no_display_is_left_alone(self) -> None:
        chosen = unset_windows(vini.parse(
            BLANK.replace("TopperDisplay = Third screen", "TopperDisplay =")))

        self.assertEqual(set(chosen), set(ALL) - {"Topper.TopperFullScreen"})

    def test_windowed_chosen_on_purpose_stays(self) -> None:
        chosen = unset_windows(vini.parse(BLANK.replace("PlayfieldFullScreen =",
                                                        "PlayfieldFullScreen = 0")))

        self.assertNotIn("Player.PlayfieldFullScreen", chosen)
        self.assertIn("Backglass.BackglassFullScreen", chosen)

    def test_a_window_placed_by_hand_stays_windowed(self) -> None:
        chosen = unset_windows(vini.parse(BLANK.replace("BackglassWndX =", "BackglassWndX = 40")))

        self.assertNotIn("Backglass.BackglassFullScreen", chosen)
        self.assertEqual(set(chosen), set(ALL) - {"Backglass.BackglassFullScreen"})

    def test_full_screen_already_chosen_is_not_written_again(self) -> None:
        text = BLANK.replace("PlayfieldFullScreen =", "PlayfieldFullScreen = 1")

        self.assertNotIn("Player.PlayfieldFullScreen", unset_windows(vini.parse(text)))


class BeforeLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "VPinballX.ini"
        self.settings = {"bin_path": "/opt/vpinball/VPinballX", "ini_path": str(self.path)}

    def _run(self, system: str) -> None:
        VPXPrepare().before_launch(self.settings, system=system)

    def test_on_a_mac_the_blank_windows_are_set_and_the_original_kept(self) -> None:
        self.path.write_text(BLANK, encoding="utf-8")

        self._run("darwin")

        self.assertEqual({key: value for key, value in _values(self.path).items()
                          if key in ALL}, ALL)
        kept = list(self.path.parent.glob("VPinballX.ini.vpinfe-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(encoding="utf-8"), BLANK)

    def test_a_second_launch_writes_nothing(self) -> None:
        self.path.write_text(BLANK, encoding="utf-8")
        self._run("darwin")
        after_first = self.path.read_text(encoding="utf-8")

        self._run("darwin")

        self.assertEqual(self.path.read_text(encoding="utf-8"), after_first)

    def test_other_systems_are_not_touched(self) -> None:
        for system in ("win32", "linux"):
            with self.subTest(system):
                self.path.write_text(BLANK, encoding="utf-8")

                self._run(system)

                self.assertEqual(self.path.read_text(encoding="utf-8"), BLANK)
                self.assertEqual(list(self.path.parent.glob("*.vpinfe-*")), [])

    def test_a_named_settings_file_not_there_yet_is_made_with_the_windows_set(self) -> None:
        self._run("darwin")

        self.assertEqual(_values(self.path), PLAYFIELD)

    def test_with_no_file_named_the_newest_versions_folder_gets_it(self) -> None:
        self.settings = {"bin_path": "/opt/vpinball/VPinballX"}
        base = Path(self._tmp.name) / "VPinballX"

        with mock.patch("apps.vpx.config._machine_folders", return_value=(base, None)):
            self._run("darwin")

        self.assertEqual(_values(base / "10.8" / "VPinballX.ini"), PLAYFIELD)

    def test_the_other_settings_and_comments_are_left_as_they_were(self) -> None:
        text = "; Full Screen\n" + BLANK.replace("[Player]", "[Player]\nAAFactor = 2")
        self.path.write_text(text, encoding="utf-8")

        self._run("darwin")

        written = self.path.read_text(encoding="utf-8")
        self.assertIn("; Full Screen\n", written)
        self.assertIn("AAFactor = 2", written)
        self.assertEqual(_values(self.path)["Player.PlayfieldDisplay"], "")


class RegisteredTests(unittest.TestCase):
    def test_visual_pinball_prepares_its_launch(self) -> None:
        self.assertIsInstance(VPX.prepare, VPXPrepare)


if __name__ == "__main__":
    unittest.main()

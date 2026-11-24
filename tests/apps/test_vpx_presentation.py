"""Protects: (a, e) a person's Visual Pinball settings file is given a Display Mode only on a
Mac, and only where nobody chose one: a window they set up themselves stays as they left it.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.vpx import ini as vini
from apps.vpx.presentation import first_setup_windows, unset_windows

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


class FirstSetupWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "VPinballX.ini"
        self.settings = {"bin_path": "/opt/vpinball/VPinballX", "ini_path": str(self.path)}

    def test_on_a_mac_the_windows_nobody_set_are_named_and_the_file_is_not_touched(self) -> None:
        self.path.write_text(BLANK, encoding="utf-8")

        self.assertEqual(first_setup_windows(self.settings, system="darwin"), ALL)
        self.assertEqual(self.path.read_text(encoding="utf-8"), BLANK)

    def test_a_mode_the_person_chose_is_not_named(self) -> None:
        self.path.write_text(BLANK.replace("PlayfieldFullScreen =", "PlayfieldFullScreen = 0"),
                             encoding="utf-8")

        self.assertNotIn("Player.PlayfieldFullScreen",
                         first_setup_windows(self.settings, system="darwin"))

    def test_other_systems_are_given_nothing(self) -> None:
        self.path.write_text(BLANK, encoding="utf-8")

        for system in ("win32", "linux"):
            with self.subTest(system):
                self.assertEqual(first_setup_windows(self.settings, system=system), {})

    def test_a_settings_file_not_there_yet_still_gets_the_playfield(self) -> None:
        self.assertEqual(first_setup_windows(self.settings, system="darwin"), PLAYFIELD)


if __name__ == "__main__":
    unittest.main()

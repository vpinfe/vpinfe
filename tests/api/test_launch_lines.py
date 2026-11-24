"""Protects: (c) a game's launch tile never says a file is used when the launch skips it.

The lines on a game's Loading Video, Loading Image and Launch Audio tiles are worked out from
the same ladder a launch walks, so what a person reads is what a launch does.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import loading_choices as choices
from common.media import launch_lines, launch_media

THEME = launch_lines.Theme(name="Neon", video="loading.mp4", audio="hum.mp3")
NOTHING: dict[str, bool] = {}
TABLE = {"loading": True, "loading_image": True, "audio_launch": True}


class LaunchLinesTests(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        patch = mock.patch.object(launch_media.paths, "LAUNCH_MEDIA_DIR", Path(held.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_game_s_own_file_under_the_starting_values_says_nothing(self) -> None:
        found = launch_lines.lines({}, THEME, TABLE)

        self.assertEqual((found["loading"], found["audio_launch"]), ("", ""))

    def test_a_picture_the_video_comes_before_says_so(self) -> None:
        found = launch_lines.lines({}, THEME, TABLE)

        self.assertIn("comes first", found["loading_image"])

    def test_an_empty_slot_says_what_it_gets_instead(self) -> None:
        found = launch_lines.lines({"loading_screen": "theme_video"}, THEME, NOTHING)

        self.assertIn("Neon", found["loading"])
        self.assertIn("Plays", found["loading"])

    def test_a_file_set_aside_by_a_choice_for_every_table_says_so(self) -> None:
        found = launch_lines.lines({"loading_screen": "theme_video"}, THEME, TABLE)

        self.assertIn("Not used", found["loading"])

    def test_a_theme_that_draws_its_own_says_so_on_all_three(self) -> None:
        own = launch_lines.Theme(name="Neon", own=True)

        found = launch_lines.lines({}, own, TABLE)

        self.assertEqual(len(set(found.values())), 1)
        self.assertIn("Neon", found["loading"])

    def test_a_spinner_chosen_as_the_fallback_ends_the_ladder_there(self) -> None:
        found = launch_lines.lines(
            {"loading_screen": "table_video", "loading_screen_fallback": "spinner"},
            THEME, NOTHING)

        self.assertEqual(choices.ladder("table_video", "spinner"), ["table_video", "spinner"])
        self.assertIn("spinner", found["loading"])

    def test_muted_audio_says_the_launch_audio_is_off(self) -> None:
        found = launch_lines.lines({"mute_audio": "true"}, THEME, TABLE)

        self.assertIn("Mute", found["audio_launch"])

    def test_the_sound_off_says_it_is_not_played(self) -> None:
        found = launch_lines.lines({"launch_audio": "off"}, THEME, TABLE)

        self.assertIn("Off", found["audio_launch"])


if __name__ == "__main__":
    unittest.main()

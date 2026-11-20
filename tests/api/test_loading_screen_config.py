"""Protects: (e) the loading screen is only asked for where a window can show it.

On Windows the frontend is minimized for a launch unless Experimental Table Focus is on,
so a screen and a sound there would play to nobody. What the launch message lists is also
what the settings say: a file named by a theme or added by the person is only offered
while it is there.
"""

from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.media import launch_media
from frontend import config_api

THEME_FILES = {"video": "loading.mp4", "image": "", "audio": "hum.mp3"}
TABLE_VIDEO = {"from": "table", "media": "loading", "kind": "video"}
TABLE_IMAGE = {"from": "table", "media": "loading_image", "kind": "image"}
TABLE_PLAYFIELD = {"from": "table", "media": "playfield", "kind": "image"}
TABLE_SOUND = {"from": "table", "media": "audio_launch"}


def _config(**behavior: str) -> configparser.ConfigParser:
    parsed = configparser.ConfigParser()
    parsed["behavior"] = behavior
    return parsed


class LoadingScreenConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for patch in (
                mock.patch.object(launch_media.paths, "LAUNCH_MEDIA_DIR", self.folder),
                mock.patch.object(config_api, "_theme_loading",
                                  return_value=("Neon Nights", THEME_FILES))):
            patch.start()
            self.addCleanup(patch.stop)

    def _on(self, platform: str, **behavior: str) -> dict:
        with mock.patch.object(config_api.sys, "platform", platform):
            return config_api.get_loading_screen_config(_config(**behavior))

    def test_the_starting_values_try_the_table_s_video_image_then_playfield(self) -> None:
        found = self._on("linux")

        self.assertEqual(found, {"mode": "video", "repeat": False, "audio": [TABLE_SOUND],
                                 "rungs": [TABLE_VIDEO, TABLE_IMAGE, TABLE_PLAYFIELD]})

    def test_the_choices_are_read_as_set(self) -> None:
        found = self._on("linux", loading_screen="table_playfield",
                         loading_screen_fallback="spinner", loading_screen_ends="repeat",
                         launch_audio="off")

        self.assertEqual(found, {"mode": "image", "repeat": True, "audio": [],
                                 "rungs": [TABLE_PLAYFIELD]})

    def test_what_is_not_a_choice_is_the_starting_value(self) -> None:
        self.assertEqual(self._on("linux", loading_screen="Nonsense")["mode"], "video")

    def test_off_and_the_spinner_list_nothing_to_show(self) -> None:
        self.assertEqual(self._on("linux", loading_screen="off")["rungs"], [])
        found = self._on("linux", loading_screen="spinner")
        self.assertEqual((found["mode"], found["rungs"]), ("spinner", []))

    def test_a_theme_file_is_served_from_the_theme_and_turned_for_a_cabinet(self) -> None:
        found = self._on("linux", loading_screen="theme_video",
                         loading_screen_fallback="table_video")

        self.assertEqual(found["rungs"][0], {"url": "/themes/Neon%20Nights/loading.mp4",
                                              "kind": "video", "turn": True})
        self.assertEqual(found["rungs"][1], TABLE_VIDEO)

    def test_a_theme_with_no_file_of_the_kind_drops_out(self) -> None:
        found = self._on("linux", loading_screen="theme_image",
                         loading_screen_fallback="table_image")

        self.assertEqual(found["rungs"], [TABLE_IMAGE, TABLE_PLAYFIELD])

    def test_a_file_the_person_added_is_offered_only_while_it_is_there(self) -> None:
        settings = {"loading_screen": "my_video", "loading_video_file": "intro.mp4"}
        self.assertEqual(self._on("linux", **settings)["rungs"], [TABLE_PLAYFIELD])

        (self.folder / "intro.mp4").write_bytes(b"x")
        self.assertEqual(self._on("linux", **settings)["rungs"][0],
                         {"url": "/launch_media/intro.mp4", "kind": "video"})

    def test_the_launch_audio_lists_the_table_s_then_the_next_named(self) -> None:
        found = self._on("linux", launch_audio="table", launch_audio_fallback="theme")

        self.assertEqual(found["audio"], [TABLE_SOUND, {"url": "/themes/Neon%20Nights/hum.mp3"}])

    def test_windows_without_table_focus_gets_the_spinner_and_no_sound(self) -> None:
        found = self._on("win32", loading_screen="table_video")

        self.assertEqual((found["mode"], found["rungs"], found["audio"]), ("spinner", [], []))

    def test_windows_with_table_focus_gets_what_was_set(self) -> None:
        found = self._on("win32", loading_screen="table_video", experimental_table_focus="true")

        self.assertEqual((found["mode"], found["audio"]), ("video", [TABLE_SOUND]))


if __name__ == "__main__":
    unittest.main()

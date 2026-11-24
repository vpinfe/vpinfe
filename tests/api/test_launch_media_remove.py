"""Protects: (a) a file added for the loading screen is never lost without a copy.

Removing one takes it out of the folder and leaves a copy in Backups. A file a setting still
plays is refused, so a launch never points at a file that is gone.
"""

from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common import service_errors
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.media import launch_media


def _config(**behavior: str) -> configparser.ConfigParser:
    parsed = configparser.ConfigParser()
    parsed["behavior"] = behavior
    return parsed


class RemoveLaunchMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        kept = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.addCleanup(kept.cleanup)
        self.folder, self.backups = Path(held.name), Path(kept.name)
        for patch in (mock.patch.object(launch_media.paths, "LAUNCH_MEDIA_DIR", self.folder),
                      mock.patch.object(backup_folder, "for_writing",
                                        return_value=self.backups)):
            patch.start()
            self.addCleanup(patch.stop)
        (self.folder / "intro.mp4").write_bytes(b"video")

    def test_a_removed_file_leaves_a_copy_in_backups(self) -> None:
        kept = backup_media.remove_launch("intro.mp4", _config())

        self.assertFalse((self.folder / "intro.mp4").exists())
        self.assertEqual(kept.read_bytes(), b"video")
        self.assertTrue(kept.is_relative_to(self.backups))

    def test_a_file_the_loading_screen_plays_cannot_go(self) -> None:
        config = _config(loading_screen="my_video", loading_video_file="intro.mp4")

        with self.assertRaises(service_errors.RefusedError):
            backup_media.remove_launch("intro.mp4", config)

        self.assertTrue((self.folder / "intro.mp4").exists())

    def test_a_chosen_file_nothing_plays_can_go(self) -> None:
        config = _config(loading_screen="spinner", loading_screen_fallback="table_image",
                         loading_video_file="intro.mp4")

        backup_media.remove_launch("intro.mp4", config)

        self.assertFalse((self.folder / "intro.mp4").exists())

    def test_a_name_outside_the_folder_is_not_found(self) -> None:
        (self.folder.parent / "outside.mp4").write_bytes(b"video")
        self.addCleanup((self.folder.parent / "outside.mp4").unlink)

        with self.assertRaises(service_errors.NotFoundError):
            backup_media.remove_launch("../outside.mp4", _config())

        self.assertTrue((self.folder.parent / "outside.mp4").exists())


if __name__ == "__main__":
    unittest.main()

"""Protects: (b) a theme's loading files and its own-screen choice are read as the contract says.

A theme names a loading video, image and sound in `config.json`, and a theme that says nothing
about its own loading screen gets core's from its contract.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from common.media import theme_loading
from common.online import theme_service


def _theme(*names: str) -> tuple[tempfile.TemporaryDirectory, Path]:
    held = tempfile.TemporaryDirectory()
    root = Path(held.name)
    for name in names:
        target = root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    return held, root


class ThemeFilesTests(unittest.TestCase):
    def test_a_file_the_theme_names_and_has_is_used(self) -> None:
        held, root = _theme("media/loading.mp4")
        self.addCleanup(held.cleanup)

        found = theme_loading.files(root, {"loading_screen": {"video": "media/loading.mp4"}})

        self.assertEqual(found["video"], "media/loading.mp4")

    def test_a_name_that_leaves_the_folder_is_no_name(self) -> None:
        held, root = _theme()
        self.addCleanup(held.cleanup)
        (root.parent / "outside.mp4").write_bytes(b"x")
        self.addCleanup((root.parent / "outside.mp4").unlink)

        found = theme_loading.files(root, {"loading_screen": {"video": "../outside.mp4"}})

        self.assertEqual(found["video"], "")

    def test_a_name_of_the_wrong_kind_is_no_name(self) -> None:
        held, root = _theme("loading.png")
        self.addCleanup(held.cleanup)

        found = theme_loading.files(root, {"loading_screen": {"video": "loading.png"}})

        self.assertEqual(found["video"], "")

    def test_a_drive_letter_is_no_name(self) -> None:
        held, root = _theme()
        self.addCleanup(held.cleanup)

        found = theme_loading.files(root, {"loading_screen": {"image": "C:/loading.png"}})

        self.assertEqual(found["image"], "")


class DrawsItsOwnTests(unittest.TestCase):
    def test_a_theme_that_says_nothing_follows_its_contract(self) -> None:
        self.assertTrue(theme_loading.draws_its_own({}, on_by_default=False))
        self.assertFalse(theme_loading.draws_its_own({}, on_by_default=True))

    def test_what_the_theme_states_wins_over_its_contract(self) -> None:
        on = {"loading_screen": {"enabled": True}}
        off = {"loading_screen": {"enabled": False}}

        self.assertFalse(theme_loading.draws_its_own(on, on_by_default=False))
        self.assertTrue(theme_loading.draws_its_own(off, on_by_default=True))


class OptionValuesTests(unittest.TestCase):
    def test_an_option_value_is_laid_over_the_config_at_its_dotted_path(self) -> None:
        merged = theme_service._with_option_values(
            {"loading_screen": {"enabled": False, "video": "a.mp4"}},
            {"loading_screen.enabled": True, "other.deep.key": 1})

        self.assertEqual(merged["loading_screen"], {"enabled": True, "video": "a.mp4"})
        self.assertEqual(merged["other"], {"deep": {"key": 1}})


if __name__ == "__main__":
    unittest.main()

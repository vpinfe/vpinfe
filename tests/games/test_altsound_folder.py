"""Where a game keeps AltSound: beside the table, or in the older folder under pinmame."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.games import asset_lens, asset_ops, game_lens
from common.games.asset_registry import altsound_folder
from common.games.game import Game
from common.games.game_parser import GameParser
from common.uploads.asset_import_service import folder_for


def _game(tmp: str, *folders: str) -> Path:
    root = Path(tmp) / "Cactus Canyon (Bally 1998)"
    root.mkdir()
    (root / "Cactus Canyon.vpx").write_bytes(b"vpx")
    (root / f"{root.name}.info").write_text(json.dumps({
        "Info": {"Title": "Cactus Canyon"},
        "VPXFile": {"filename": "Cactus Canyon.vpx"},
    }), encoding="utf-8")
    for folder in folders:
        (root / folder).mkdir(parents=True)
    return root


def _subdirs(root: Path) -> list[str]:
    return [one.name for one in root.iterdir() if one.is_dir()]


class FolderTests(unittest.TestCase):
    def test_the_folder_beside_the_table_is_found(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, "altsound/cc_13")
            self.assertEqual(altsound_folder(root, _subdirs(root)), "altsound")

    def test_the_older_folder_still_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, "pinmame/altsound/cc_13")
            self.assertEqual(altsound_folder(root, _subdirs(root)), "pinmame/altsound")

    def test_the_folder_beside_the_table_wins_when_both_exist(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, "altsound/cc_13", "pinmame/altsound/cc_13")
            self.assertEqual(altsound_folder(root, _subdirs(root)), "altsound")

    def test_a_folder_named_in_capitals_is_found_under_its_own_name(self) -> None:
        self.assertEqual(altsound_folder(Path("/nowhere"), ["AltSound", "medias"]),
                         "AltSound")

    def test_a_game_with_neither_has_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, "pinmame/roms")
            self.assertEqual(altsound_folder(root, _subdirs(root)), "")

    def test_an_import_is_placed_beside_the_table(self) -> None:
        self.assertEqual(folder_for("altsound", "/g", "cc_13"), Path("/g/altsound/cc_13"))


class ReadersTests(unittest.TestCase):
    def test_the_scan_reports_either_folder(self) -> None:
        for folder in ("altsound/cc_13", "pinmame/altsound/cc_13"):
            with self.subTest(folder=folder), TemporaryDirectory() as tmp:
                _game(tmp, folder)
                self.assertTrue(GameParser(tmp).get_all_games()[0].alt_sound_exists)

    def test_the_scan_reports_none_for_a_game_without(self) -> None:
        with TemporaryDirectory() as tmp:
            _game(tmp, "pinmame/roms")
            self.assertFalse(GameParser(tmp).get_all_games()[0].alt_sound_exists)

    def test_the_game_resource_reports_either_folder(self) -> None:
        for folder in ("altsound/cc_13", "pinmame/altsound/cc_13"):
            with self.subTest(folder=folder), TemporaryDirectory() as tmp:
                root = _game(tmp, folder)
                self.assertTrue(game_lens.inventory_assets(root)["alt_sound"]["present"])

    def test_the_asset_lens_names_the_folder_it_found(self) -> None:
        for folder, named in (("altsound/cc_13", "altsound"),
                              ("pinmame/altsound/cc_13", "pinmame/altsound")):
            with self.subTest(folder=folder), TemporaryDirectory() as tmp:
                root = _game(tmp, folder)
                self.assertEqual(asset_lens.whole_folders(root), {named})


class RemoveTests(unittest.TestCase):
    def _remove(self, root: Path, path: str) -> None:
        game = Game()
        game.full_path_game = str(root)
        game.game_dir_name = root.name
        with mock.patch.object(asset_ops.game_lens, "game_or_refuse", return_value=game):
            asset_ops.remove("g1", path)

    def test_each_folder_of_a_game_with_both_can_be_removed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, "altsound/cc_13", "pinmame/altsound/cc_13")
            self._remove(root, "altsound")
            self.assertEqual(asset_lens.whole_folders(root), {"pinmame/altsound"})
            self._remove(root, "pinmame/altsound")
            self.assertEqual(asset_lens.whole_folders(root), set())

    def test_a_folder_named_in_capitals_is_removed_under_its_own_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, "AltSound/cc_13")
            self._remove(root, "AltSound")
            self.assertFalse((root / "AltSound").exists())


if __name__ == "__main__":
    unittest.main()

"""Removing one ROM file from a game's pinmame/roms folder, and only there."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common import service_errors
from common.games import asset_ops
from common.games.game import Game


def _game(tmp: str, files: dict[str, bytes]) -> Path:
    root = Path(tmp) / "Cactus Canyon (Bally 1998)"
    root.mkdir()
    (root / "Cactus Canyon.vpx").write_bytes(b"vpx")
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(body)
    return root


class RemovingAROMFile(unittest.TestCase):
    def _remove(self, root: Path, path: str) -> None:
        game = Game()
        game.full_path_game = str(root)
        game.game_dir_name = root.name
        with mock.patch.object(asset_ops.game_lens, "game_or_refuse", return_value=game):
            asset_ops.remove("g1", path)

    def test_a_file_in_the_roms_folder_goes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, {"pinmame/roms/cc_13.zip": b"z", "pinmame/roms/cc_12.zip": b"z"})

            self._remove(root, "pinmame/roms/cc_13.zip")

            self.assertEqual(["cc_12.zip"], [one.name for one in (root / "pinmame/roms").iterdir()])

    def test_a_folder_named_in_capitals_still_counts_as_the_roms_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, {"PinMAME/ROMs/cc_13.zip": b"z"})

            self._remove(root, "PinMAME/ROMs/cc_13.zip")

            self.assertFalse((root / "PinMAME/ROMs/cc_13.zip").exists())

    def test_a_file_elsewhere_in_pinmame_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, {"pinmame/nvram/cc_13.nv": b"n"})

            with self.assertRaises(service_errors.RefusedError):
                self._remove(root, "pinmame/nvram/cc_13.nv")

            self.assertTrue((root / "pinmame/nvram/cc_13.nv").exists())

    def test_a_file_in_a_folder_inside_the_roms_folder_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, {"pinmame/roms/extra/readme.txt": b"t"})

            with self.assertRaises(service_errors.RefusedError):
                self._remove(root, "pinmame/roms/extra/readme.txt")

    def test_the_roms_folder_itself_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _game(tmp, {"pinmame/roms/cc_13.zip": b"z"})

            with self.assertRaises(service_errors.RefusedError):
                self._remove(root, "pinmame/roms")

            self.assertTrue((root / "pinmame/roms/cc_13.zip").exists())


if __name__ == "__main__":
    unittest.main()

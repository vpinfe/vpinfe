"""A file a game's folder holds is addressed by its path there, and shown where it is."""

from __future__ import annotations

import unittest
from pathlib import Path

from manager.workbench import game_media


class AGameFileSaysWhereItIs(unittest.TestCase):
    def test_the_path_is_joined_to_the_games_folder(self) -> None:
        context = {"game": {"folder": "/library/Alpha"}}
        self.assertEqual(str(Path("/library/Alpha") / "medias/table.png"),
                         game_media._on_disk(context, "medias/table.png"))

    def test_nothing_is_said_without_a_folder_or_a_path(self) -> None:
        self.assertEqual("", game_media._on_disk({"game": {"folder": ""}}, "table.png"))
        self.assertEqual("", game_media._on_disk({"game": {"folder": "/library/Alpha"}}, ""))
        self.assertEqual("", game_media._on_disk({}, "table.png"))


if __name__ == "__main__":
    unittest.main()

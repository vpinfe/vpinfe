"""Each table in a game's folder answers for its own ROM."""

from __future__ import annotations

from common.games import table_lens
from common.games.game_parser import GameParser
from tests.support.library import TempTree, write_game

FOLDER = "Example (Bally 1990)"
WITH, WITHOUT = "Example.vpx", "Example Original.vpx"


class TableRomTests(TempTree):
    def test_a_table_carries_the_rom_its_script_names_and_one_without_carries_none(
            self) -> None:
        folder = write_game(self.root, FOLDER, info={
            "Info": {"Title": "Example", "Manufacturer": "Bally", "Year": "1990"},
            "vpinfe": {"schema": 2, "game_id": "Game00000001"},
            "tables": {
                "tblRom000001": {"id": "tblRom000001", "filename": WITH, "rom": "ex_l1",
                                 "added": "2024-01-01T00:00:00Z"},
                "tblNone00001": {"id": "tblNone00001", "filename": WITHOUT,
                                 "added": "2025-01-01T00:00:00Z"},
            },
        }, vpx=False, files={WITH: b"a", WITHOUT: b"b"})
        (game,) = GameParser(str(self.root)).get_all_games()

        rows = {row["filename"]: row["rom"]
                for row in table_lens.table_rows(game, {"game_dir": str(folder)})}

        self.assertEqual(rows, {WITH: "ex_l1", WITHOUT: ""})

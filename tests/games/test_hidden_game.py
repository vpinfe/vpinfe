"""A game is hidden when it has hidden tables and none left to offer."""

from __future__ import annotations

import copy

from common.games import game_lens
from common.games.game_parser import GameParser
from common.games.game_repository import game_to_row
from common.games.tables import ABSENT_SINCE_KEY
from httpapi.models import GameResource
from tests.support.library import TempTree, write_game

GAME_ID = "Game00000001"
INFO = {
    "Info": {"Title": "Example", "Manufacturer": "Bally", "Year": "1990"},
    "vpinfe": {"schema": 2, "game_id": GAME_ID},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": "Alpha.vpx"},
        "tbl0000002": {"id": "tbl0000002", "filename": "Bravo.vpx", "hidden": True},
    },
}


class HiddenGameTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, "Example (Bally 1990)", info=copy.deepcopy(INFO), vpx=False,
                   files={"Alpha.vpx": b"vpx", "Bravo.vpx": b"vpx"})
        (self.game,) = GameParser(str(self.root)).get_all_games()
        assert self.game.meta_config is not None
        self.tables = self.game.meta_config["tables"]
        self.alpha = self.tables["tbl0000001"]

    def _listed(self) -> GameResource:
        return GameResource.model_validate(
            game_lens.game_resource(game_to_row(self.game), GAME_ID))

    def test_a_game_with_a_table_left_to_offer_is_not_hidden(self) -> None:
        self.assertFalse(self._listed().hidden)

    def test_a_game_with_every_table_hidden_is(self) -> None:
        self.alpha["hidden"] = True

        self.assertTrue(self._listed().hidden)

    def test_a_game_whose_other_table_is_gone_is(self) -> None:
        self.alpha[ABSENT_SINCE_KEY] = "2026-01-01T00:00:00Z"

        self.assertTrue(self._listed().hidden)

    def test_a_game_whose_tables_are_all_gone_is_missing_them_not_hiding_them(self) -> None:
        for table in self.tables.values():
            table.pop("hidden", None)
            table[ABSENT_SINCE_KEY] = "2026-01-01T00:00:00Z"

        self.assertFalse(self._listed().hidden)

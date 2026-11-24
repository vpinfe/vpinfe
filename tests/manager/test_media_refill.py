"""A Media fill puts the filled games' rows back in place instead of drawing the page again."""

from __future__ import annotations

import asyncio
import unittest

from manager.kit import grid
from manager.library import media


def _gap(game: str, kind: str) -> dict:
    return {"id": f"{game}:{kind}:", "game_id": game, "game": game.upper(), "kind": kind,
            "present": False}


def _file(game: str, kind: str) -> dict:
    return {"id": f"{game}:{kind}:{kind}.png", "game_id": game, "game": game.upper(),
            "kind": kind, "present": True, "path": f"{kind}.png"}


class _Library:
    def __init__(self, found: list[dict]) -> None:
        self.found = found
        self.forgot: list[str] = []
        self.loads = 0

    def forget_media(self, game_id: str) -> None:
        self.forgot.append(game_id)

    def load_media_rows(self) -> list[dict]:
        self.loads += 1
        return self.found

    def media_rows(self) -> list[dict]:
        return []


class _Grid:
    id = 7
    is_deleted = False

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def run_grid_method(self, *call: object) -> None:
        self.sent.append(call)


class RefillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.held = media.rows([_gap("g1", "wheel"), _gap("g1", "backglass"),
                                _gap("g2", "wheel")])
        self.by_id = {row["id"]: row for row in self.held}
        self.chosen = grid.Selection(self.held)
        self.chosen.take({"turn": 1, "at": 0, "of": 1,
                          "ids": ["g1:wheel:", "g1:backglass:", "g2:wheel:"]})
        self.library = _Library([_file("g1", "wheel"), _gap("g1", "backglass"),
                                 _gap("g2", "wheel")])
        self.table = _Grid()

    def _refill(self, *games: str) -> None:
        asyncio.run(media.refill(self.library, self.table, self.held, self.by_id,
                                 list(games)))

    def test_the_grid_gets_one_transaction_for_the_filled_game(self) -> None:
        self._refill("g1")

        self.assertEqual([call[0] for call in self.table.sent].count("applyTransaction"), 1)
        name, transaction = self.table.sent[0]
        self.assertEqual(name, "applyTransaction")
        self.assertEqual([row["id"] for row in transaction["remove"]], ["g1:wheel:"])
        self.assertEqual([row["id"] for row in transaction["add"]], ["g1:wheel:wheel.png"])
        self.assertEqual([row["id"] for row in transaction["update"]], ["g1:backglass:"])

    def test_the_rest_of_the_selection_is_still_selected(self) -> None:
        self._refill("g1")

        self.assertEqual([row["id"] for row in self.chosen.rows()],
                         ["g1:backglass:", "g2:wheel:"])

    def test_a_filled_row_reads_as_filled(self) -> None:
        self._refill("g1")

        filled = self.by_id["g1:wheel:wheel.png"]
        self.assertTrue(filled["present"])
        self.assertEqual(filled["reason"], "in_use")
        self.assertNotIn("g1:wheel:", self.by_id)

    def test_the_cache_is_dropped_for_each_game_and_read_once(self) -> None:
        self.library.found = self.library.found + [_file("g2", "wheel")]

        self._refill("g1", "g2")

        self.assertEqual(self.library.forgot, ["g1", "g2"])
        self.assertEqual(self.library.loads, 1)

    def test_what_is_placed_is_what_the_read_answered(self) -> None:
        self._refill("g1")

        self.assertIn("g1:wheel:wheel.png", self.by_id)

    def test_a_page_that_has_gone_is_left_alone(self) -> None:
        self.table.is_deleted = True

        self._refill("g1")

        self.assertEqual(self.table.sent, [])
        self.assertEqual(self.library.forgot, ["g1"])


if __name__ == "__main__":
    unittest.main()

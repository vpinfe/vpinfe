"""Putting one game's rows back on screen after a write, without rebuilding the page.

Rebuilding reads as the grid flashing: scroll position, focus and the open panel all go,
for a write that touched one game. AG Grid is already told the row's id, so a transaction
leaves all three alone - what this has to get right is which of add, update and remove
each row needs, and that nothing belonging to another game is touched.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from common.i18n import t
from manager.kit import grid, stars
from manager.library.games import add_index, row_transaction


def _showing(*pairs):
    return {row_id: {"id": row_id, "game_id": game} for row_id, game in pairs}


def _fresh(*pairs):
    return [{"id": row_id, "game_id": game} for row_id, game in pairs]


class _Grid:
    id = 7

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def run_grid_method(self, name: str, *args: object) -> None:
        self.sent.append((name, *args))


class RowTransactionTests(unittest.TestCase):
    def test_a_table_that_arrived_is_added(self) -> None:
        got = row_transaction(_showing(), "g1", _fresh(("t1", "g1")))

        self.assertEqual([row["id"] for row in got["add"]], ["t1"])
        self.assertNotIn("update", got)
        self.assertNotIn("remove", got)

    def test_one_that_changed_is_updated(self) -> None:
        got = row_transaction(_showing(("t1", "g1")), "g1", _fresh(("t1", "g1")))

        self.assertEqual([row["id"] for row in got["update"]], ["t1"])
        self.assertNotIn("add", got)

    def test_one_that_went_is_removed(self) -> None:
        got = row_transaction(_showing(("t1", "g1")), "g1", [])

        self.assertEqual(got["remove"], [{"id": "t1"}])

    def test_a_write_can_do_two_at_once(self) -> None:
        """Giving a game its first table does exactly this: a row arrives and the
        default moves off whatever held it."""
        got = row_transaction(_showing(("t1", "g1")), "g1",
                              _fresh(("t1", "g1"), ("t2", "g1")))

        self.assertEqual([row["id"] for row in got["add"]], ["t2"])
        self.assertEqual([row["id"] for row in got["update"]], ["t1"])

    def test_another_game_s_rows_are_left_alone(self) -> None:
        """The one that would empty the grid: every row on screen that this write did
        not touch is not a removal."""
        got = row_transaction(_showing(("t1", "g1"), ("t9", "g2")), "g1",
                              _fresh(("t1", "g1")))

        self.assertNotIn("remove", got)
        self.assertEqual([row["id"] for row in got["update"]], ["t1"])

    def test_nothing_to_say_says_nothing(self) -> None:
        """An empty transaction is not sent, so a write that changed nothing does not
        make the grid do work."""
        self.assertEqual(row_transaction(_showing(("t9", "g2")), "g1", []), {})


class InPlaceTests(unittest.TestCase):
    """Where an added row goes, and the rows the count reads once it has."""

    BUILT = _fresh(("a1", "ga"), ("b1", "gb"), ("b2", "gb"), ("c1", "gc"))

    def _apply(self, fresh):
        showing = {row["id"]: row for row in self.BUILT}
        transaction = row_transaction(showing, "gb", fresh)
        built = list(self.BUILT)
        add_index(built, "gb", transaction)
        grid.transact(_Grid(), built, transaction)
        return transaction, built

    def test_an_added_row_follows_its_game_s_rows(self) -> None:
        transaction, built = self._apply(_fresh(("b1", "gb"), ("b2", "gb"), ("b3", "gb")))

        self.assertEqual(transaction["addIndex"], 3)
        self.assertEqual([row["id"] for row in built], ["a1", "b1", "b2", "b3", "c1"])

    def test_a_removal_leaves_the_rest_in_order(self) -> None:
        transaction, built = self._apply(_fresh(("b2", "gb")))

        self.assertNotIn("addIndex", transaction)
        self.assertEqual([row["id"] for row in built], ["a1", "b2", "c1"])

    def test_an_add_and_a_removal_at_once_count_what_stayed(self) -> None:
        transaction, built = self._apply(_fresh(("b2", "gb"), ("b3", "gb")))

        self.assertEqual(transaction["addIndex"], 2)
        self.assertEqual([row["id"] for row in built], ["a1", "b2", "b3", "c1"])


class TransactTests(unittest.TestCase):
    """The rows a grid holds, which a selection is read against, follow its screen."""

    def setUp(self) -> None:
        self.held: list[dict[str, Any]] = [
            {"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}]
        self.by_id: dict[str, Any] = {row["id"]: row for row in self.held}
        self.screen = _Grid()

    def test_an_edited_row_is_held_as_edited(self) -> None:
        grid.transact(self.screen, self.held, {"update": [{"id": "b", "v": 2}]}, self.by_id)

        self.assertEqual([row["v"] for row in self.held], [1, 2, 1])
        self.assertIs(self.by_id["b"], self.held[1])

    def test_a_removed_row_is_not_held(self) -> None:
        grid.transact(self.screen, self.held, {"remove": [{"id": "a"}]}, self.by_id)

        self.assertEqual([row["id"] for row in self.held], ["b", "c"])
        self.assertNotIn("a", self.by_id)

    def test_an_add_goes_where_the_screen_puts_it(self) -> None:
        grid.transact(self.screen, self.held, {"add": [{"id": "d"}], "addIndex": 1})
        grid.transact(self.screen, self.held, {"add": [{"id": "e"}]})

        self.assertEqual([row["id"] for row in self.held], ["a", "d", "b", "c", "e"])

    def test_the_screen_is_sent_the_same_transaction(self) -> None:
        transaction = {"update": [{"id": "c", "v": 3}]}
        grid.transact(self.screen, self.held, transaction)

        self.assertEqual(self.screen.sent[0], ("applyTransaction", transaction))

    def test_an_updated_row_is_redrawn_and_only_that_row(self) -> None:
        grid.transact(self.screen, self.held, {"update": [{"id": "c", "v": 3}]})

        (name, options), = self.screen.sent[1:]
        self.assertEqual(name, "refreshCells")
        self.assertTrue(options["force"])
        self.assertIn('["c"]', options[":rowNodes"])
        self.assertIn("getElement(7)", options[":rowNodes"])

    def test_an_id_with_a_quote_or_backslash_reaches_the_script_escaped(self) -> None:
        awkward = 'a"b\\c'
        self.held.append({"id": awkward, "v": 0})

        grid.transact(self.screen, self.held, {"update": [{"id": awkward, "v": 1}]})

        (_, options), = self.screen.sent[1:]
        self.assertIn(json.dumps([awkward]), options[":rowNodes"])

    def test_a_removal_an_add_or_an_empty_update_redraws_nothing(self) -> None:
        grid.transact(self.screen, self.held, {"remove": [{"id": "a"}]})
        grid.transact(self.screen, self.held, {"add": [{"id": "d"}]})
        grid.transact(self.screen, self.held, {"update": []})

        self.assertEqual([name for name, *_ in self.screen.sent],
                         ["applyTransaction"] * 3)


class MovedTests(unittest.TestCase):
    def _replace(self, held: list[dict], fresh: list[dict], game: str | None = None):
        by_id = {row["id"]: row for row in held}
        screen = _Grid()
        grid.replace_rows(screen, held, by_id, fresh,
                          lambda row: game is None or row.get("game_id") == game)
        return screen, by_id

    def test_an_update_whose_place_changed_moves(self) -> None:
        held = [{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}]

        screen, by_id = self._replace(held, [{"id": "c", "v": 2}, {"id": "a", "v": 1},
                                             {"id": "b", "v": 1}])

        self.assertEqual([row["id"] for row in held], ["c", "a", "b"])
        (method, option, rows), (redrawn, options) = screen.sent
        self.assertEqual((redrawn, options), ("refreshCells", {"force": True}))
        self.assertEqual((method, option), ("setGridOption", "rowData"))
        self.assertEqual([row["id"] for row in rows], ["c", "a", "b"])
        self.assertEqual(by_id["c"]["v"], 2)

    def test_rows_another_game_holds_keep_their_slots(self) -> None:
        held = _fresh(("a1", "ga"), ("b1", "gb"), ("a2", "ga"))

        self._replace(held, _fresh(("a2", "ga"), ("a1", "ga")), game="ga")

        self.assertEqual([row["id"] for row in held], ["a2", "b1", "a1"])

    def test_a_move_takes_what_went_and_what_came_with_it(self) -> None:
        held = [{"id": "a"}, {"id": "b"}, {"id": "c"}]

        _screen, by_id = self._replace(held, [{"id": "c"}, {"id": "a"}, {"id": "d"}])

        self.assertEqual([row["id"] for row in held], ["c", "a", "d"])
        self.assertEqual(set(by_id), {"a", "c", "d"})

    def test_an_update_in_its_place_is_a_transaction(self) -> None:
        held = [{"id": "a", "v": 1}, {"id": "b", "v": 1}]

        screen, _by_id = self._replace(held, [{"id": "a", "v": 1}, {"id": "b", "v": 2}])

        self.assertEqual(screen.sent[0][0], "applyTransaction")
        self.assertEqual([row["v"] for row in held], [1, 2])


class _Library:
    def __init__(self, refuses: str = "") -> None:
        self.written: list[tuple] = []
        self.refuses = refuses

    def set_game_rating(self, game_id: str, value: int) -> None:
        if game_id == self.refuses:
            raise RuntimeError("disk full")
        self.written.append((game_id, value))

    def set_table_rating(self, game_id: str, table_id: str, value: int) -> None:
        self.written.append((game_id, table_id, value))


class RatingTests(unittest.TestCase):
    def _rate(self, held: list[dict], **args: object) -> tuple[_Grid, _Library]:
        screen, library = _Grid(), _Library()
        by_id = {row["id"]: row for row in held}
        rate = stars.rating_handler(held, by_id, lambda: screen, library,
                                    "game_rating" if "game_id" in held[0] else "rating")
        asyncio.run(rate(SimpleNamespace(args=args)))
        return screen, library

    def test_a_rated_row_is_held_with_its_rating(self) -> None:
        held = [{"id": "g1", "rating": 0}, {"id": "g2", "rating": 4}]

        screen, library = self._rate(held, game="g1", value=3)

        self.assertEqual([row["rating"] for row in held], [3, 4])
        self.assertEqual(library.written, [("g1", 3)])
        self.assertEqual(screen.sent[0][1], {"update": [{"id": "g1", "rating": 3}]})

    def test_a_game_rated_on_a_table_s_row_repaints_every_table_of_it(self) -> None:
        held = [{"id": "t1", "game_id": "g1", "rating": 2, "game_rating": 0},
                {"id": "t2", "game_id": "g1", "rating": 0, "game_rating": 0},
                {"id": "t3", "game_id": "g2", "rating": 0, "game_rating": 1}]

        _screen, library = self._rate(held, game="g1", value=4)

        self.assertEqual(library.written, [("g1", 4)])
        self.assertEqual([(row["rating"], row["game_rating"]) for row in held],
                         [(2, 4), (0, 4), (0, 1)])

    def _rate_games(self, held: list[dict], game_ids: list[str], value: int,
                    library: _Library) -> tuple[_Grid, bool]:
        screen = _Grid()
        rater = stars.game_rater(held, {row["id"]: row for row in held}, lambda: screen,
                                 library)
        return screen, asyncio.run(rater(game_ids, value))

    def test_several_games_rated_at_once_repaint_in_one_transaction(self) -> None:
        held = [{"id": "g1", "rating": 0}, {"id": "g2", "rating": 5}, {"id": "g3", "rating": 2}]

        screen, every = self._rate_games(held, ["g1", "g3"], 4, _Library())

        self.assertTrue(every)
        self.assertEqual([row["rating"] for row in held], [4, 5, 4])
        self.assertEqual([name for name, *_ in screen.sent].count("applyTransaction"), 1)

    def test_a_game_that_cannot_be_saved_stops_the_run_and_keeps_what_was_saved(self) -> None:
        held = [{"id": "g1", "rating": 0}, {"id": "g2", "rating": 0}, {"id": "g3", "rating": 0}]
        library = _Library(refuses="g2")

        with mock.patch.object(stars.ui, "notify") as notify:
            _screen, every = self._rate_games(held, ["g1", "g2", "g3"], 4, library)

        self.assertFalse(every)
        self.assertEqual(notify.call_args.args, (t("manager.stars.save_failed"),))
        self.assertEqual(notify.call_args.kwargs["type"], "negative")
        self.assertEqual(library.written, [("g1", 4)])
        self.assertEqual([row["rating"] for row in held], [4, 0, 0])

    def test_a_table_rated_leaves_its_siblings_and_its_game_alone(self) -> None:
        held = [{"id": "t1", "game_id": "g1", "rating": 0, "game_rating": 3},
                {"id": "t2", "game_id": "g1", "rating": 0, "game_rating": 3}]

        _screen, library = self._rate(held, game="g1", table="t2", value=5)

        self.assertEqual(library.written, [("g1", "t2", 5)])
        self.assertEqual([(row["rating"], row["game_rating"]) for row in held],
                         [(0, 3), (5, 3)])


if __name__ == "__main__":
    unittest.main()

    def test_the_panel_is_told_which_games_were_rated(self) -> None:
        held: list[dict[str, Any]] = [{"id": "g1", "rating": 0}, {"id": "g2", "rating": 0},
                                      {"id": "g3", "rating": 0}]
        told: list[set[str]] = []

        async def then(games: set[str]) -> None:
            told.append(games)

        rater = stars.game_rater(held, {row["id"]: row for row in held}, lambda: _Grid(),
                                 _Library(refuses="g2"), then=then)
        with mock.patch.object(stars.ui, "notify"):
            asyncio.run(rater(["g1", "g2", "g3"], 4))

        self.assertEqual(told, [{"g1"}])

    def test_a_table_rated_in_the_grid_tells_the_panel_its_game(self) -> None:
        held = [{"id": "t1", "game_id": "g1", "rating": 0, "game_rating": 0}]
        told: list[set[str]] = []
        rate = stars.rating_handler(held, {"t1": held[0]}, lambda: _Grid(), _Library(),
                                    "game_rating", then=told.append)

        asyncio.run(rate(SimpleNamespace(args={"game": "g1", "table": "t1", "value": 3})))

        self.assertEqual(told, [{"g1"}])

"""Private in the Games grid and the Manager's client: the column, the rows, and the two
writes - one game, and a whole selection in one request."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from manager.kit import data, game_tables
from manager.kit.api import ApiClient
from manager.library import games


class PrivateColumnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.column = next(one for one in games.COLUMNS if one["field"] == "private")

    def test_only_a_private_game_is_drawn_and_as_a_quiet_chip(self) -> None:
        states = self.column["cellRendererParams"]["states"]

        self.assertEqual(list(states), [game_tables.PRIVATE_CELL])
        drawn = states[game_tables.PRIVATE_CELL]
        self.assertEqual(drawn["label"], game_tables.PRIVATE_WORDS[0])
        self.assertEqual(drawn["tone"], "quiet")
        self.assertIn(game_tables.PRIVATE_HELP, drawn["why"])

    def test_the_funnel_offers_the_pair_notable_first(self) -> None:
        choices = self.column["filterParams"]["choices"]

        self.assertEqual([one["label"] for one in choices],
                         list(game_tables.PRIVATE_WORDS))
        self.assertEqual([one["value"] for one in choices], [game_tables.PRIVATE_CELL, ""])

    def test_the_frontend_view_shows_it_beside_hidden(self) -> None:
        shown = list(getattr(games.GAME_VIEWS["manager.view.frontend"], "columns", ()))

        self.assertEqual(shown.index("private"), shown.index("hidden") + 1)

    def test_its_chip_opens_the_panel_where_the_switch_is(self) -> None:
        self.assertEqual(games.COLUMN_SECTIONS["private"], "game_details")


def _library(games_held: list[dict]) -> tuple[data.Library, Mock]:
    library = data.Library.__new__(data.Library)
    library.games = games_held
    library.media = {}
    client = Mock()
    library._client = client
    return library, client


class PrivateRowTests(unittest.TestCase):
    def test_a_game_row_carries_the_flag(self) -> None:
        library, _client = _library([{"id": "g1", "name": "A", "private": True},
                                     {"id": "g2", "name": "B"}])

        self.assertEqual([row["private"] for row in library.game_rows()],
                         [game_tables.PRIVATE_CELL, ""])

    def test_a_selection_is_written_once_and_the_copy_follows_the_answer(self) -> None:
        library, client = _library([{"id": "g1", "private": False},
                                    {"id": "g2", "private": True},
                                    {"id": "g3", "private": False}])
        client.set_private_many.return_value = {"private": True, "games": 2, "changed": 1}

        said = library.set_games_private(["g1", "g2"], True)

        client.set_private_many.assert_called_once_with(["g1", "g2"], True)
        self.assertEqual(said["changed"], 1)
        self.assertEqual([game["private"] for game in library.games], [True, True, False])

    def test_one_game_is_written_and_read_again(self) -> None:
        library, client = _library([{"id": "g1", "private": False}])
        client.game.return_value = {"id": "g1", "private": True}

        library.set_game_private("g1", True)

        client.set_private.assert_called_once_with("g1", True)
        self.assertIs(library.games[0]["private"], True)


class _Answer:
    ok = True

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


class PrivateClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ApiClient("http://127.0.0.1:1")
        self.session = Mock()
        self.client._session = self.session

    def test_one_game_puts_its_own_flag(self) -> None:
        self.session.put.return_value = _Answer({"private": True})

        self.client.set_private("g1", True)

        self.session.put.assert_called_once()
        url, = self.session.put.call_args.args
        self.assertTrue(url.endswith("/games/g1/private"))
        self.assertEqual(self.session.put.call_args.kwargs["json"], {"private": True})

    def test_a_selection_is_one_request_naming_every_game(self) -> None:
        answer = {"private": False, "games": 3, "changed": 2}
        self.session.put.return_value = _Answer(answer)

        said = self.client.set_private_many(["g1", "g2", "g3"], False)

        self.session.put.assert_called_once()
        url, = self.session.put.call_args.args
        self.assertTrue(url.endswith("/library/private"))
        self.assertEqual(self.session.put.call_args.kwargs["json"],
                         {"game_ids": ["g1", "g2", "g3"], "private": False})
        self.assertEqual(said, answer)


if __name__ == "__main__":
    unittest.main()

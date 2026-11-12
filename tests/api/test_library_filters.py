"""What a library can be filtered on, over HTTP.

The five `get_filter_*` methods on the window channel had no HTTP equivalent, so a
client that was not a theme window could not learn what a filter collection could
filter by. This projects the same registry the resolver matches on, so an axis a
client is offered is one that will actually resolve.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games.collection_filters import AXES
from tests.support.library import TempTree, fake_game, write_game

GAMES = [
    ("Attack from Mars (Bally 1995)", "Bally", "1995", ["Space"], "SS"),
    ("Congo (Williams 1995)", "Williams", "1995", ["Adventure"], "SS"),
    ("Medieval Madness (Williams 1997)", "Williams", "1997", ["Fantasy", "Medieval"], "EM"),
]


def _meta(name, manufacturer, year, themes, game_type) -> dict:
    return {"Info": {"Name": name, "Manufacturer": manufacturer, "Year": year,
                     "Themes": themes, "Type": game_type},
            "VPinFE": {}, "User": {}}


class LibraryFilterTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        games = []
        for row in GAMES:
            meta = _meta(*row)
            games.append(fake_game(write_game(self.root, row[0], info=meta), row[0],
                                   meta=meta))
        patcher = patch("common.games.game_repository.all_games",
                        return_value=games)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _axes(self) -> dict:
        response = self.client.get("/library/filters")
        self.assertEqual(response.status_code, 200)
        return {axis["name"]: axis for axis in response.json()["axes"]}

    def test_every_axis_the_resolver_knows_is_reported(self) -> None:
        """Projected from the registry, so the two cannot disagree - a client is never
        offered an axis that nothing would filter by, or left guessing about a new one."""
        self.assertEqual(sorted(self._axes()), sorted(axis.name for axis in AXES))

    def test_an_axis_carries_what_it_means(self) -> None:
        theme = self._axes()["theme"]

        self.assertEqual(theme["scope"], "game")
        self.assertEqual(theme["kind"], "choice")
        self.assertTrue(theme["summary"])

    def test_the_values_are_the_ones_this_library_holds(self) -> None:
        """Not every value that exists - a choice that matches nothing is not a choice."""
        axes = self._axes()

        self.assertEqual(axes["manufacturer"]["values"], ["Bally", "Williams"])
        self.assertEqual(axes["year"]["values"], ["1995", "1997"])
        self.assertEqual(axes["game_type"]["values"], ["EM", "SS"])
        self.assertEqual(axes["letter"]["values"], ["A", "C", "M"])

    def test_each_value_says_how_many_games_hold_it(self) -> None:
        axes = self._axes()

        self.assertEqual({"Bally": 1, "Williams": 2}, axes["manufacturer"]["counts"])
        self.assertEqual({"1995": 2, "1997": 1}, axes["year"]["counts"])

    def test_a_game_counts_once_for_each_value_it_holds(self) -> None:
        self.assertEqual({"Adventure": 1, "Fantasy": 1, "Medieval": 1, "Space": 1},
                         self._axes()["theme"]["counts"])

    def test_a_scale_carries_no_counts(self) -> None:
        self.assertIsNone(self._axes()["rating"]["counts"])

    def test_a_game_with_several_themes_contributes_all_of_them(self) -> None:
        self.assertEqual(self._axes()["theme"]["values"],
                         ["Adventure", "Fantasy", "Medieval", "Space"])

    def test_a_rating_axis_carries_its_scale(self) -> None:
        """It is half-star steps 1-10 on every install. Enumerating the ratings in use would offer a
        different scale to two libraries and a shrinking one as ratings change."""
        axes = self._axes()

        self.assertEqual([str(step) for step in range(1, 11)], axes["rating"]["values"])
        self.assertIsNone(axes["rating_or_higher"]["values"])

    def test_an_axis_read_under_another_field_says_which(self) -> None:
        axes = self._axes()

        self.assertEqual("year", axes["year_range"]["field"])
        self.assertEqual("rating", axes["rating_or_higher"]["field"])
        self.assertEqual("", axes["year"]["field"])
        self.assertEqual(("manufacturer", "none_of"),
                         (axes["manufacturer_none_of"]["field"],
                          axes["manufacturer_none_of"]["kind"]))

    def test_none_of_offers_no_values_beside_its_field_s(self) -> None:
        axes = self._axes()

        self.assertIsNone(axes["manufacturer_none_of"]["values"])
        self.assertTrue(axes["manufacturer"]["values"])

    def test_an_empty_library_reports_axes_with_no_values(self) -> None:
        """The axes exist whether or not anything is installed; only the choices are empty."""
        with patch("common.games.game_repository.all_games", return_value=[]):
            axes = {axis["name"]: axis for axis
                    in self.client.get("/library/filters").json()["axes"]}

        self.assertEqual(sorted(axes), sorted(axis.name for axis in AXES))
        self.assertEqual(axes["manufacturer"]["values"], [])
        self.assertEqual([str(step) for step in range(1, 11)], axes["rating"]["values"])


class FilterOptionParityTests(TempTree):
    """The frontend and the API answer from one implementation.

    Three separate versions of this computation existed; the window channel's
    `get_filter_*` and this route now share the one on `GameListFilters`, so a filter
    offered on a theme is a filter the API resolves the same way.
    """

    def test_the_frontend_reads_the_same_options_the_api_serves(self) -> None:
        from common.games.collection_filters import GameListFilters
        from frontend.game_state import filter_options

        games = [fake_game(write_game(self.root, row[0], info=_meta(*row)), row[0],
                           meta=_meta(*row)) for row in GAMES]

        self.assertEqual(filter_options(games),
                         GameListFilters(games).available_options())


if __name__ == "__main__":
    unittest.main()

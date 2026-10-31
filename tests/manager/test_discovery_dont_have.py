"""Games You Don't Have on Discovery's front page: the shelf drawn from one read, what a
tile says, and the menu on it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from nicegui import ui

from common.online import vps_home, vps_marks
from manager.kit import views
from manager.kit.data import Library
from manager.library import discovery, discovery_ask, discovery_marks
from tests.support.home_shelf import (
    KEPT,
    PEOPLE,
    ShelfHarness,
    entry,
    grid_order,
    mark,
    passes,
    settle,
    table,
    words_of,
)

FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
    entry("g1", "Alien", [table("t1", 0)], in_library=True),
    entry("g2", "Batman", [table("t2", 0), table("t3", 1), table("t4", 2)],
           manufacturer="Williams", year=1997),
    entry("g3", "Cactus", [table("t5", 0)], manufacturer="Bally"),
    entry("g4", "Dune", [table("t6", 0, "FP")]),
]}


class DrivenShelf(ShelfHarness):
    """Games You Don't Have, on a front page with a client that answers."""

    FOUND = FOUND

    async def test_reading_the_shelves_again_draws_this_one_from_the_new_read(self) -> None:
        body = await self._draw()
        self.assertIn("Cactus", words_of(body))
        self.found = {**FOUND, "rows": [row for row in FOUND["rows"] if row["vps_id"] != "g3"]}

        await self.refresh()
        await settle()

        self.assertNotIn("Cactus", words_of(body))
        self.assertIn("Batman", words_of(body))

    async def test_the_shelf_lists_the_games_the_library_lacks_with_the_most_authors_first(
            self) -> None:
        words = words_of(await self._draw())

        self.assertIn("Games You Don't Have", words)
        self.assertIn("Most authors first", words)
        self.assertLess(words.index("Batman"), words.index("Cactus"))
        self.assertIn("Tables by 3 authors", words)
        self.assertIn("Tables by 1 author", words)

    async def test_a_tile_says_who_made_the_game_and_when(self) -> None:
        words = words_of(await self._draw())

        self.assertIn("Williams 1997", words)
        self.assertIn("Bally", words)

    async def test_a_game_the_library_holds_or_without_a_table_it_plays_is_not_on_it(
            self) -> None:
        words = words_of(await self._draw())

        self.assertNotIn("Dune", words)
        # Alien is on the New shelf at most: its table is dated 2020, outside the window.
        self.assertNotIn("Alien", words)

    async def test_the_shelf_follows_the_missing_shelf_and_comes_before_the_authors(
            self) -> None:
        self.found = {**FOUND, "rows": [
            *FOUND["rows"],
            {**entry("g5", "Eden", [table("t7", 0)], in_library=True),
             "listed": ["tableFiles", "b2sFiles"],
             "releases": {"tableFiles": [{**table("t7", 0), "held": True}],
                          "b2sFiles": [table("b7", 1)]}}]}

        words = words_of(await self._draw())

        self.assertLess(words.index("Missing from Your Games"),
                        words.index("Games You Don't Have"))
        self.assertLess(words.index("Games You Don't Have"), words.index("Authors You Collect"))

    async def test_nothing_to_have_draws_no_shelf(self) -> None:
        self.found = {**FOUND, "rows": [FOUND["rows"][0]]}

        self.assertNotIn("Games You Don't Have", words_of(await self._draw()))

    async def test_a_library_never_compared_draws_no_shelf(self) -> None:
        self.found = {**FOUND, "counted": "", "rows": [
            {**row, "in_library": None} for row in FOUND["rows"]]}

        self.assertNotIn("Games You Don't Have", words_of(await self._draw()))

    async def test_an_ignored_game_leaves_the_shelf(self) -> None:
        self.client.discovery_marks.return_value = [
            {"state": "ignored", "scope": "game", "id": "g2", "detail": "", "since": "x"}]

        words = words_of(await self._draw())

        self.assertNotIn("Batman", words)
        self.assertIn("Cactus", words)

    async def test_a_wanted_game_stays_on_the_shelf_and_the_wanted_one(self) -> None:
        self.client.discovery_marks.return_value = [
            {"state": "wanted", "scope": "game", "id": "g2", "detail": "", "since": "x"}]

        words = words_of(await self._draw())

        self.assertEqual(words.count("Batman"), 2)

    async def test_the_shelf_has_a_see_all_of_its_own(self) -> None:
        with_shelf = self._see_alls(await self._draw())
        self.found = {**FOUND, "rows": [FOUND["rows"][0]]}

        self.assertEqual(with_shelf - self._see_alls(await self._draw()), 1)

    async def test_see_all_opens_browse_on_the_view_named_for_the_shelf(self) -> None:
        await self._draw()

        self.spied["see_dont_have"]()

        opening = discovery_ask.opening_on(self.state, discovery.BROWSE)
        assert opening is not None
        self.assertEqual(opening["name"], "Games You Don't Have")
        self.assertEqual(opening["filters"], discovery.dont_have_opening(counted=True)["filters"])
        self.assertEqual(discovery.tab_of(self.state), discovery.BROWSE)

    async def test_each_tile_has_a_menu(self) -> None:
        body = await self._draw()

        tiles = [one for one in body.descendants()
                 if not one.is_deleted and "manager-shelf-tile" in one.classes
                 and any(isinstance(child, ui.label) and child.text == "Tables by 1 author"
                         for child in one.descendants())]

        self.assertEqual(len(tiles), 1)
        self.assertEqual(sum(isinstance(child, ui.context_menu)
                             for child in tiles[0].descendants()), 1)


class BrowseViewTests(unittest.TestCase):
    """See All opens All Games on exactly the games, in the order, the shelf draws from."""

    FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
        *FOUND["rows"],
        entry("g5", "Echo", [table("t7", 1), table("t8", 1)]),
        entry("g6", "Fargo", [table("t9", 2)]),
        entry("g7", "Golem", [{**table("t10", 0), "by": []}]),
        entry("g8", "Hydra", [table("t11", 0), table("t12", 1)]),
        entry("g9", "Igloo", [table("t13", 0), table("t14", 1)], in_library=True),
        entry("ga", " ", [table("t15", 0)]),
        {**entry("gb", "Jungle", []), "releases": {}},
    ]}

    def setUp(self) -> None:
        self.MARKS = vps_marks.Marks([mark("ignored", "game", "g6"),
                                      mark("wanted", "game", "g8")])

    PLAYS = ("VPX",)

    def _rows(self, found: dict[str, Any] | None = None,
              formats: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        return discovery.rows(found or self.FOUND, KEPT, self.MARKS,
                              self.PLAYS if formats is None else formats)

    def test_the_view_keeps_the_games_the_shelf_counts(self) -> None:
        shelf = vps_home.dont_have(self.FOUND, self.MARKS, self.PLAYS)
        view = discovery.dont_have_opening(counted=True)

        kept = [row for row in self._rows() if passes(row, view["filters"])]

        self.assertEqual({row["id"] for row in kept}, {one["vps_id"] for one in shelf})
        self.assertEqual(len(kept), len(shelf))
        self.assertEqual({"g2", "g3", "g5", "g7", "g8"}, {row["id"] for row in kept})

    def test_the_view_orders_the_games_as_the_shelf_does(self) -> None:
        shelf = [one["vps_id"] for one in vps_home.dont_have(self.FOUND, self.MARKS, self.PLAYS)]
        view = discovery.dont_have_opening(counted=True)
        kept = [row for row in self._rows() if passes(row, view["filters"])]

        self.assertEqual(grid_order(kept, view["sort"]), shelf)

    def test_a_format_a_launcher_plays_counts_in_the_column_the_view_and_the_shelf(self) -> None:
        for plays in (("VPX", "FP"), ()):
            with self.subTest(plays=plays):
                shelf = vps_home.dont_have(self.FOUND, self.MARKS, plays)
                view = discovery.dont_have_opening(counted=True)
                rows = self._rows(formats=plays)
                kept = [row for row in rows if passes(row, view["filters"])]

                self.assertEqual({row["id"]: row for row in rows}["g4"][discovery.TABLE_AUTHORS], 1)
                self.assertEqual({row["id"] for row in kept}, {one["vps_id"] for one in shelf})
                self.assertIn("g4", {row["id"] for row in kept})
                self.assertEqual(grid_order(kept, view["sort"]),
                                 [one["vps_id"] for one in shelf])

    def test_a_format_no_launcher_plays_is_not_counted_in_the_column(self) -> None:
        by_id = {row["id"]: row for row in self._rows(formats=("VPX",))}

        self.assertIsNone(by_id["g4"][discovery.TABLE_AUTHORS])
        self.assertEqual(by_id["g2"][discovery.TABLE_AUTHORS], 3)

    def test_the_rows_read_the_formats_the_enabled_launchers_play(self) -> None:
        library = mock.Mock()
        with (mock.patch.object(discovery, "kept_listings", return_value=KEPT),
              mock.patch.object(discovery.discovery_marks, "read", return_value=self.MARKS),
              mock.patch.object(discovery.launchers, "get_launcher_store"),
              mock.patch.object(discovery.launchers, "playable_formats",
                                return_value=("FP",)) as plays):
            by_id = {row["id"]: row for row in discovery.rows_of(library, self.FOUND)}

        plays.assert_called_once()
        self.assertEqual(by_id["g4"][discovery.TABLE_AUTHORS], 1)
        self.assertIsNone(by_id["g2"][discovery.TABLE_AUTHORS])

    def test_a_game_with_a_table_that_names_no_one_is_in_the_view_with_a_zero(self) -> None:
        by_id = {row["id"]: row for row in self._rows()}

        self.assertEqual(by_id["g7"][discovery.TABLE_AUTHORS], 0)
        self.assertIsNone(by_id["g4"][discovery.TABLE_AUTHORS])
        self.assertIsNone(by_id["gb"][discovery.TABLE_AUTHORS])

    def test_an_ignored_game_is_in_browse_but_not_in_the_view(self) -> None:
        by_id = {row["id"]: row for row in self._rows()}
        view = discovery.dont_have_opening(counted=True)

        self.assertEqual(by_id["g6"]["mark"], "ignored")
        self.assertFalse(passes(by_id["g6"], view["filters"]))
        self.assertTrue(passes(by_id["g8"], view["filters"]))

    def test_before_the_library_is_compared_only_the_table_filter_is_set(self) -> None:
        self.assertEqual(list(discovery.dont_have_opening(counted=False)["filters"]),
                         [discovery.TABLE_AUTHORS])

    def test_it_is_named_for_the_shelf_and_orders_by_authors_then_name(self) -> None:
        view = discovery.dont_have_opening(counted=True)

        self.assertEqual(view["name"], "Games You Don't Have")
        self.assertEqual([(one["colId"], one["sort"]) for one in view["sort"]],
                         [(discovery.TABLE_AUTHORS, "desc"), ("name", "asc")])


class SameNameTests(unittest.TestCase):
    """Entries of one name that the same number of people made a table for keep the catalog's
    order, which is the order the grid keeps them in."""

    FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
        entry("zz9", "Joust", [table("t1", 0)], manufacturer="Atari", year=1990),
        entry("aa1", "Joust", [table("t2", 0)], manufacturer="Williams", year=1991),
    ]}

    def test_the_shelf_and_the_view_agree_where_the_ids_would_sort_the_other_way(self) -> None:
        shelf = [one["vps_id"] for one in vps_home.dont_have(self.FOUND, None, ("VPX",))]
        view = discovery.dont_have_opening(counted=True)
        kept = [row for row in discovery.rows(self.FOUND, KEPT, None, ("VPX",))
                if passes(row, view["filters"])]

        self.assertEqual(shelf, ["zz9", "aa1"])
        self.assertEqual(grid_order(kept, view["sort"]), shelf)


class ColumnTests(unittest.TestCase):
    def _column(self, field: str) -> dict[str, Any]:
        return next(one for one in discovery.COLUMNS if one["field"] == field)

    def test_browse_has_a_table_authors_column_that_counts(self) -> None:
        column = self._column(discovery.TABLE_AUTHORS)

        self.assertEqual(column["headerName"].replace("\n", " "), "Table Authors")
        self.assertEqual(column["filter"], "agNumberColumnFilter")
        self.assertTrue(column["headerTooltip"])

    def test_the_new_view_replaces_not_in_library_among_browses_own(self) -> None:
        self.assertEqual(list(discovery.VIEWS),
                         ["manager.view.everything", "manager.view.missing_games"])

    def test_everything_leaves_it_to_the_column_picker(self) -> None:
        everything = discovery.VIEWS["manager.view.everything"]
        assert isinstance(everything, views.Preset)

        self.assertNotIn(discovery.TABLE_AUTHORS, everything.columns)

    def test_the_opening_shows_it_and_sorts_on_it(self) -> None:
        fields = [one["field"] for one in discovery.COLUMNS]
        opening = discovery.dont_have_opening(counted=True)

        view = views.opened(views.builtins(discovery.VIEWS)[0], opening["name"],
                            opening["filters"], fields, sort=opening["sort"])

        self.assertIn(discovery.TABLE_AUTHORS, view.columns)
        self.assertEqual(view.sort, opening["sort"])
        self.assertEqual(view.filters, opening["filters"])

    def test_an_opening_with_no_sort_keeps_the_views_own(self) -> None:
        start = views.builtins(discovery.VIEWS)[0]

        view = views.opened(start, "Named", {}, [one["field"] for one in discovery.COLUMNS])

        self.assertEqual(view.sort, start.sort)

    def test_browse_status_can_pick_the_games_nothing_is_marked_on(self) -> None:
        choices = self._column(discovery_marks.FIELD)["filterParams"]["choices"]

        self.assertEqual([(one["value"], one["label"]) for one in choices],
                         [("wanted", "Wanted"), ("ignored", "Ignored"), ("", "None")])


def _labels(entries: list[Any]) -> list[str]:
    return [one.label for one in entries]


class AStoredViewThatIsGone(unittest.TestCase):
    """Not in Library was a built-in a user could have left showing."""

    def test_the_view_control_opens_on_everything(self) -> None:
        from manager.library import games

        library = mock.Mock()
        library.preferences.return_value = {"views": [], "active": "builtin:manager.view.not_held"}
        fields = ["name", "in_library"]
        columns = [{"field": field} for field in fields]
        with mock.patch.object(games, "ui"), mock.patch.object(games, "panel") as panel:
            games.view_control(library, discovery.SCOPE, discovery.VIEWS, fields, columns,
                               bar=mock.MagicMock())

        shown = panel.DescribedSelect.call_args
        self.assertEqual(views.builtin_id("manager.view.everything"), shown.kwargs["value"])
        self.assertNotIn("builtin:manager.view.not_held", shown.args[0])


class MenuTests(unittest.TestCase):
    ITEM = {"vps_id": "g2", "game": "Batman", "authors": 3, "manufacturer": "", "year": ""}

    def _verbs(self, *marked: dict[str, str]) -> list[str]:
        library = mock.Mock(spec=Library)
        library.marks.return_value = list(marked)
        return _labels(discovery_marks.Marking(library, mock.AsyncMock()).verbs(
            discovery_marks.DONT_HAVE, self.ITEM, "", ""))

    def test_a_game_can_be_wanted_or_ignored(self) -> None:
        self.assertEqual(self._verbs(), ["Add to Wanted", "Ignore Game"])

    def test_a_wanted_game_can_be_taken_off(self) -> None:
        self.assertEqual(self._verbs(mark("wanted", "game", "g2")),
                         ["Remove from Wanted", "Ignore Game"])

    def test_an_ignored_game_can_be_stopped(self) -> None:
        self.assertEqual(self._verbs(mark("ignored", "game", "g2")),
                         ["Add to Wanted", "Stop Ignoring"])

    def test_the_menu_is_titled_with_the_game(self) -> None:
        marking = discovery_marks.Marking(mock.Mock(spec=Library), mock.AsyncMock())

        self.assertEqual(marking.title(discovery_marks.DONT_HAVE, self.ITEM), "Batman")

    def test_a_game_is_wanted_by_its_own_mark(self) -> None:
        library = mock.Mock(spec=Library)
        library.marks.return_value = [mark("wanted", "game", "g2"),
                                      mark("wanted", "release", "g3")]
        marking = discovery_marks.Marking(library, mock.AsyncMock())

        self.assertTrue(marking.wanted_game("g2"))
        self.assertFalse(marking.wanted_game("g3"))


if __name__ == "__main__":
    unittest.main()

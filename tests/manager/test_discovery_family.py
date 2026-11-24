"""Other Versions of Your Games on Discovery's front page: the shelf drawn from one read, what
a tile says, and the All Games view and column See All opens."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from common.online import vps_home, vps_marks
from manager.kit import dropdown, views
from manager.library import discovery, discovery_ask
from tests.support.home_shelf import (
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


def game(vps_id: str, name: str, tables: list[dict[str, Any]], *, held: bool = False,
          manufacturer: str = "", year: int | None = None) -> dict[str, Any]:
    return entry(vps_id, name, tables, in_library=held, manufacturer=manufacturer, year=year)


FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
    game("h1", "Atlantis", [table("t1", 0)], held=True, manufacturer="Gottlieb", year=1975),
    game("o1", "Atlantis", [table("t2", 0), table("t3", 1)], manufacturer="Bally", year=1989),
    game("h2", "Joust", [table("t4", 0)], held=True, manufacturer="Bally", year=1969),
    game("h3", "Joust", [table("t5", 0)], held=True, manufacturer="Williams", year=1983),
    game("o2", "Joust", [table("t6", 0)], manufacturer="Atari", year=1990),
    game("n1", "Batman", [table("t7", 0)], manufacturer="Williams", year=1997),
]}


class FamilyShelf(ShelfHarness):
    FOUND = FOUND

    async def test_the_shelf_lists_the_games_that_share_a_title_with_one_you_have(self) -> None:
        words = words_of(await self._draw())

        self.assertIn("Other Versions of Your Games", words)
        self.assertIn("Bally 1989", words)
        self.assertIn("Atari 1990", words)

    async def test_a_tile_says_what_you_have_of_the_title(self) -> None:
        words = words_of(await self._draw())

        self.assertIn("You have Gottlieb 1975", words)
        self.assertIn("You have 2 with this title", words)

    async def test_the_games_with_the_most_authors_come_first(self) -> None:
        words = words_of(await self._draw())
        shelf = words[words.index("Other Versions of Your Games"):]

        self.assertLess(shelf.index("Bally 1989"), shelf.index("Atari 1990"))

    async def test_a_game_with_no_held_title_is_only_on_games_you_dont_have(self) -> None:
        words = words_of(await self._draw())

        self.assertEqual(words.count("Batman"), 1)
        self.assertEqual(words.count("Joust"), 2)

    async def test_the_shelf_follows_games_you_dont_have_and_comes_before_the_authors(
            self) -> None:
        self.found = {**FOUND, "rows": [
            *FOUND["rows"],
            {**entry("g5", "Eden", [table("t90", 0)], in_library=True),
             "releases": {"tableFiles": [{**table("t90", 0), "held": True}]}}]}

        words = words_of(await self._draw())

        self.assertLess(words.index("Games You Don't Have"),
                        words.index("Other Versions of Your Games"))
        self.assertLess(words.index("Other Versions of Your Games"),
                        words.index("Authors You Collect"))

    async def test_no_other_version_draws_no_shelf(self) -> None:
        self.found = {**FOUND, "rows": [FOUND["rows"][0], FOUND["rows"][5]]}

        self.assertNotIn("Other Versions of Your Games", words_of(await self._draw()))

    async def test_a_library_never_compared_draws_no_shelf(self) -> None:
        self.found = {**FOUND, "counted": "", "rows": [
            {**row, "in_library": None} for row in FOUND["rows"]]}

        self.assertNotIn("Other Versions of Your Games", words_of(await self._draw()))

    async def test_an_ignored_game_leaves_the_shelf(self) -> None:
        self.client.discovery_marks.return_value = [mark("ignored", "game", "o1")]

        words = words_of(await self._draw())

        self.assertNotIn("Bally 1989", words)
        self.assertIn("Atari 1990", words)

    async def test_the_shelf_has_a_see_all_of_its_own(self) -> None:
        with_shelf = self._see_alls(await self._draw())
        self.found = {**FOUND, "rows": [FOUND["rows"][0], FOUND["rows"][5]]}

        self.assertEqual(with_shelf - self._see_alls(await self._draw()), 1)

    async def test_picking_another_window_draws_the_shelf_from_the_new_read(self) -> None:
        body = await self._draw()
        self.found = {**FOUND, "rows": [FOUND["rows"][0], FOUND["rows"][5]]}
        window = next(one for one in body.descendants()
                      if isinstance(one, dropdown.Select) and not one.is_deleted
                      and one.value == 7)

        window.set_value(30)
        await settle()

        self.assertNotIn("Other Versions of Your Games", words_of(body))

    async def test_see_all_opens_browse_on_the_view_named_for_the_shelf(self) -> None:
        await self._draw()

        self.spied["see_family"]()

        opening = discovery_ask.opening_on(self.state, discovery.BROWSE)
        assert opening is not None
        self.assertEqual(opening["name"], "Other Versions of Your Games")
        self.assertEqual(opening["filters"], discovery.family_opening(counted=True)["filters"])
        self.assertEqual(discovery.tab_of(self.state), discovery.BROWSE)

    async def test_a_tile_opens_the_game_and_has_no_menu(self) -> None:
        body = await self._draw()
        tiles = [one for one in body.descendants()
                 if not one.is_deleted and "manager-shelf-tile" in one.classes
                 and any(getattr(child, "text", "") == "You have 2 with this title"
                         for child in one.descendants())]

        self.assertEqual(len(tiles), 1)
        self.assertEqual([child for child in tiles[0].descendants()
                          if type(child).__name__ == "ContextMenu"], [])
        for listener in tiles[0]._event_listeners.values():
            if listener.type == "click" and listener.handler is not None:
                answer = listener.handler(mock.Mock())
                if asyncio.iscoroutine(answer):
                    await answer
        self.assertEqual(self.selected, [{"id": "o2"}])


class RefreshTests(ShelfHarness):
    FOUND = FOUND

    async def test_reading_the_shelves_again_draws_this_one_from_the_new_read(self) -> None:
        body = await self._draw()
        self.assertIn("Other Versions of Your Games", words_of(body))
        self.found = {**FOUND, "rows": [FOUND["rows"][0], FOUND["rows"][5]]}

        await self.refresh()
        await settle()

        self.assertNotIn("Other Versions of Your Games", words_of(body))


class SameNameTests(unittest.TestCase):
    """Entries of one name that the same number of people made a table for keep the catalog's
    order, which is the order the grid keeps them in."""

    FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
        game("h1", "Joust", [table("t1", 0)], held=True, manufacturer="Bally", year=1982),
        game("zz9", "Joust", [table("t2", 0)], manufacturer="Atari", year=1990),
        game("aa1", "Joust", [table("t3", 0)], manufacturer="Williams", year=1991),
    ]}

    def test_the_shelf_and_the_view_agree_where_the_ids_would_sort_the_other_way(self) -> None:
        shelf = [one["vps_id"] for one in vps_home.family(self.FOUND, None, ("VPX",))]
        view = discovery.family_opening(counted=True)
        rows = discovery.rows(self.FOUND, discovery_kept(), None, ("VPX",))
        kept = [row for row in rows if passes(row, view["filters"])]

        self.assertEqual(shelf, ["zz9", "aa1"])
        self.assertEqual(grid_order(kept, view["sort"]), shelf)


class UntitledTests(ShelfHarness):
    """An entry with only a bracketed part or punctuation for a name has no title to share."""

    FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
        game("h1", "(Pro)", [table("t1", 0)], held=True),
        game("o1", "(Beta)", [table("t2", 0)]),
        game("o2", "!", [table("t3", 0)]),
        game("h2", "Joust", [table("t4", 0)], held=True),
    ]}

    async def test_an_untitled_game_held_does_not_put_the_others_on_the_shelf(self) -> None:
        words = words_of(await self._draw())

        self.assertNotIn("Other Versions of Your Games", words)
        self.assertIn("Games You Don't Have", words)

    def test_it_does_not_mark_the_others_in_the_column(self) -> None:
        by_id = {row["id"]: row for row in discovery.rows(self.FOUND, discovery_kept(), None,
                                                          ("VPX",))}

        self.assertEqual(by_id["o1"][discovery.OTHER_HELD], discovery.NO)
        self.assertEqual(by_id["o2"][discovery.OTHER_HELD], discovery.NO)


class UnnamedHeldTests(ShelfHarness):
    """The line under a tile always says something: a held game with no maker and no year is
    counted instead of named."""

    FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
        game("h1", "Joust", [table("t1", 0)], held=True),
        game("o1", "Joust", [table("t2", 0)], manufacturer="Atari", year=1990),
    ]}

    async def test_a_held_game_with_no_maker_or_year_is_counted(self) -> None:
        words = words_of(await self._draw())

        self.assertIn("Atari 1990", words)
        self.assertIn("You have 1 with this title", words)
        self.assertNotIn("You have", [word.strip() for word in words])


class BrowseViewTests(unittest.TestCase):
    """See All opens All Games on exactly the games, in the order, the shelf draws from."""

    FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
        *FOUND["rows"],
        game("o3", "Joust", [table("t8", 0), table("t9", 1)]),
        game("o4", "Atlantis", [table("t10", 0, "FP")]),
        game("o5", "Atlántis (Pro)", [table("t11", 2)]),
    ]}
    MARKS = vps_marks.Marks([mark("ignored", "game", "o5")])
    PLAYS = ("VPX",)

    def _rows(self, found: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return discovery.rows(found or self.FOUND, discovery_kept(), self.MARKS, self.PLAYS)

    def test_the_view_keeps_the_games_the_shelf_counts(self) -> None:
        shelf = vps_home.family(self.FOUND, self.MARKS, self.PLAYS)
        view = discovery.family_opening(counted=True)

        kept = [row for row in self._rows() if passes(row, view["filters"])]

        self.assertEqual({row["id"] for row in kept}, {one["vps_id"] for one in shelf})
        self.assertEqual(len(kept), len(shelf))
        self.assertEqual({"o1", "o2", "o3"}, {row["id"] for row in kept})

    def test_the_view_orders_the_games_as_the_shelf_does(self) -> None:
        shelf = [one["vps_id"] for one in vps_home.family(self.FOUND, self.MARKS, self.PLAYS)]
        view = discovery.family_opening(counted=True)
        kept = [row for row in self._rows() if passes(row, view["filters"])]

        self.assertEqual(grid_order(kept, view["sort"]), shelf)

    def test_a_format_a_launcher_plays_counts_in_the_view_and_the_shelf(self) -> None:
        for plays in (("VPX", "FP"), ()):
            with self.subTest(plays=plays):
                shelf = vps_home.family(self.FOUND, self.MARKS, plays)
                view = discovery.family_opening(counted=True)
                rows = discovery.rows(self.FOUND, discovery_kept(), self.MARKS, plays)
                kept = [row for row in rows if passes(row, view["filters"])]

                self.assertEqual({row["id"] for row in kept}, {one["vps_id"] for one in shelf})
                self.assertIn("o4", {row["id"] for row in kept})

    def test_the_column_is_yes_for_a_game_whose_title_another_held_game_has(self) -> None:
        by_id = {row["id"]: row for row in self._rows()}

        self.assertEqual(by_id["o1"][discovery.OTHER_HELD], discovery.YES)
        self.assertEqual(by_id["n1"][discovery.OTHER_HELD], discovery.NO)

    def test_a_held_game_is_yes_only_where_another_is_held_too(self) -> None:
        by_id = {row["id"]: row for row in self._rows()}

        self.assertEqual(by_id["h2"][discovery.OTHER_HELD], discovery.YES)
        self.assertEqual(by_id["h1"][discovery.OTHER_HELD], discovery.NO)

        alone = {row["id"]: row for row in self._rows(
            {**self.FOUND, "rows": [FOUND["rows"][0]]})}
        self.assertEqual(alone["h1"][discovery.OTHER_HELD], discovery.NO)

    def test_the_column_is_not_compared_until_the_library_is_compared(self) -> None:
        rows = discovery.rows({**self.FOUND, "counted": ""}, discovery_kept(), self.MARKS,
                              self.PLAYS)

        self.assertEqual({row[discovery.OTHER_HELD] for row in rows}, {"not_compared"})

    def test_before_the_library_is_compared_only_the_table_filter_is_set(self) -> None:
        self.assertEqual(list(discovery.family_opening(counted=False)["filters"]),
                         [discovery.TABLE_AUTHORS])

    def test_it_is_named_for_the_shelf_and_orders_by_authors_then_name(self) -> None:
        view = discovery.family_opening(counted=True)

        self.assertEqual(view["name"], "Other Versions of Your Games")
        self.assertEqual([(one["colId"], one["sort"]) for one in view["sort"]],
                         [(discovery.TABLE_AUTHORS, "desc"), ("name", "asc")])

    def test_the_games_you_dont_have_view_is_unchanged(self) -> None:
        self.assertEqual(discovery.dont_have_opening(counted=True)["name"],
                         "Games You Don't Have")
        self.assertNotIn(discovery.OTHER_HELD,
                         discovery.dont_have_opening(counted=True)["filters"])


def discovery_kept() -> list[str]:
    return ["tableFiles", "b2sFiles"]


class ColumnTests(unittest.TestCase):
    def test_browse_has_an_other_version_held_column_with_a_yes_no_filter(self) -> None:
        column = next(one for one in discovery.COLUMNS if one["field"] == discovery.OTHER_HELD)

        self.assertEqual(column["headerName"].replace("\n", " "), "Have Other Version")
        self.assertTrue(column["headerTooltip"])
        self.assertLessEqual(len(column["headerTooltip"].split("\n")[0]), 80)
        self.assertEqual([one["label"] for one in column["filterParams"]["choices"]],
                         ["Yes", "No", "Not Compared Yet"])

    def test_it_is_not_compared_until_compared(self) -> None:
        self.assertIn(discovery.OTHER_HELD, discovery.COUNTED_FIELDS)

    def test_everything_leaves_it_to_the_column_picker(self) -> None:
        everything = discovery.VIEWS["manager.view.everything"]
        assert isinstance(everything, views.Preset)

        self.assertNotIn(discovery.OTHER_HELD, everything.columns)

    def test_the_opening_shows_it_and_sorts_on_the_authors(self) -> None:
        fields = [one["field"] for one in discovery.COLUMNS]
        opening = discovery.family_opening(counted=True)

        view = views.opened(views.builtins(discovery.VIEWS)[0], opening["name"],
                            opening["filters"], fields, sort=opening["sort"])

        self.assertIn(discovery.OTHER_HELD, view.columns)
        self.assertEqual(view.sort, opening["sort"])


if __name__ == "__main__":
    unittest.main()

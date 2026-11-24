"""Library Health on Discovery's front page: the shelf drawn from one read, what a tile says and
opens, and the Games view and column See All opens."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from common.online import vps_home
from manager.kit import data, dropdown, views
from manager.library import games
from manager.overview import needs
from tests.support.home_shelf import (
    PEOPLE,
    ShelfHarness,
    entry,
    held_game,
    mark,
    settle,
    table,
    words_of,
)

FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
    entry("E1", "Atlantis", [], in_library=True, manufacturer="Gottlieb", year=1975),
    entry("E2", "Joust", [], in_library=True, manufacturer="Williams", year=1982),
    entry("E3", "Eden", [], in_library=True, manufacturer="Bally", year=1991),
]}

LIBRARY = [
    held_game("g1", "Atlantis", "E1"), held_game("g2", "Atlantis again", "E1"),
    held_game("g3", "Atlantis third", "E1"),
    held_game("g4", "Joust", "E2", tables=0),
    held_game("g5", "Mine", "", tables=0),
    held_game("g6", "Eden", "E3"),
]


def _picker(body: Any, finding: str) -> dropdown.Select:
    """The shelf's dropdown of findings, found by one it offers."""
    return next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                and not one.is_deleted and finding in list(one.options))


def _offered(picker: dropdown.Select) -> list[str]:
    assert isinstance(picker.options, dict)
    return list(picker.options.values())


def _preset(key: str) -> views.Preset:
    preset = games.GAME_VIEWS[key]
    assert isinstance(preset, views.Preset)
    return preset


def _tiles(body: Any) -> list[Any]:
    return [one for one in body.descendants()
            if not one.is_deleted and "manager-shelf-tile" in one.classes]


def _tile_of(body: Any, word: str) -> Any:
    return next(one for one in _tiles(body)
                if any(getattr(child, "text", "") == word for child in one.descendants()))


async def _press(tile: Any) -> None:
    for listener in tile._event_listeners.values():
        if listener.type == "click" and listener.handler is not None:
            answer = listener.handler(mock.Mock())
            if asyncio.iscoroutine(answer):
                await answer


class HealthShelf(ShelfHarness):
    FOUND = FOUND

    def setUp(self) -> None:
        super().setUp()
        self.games = list(LIBRARY)
        self.opened: list[needs.Opens] = []

        async def open_on(opens: needs.Opens) -> None:
            self.opened.append(opens)

        self.state["open_on"] = open_on

    async def test_the_shelf_opens_on_the_duplicates_with_the_findings_counted(self) -> None:
        body = await self._draw()
        words = words_of(body)

        self.assertIn("Library Health", words)
        choices = _picker(body, "duplicates")
        self.assertEqual(_offered(choices), ["Duplicates 1", "No Tables 2"])
        self.assertEqual(choices.value, "duplicates")
        self.assertIn("Atlantis", words)
        self.assertIn("Gottlieb 1975", words)
        self.assertIn("3 of your games", words)
        self.assertNotIn("Mine", words)

    async def test_the_other_finding_lists_its_games_by_name(self) -> None:
        body = await self._draw()
        choices = _picker(body, "no_tables")

        choices.set_value("no_tables")
        await settle()
        words = words_of(body)

        self.assertLess(words.index("Joust"), words.index("Mine"))
        self.assertIn("Williams 1982", words)
        self.assertNotIn("3 of your games", words)

    async def test_a_finding_with_nothing_is_left_out_of_the_dropdown(self) -> None:
        self.games = [held_game("g1", "Mine", "", tables=0)]

        body = await self._draw()

        choices = _picker(body, "no_tables")
        self.assertEqual(list(choices.options), ["no_tables"])
        self.assertEqual(choices.value, "no_tables")

    async def test_a_library_with_neither_finding_draws_no_shelf(self) -> None:
        self.games = [held_game("g1", "Atlantis", "E1"), held_game("g6", "Eden", "E3")]

        self.assertNotIn("Library Health", words_of(await self._draw()))

    async def test_a_library_that_could_not_be_read_draws_no_shelf(self) -> None:
        self.games = []

        self.assertNotIn("Library Health", words_of(await self._draw()))

    async def test_the_shelf_comes_after_the_family_shelf_and_before_the_authors(self) -> None:
        self.found = {**FOUND, "rows": [
            entry("E1", "Atlantis", [{**table("t1", 0), "held": True}], in_library=True),
            entry("O1", "Atlantis", [table("t2", 0)]),
            *FOUND["rows"][1:]]}

        words = words_of(await self._draw())

        self.assertLess(words.index("Other Versions of Your Games"),
                        words.index("Library Health"))
        self.assertLess(words.index("Library Health"), words.index("Authors You Collect"))

    async def test_an_ignored_entry_leaves_the_shelf(self) -> None:
        self.client.discovery_marks.return_value = [mark("ignored", "game", "E1")]

        words = words_of(await self._draw())

        self.assertNotIn("3 of your games", words)
        self.assertIn("Library Health", words)

    async def test_reading_the_shelves_again_draws_it_from_the_new_read(self) -> None:
        body = await self._draw()
        self.games = [held_game("g1", "Atlantis", "E1")]

        await self.refresh()
        await settle()

        self.assertNotIn("Library Health", words_of(body))

    async def test_each_finding_has_a_see_all_that_opens_its_games_view(self) -> None:
        await self._draw()

        await self.spied["see_health"](vps_home.DUPLICATES)
        await self.spied["see_health"](vps_home.NO_TABLES)

        self.assertEqual([(one.view, one.keys[games.ARRIVE_VIEW]) for one in self.opened],
                         [("games", games.DUPLICATES_VIEW), ("games", games.NO_TABLES_VIEW)])

    async def test_a_tile_of_a_game_with_an_entry_opens_that_entrys_pane_and_has_no_menu(
            self) -> None:
        body = await self._draw()
        tile = _tile_of(body, "3 of your games")

        self.assertEqual([child for child in tile.descendants()
                          if type(child).__name__ == "ContextMenu"], [])
        await _press(tile)

        self.assertEqual(self.selected, [{"id": "E1"}])

    async def test_a_tile_of_a_game_matched_to_no_entry_lists_it_on_the_no_tables_view(
            self) -> None:
        body = await self._draw()
        _picker(body, "no_tables").set_value("no_tables")
        await settle()

        await _press(_tile_of(body, "Mine"))

        self.assertEqual(self.selected, [])
        self.assertEqual([(one.view, one.keys[games.ARRIVE_VIEW]) for one in self.opened],
                         [("games", games.NO_TABLES_VIEW)])


    async def test_a_tile_of_an_entry_the_catalog_does_not_list_opens_the_duplicates_view(
            self) -> None:
        self.games = [held_game("g1", "Gone", "GONE"), held_game("g2", "Gone too", "GONE")]
        body = await self._draw()

        await _press(_tile_of(body, "2 of your games"))

        self.assertEqual(self.selected, [])
        self.assertEqual([(one.view, one.keys[games.ARRIVE_VIEW]) for one in self.opened],
                         [("games", games.DUPLICATES_VIEW)])

    async def test_picking_another_window_draws_the_shelf_from_the_new_read(self) -> None:
        body = await self._draw()
        self.games = [held_game("g1", "Atlantis", "E1")]
        window = next(one for one in body.descendants()
                      if isinstance(one, dropdown.Select) and not one.is_deleted
                      and one.value == 7)

        window.set_value(30)
        await settle()

        self.assertNotIn("Library Health", words_of(body))

    async def test_reading_the_shelves_again_keeps_the_finding_the_user_picked(self) -> None:
        body = await self._draw()
        _picker(body, "no_tables").set_value("no_tables")
        await settle()

        await self.refresh()
        await settle()

        self.assertEqual(_picker(body, "no_tables").value, "no_tables")
        self.assertIn("Mine", words_of(body))
        self.assertNotIn("3 of your games", words_of(body))


class GamesViewTests(unittest.TestCase):
    """See All opens Games on the same games the shelf counts."""

    HELD: list[dict[str, Any]] = [
        {"id": "g1", "name": "Atlantis", "vps_id": "E1", "table_count": 1},
        {"id": "g2", "name": "Atlantis again", "vps_id": "E1", "table_count": 0},
        {"id": "g3", "name": "Joust", "vps_id": "E2", "table_count": 2},
        {"id": "g4", "name": "Mine", "table_count": 0},
        {"id": "g5", "name": "Also mine", "table_count": 1}]

    def _rows(self) -> list[dict[str, Any]]:
        library = data.Library.__new__(data.Library)
        library.games = self.HELD
        library.media = {}
        library._client = mock.Mock()
        return library.game_rows()

    def _shelf(self) -> dict[str, Any]:
        return vps_home.health(
            {"counted": "x", "people": [], "rows": []},
            [held_game(one["id"], one["name"], one.get("vps_id", ""), one["table_count"])
             for one in self.HELD])

    def test_a_game_is_shared_where_another_is_matched_to_its_entry(self) -> None:
        self.assertEqual({row["id"]: row["vps_shared"] for row in self._rows()},
                         {"g1": "shared", "g2": "shared", "g3": "", "g4": "", "g5": ""})

    def test_games_matched_to_nothing_do_not_share_it(self) -> None:
        shared = {row["id"] for row in self._rows() if row["vps_shared"]}

        self.assertNotIn("g4", shared)
        self.assertNotIn("g5", shared)

    def test_the_view_keeps_the_games_the_duplicates_finding_names(self) -> None:
        preset = _preset(games.DUPLICATES_VIEW)
        kept = {row["id"] for row in self._rows()
                if row[next(iter(preset.filters))] in preset.filters["vps_shared"]["values"]}

        [duplicate] = self._shelf()["duplicates"]["games"]
        self.assertEqual(kept, set(duplicate["game_ids"]))

    def test_the_no_tables_view_keeps_the_games_the_other_finding_names(self) -> None:
        preset = _preset(games.NO_TABLES_VIEW)
        self.assertEqual(preset.filters, games.NO_TABLES)

        counted = {row["id"] for row in self._rows() if row["table_count"] == 0}

        self.assertEqual(counted, {one["game_id"] for one in self._shelf()["no_tables"]["games"]})

    def test_the_view_shows_the_name_the_table_count_and_whether_it_is_shared(self) -> None:
        preset = _preset(games.DUPLICATES_VIEW)

        self.assertEqual(preset.columns[:3], ("name", "table_count", "vps_shared"))
        self.assertTrue(preset.help)

    def test_the_view_is_named_and_arrives_through_the_needs_opener(self) -> None:
        opens = needs.duplicates_opens()

        self.assertEqual((opens.view, opens.keys[games.ARRIVE_VIEW]),
                         ("games", games.DUPLICATES_VIEW))

    def test_the_column_says_shared_only_where_it_is_and_filters_on_the_pair(self) -> None:
        column = next(one for one in games.COLUMNS if one["field"] == "vps_shared")

        self.assertEqual(column["headerName"].replace("\n", " "), "VPS Entry")
        self.assertIn('"Shared"', column[":valueFormatter"])
        self.assertEqual([(one["value"], one["label"])
                          for one in column["filterParams"]["choices"]],
                         [("shared", "Shared"), ("", "Not Shared")])
        self.assertEqual(games.COLUMN_SECTIONS["vps_shared"], "game_details")


if __name__ == "__main__":
    unittest.main()

"""Games to Match on Discovery's front page: the shelf drawn from one read, what a tile says,
the Match picker it opens, and where See All goes."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from manager.library import discovery, games
from manager.overview import needs
from tests.manager.test_needs import FakeLibrary
from tests.manager.test_needs import game as need_game
from tests.support.home_shelf import (
    PEOPLE,
    ShelfHarness,
    entry,
    held_game,
    settle,
    table,
    words_of,
)

FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
    entry("e1", "Joust", [table("t1", 0)], manufacturer="Williams", year=1983),
    entry("e2", "Batman", [table("t2", 0)], manufacturer="Data East", year=1991),
]}
UNMATCHED: list[dict[str, Any]] = [
    {"game_id": "g1", "name": "Joust", "manufacturer": "", "year": "1984"},
    {"game_id": "g2", "name": "Unknown Machine", "manufacturer": "", "year": "1990"},
    {"game_id": "g3", "name": "batman", "manufacturer": "Data East", "year": "1991"},
]


def _tile(body: Any, name: str) -> Any:
    [card] = [one for one in body.descendants()
              if not one.is_deleted and "manager-shelf-tile" in one.classes
              and any(getattr(child, "text", "") == "Looks like Williams 1983"
                      or getattr(child, "text", "") == "Looks like Data East 1991"
                      for child in one.descendants())
              and any(getattr(child, "text", "") == name for child in one.descendants())]
    return card


async def _click(card: Any) -> None:
    for listener in list(card._event_listeners.values()):
        if listener.type == "click" and listener.handler is not None:
            answer = listener.handler(mock.Mock())
            if asyncio.iscoroutine(answer):
                await answer


class UnmatchedShelf(ShelfHarness):
    FOUND = FOUND
    UNMATCHED = UNMATCHED

    def setUp(self) -> None:
        super().setUp()
        self.walk = mock.AsyncMock(return_value=1)
        self.forgot = mock.Mock()
        self.library.forget_catalog = self.forgot  # type: ignore[method-assign]
        patcher = mock.patch.object(discovery.vps_match, "walk", self.walk)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_the_shelf_lists_the_games_that_have_a_likely_entry(self) -> None:
        words = words_of(await self._draw())

        self.assertIn("Games to Match", words)
        self.assertIn("Joust", words)
        self.assertIn("batman", words)
        self.assertNotIn("Unknown Machine", words)

    async def test_a_tile_says_what_the_game_is_likely_to_be(self) -> None:
        words = words_of(await self._draw())

        self.assertIn("Looks like Williams 1983", words)
        self.assertIn("Looks like Data East 1991", words)
        self.assertIn("Data East 1991", words)

    async def test_the_games_are_listed_by_name(self) -> None:
        words = words_of(await self._draw())
        shelf = words[words.index("Games to Match"):]

        self.assertLess(shelf.index("batman"), shelf.index("Joust"))

    async def test_the_shelf_follows_the_other_versions_and_comes_before_the_authors(
            self) -> None:
        self.found = {**FOUND, "rows": [
            *FOUND["rows"],
            entry("h1", "Joust", [table("t3", 0)], in_library=True, manufacturer="Bally",
                  year=1969),
            {**entry("g5", "Eden", [table("t90", 0)], in_library=True),
             "releases": {"tableFiles": [{**table("t90", 0), "held": True}]}}]}

        words = words_of(await self._draw())

        self.assertLess(words.index("Other Versions of Your Games"), words.index("Games to Match"))
        self.assertLess(words.index("Games to Match"), words.index("Authors You Collect"))

    async def test_no_likely_entry_draws_no_shelf(self) -> None:
        self.unmatched = [UNMATCHED[1]]

        self.assertNotIn("Games to Match", words_of(await self._draw()))

    async def test_no_unmatched_game_draws_no_shelf(self) -> None:
        self.unmatched = []

        self.assertNotIn("Games to Match", words_of(await self._draw()))

    async def test_it_has_a_see_all_of_its_own(self) -> None:
        with_shelf = self._see_alls(await self._draw())
        self.unmatched = []

        self.assertEqual(with_shelf - self._see_alls(await self._draw()), 1)

    async def test_see_all_opens_the_games_unmatched_view(self) -> None:
        self.state["open_on"] = mock.Mock()
        await self._draw()

        self.spied["see_unmatched"]()

        self.state["open_on"].assert_called_once_with(needs.Opens("games", games.UNMATCHED))

    async def test_see_all_is_the_view_the_overview_need_opens(self) -> None:
        self.state["open_on"] = mock.Mock()
        await self._draw()
        self.spied["see_unmatched"]()
        need = {one.key: one for one in needs.collect(
            FakeLibrary([need_game("g1", vps_id="")]), {})}["unmatched"]

        self.state["open_on"].assert_called_once_with(need.opens)

    async def test_a_tile_opens_the_match_picker_on_its_game_and_has_no_menu(self) -> None:
        body = await self._draw()
        card = _tile(body, "Joust")

        self.assertEqual([child for child in card.descendants()
                          if type(child).__name__ == "ContextMenu"], [])
        await _click(card)

        self.walk.assert_awaited_once_with(self.library, [{"id": "g1", "name": "Joust"}])
        self.forgot.assert_called_once_with()
        self.assertEqual(self.selected, [])

    async def test_a_picker_closed_with_no_pick_leaves_the_shelf_and_the_catalog_alone(
            self) -> None:
        body = await self._draw()
        card = _tile(body, "Joust")
        self.walk.return_value = 0
        drawn = self.client.discovery_home.call_count

        await _click(card)
        await settle()

        self.forgot.assert_not_called()
        self.assertEqual(self.client.discovery_home.call_count, drawn)
        self.assertIn("Joust", words_of(body))

    async def test_a_game_just_matched_leaves_the_shelf(self) -> None:
        body = await self._draw()
        card = _tile(body, "Joust")

        async def matched(*_args: Any) -> int:
            self.unmatched = [one for one in self.unmatched if one["game_id"] != "g1"]
            return 1

        self.walk.side_effect = matched
        await _click(card)
        await settle()

        words = words_of(body)
        self.assertNotIn("Joust", words[words.index("Games to Match"):])
        self.assertIn("batman", words)

    async def test_picking_another_window_draws_the_shelf_from_the_new_read(self) -> None:
        from manager.kit import dropdown
        body = await self._draw()
        self.unmatched = []
        window = next(one for one in body.descendants()
                      if isinstance(one, dropdown.Select) and not one.is_deleted
                      and one.value == 7)

        window.set_value(30)
        await settle()

        self.assertNotIn("Games to Match", words_of(body))

    async def test_reading_the_shelves_again_draws_this_one_from_the_new_read(self) -> None:
        body = await self._draw()
        self.unmatched = []

        await self.refresh()
        await settle()

        self.assertNotIn("Games to Match", words_of(body))


class EveryShelfTogether(ShelfHarness):
    """Every Home shelf present at once, in the order Home draws them."""

    FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
        entry("h1", "Joust", [{**table("t1", 0), "held": True}], in_library=True,
              manufacturer="Bally", year=1982),
        {**entry("f1", "Joust", [table("t2", 0)], manufacturer="Atari", year=1990)},
        entry("n1", "Eden", [table("t3", 0)], in_library=True),
        entry("o1", "Batman", [table("t4", 0)], manufacturer="Data East", year=1991),
        entry("u1", "Alien", [table("t5", 0)], manufacturer="Williams", year=1980),
    ]}
    UNMATCHED = [{"game_id": "g9", "name": "Alien", "manufacturer": "", "year": "1980"}]

    def setUp(self) -> None:
        super().setUp()
        self.games = [held_game("g1", "Mine", "", tables=0)]
        self.journal = [{"game_id": "o1", "kind": "tableFiles", "vps_file_id": "ro1",
                         "page": "https://x/p",
                         "at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(
                             timespec="seconds").replace("+00:00", "Z")}]

    async def test_the_shelves_follow_each_other_in_the_order_they_were_asked_for(self) -> None:
        words = words_of(await self._draw())

        order = ["Games You Don't Have", "Other Versions of Your Games", "Games to Match",
                 "Library Health", "Opened but Not Imported", "Authors You Collect"]
        self.assertEqual([word for word in words if word in order], order)


if __name__ == "__main__":
    unittest.main()

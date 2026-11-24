"""Opened but Not Imported on Discovery's front page: the shelf drawn from one read, and what
a tile says and does."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from common.online import vps_home
from manager.kit import dropdown
from tests.support.home_shelf import (
    PEOPLE,
    ShelfHarness,
    entry,
    mark,
    settle,
    table,
    words_of,
)

HEADING = "Opened but Not Imported"


def game(vps_id: str, name: str, *, held: bool = False, manufacturer: str = "",
         year: int | None = None) -> dict[str, Any]:
    return entry(vps_id, name, [table("t" + vps_id, 0)], in_library=held,
                 manufacturer=manufacturer, year=year)


def page(vps_id: str, **ago: float) -> dict[str, Any]:
    at = (datetime.now(UTC) - timedelta(**ago)).isoformat(timespec="seconds")
    return {"game_id": vps_id, "kind": "tableFiles", "vps_file_id": "r" + vps_id,
            "page": "https://x/p", "at": at.replace("+00:00", "Z")}


FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
    {**game("h1", "Joust", held=True, manufacturer="Bally", year=1982),
     "releases": {"tableFiles": [{**table("th1", 0), "held": True}]}},
    game("o1", "Atlantis", manufacturer="Bally", year=1989),
    game("o2", "Batman", manufacturer="Williams", year=1997),
    game("n1", "Eden"),
    game("f1", "Joust", manufacturer="Atari", year=1990),
]}


class OpenedShelf(ShelfHarness):
    FOUND = FOUND

    def setUp(self) -> None:
        super().setUp()
        self.journal = [page("o2", minutes=5), page("o1", hours=2)]

    async def test_the_shelf_lists_the_games_whose_page_was_opened(self) -> None:
        words = words_of(await self._draw())

        self.assertIn(HEADING, words)
        self.assertIn("Last opened first", words)
        self.assertIn("Williams 1997", words)
        self.assertIn("Bally 1989", words)

    async def test_a_tile_says_when_the_page_was_opened(self) -> None:
        words = words_of(await self._draw())

        self.assertIn("You opened its page 5 minutes ago", words)
        self.assertIn("You opened its page 2 hours ago", words)

    async def test_the_game_opened_last_comes_first(self) -> None:
        words = words_of(await self._draw())
        shelf = words[words.index(HEADING):]

        self.assertLess(shelf.index("Williams 1997"), shelf.index("Bally 1989"))

    async def test_a_game_never_opened_is_not_on_it(self) -> None:
        shelf = words_of(await self._draw())
        shelf = shelf[shelf.index(HEADING):]

        self.assertNotIn("Eden", shelf)

    async def test_the_shelf_follows_games_you_dont_have_and_comes_before_the_authors(
            self) -> None:
        words = words_of(await self._draw())

        self.assertLess(words.index("Games You Don't Have"),
                        words.index("Other Versions of Your Games"))
        self.assertLess(words.index("Other Versions of Your Games"), words.index(HEADING))
        self.assertLess(words.index(HEADING), words.index("Authors You Collect"))

    async def test_nothing_opened_draws_no_shelf(self) -> None:
        self.journal = []

        self.assertNotIn(HEADING, words_of(await self._draw()))

    async def test_a_game_the_library_holds_draws_no_shelf(self) -> None:
        self.journal = [page("h1", minutes=5)]

        self.assertNotIn(HEADING, words_of(await self._draw()))

    async def test_a_library_never_compared_draws_no_shelf(self) -> None:
        self.found = {**FOUND, "counted": "", "rows": [
            {**row, "in_library": None} for row in FOUND["rows"]]}

        self.assertNotIn(HEADING, words_of(await self._draw()))

    async def test_an_ignored_game_leaves_the_shelf(self) -> None:
        self.client.discovery_marks.return_value = [mark("ignored", "game", "o2")]

        words = words_of(await self._draw())

        self.assertNotIn("Williams 1997", words[words.index(HEADING):])
        self.assertIn("Bally 1989", words[words.index(HEADING):])

    async def test_the_shelf_has_no_see_all_and_every_game_has_a_tile(self) -> None:
        with_shelf = self._see_alls(await self._draw())
        self.journal = []
        without = self._see_alls(await self._draw())
        many = [game(f"g{n:02d}", f"Game {n:02d}") for n in range(vps_home.SHELF_SHOWN + 3)]
        self.found = {**FOUND, "rows": [*FOUND["rows"], *many]}
        self.journal = [page(one["vps_id"], minutes=n + 1) for n, one in enumerate(many)]
        words = words_of(await self._draw())

        self.assertEqual(with_shelf, without)
        self.assertEqual(
            sum(1 for one in words[words.index(HEADING):] if one.startswith("Game ")), len(many))

    async def test_picking_another_window_draws_the_shelf_from_the_new_read(self) -> None:
        body = await self._draw()
        self.journal = []
        window = next(one for one in body.descendants()
                      if isinstance(one, dropdown.Select) and not one.is_deleted
                      and one.value == 7)

        window.set_value(30)
        await settle()

        self.assertNotIn(HEADING, words_of(body))

    async def test_a_tile_opens_the_game_and_has_no_menu(self) -> None:
        body = await self._draw()
        tiles = [one for one in body.descendants()
                 if not one.is_deleted and "manager-shelf-tile" in one.classes
                 and any(getattr(child, "text", "") == "You opened its page 5 minutes ago"
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

    async def test_reading_the_shelves_again_draws_this_one_from_the_new_read(self) -> None:
        body = await self._draw()
        self.assertIn(HEADING, words_of(body))
        self.journal = []

        await self.refresh()
        await settle()

        self.assertNotIn(HEADING, words_of(body))

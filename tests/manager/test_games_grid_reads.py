"""What the Games grid reads before it draws, after an import let the page's copy go."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from typing import Any
from unittest import mock
from unittest.mock import Mock

from nicegui import core, ui

from manager import page
from manager.kit import grid
from manager.kit.data import Library
from manager.library import games, import_dialog, uploads
from manager.workbench import game_table

GAME = {"id": "g-1", "name": "Some Game", "folder": "/games/Some Game"}
TABLE = {"id": "t-1", "filename": "Some Game.vpx", "default": True}
DROPPED = {"id": "t-2", "filename": "Some Game (Mod).vpx"}


def _library() -> tuple[Library, Mock]:
    client = Mock()
    client.games.return_value = [GAME]
    client.library_game_collections.return_value = {}
    client.all_media.return_value = [
        {"id": "g-1:wheel:", "game_id": "g-1", "kind": "wheel", "present": True,
         "file": "wheel.png", "via": "game"}]
    client.all_tables.return_value = [{"game_id": "g-1", **TABLE}]
    client.library_policy.return_value = {}
    library = Library(client)
    library.games = [GAME]
    library.media = library._shared_media()
    library.load_game_collections()
    return library, client


class TheGamesGridAfterAnImport(unittest.TestCase):
    def test_it_reads_its_media_again_before_it_draws(self) -> None:
        library, _client = _library()
        library.refresh_after_import()

        reads = page.reads_before_drawing("games", library)

        assert reads is not None
        reads()
        self.assertEqual(library.kinds_present(), ["wheel"])
        self.assertIn("media_wheel", library.game_rows()[0])

    def test_it_reads_the_media_once_for_every_game(self) -> None:
        library, client = _library()
        library.refresh_after_import()
        client.all_media.reset_mock()
        reads = page.reads_before_drawing("games", library)

        assert reads is not None
        reads()
        self.assertEqual(client.all_media.call_count, 1)

    def test_it_reads_nothing_when_it_holds_what_it_draws(self) -> None:
        library, _client = _library()
        library.load_games_grid()

        self.assertIsNone(page.reads_before_drawing("games", library))


class ARowPutBackAfterAnImport(unittest.TestCase):
    """The Games grid's own row rebuilds, driven on a grid built from the library."""

    def setUp(self) -> None:
        self.library, self.client = _library()
        self.client.preferences.return_value = {}
        self.client.tables.return_value = [TABLE]
        self.client.upload_plan.return_value = {"items": [{"kind": "table"}]}
        self.library.load_kept_kinds()
        self.before = self.library.game_rows()[0]["media_wheel"]
        self.state: dict[str, Any] = {"view": "games"}
        self.sent: list[dict[str, Any]] = []
        for patcher in (mock.patch.object(ui, "run_javascript"),
                        mock.patch.object(ui, "notify"),
                        mock.patch.object(grid, "transact", self._transact)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _transact(self, _table: Any, _rows: Any, transaction: dict[str, Any],
                  _by_id: Any) -> None:
        self.sent.extend(transaction.get("update") or [])

    def _on_the_grid(self, act: Callable[[], Awaitable[None]]) -> None:
        holder = ui.card()

        async def inside() -> None:
            was = core.loop
            core.loop = asyncio.get_running_loop()
            try:
                with holder:
                    games.build(self.library.game_rows(), self.library.kinds_present(),
                                self.library, lambda _row: None, self.state)
                    await act()
            finally:
                core.loop = was
        asyncio.run(inside())

    async def _imported(self, *_args: Any, on_done: Callable[[Any], Awaitable[None]],
                        **_kwargs: Any) -> None:
        await on_done({})

    def test_a_table_dropped_on_the_row_leaves_its_media(self) -> None:
        async def drop() -> None:
            done = await game_table.after_a_row_drop(self.library, self.state, "g-1",
                                                    lambda: None)
            self.client.tables.return_value = [TABLE, DROPPED]
            with mock.patch.object(import_dialog, "open_for", self._imported):
                await uploads.confirmed_import(
                    self.library, "u-1", {}, source=DROPPED["filename"], on_done=done,
                    game_id="g-1", game_dir=GAME["folder"], add_table=True)

        self._on_the_grid(drop)

        self.assertEqual(["g-1"], [row["id"] for row in self.sent])
        self.assertEqual(self.before, self.sent[0].get("media_wheel"))

    def test_a_collection_add_after_an_import_leaves_its_media(self) -> None:
        async def add() -> None:
            self.library.refresh_after_import()
            await self.state["refresh_games"](["g-1"])

        self._on_the_grid(add)

        self.assertEqual(["g-1"], [row["id"] for row in self.sent])
        self.assertEqual(self.before, self.sent[0].get("media_wheel"))

    def test_a_row_put_back_from_a_task_with_no_slot_still_reaches_the_grid(self) -> None:
        # A panel rebuild runs as a task of its own once the control that started it is
        # gone, so there is no current slot to read.
        async def from_a_bare_task() -> None:
            await asyncio.create_task(self.state["refresh_game"]("g-1"))

        self._on_the_grid(from_a_bare_task)

        self.assertEqual(["g-1"], [row["id"] for row in self.sent])


if __name__ == "__main__":
    unittest.main()

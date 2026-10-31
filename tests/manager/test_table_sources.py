"""How a table reaches a game from Add a Table, and from a drop on the game's panel or
its row."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.i18n import t
from manager import page
from manager.library import import_dialog, mediasource, uploads
from manager.library.drop_target import Where
from tests.support.clicks import press


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


def _context(library: Any) -> dict[str, Any]:
    return {"library": library, "game_id": "g-1",
            "game": {"name": "Some Game", "folder": "/games/Some Game"}}


class CopyOrUseWhereItIs(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.library = Mock()
        self.done = AsyncMock()
        self.sources = mediasource._Table(_context(self.library), self.done)
        for patched in (patch("manager.kit.offload.run.io_bound", new=_now),
                        patch.object(mediasource.ui, "notify")):
            patched.start()
            self.addCleanup(patched.stop)

    async def test_a_copy_is_the_tables_import(self) -> None:
        await press(self.sources._chosen, "/share/Other.vpx")

        self.library.import_table_file.assert_called_once_with("g-1", "/share/Other.vpx")
        self.library.add_referenced_table.assert_not_called()
        self.done.assert_awaited_once()

    async def test_use_it_where_it_is_points_at_it(self) -> None:
        self.sources.copies = False

        await press(self.sources._chosen, "/share/Other.vpx")

        self.library.add_referenced_table.assert_called_once_with("g-1", "/share/Other.vpx")
        self.library.import_table_file.assert_not_called()

    async def test_a_refusal_adds_nothing(self) -> None:
        self.library.import_table_file.side_effect = RuntimeError("not readable")

        await press(self.sources._chosen, "/elsewhere/Other.vpx")

        self.done.assert_not_awaited()


class Uploaded(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.done = AsyncMock()
        self.confirmed = AsyncMock()
        for patched in (patch.object(uploads, "analyzed", new=AsyncMock(return_value={})),
                        patch.object(uploads, "confirmed_import", new=self.confirmed)):
            patched.start()
            self.addCleanup(patched.stop)

    async def _dropped(self) -> Mapping[str, Any]:
        sources = mediasource._Table(_context(Mock()), self.done)
        await press(sources.arrived, uploads.Drop(upload_id="u-1", name="Other.vpx"))
        assert self.confirmed.await_args is not None
        return self.confirmed.await_args.kwargs

    async def test_it_joins_the_game_rather_than_replacing_its_table(self) -> None:
        asked = await self._dropped()

        self.assertTrue(asked["add_table"])
        self.assertEqual(asked["game_dir"], "/games/Some Game")

    async def test_a_drop_on_the_panel_finishes_with_no_dialog_to_close(self) -> None:
        asked = await self._dropped()

        await press(asked["on_done"])

        self.done.assert_awaited_once()


class ADropOnAGamesRow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.library = Mock(games=[{"id": "g-1", "name": "Some Game",
                                    "folder": "/games/Some Game"}])
        self.library.tables_for.return_value = [
            {"id": "t-1", "version": "0.9", "authors": ["Someone"], "default": True}]
        self.confirmed = AsyncMock()
        self.said = Mock()
        for patched in (patch("manager.kit.offload.run.io_bound", new=_now),
                        patch.object(uploads, "analyzed",
                                     new=AsyncMock(return_value={"has_game": True})),
                        patch.object(uploads, "confirmed_import", new=self.confirmed),
                        patch.object(ui, "notify", new=self.said)):
            patched.start()
            self.addCleanup(patched.stop)

    async def _dropped(self, **where: Any) -> Mapping[str, Any]:
        drop = uploads.Drop(where=Where(row_id="g-1", **where), upload_id="u-1",
                            name="Other.vpx")
        await press(page._took_a_drop, self.library, {"view": "games"}, Mock(), drop)
        assert self.confirmed.await_args is not None
        return self.confirmed.await_args.kwargs

    async def test_it_joins_that_game_rather_than_replacing_its_table(self) -> None:
        asked = await self._dropped()

        self.assertTrue(asked["add_table"])
        self.assertEqual(asked["game_dir"], "/games/Some Game")

    async def test_a_drop_on_a_picture_adds_no_table(self) -> None:
        asked = await self._dropped(col_id="media_wheel", cell=("manager-drop-media",))

        self.assertFalse(asked["add_table"])

    async def test_what_arrived_is_named_with_its_game(self) -> None:
        asked = await self._dropped()
        self.library.tables_for.return_value = [
            {"id": "t-1", "version": "0.9", "authors": ["Someone"], "default": True},
            {"id": "t-2", "version": "1.0", "authors": []}]

        await press(asked["on_done"])

        self.said.assert_called_once_with(
            t("manager.game_tables.added", table="1.0", game="Some Game"), type="positive")


class AnAddIsSaidOnce(unittest.IsolatedAsyncioTestCase):
    async def _imported(self, report: dict[str, Any]) -> Mock:
        library = Mock()
        said = Mock()
        with patch("manager.kit.offload.run.io_bound", new=_now), \
                patch.object(import_dialog.frame, "opened",
                             return_value=_Confirmed({"report": report})), \
                patch.object(import_dialog.frame, "footer"), \
                patch.object(import_dialog.frame, "cancel"), \
                patch.object(import_dialog.frame, "answer"), \
                patch.object(import_dialog, "ui") as drawn:
            drawn.notify = said
            await press(import_dialog.open_for,
                        library, "u-1", {"items": [{"index": 0}]}, source="Other.vpx")
        return said

    async def test_a_table_added_leaves_the_naming_to_the_add(self) -> None:
        said = await self._imported({"imported": ["table"], "added_tables": ["t-2"]})

        said.assert_not_called()

    async def test_anything_else_is_counted(self) -> None:
        said = await self._imported({"imported": ["backglass"], "added_tables": []})

        said.assert_called_once_with(
            t("manager.import_dialog.imported_item", count=1), type="positive")


class _Confirmed:
    """The import dialog, answered with what the import it ran ended in."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def __enter__(self) -> _Confirmed:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def __await__(self) -> Any:
        return _answered(self.answer).__await__()


async def _answered(value: Any) -> Any:
    return value


class AnAddDialogTakesItsOwnDrop(unittest.TestCase):
    def test_it_declares_itself_so_the_page_leaves_it_alone(self) -> None:
        element = Mock(_props={})

        with patch.object(uploads, "listener"):
            mediasource.take_drops(element, AsyncMock())

        self.assertEqual(json.loads(element._props[uploads.DROP]), {"own": True})
        self.assertEqual({"dragover", "dragleave", "drop"},
                         {one.args[0] for one in element.on.call_args_list})


if __name__ == "__main__":
    unittest.main()

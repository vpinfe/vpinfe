"""What the Manager does with a drop once it has said where the file landed."""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.i18n import t
from manager import page
from manager.library import uploads
from manager.library.drop_target import Where
from manager.workbench import core
from tests.support.arrivals import GAME, library
from tests.support.clicks import press

TABLES = [{"id": "t-1", "game_id": "g-1"}, {"id": "t-2", "game_id": "g-1"}]
MEDIA = [
    {"id": "g-1:wheel:", "game_id": "g-1", "kind": "wheel", "page": 1, "table": "",
     "via": None},
    {"id": "g-1:flyer::2", "game_id": "g-1", "kind": "flyer", "page": 2, "table": "",
     "via": None},
    {"id": "g-1:topper:", "game_id": "g-1", "kind": "topper", "page": 1, "table": "",
     "via": "unused"},
    {"id": "g-1:flyer:t-9", "game_id": "g-1", "kind": "flyer", "page": 1, "table": "t-9",
     "via": "orphan"},
]
ASSETS = [{"id": "g-1:pov:", "game_id": "g-1", "kind": "pov", "table": ""}]
SLOT = ("manager-drop-media",)


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class ADrop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.library, _client = library(tables=TABLES, media=MEDIA, assets=ASSETS)
        self.aborted = Mock()
        self.library.abort_upload = self.aborted  # type: ignore[method-assign]
        self.analyzed = AsyncMock(return_value={"has_game": True})
        self.confirmed = AsyncMock()
        self.said = Mock()
        for patched in (patch("manager.kit.offload.run.io_bound", new=_now),
                        patch("manager.page.run.io_bound", new=_now),
                        patch.object(uploads, "analyzed", new=self.analyzed),
                        patch.object(uploads, "confirmed_import", new=self.confirmed),
                        patch.object(page.game_table, "after_a_row_drop",
                                     new=AsyncMock(return_value=Mock())),
                        patch.object(ui, "notify", new=self.said)):
            patched.start()
            self.addCleanup(patched.stop)

    async def _dropped(self, view: str, upload_id: str = "u-1", **where: Any) -> None:
        drop = uploads.Drop(where=Where(**where), upload_id=upload_id, name="Other.png")
        await press(page._took_a_drop, self.library, {"view": view}, Mock(), drop)

    def _sent(self) -> dict[str, Any]:
        assert self.confirmed.await_args is not None
        return dict(self.confirmed.await_args.kwargs)

    def _turned_away_with(self, key: str) -> None:
        self.said.assert_called_once_with(t(key), type="warning")
        self.confirmed.assert_not_awaited()


class ASlotDrop(ADrop):
    async def test_it_skips_the_analyzer_and_names_the_slot_and_page(self) -> None:
        await self._dropped("media", row_id="g-1:flyer::2")

        self.analyzed.assert_not_awaited()
        sent = self._sent()
        self.assertEqual((sent["media_kind"], sent["page"], sent["game_id"]),
                         ("flyer", 2, "g-1"))
        self.assertEqual(sent["game_dir"], GAME["folder"])

    async def test_a_grid_cell_names_the_slot_the_same_way(self) -> None:
        await self._dropped("games", row_id="g-1", col_id="media_wheel", cell=SLOT)

        self.analyzed.assert_not_awaited()
        sent = self._sent()
        self.assertEqual(sent["media_kind"], "wheel")
        self.assertFalse(sent["add_table"])

    async def test_a_declared_tile_names_its_table_too(self) -> None:
        await self._dropped("games", declared={"game": "g-1", "table": "t-2", "slot": "wheel"})

        sent = self._sent()
        self.assertEqual((sent["media_kind"], sent["table_id"]), ("wheel", "t-2"))


class WhatIsPutRightAfterASlotDrop(ADrop):
    """The grid keeps its scroll: the rows are refreshed in place, never the page."""

    async def _finished(self, view: str, state: dict[str, Any], **where: Any) -> Mock:
        redraw = Mock()
        drop = uploads.Drop(where=Where(**where), upload_id="u-1", name="Other.png")
        await press(page._took_a_drop, self.library, {"view": view, **state}, redraw, drop)
        await press(self._sent()["on_done"])
        return redraw

    async def test_the_games_grid_refreshes_that_games_row(self) -> None:
        refresh = AsyncMock()

        redraw = await self._finished("games", {"refresh_game": refresh}, row_id="g-1",
                                      col_id="media_wheel", cell=SLOT)

        refresh.assert_awaited_once_with("g-1")
        redraw.assert_not_called()

    async def test_a_panel_open_on_that_game_is_rebuilt_in_place_of_the_row(self) -> None:
        shown, refresh = AsyncMock(), AsyncMock()

        redraw = await self._finished(
            "games", {"panel_rebuild": shown, "refresh_game": refresh, "game": "g-1"},
            row_id="g-1", col_id="media_wheel", cell=SLOT)

        shown.assert_awaited_once_with()
        refresh.assert_not_awaited()
        redraw.assert_not_called()

    async def test_a_panel_open_on_another_game_is_left_alone(self) -> None:
        shown, refresh = AsyncMock(), AsyncMock()

        await self._finished(
            "games", {"panel_rebuild": shown, "refresh_game": refresh, "game": "g-2"},
            row_id="g-1", col_id="media_wheel", cell=SLOT)

        shown.assert_not_awaited()
        refresh.assert_awaited_once_with("g-1")

    async def test_the_media_grid_refreshes_that_games_files(self) -> None:
        refresh = AsyncMock()

        redraw = await self._finished("media", {"refresh_files": refresh},
                                      row_id="g-1:wheel:")

        refresh.assert_awaited_once_with("g-1")
        redraw.assert_not_called()

    async def test_an_asset_drop_on_the_assets_grid_refreshes_in_place_too(self) -> None:
        refresh = AsyncMock()

        redraw = await self._finished("assets", {"refresh_files": refresh}, row_id="g-1:pov:")

        refresh.assert_awaited_once_with("g-1")
        redraw.assert_not_called()

    async def test_a_panel_that_registered_no_rebuild_leaves_the_row_refresh(self) -> None:
        refresh = AsyncMock()

        await self._finished(
            "games", {"panel_rebuild": None, "refresh_game": refresh, "game": "g-1"},
            row_id="g-1", col_id="media_wheel", cell=SLOT)

        refresh.assert_awaited_once_with("g-1")

    async def test_a_view_with_no_row_refresh_redraws_the_page(self) -> None:
        redraw = await self._finished("overview", {}, declared={"game": "g-1", "slot": "wheel"})

        redraw.assert_called_once_with()


class ATableDrop(ADrop):
    async def test_a_tables_row_replaces_that_table(self) -> None:
        await self._dropped("tables", row_id="t-2")

        sent = self._sent()
        self.assertEqual((sent["table_id"], sent["replace_table"], sent["add_table"]),
                         ("t-2", "t-2", False))

    async def test_a_game_adds_a_table_to_it(self) -> None:
        await self._dropped("games", row_id="g-1", col_id="name")

        sent = self._sent()
        self.assertTrue(sent["add_table"])
        self.assertEqual((sent["table_id"], sent["replace_table"]), ("", ""))

    async def test_an_asset_cell_replaces_no_table(self) -> None:
        await self._dropped("tables", row_id="t-2", col_id="asset_pov",
                            cell=("manager-drop-asset",))

        sent = self._sent()
        self.assertEqual((sent["asset_kind"], sent["table_id"], sent["replace_table"]),
                         ("pov", "t-2", ""))


class ARowThatTakesNothing(ADrop):
    async def test_an_orphan_row_says_why_and_lets_go_of_the_upload(self) -> None:
        await self._dropped("media", row_id="g-1:flyer:t-9")

        self._turned_away_with("manager.drop.orphan")
        self.aborted.assert_called_once_with("u-1")

    async def test_a_row_the_library_does_not_hold_says_so(self) -> None:
        await self._dropped("games", row_id="g-9")

        self._turned_away_with("manager.page.row_unknown")
        self.aborted.assert_called_once_with("u-1")

    async def test_a_library_that_cannot_be_read_lets_go_of_the_upload(self) -> None:
        with patch.object(self.library, "load_tables", side_effect=OSError("gone")), \
                self.assertLogs("vpinfe.manager.page", "ERROR"):
            await self._dropped("tables", row_id="t-1")

        self._turned_away_with("manager.uploads.not_work")
        self.aborted.assert_called_once_with("u-1")


class APictureDrop(ADrop):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.library.upload_image = Mock(return_value=("picture.png", b"png"))  # type: ignore[method-assign]
        self.library.set_collection_image = Mock()  # type: ignore[method-assign]
        self.reread = AsyncMock()
        self.state: dict[str, Any] = {"view": "collections", "refresh_collections": self.reread}
        patcher = patch.object(page.players_page, "dropped_picture", new=AsyncMock())
        self.player = patcher.start()
        self.addCleanup(patcher.stop)

    async def _on(self, **where: Any) -> None:
        drop = uploads.Drop(where=Where(**where), upload_id="u-1", name="Other.png")
        await press(page._took_a_drop, self.library, self.state, Mock(), drop)

    async def test_a_collections_row_sets_that_collections_picture_in_place(self) -> None:
        await self._on(row_id="Favorites")

        self.library.set_collection_image.assert_called_once_with(
            "Favorites", "picture.png", b"png")
        self.reread.assert_awaited_once_with("")
        self.aborted.assert_called_once_with("u-1")
        self.confirmed.assert_not_awaited()
        self.analyzed.assert_not_awaited()

    async def test_a_row_that_is_the_open_panel_keeps_that_panel_on_it(self) -> None:
        self.state["collection"] = "Favorites"

        await self._on(row_id="Favorites")

        self.reread.assert_awaited_once_with("Favorites")

    async def test_a_picture_that_saved_is_not_reported_as_failed_when_the_redraw_fails(
            self) -> None:
        redraw = AsyncMock(side_effect=RuntimeError("grid"))
        self.player.return_value = redraw
        self.state["view"] = "players"

        with self.assertRaises(RuntimeError):
            await self._on(declared={"picture": "p-1", "of": "player"})

        self.said.assert_not_called()

    async def test_a_player_declaration_sets_that_players_picture(self) -> None:
        self.state["view"] = "players"
        await self._on(declared={"picture": "p-1", "of": "player"})

        self.player.assert_awaited_once_with(
            self.library, self.state, "p-1", "picture.png", b"png")
        self.library.set_collection_image.assert_not_called()
        self.aborted.assert_called_once_with("u-1")

    async def test_a_drop_that_is_not_one_picture_says_why_and_sets_nothing(self) -> None:
        self.library.upload_image = Mock(side_effect=RuntimeError("A picture takes one image file"))

        await self._on(row_id="Favorites")

        self.said.assert_called_once_with("A picture takes one image file", type="warning")
        self.library.set_collection_image.assert_not_called()
        self.reread.assert_not_awaited()
        self.aborted.assert_called_once_with("u-1")

    async def test_a_picture_the_collection_refuses_says_so_and_still_lets_go(self) -> None:
        self.library.set_collection_image = Mock(side_effect=RuntimeError("no such collection"))

        await self._on(row_id="Gone")

        self.said.assert_called_once_with(
            t("said.save_failed"), caption="no such collection", type="negative")
        self.reread.assert_not_awaited()
        self.aborted.assert_called_once_with("u-1")


class APictureDroppedOnAPlayer(ADrop):
    async def test_it_saves_the_picture_offers_undo_and_redraws_the_panel(self) -> None:
        self.library.player_picture_bytes = Mock(return_value=None)  # type: ignore[method-assign]
        self.library.set_player_picture = Mock()  # type: ignore[method-assign]
        shown = AsyncMock()
        state: dict[str, Any] = {"player": "p-1", "show_player": shown}
        offered = Mock()
        with patch("manager.frontend.players.run.io_bound", new=_now), \
                patch.object(page.players_page.undo, "offer", new=offered):
            redraw = await press(page.players_page.dropped_picture, self.library, state,
                                 "p-1", "picture.png", b"png")
            shown.assert_not_awaited()
            await redraw()

        self.library.set_player_picture.assert_called_once_with("p-1", "picture.png", b"png")
        offered.assert_called_once()
        shown.assert_awaited_once_with({"id": "p-1"})


class AnUnusedRow(ADrop):
    async def test_it_says_the_slot_is_still_covered_and_then_takes_the_file(self) -> None:
        await self._dropped("media", row_id="g-1:topper:")

        self.said.assert_called_once_with(t("manager.drop.covered"), type="info")
        self.assertEqual(self._sent()["media_kind"], "topper")


class APageThatTakesNoFiles(ADrop):
    async def test_a_finished_upload_is_let_go_of_with_the_reason(self) -> None:
        await self._dropped("settings")

        self._turned_away_with("manager.drop.not_here")
        self.aborted.assert_called_once_with("u-1")

    async def test_a_refusal_before_anything_uploaded_has_nothing_to_let_go_of(self) -> None:
        await self._dropped("settings", upload_id="")

        self._turned_away_with("manager.drop.not_here")
        self.aborted.assert_not_called()


class ANewPaneBuild(unittest.IsolatedAsyncioTestCase):
    async def test_it_clears_what_the_last_panel_declared_before_it_draws(self) -> None:
        seen: list[Any] = []
        declare = Mock(side_effect=lambda **ids: seen.append(("declared", ids)))
        state: dict[str, Any] = {"declare_drop": declare, "panel_rebuild": object()}

        async def draw() -> None:
            seen.append(("drew", state["panel_rebuild"]))

        with patch.object(core.busy, "held", return_value=nullcontext()):
            await core._pane_build(Mock(), state, draw)

        self.assertEqual(seen[-2:], [("declared", {}), ("drew", None)])


if __name__ == "__main__":
    unittest.main()

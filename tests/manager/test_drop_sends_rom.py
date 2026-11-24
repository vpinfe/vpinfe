"""An alt color or alt sound dropped on a game is planned and imported under that game's
ROM, from every way a drop names a game."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from common.i18n import t
from manager import page
from manager.library import drop_sheet, import_dialog, mediasource, uploads
from manager.library.drop_target import Where
from tests.support.clicks import press

WITH_ROM = {"id": "g-1", "name": "Medieval Madness", "folder": "/games/Medieval Madness",
            "rom": "mm_109c"}
WITHOUT_ROM = {"id": "g-2", "name": "An Original", "folder": "/games/An Original",
               "rom": ""}
PLANNED = {"game_dir": WITH_ROM["folder"], "blocked": [],
           "items": [{"index": 0, "kind": "altcolor_serum",
                      "destination": "/games/Medieval Madness/serum/mm_109c/mm_109c.cRZ"}]}
NO_ROM = {"kind": "altcolor_serum", "reason": t("error.uploads.no_rom")}
REFUSED = {"game_dir": WITHOUT_ROM["folder"], "items": [], "blocked": [NO_ROM]}


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class _Dropping(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.library = Mock(games=[WITH_ROM, WITHOUT_ROM])
        self.library.upload_plan.return_value = PLANNED
        self.library.tables_for.return_value = []
        self.shown = AsyncMock()
        self.said = Mock()
        for patched in (patch("manager.kit.offload.run.io_bound", new=_now),
                        patch.object(uploads, "analyzed", new=AsyncMock(return_value={})),
                        patch.object(import_dialog, "open_for", new=self.shown),
                        patch.object(uploads.ui, "notify", new=self.said)):
            patched.start()
            self.addCleanup(patched.stop)

    def planned_rom(self) -> str:
        return str(self.library.upload_plan.call_args.kwargs["rom_name"])

    def imported_rom(self) -> str:
        opened = self.shown.await_args
        assert opened is not None
        return str(opened.kwargs["rom_name"])


class ThePlanNamesTheGamesRom(_Dropping):
    async def _confirmed(self, game: dict[str, Any] | None) -> None:
        await press(
            uploads.confirmed_import,
            self.library, "u-1", {}, source="mm_109c.cRZ", on_done=AsyncMock(),
            game_id=str((game or {}).get("id") or ""),
            game_dir=str((game or {}).get("folder") or ""),
            allow_new_game=game is None)

    async def test_the_plan_and_the_import_carry_the_rom_of_the_game(self) -> None:
        await self._confirmed(WITH_ROM)

        self.assertEqual("mm_109c", self.planned_rom())
        self.assertEqual("mm_109c", self.imported_rom())

    async def test_a_game_with_no_rom_is_told_why_and_nothing_is_imported(self) -> None:
        self.library.upload_plan.return_value = REFUSED

        await self._confirmed(WITHOUT_ROM)

        self.assertEqual("", self.planned_rom())
        self.said.assert_called_once_with(drop_sheet.Sheet(REFUSED).refusals()[0], type="warning")
        self.shown.assert_not_awaited()
        self.library.abort_upload.assert_called_once_with("u-1")

    async def test_a_drop_that_names_no_game_names_no_rom(self) -> None:
        await self._confirmed(None)

        self.assertEqual("", self.planned_rom())


class EveryWayOntoAGameSendsIt(_Dropping):
    async def _dropped_on_the_grid(self, **where: Any) -> None:
        drop = uploads.Drop(where=Where(**where), upload_id="u-1", name="mm_109c.cRZ")
        await press(page._took_a_drop, self.library, {"view": "games"}, Mock(), drop)

    async def test_a_games_row(self) -> None:
        await self._dropped_on_the_grid(row_id="g-1")

        self.assertEqual(("mm_109c", "mm_109c"), (self.planned_rom(), self.imported_rom()))

    async def test_an_asset_cell(self) -> None:
        await self._dropped_on_the_grid(row_id="g-1", col_id="asset_alt_color",
                                        cell=("manager-drop-asset",))

        self.assertEqual(("mm_109c", "mm_109c"), (self.planned_rom(), self.imported_rom()))

    async def test_the_add_dialog_on_the_games_panel(self) -> None:
        context = {"library": self.library, "game_id": "g-1", "game": WITH_ROM}
        sources = mediasource._Folder(context, "alt_color", "Alt Color", AsyncMock())

        await press(sources.use_drop, uploads.Drop(upload_id="u-1", name="mm_109c.cRZ"))

        self.assertEqual(("mm_109c", "mm_109c"), (self.planned_rom(), self.imported_rom()))


if __name__ == "__main__":
    unittest.main()

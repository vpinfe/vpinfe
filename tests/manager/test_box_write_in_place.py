"""A DMD box dragged or found on the backglass preview draws the file's form and the
preview again where they are, not the whole panel: the picture does not reload and the
grid and the dock stay put."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import run

from manager.workbench import file
from tests.support.clicks import press
from tests.support.workbench_patch import patch_workbench

VALUES = {"B2SBackglassDMDX": "10"}


def _inner(**held: Any) -> dict[str, Any]:
    return {"config_write": Mock(return_value={}), "rebuild": AsyncMock(),
            "followers": [], "config_values": {"stale": {}}, **held}


class BoxWriteInPlace(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.keeping = self.enterContext(
            patch_workbench("_keeping_place", new=AsyncMock()))
        self.enterContext(patch.object(run, "io_bound",
                                       new=AsyncMock(return_value={})))
        self.enterContext(patch_workbench("_config_values",
                                       new=AsyncMock(return_value={"fresh": {}})))

    async def test_a_dragged_box_draws_the_form_and_the_followers_not_the_panel(self) -> None:
        follow, redraw, grid = Mock(), AsyncMock(), AsyncMock()
        inner = _inner(followers=[follow], redraw_form=redraw, refresh_grid=grid)

        await press(file._write_overlay, inner, VALUES, in_place=True)

        follow.assert_called_once_with({"fresh": {}})
        redraw.assert_awaited_once()
        grid.assert_awaited_once()
        self.keeping.assert_not_awaited()
        self.assertNotIn("stale", inner.get("config_values", {}))

    async def test_without_in_place_the_whole_panel_is_drawn(self) -> None:
        redraw = AsyncMock()
        inner = _inner(redraw_form=redraw)

        await press(file._write_overlay, inner, VALUES)

        self.keeping.assert_awaited_once()
        redraw.assert_not_awaited()

    async def test_a_form_that_cannot_be_drawn_alone_draws_the_whole_panel(self) -> None:
        await press(file._write_overlay, _inner(), VALUES, in_place=True)

        self.keeping.assert_awaited_once()

    async def test_settings_that_cannot_be_read_back_draw_the_whole_panel(self) -> None:
        redraw = AsyncMock()
        inner = _inner(redraw_form=redraw)
        with patch_workbench("_config_values",
                          new=AsyncMock(side_effect=OSError("gone"))):
            await press(file._write_overlay, inner, VALUES, in_place=True)

        self.keeping.assert_awaited_once()
        redraw.assert_not_awaited()


class TheFitButton(unittest.TestCase):
    def test_only_the_first_number_of_a_dmd_box_carries_it(self) -> None:
        self.assertEqual("Backglass", file._dmd_part("Plugin.B2S.BackglassDMDX"))
        self.assertEqual("ScoreView", file._dmd_part("Plugin.B2SLegacy.ScoreViewDMDX"))
        for key in ("Plugin.B2S.BackglassDMDY", "Plugin.B2S.BackglassDMDW",
                    "Plugin.B2S.BackglassDMDAutoPos", "Plugin.B2S.ShowGrill",
                    "Plugin.DMDUtil.ZeDMD", "Player.DMDX"):
            self.assertEqual("", file._dmd_part(key), key)


if __name__ == "__main__":
    unittest.main()

"""Reset on a settings row draws that row's control again and leaves the rest of the page
alone, unless the write moved some other setting or the settings cannot be read back."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import run, ui

from manager.workbench import all_settings
from tests.support.clicks import press
from tests.support.workbench_patch import patch_workbench

_PAGE = ui.element()

FIELD = SimpleNamespace(key="Player.Speed", type="text", label="Speed", default="1",
                        choices=(), blank="", scopes=("launcher",), help="", description="",
                        reported=())
OTHER = SimpleNamespace(**{**vars(FIELD), "key": "Player.Other", "label": "Other"})

CHANGED = {"Player.Speed": {"value": "5", "scope": "launcher", "set_here": True,
                            "in_effect": True},
           "Player.Other": {"value": "2", "scope": "launcher", "set_here": False}}
RESET = {"Player.Speed": {"value": "1", "scope": "", "set_here": False},
         "Player.Other": CHANGED["Player.Other"]}


class ResetInPlace(unittest.IsolatedAsyncioTestCase):
    async def _drawn(self, reads: list[Any]) -> tuple[ui.element, Any, Mock, list[Any]]:
        self.reads = self.enterContext(
            patch_workbench("_config_values", new=AsyncMock(side_effect=reads)))
        self.enterContext(patch_workbench("_set_by_tables",
                                       new=AsyncMock(return_value=[])))
        self.enterContext(patch.object(
            run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.rebuild = AsyncMock()

        self.kept: list[bool] = []

        async def keeping(_client: Any, rebuild: Any, _written: str, *,
                          in_place: bool = False) -> None:
            self.kept.append(in_place)
            await rebuild()

        self.enterContext(patch_workbench("_keeping_place", new=keeping))
        asked: list[dict[str, Any]] = []
        real = all_settings._marked

        def marked(*args: Any, **kwargs: Any) -> Any:
            asked.append(kwargs)
            return real(*args, **kwargs)

        self.enterContext(patch_workbench("_marked", new=marked))
        write = Mock(return_value={})
        context = {"library": Mock(), "launcher": {"launcher_id": "probe", "app_name": "App"},
                   "config_scope": "launcher", "rebuild": self.rebuild, "config_write": write,
                   "followers": []}
        entries = await all_settings._setting_entries(context, [("", "", [FIELD, OTHER])])
        with _PAGE:
            holder = ui.element()
            with holder:
                for _, draw in entries:
                    draw() if callable(draw) else None
        return holder, write, self.rebuild, asked

    @staticmethod
    def _speed(holder: ui.element) -> ui.input:
        return next(one for one in holder.descendants() if isinstance(one, ui.input))

    @staticmethod
    def _other(holder: ui.element) -> ui.input:
        return [one for one in holder.descendants() if isinstance(one, ui.input)][1]

    async def test_the_control_shows_what_is_in_force_and_the_page_is_not_drawn(self) -> None:
        holder, write, rebuild, asked = await self._drawn([CHANGED, RESET])
        before = self._speed(holder)
        other = self._other(holder)
        self.assertEqual("5", before.value)

        await press(asked[0]["clear"])

        write.assert_called_once_with({"Player.Speed": ""})
        rebuild.assert_not_awaited()
        shown = self._speed(holder)
        self.assertEqual("1", shown.value)
        self.assertIsNot(before, shown)
        self.assertIs(other, self._other(holder))
        self.assertEqual([True], self.kept)

    async def test_a_reset_that_moves_another_setting_draws_the_page(self) -> None:
        moved = {**RESET, "Player.Other": {"value": "9", "scope": "launcher"}}
        _, _, rebuild, asked = await self._drawn([CHANGED, moved])

        await press(asked[0]["clear"])

        rebuild.assert_awaited_once()

    async def test_settings_that_cannot_be_read_back_draw_the_page(self) -> None:
        _, _, rebuild, asked = await self._drawn([CHANGED, OSError("gone")])

        await press(asked[0]["clear"])

        rebuild.assert_awaited_once()


class WhatMoved(unittest.TestCase):
    def test_a_pair_written_together_has_not_moved_either_of_its_own(self) -> None:
        before = {"a": {"value": "1"}, "b": {"value": "2"}, "c": {"value": "3"}}
        after = {"a": {"value": ""}, "b": {"value": ""}, "c": {"value": "3"}}

        self.assertFalse(all_settings._moved(before, after, ["a", "b"]))
        self.assertTrue(all_settings._moved(before, after, "a"))


if __name__ == "__main__":
    unittest.main()

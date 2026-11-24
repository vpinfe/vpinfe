"""A game's DOF event in the panel: what an empty field says, and Test beside it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from nicegui import run

from common.i18n import t
from manager.settings import dof
from manager.workbench import core, game_table
from tests.support.workbench_patch import patch_workbench


def _context(event: str = "") -> dict[str, Any]:
    return {"game": {"overrides": {"frontend_dof_event": event}}, "library": object(),
            "game_id": "g1"}


class GameDofRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.override = self.enterContext(patch_workbench("_override"))
        self.ui = self.enterContext(patch_workbench("ui"))
        self.said: list[str] = []
        self.ui.label.side_effect = self._say

    def _say(self, text: str) -> MagicMock:
        self.said.append(text)
        return MagicMock()

    def _draw(self, dof_state: dict[str, Any] | None) -> Any:
        _, row = game_table._game_frontend(_context("E905"), dof_state)[1]
        row()
        return self.override.call_args

    def test_an_empty_field_reads_random_and_the_range_is_said_under_it(self) -> None:
        call = self._draw(None)

        self.assertEqual(call.args[0], "E905")
        self.assertEqual(call.kwargs["placeholder"], t("manager.workbench.dof_random"))
        self.assertEqual(self.said, [t("manager.workbench.dof_event_hint")])

    def test_where_the_device_has_no_frontend_there_is_no_test(self) -> None:
        self.assertIsNone(self._draw(None).kwargs["after"])

    def test_where_it_has_one_test_follows_what_is_typed_in_the_field(self) -> None:
        after = self._draw({"event": None, "blocked": ""}).kwargs["after"]
        field = MagicMock()
        field.value = "s27"

        with patch.object(dof, "Tester") as tester:
            after(field)

        library, state, event = tester.call_args.args
        self.assertEqual(state, {"event": None, "blocked": ""})
        self.assertEqual(event(), "s27")
        tester.return_value.assert_called_once_with()
        field.on_value_change.assert_called_once_with(tester.return_value.refresh)


class RefusedSaveTests(unittest.IsolatedAsyncioTestCase):
    async def _save(self, error: Exception | None) -> bool:
        async def now(callback: Any, *args: Any) -> Any:
            return callback(*args)

        library = MagicMock()
        library.set_game_overrides.side_effect = error
        rebuild = AsyncMock()
        context: dict[str, Any] = {"library": library, "game_id": "g1", "rebuild": rebuild}
        with patch.object(run, "io_bound", new=now), \
                patch_workbench("ui"), patch("manager.kit.on_page.ui"):
            saved = await core._save_overrides(context, {"frontend_dof_event": "905"},
                                                    table=False)
        self.rebuilt = rebuild.await_count
        return saved

    async def test_a_refused_save_says_so_and_rebuilds_nothing(self) -> None:
        self.assertIs(await self._save(ValueError("refused")), False)
        self.assertEqual(self.rebuilt, 0)

    async def test_a_save_that_went_through_rebuilds(self) -> None:
        self.assertIs(await self._save(None), True)
        self.assertEqual(self.rebuilt, 1)


if __name__ == "__main__":
    unittest.main()

"""The import dialog's sheet, planned again as the new game's folder name is typed."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from manager.library import import_dialog


class TheSheetFollowsTheFolderName(unittest.IsolatedAsyncioTestCase):
    def _following(self) -> tuple[dict[str, Any], AsyncMock, Any]:
        named: dict[str, Any] = {"folder": "Old Name", "vps_id": ""}
        replan = AsyncMock()
        with patch.object(import_dialog.ui, "timer") as timer:
            import_dialog._following(named, replan)
        return named, replan, timer.call_args.args[1]

    async def test_a_new_name_plans_again_without_a_notice_on_refusal(self) -> None:
        named, replan, look = self._following()

        named["folder"] = "New Name"
        await look()

        replan.assert_awaited_once_with(quiet=True)

    async def test_the_same_name_asks_nothing(self) -> None:
        _named, replan, look = self._following()

        await look()

        replan.assert_not_awaited()

    async def test_a_name_is_asked_once_until_it_changes_again(self) -> None:
        named, replan, look = self._following()

        named["folder"] = "New Name"
        await look()
        await look()
        named["folder"] = "Newer Name"
        await look()

        self.assertEqual(replan.await_count, 2)


if __name__ == "__main__":
    unittest.main()

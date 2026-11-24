"""Which table is offered Delete on a .vbs, and who its confirm says loses it."""

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from common.i18n import t
from manager.kit import confirm, game_tables
from manager.workbench import game_table
from tests.support.clicks import press

FOLDER_NAMED = "Multi VPX (Original 2024).vpx"
ALT = "Multi VPX (Original 2024) - alt.vpx"


def _table(filename: str, script: str = "", resolution: str = "none") -> dict[str, Any]:
    return {"id": "t1", "filename": filename,
            "assets": {"script": {"resolution": resolution, "file": script or None}}}


class OwnScriptTests(unittest.TestCase):
    def test_a_vbs_named_for_the_table_is_its_own(self) -> None:
        for table in (_table(FOLDER_NAMED, "Multi VPX (Original 2024).vbs", "shared"),
                      _table(ALT, "multi vpx (original 2024) - ALT.vbs", "dedicated")):
            with self.subTest(table=table["filename"]):
                self.assertTrue(game_tables.runs_its_own_script(table))

    def test_the_folders_vbs_or_none_is_not(self) -> None:
        for table in (_table(ALT, "Multi VPX (Original 2024).vbs", "shared"), _table(ALT),
                      {"filename": ALT}):
            with self.subTest(table=table):
                self.assertFalse(game_tables.runs_its_own_script(table))

    def test_the_folders_vbs_is_shared(self) -> None:
        self.assertTrue(game_tables.shares_its_script(
            _table(FOLDER_NAMED, "Multi VPX (Original 2024).vbs", "shared")))
        self.assertFalse(game_tables.shares_its_script(
            _table(ALT, "Multi VPX (Original 2024) - alt.vbs", "dedicated")))


class DeleteConfirmTests(unittest.IsolatedAsyncioTestCase):
    async def _detail(self, table: dict[str, Any]) -> str:
        ask = AsyncMock(return_value=False)
        with patch.object(confirm, "ask", ask):
            await press(game_table._drop_script, {"library": None}, table)
        ask.assert_awaited_once()
        assert ask.await_args is not None
        return str(ask.await_args.kwargs["detail"])

    async def test_the_folders_vbs_says_every_table_loses_it(self) -> None:
        detail = await self._detail(
            _table(FOLDER_NAMED, "Multi VPX (Original 2024).vbs", "shared"))

        self.assertEqual(t("manager.game_tables.script_detail"), detail)

    async def test_a_tables_own_vbs_says_the_table_goes_back(self) -> None:
        detail = await self._detail(
            _table(ALT, "Multi VPX (Original 2024) - alt.vbs", "dedicated"))

        self.assertEqual(t("manager.workbench.delete_detail"), detail)


if __name__ == "__main__":
    unittest.main()

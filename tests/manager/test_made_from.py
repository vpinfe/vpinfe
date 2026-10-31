"""A patched table says what it was made from, and whatever takes the base away names
what was made from it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from common.i18n import t
from manager.kit import confirm, game_tables, panel
from manager.library import import_dialog
from manager.workbench import game_table
from tests.support.clicks import press
from tests.support.workbench_patch import patch_workbench

BASE_ID, MADE_ID = "tblBase", "tblMade"


def _base(**said: Any) -> dict[str, Any]:
    return {"file": "Example.vpx", "table_id": BASE_ID, "available": True, **said}


def _tables(base: dict[str, Any], *, on_disk: bool = True) -> list[dict[str, Any]]:
    """The patched table first, then a table of the base's name, on disk or only a record."""
    made = {"id": MADE_ID, "filename": "Example VPW Mod.vpx", "available": True,
            "source": {"base": base}}
    return [made, {"id": BASE_ID, "filename": "Example.vpx", "available": on_disk,
                   "source": None}]


class MadeFromRow(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = self.enterContext(patch_workbench("ui"))
        self.link = self.enterContext(patch.object(panel, "link"))

    def _drawn(self, tables: list[dict[str, Any]]) -> list[str]:
        (label, draw), = game_table._made_from(tables[0], {"tables": tables, "game_id": "g1"})
        self.assertEqual("Made from", label)
        if draw is not self.link.return_value:
            draw()
        return [one.args[0] for one in self.ui.label.call_args_list]

    def test_a_base_on_disk_opens_that_table(self) -> None:
        tables = _tables(_base())

        self.assertEqual([], self._drawn(tables))
        self.assertEqual(game_tables.name_among(tables[1], tables),
                         self.link.call_args.args[0])
        self.assertIn(f"table={BASE_ID}", self.link.call_args.kwargs["to"])
        self.assertNotIn("on_click", self.link.call_args.kwargs,
                         "a click handler redraws the panel but leaves the Games grid on screen")

    def test_a_base_gone_from_disk_is_missing_while_its_record_stays(self) -> None:
        tables = _tables(_base(available=False), on_disk=False)

        self.assertEqual(["Example.vpx", game_tables.FILE_WORDS[0]], self._drawn(tables))
        self.link.assert_not_called()

    def test_a_base_forgotten_too_is_missing(self) -> None:
        tables = _tables(_base(table_id="", available=False))[:1]

        self.assertEqual(["Example.vpx", game_tables.FILE_WORDS[0]], self._drawn(tables))

    def test_a_base_another_file_took_the_name_of_is_replaced(self) -> None:
        tables = _tables(_base(table_id="", available=False))

        self.assertEqual(["Example.vpx", "Replaced"], self._drawn(tables))

    def test_a_base_on_disk_with_no_panel_to_open_it_in_is_named(self) -> None:
        self.assertEqual([("Made from", "Example.vpx")],
                         game_table._made_from(_tables(_base())[0], None))

    def test_a_table_no_patch_made_says_nothing(self) -> None:
        self.assertEqual([], game_table._made_from({"source": {"vps_file_id": "r"}}, None))


class Finding(unittest.TestCase):
    def _lines(self, table: dict[str, Any]) -> list[str]:
        return [t(fault.words, count=1, **fault.params)
                for fault in game_table._faults({"tables": []}, table, {}, "")]

    def test_a_patched_table_whose_base_is_gone_says_so(self) -> None:
        table = _tables(_base(available=False))[0]

        self.assertEqual(["The file it was made from is missing"], self._lines(table))

    def test_a_base_on_disk_is_no_finding(self) -> None:
        self.assertEqual([], self._lines(_tables(_base())[0]))


class Forget(unittest.IsolatedAsyncioTestCase):
    async def test_forgetting_a_base_names_what_was_made_from_it(self) -> None:
        tables = _tables(_base(available=False))
        ask = AsyncMock(return_value=False)
        with patch.object(confirm, "ask", ask):
            await press(game_table._forget_table, {"tables": tables}, tables[1])

        self.assertEqual("Made from it: " + game_tables.name_among(tables[0], tables),
                         ask.call_args.kwargs["lines"][-1])

    async def test_forgetting_any_other_table_names_only_it(self) -> None:
        tables = _tables(_base(available=False))
        ask = AsyncMock(return_value=False)
        with patch.object(confirm, "ask", ask):
            await press(game_table._forget_table, {"tables": tables}, tables[0])

        self.assertEqual(1, len(ask.call_args.kwargs["lines"]))


class ReplaceLine(unittest.TestCase):
    def test_replacing_a_base_names_what_was_made_from_it(self) -> None:
        item = {"index": 0, "label": "Table", "name": "Example 2.0.vpx", "size": 1,
                "destination": "/games/Example/Example 2.0.vpx", "action": "replace_vpx",
                "replaces": "replaces Example.vpx", "made_from_it": ["Example VPW Mod.vpx"]}
        with patch.object(import_dialog, "ui") as ui:
            import_dialog._draw_row(item, {"game_dir": "/games/Example"}, {0: True}, True,
                                    lambda: None)

        said = [one.args[0] for one in ui.label.call_args_list]
        self.assertIn("Made from it: Example VPW Mod.vpx", said)


if __name__ == "__main__":
    unittest.main()

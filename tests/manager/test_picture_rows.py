"""The Pictures group in a game's Play section, as rows before anything is drawn."""

from __future__ import annotations

import unittest
from typing import Any

from common.i18n import t
from manager.kit import game_tables, panel
from manager.library.pictures import rows, table_names

TABLES: list[dict[str, Any]] = [{"id": "t1", "filename": "Example.vpx", "default": True},
                                {"id": "t2", "filename": "Example (Mod).vpx"}]


def _shot(name: str, table_id: str = "t1") -> dict:
    return {"name": name, "taken": "2026-09-28T21:04:05Z", "table_id": table_id,
            "size_bytes": 10, "width": 40, "height": 80, "version": "v1"}


def _context() -> dict:
    return {"lens": "", "tables": TABLES, "game_id": "g1"}


class PictureRowsTests(unittest.TestCase):
    def test_no_pictures_is_no_group(self) -> None:
        self.assertEqual(rows(_context(), {"pictures": []}), [])

    def test_the_group_is_its_heading_and_the_pictures_across_the_panel(self) -> None:
        drawn = rows(_context(), {"pictures": [_shot("a.png"), _shot("b.png")]})

        self.assertEqual([label for label, _value in drawn], [panel.HEADING, panel.FULL])
        self.assertEqual(drawn[0][1], t("manager.workbench.pictures"))

    def test_a_read_that_failed_says_so_with_its_reason(self) -> None:
        drawn = rows(_context(), {"reason": "The folder is not there"})

        self.assertEqual([label for label, _value in drawn], [panel.HEADING, panel.LEDE])
        self.assertEqual(drawn[1][1].args, (t("manager.workbench.pictures_unreadable"),))
        self.assertEqual(drawn[1][1].keywords, {"hint": "The folder is not there"})

    def test_a_table_is_named_only_where_the_pictures_come_from_more_than_one(self) -> None:
        self.assertEqual(table_names(_context(), [_shot("a.png"), _shot("b.png")]), {})

        named = table_names(_context(), [_shot("a.png", "t1"), _shot("b.png", "t2")])

        self.assertEqual(named, {table["id"]: game_tables.name_among(table, TABLES)
                                 for table in TABLES})
        self.assertNotEqual(named["t1"], named["t2"])

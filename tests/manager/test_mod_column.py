"""The Tables grid's Mod of column."""

from __future__ import annotations

import unittest
from typing import Any

from manager.kit import views
from manager.library import games


def _row(mod_of: dict[str, Any] | None) -> dict[str, Any]:
    return games.table_rows([{"id": "t1", "game": "A",
                              "source": {"vps_file_id": "r", "mod_of": mod_of}}])[0]


def _mod(parent: str = "", **said: Any) -> dict[str, Any]:
    return {"vps_file_id": parent, "version": "", "authors": [], "game": "", "url": "",
            "note": "", "game_id": "", "table_id": "", **said}


class ModOfColumn(unittest.TestCase):
    def test_a_mod_says_what_it_is_a_mod_of_and_filters_as_one(self) -> None:
        row = _row(_mod("p", version="1.2", authors=["VPW"], game="Other Game"))

        self.assertEqual((games._MOD, "Other Game · 1.2 · VPW"),
                         (row["mod_of"], row["mod_of_said"]))

    def test_a_mod_with_nothing_to_go_on_is_still_one(self) -> None:
        row = _row(_mod())

        self.assertEqual((games._MOD, "Unknown"), (row["mod_of"], row["mod_of_said"]))

    def test_a_table_that_is_no_mod_reads_blank(self) -> None:
        for source in ({"vps_file_id": "r", "mod_of": None}, None):
            with self.subTest(source=source):
                row = games.table_rows([{"id": "t1", "game": "A", "source": source}])[0]
                self.assertEqual(("", ""), (row["mod_of"], row["mod_of_said"]))

    def test_it_filters_on_mod_and_none_known_and_no_preset_shows_it(self) -> None:
        column = next(one for one in games.TABLE_COLUMNS if one["field"] == "mod_of")

        self.assertEqual([games._MOD, ""],
                         [one["value"] for one in column["filterParams"]["choices"]])
        self.assertEqual([], [view.name for view in views.builtins(games.TABLE_VIEWS)
                              if "mod_of" in view.columns])


if __name__ == "__main__":
    unittest.main()

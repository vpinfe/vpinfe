"""A collection's row whose table is hidden, in the collection's panel.

The rows are the members lens's own shape, as `/collections/{name}/members` sends them.
"""

from __future__ import annotations

import unittest

from common.i18n import t
from manager.workbench import collection

LOCKED_HIDDEN = {"game": "Game00000001", "origin": "named", "included": False,
                 "ref_table": "tbl0000002",
                 "tables": [{"id": "tbl0000002", "included": False, "origin": "hidden"}]}
FOLLOWING = {"game": "Game00000002", "origin": "named", "included": True, "ref_table": "",
             "tables": [{"id": "tbl0000001", "included": True, "origin": "default"}]}
TAKEN_OUT = {"game": "Game00000003", "origin": "excluded", "included": False,
             "ref_table": "tbl0000004",
             "tables": [{"id": "tbl0000004", "included": False, "origin": "excluded"}]}


class HiddenMemberTests(unittest.TestCase):
    def test_only_the_row_whose_table_is_hidden_is_marked(self) -> None:
        self.assertEqual([True, False, False],
                         [collection._is_hidden(one)
                          for one in (LOCKED_HIDDEN, FOLLOWING, TAKEN_OUT)])

    def test_a_hidden_row_still_reads_locked_to_its_table(self) -> None:
        self.assertEqual(["tbl0000002", "", ""],
                         [collection._locked_to(one)
                          for one in (LOCKED_HIDDEN, FOLLOWING, TAKEN_OUT)])

    def test_the_heading_counts_the_hidden_rows_it_lists(self) -> None:
        self.assertEqual(t("manager.workbench.count_hidden", count=0, hidden=1),
                         collection._games_heading(0, 1, 0))
        self.assertEqual(t("manager.workbench.count_both", count=1,
                           hidden=1, taken=1),
                         collection._games_heading(1, 1, 1))
        self.assertEqual(t("manager.workbench.count_games", count=2),
                         collection._games_heading(2, 0, 0))


if __name__ == "__main__":
    unittest.main()

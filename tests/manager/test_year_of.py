"""A year reaches a grid as a number, or as nothing."""

from __future__ import annotations

import unittest

from manager.kit import game_tables
from manager.library import discovery


class YearOf(unittest.TestCase):
    def test_what_the_sources_send_is_read_as_a_number(self) -> None:
        for said, year in (("1995", 1995), (1995, 1995), (" 1995 ", 1995),
                           ("1995,1997", 1995), (1995.0, 1995)):
            with self.subTest(said=said):
                self.assertEqual(game_tables.year_of(said), year)

    def test_a_blank_or_unreadable_year_is_none(self) -> None:
        for said in (None, "", "   ", 0, "unknown", "95", False, 1, "0000", "19951"):
            with self.subTest(said=said):
                self.assertIsNone(game_tables.year_of(said))

    def test_a_discovery_row_holds_the_number(self) -> None:
        found = {"counted": True, "rows": [
            {"vps_id": "a", "name": "Taxi", "year": "1988"},
            {"vps_id": "b", "name": "Orbit", "year": ""},
            {"vps_id": "c", "name": "Zed", "year": 1971}]}
        self.assertEqual([one["year"] for one in discovery.rows(found)], [1988, None, 1971])


if __name__ == "__main__":
    unittest.main()

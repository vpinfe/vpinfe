"""A grid's search, which reads a column as it is shown."""

from __future__ import annotations

import unittest

from manager.kit import grid
from manager.library import games


class AGridSearch(unittest.TestCase):
    def test_a_formatted_column_is_searched_by_what_it_shows(self) -> None:
        settings = next(one for one in games.TABLE_COLUMNS if one["field"] == "settings")

        self.assertEqual(settings[":valueFormatter"], settings[":getQuickFilterText"])

    def test_a_column_with_search_words_of_its_own_keeps_them(self) -> None:
        column = grid.list_column("tags", "Tags")

        self.assertNotEqual(column[":valueFormatter"], column[":getQuickFilterText"])

    def test_a_column_shown_as_it_holds_it_is_searched_as_it_holds_it(self) -> None:
        self.assertNotIn(":getQuickFilterText", grid.column("year", "Year"))

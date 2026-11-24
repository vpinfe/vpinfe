"""How wide a column is for its header to read whole: beside the sort arrow and the
filter button always, and beside the order number where a built-in sorts on it together
with another column."""

from __future__ import annotations

import unittest

from manager.kit import grid

TWO_KEYS = ({"colId": "rating", "sort": "desc", "sortIndex": 0},
            {"colId": "ratings", "sort": "desc", "sortIndex": 1})
ONE_KEY = ({"colId": "plays", "sort": "desc", "sortIndex": 0},)


def _columns() -> list[dict]:
    return [grid.column("rating", "Rating"), grid.column("ratings", "Ratings"),
            grid.column("plays", "Plays"), grid.column("name", "Name", 240),
            grid.column("icon", "", 56)]


def _widths(columns: list[dict]) -> dict[str, int]:
    return {definition["field"]: definition["width"] for definition in columns}


class RoomForSortOrderTests(unittest.TestCase):
    def test_a_column_sorted_together_with_another_gets_room_for_the_order_number(self) -> None:
        columns = _columns()

        grid.room_for_sort_order(columns, [TWO_KEYS, ONE_KEY])

        widths = _widths(columns)
        self.assertEqual(grid.header_width("Rating") + grid._SORT_ORDER_PX, widths["rating"])
        self.assertEqual(grid.header_width("Ratings") + grid._SORT_ORDER_PX,
                         widths["ratings"])

    def test_a_column_sorted_alone_keeps_its_width(self) -> None:
        columns = _columns()

        grid.room_for_sort_order(columns, [TWO_KEYS, ONE_KEY])

        self.assertEqual(grid.header_width("Plays"), _widths(columns)["plays"])

    def test_a_column_already_wide_enough_keeps_its_width(self) -> None:
        columns = _columns()

        grid.room_for_sort_order(columns, [({"colId": "name"}, {"colId": "rating"})])

        self.assertEqual(240, _widths(columns)["name"])

    def test_a_column_with_no_header_is_not_widened(self) -> None:
        columns = _columns()

        grid.room_for_sort_order(columns, [({"colId": "icon"}, {"colId": "rating"})])

        self.assertEqual(56, _widths(columns)["icon"])

    def test_a_second_pass_changes_nothing(self) -> None:
        """A page's columns can be module constants, passed through again on every
        load."""
        columns = _columns()
        grid.room_for_sort_order(columns, [TWO_KEYS])
        once = _widths(columns)

        grid.room_for_sort_order(columns, [TWO_KEYS])

        self.assertEqual(once, _widths(columns))


if __name__ == "__main__":
    unittest.main()

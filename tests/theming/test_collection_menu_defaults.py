from __future__ import annotations

import re
import unittest
from pathlib import Path

from common.games.collection_store import DEFAULT_DIRECTION, DEFAULT_ORDER_BY

PAGE = (Path(__file__).resolve().parents[2]
        / "frontend" / "static" / "collectionmenu" / "collectionmenu.html")


def _first(name: str) -> str | None:
    found = re.search(rf"let {name} = \[\s*'([^']+)'", PAGE.read_text(encoding="utf-8"))
    return found.group(1) if found else None


class CollectionMenuDefaultsTests(unittest.TestCase):
    def test_the_first_sort_is_the_one_the_library_shows_with_nothing_chosen(self) -> None:
        self.assertEqual(_first("sortOptions"), DEFAULT_ORDER_BY)

    def test_the_first_order_is_the_one_the_library_shows_with_nothing_chosen(self) -> None:
        self.assertEqual(_first("orderOptions"), DEFAULT_DIRECTION)


if __name__ == "__main__":
    unittest.main()

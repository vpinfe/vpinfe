"""Finding a catalog entry by typing at it.

Punctuation is the case worth a test: a library title is stored with its article moved
for sorting - "Addams Family, The" - and a surface seeds this box with the game's own
name. Matching the comma literally made that seed the one query guaranteed to fail.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

ENTRIES = [
    {"id": "aaaaaaaaaa", "name": "The Addams Family", "manufacturer": "Bally",
     "year": 1992, "type": "SS"},
    {"id": "bbbbbbbbbb", "name": "Attack from Mars", "manufacturer": "Bally",
     "year": 1995, "type": "SS"},
    {"id": "cccccccccc", "name": "Space Invaders", "manufacturer": "Gottlieb",
     "year": 1980, "type": "EM"},
]


class VpsSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("common.games.game_service.load_vpsdb",
                        return_value=list(ENTRIES))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _names(self, term: str) -> list[str]:
        from common.games.game_service import search_vpsdb

        return [str(item.get("name")) for item in search_vpsdb(term)]

    def test_a_library_title_finds_its_own_game(self) -> None:
        self.assertEqual(self._names("Addams Family, The"), ["The Addams Family"])

    def test_punctuation_is_not_a_word(self) -> None:
        self.assertEqual(self._names("attack: from *mars*"), ["Attack from Mars"])

    def test_every_word_still_has_to_appear(self) -> None:
        """The looser comparison must not quietly turn "and" into "or"."""
        self.assertEqual(self._names("attack gottlieb"), [])

    def test_maker_and_year_are_searchable(self) -> None:
        self.assertEqual(self._names("gottlieb 1980"), ["Space Invaders"])

    def test_nothing_typed_finds_nothing(self) -> None:
        self.assertEqual(self._names("   "), [])


if __name__ == "__main__":
    unittest.main()

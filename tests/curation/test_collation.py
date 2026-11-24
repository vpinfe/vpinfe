"""Ordering and grouping titles that are not ASCII.

Every case here was a measured defect before `common.games.collation` existed: umlauts filed
after Z, and one letter group per kanji in a library of Japanese tables.
"""

import unittest

from common.games import collation


class TestFold(unittest.TestCase):
    def test_marks_are_dropped(self) -> None:
        self.assertEqual(collation.fold("Ähre"), "ahre")
        self.assertEqual(collation.fold("île mystérieuse"), "ile mysterieuse")

    def test_letters_that_decompose_to_nothing_are_mapped(self) -> None:
        self.assertEqual(collation.fold("Æon"), "aeon")
        self.assertEqual(collation.fold("Ølsen"), "olsen")
        self.assertEqual(collation.fold("Łódź"), "lodz")

    def test_sharp_s_folds_to_ss(self) -> None:
        self.assertEqual(collation.fold("Straße"), "strasse")

    def test_a_script_with_no_latin_base_is_left_alone(self) -> None:
        self.assertEqual(collation.fold("Медведь"), "медведь")


class TestSortKey(unittest.TestCase):
    def test_umlauts_file_with_their_base_letter(self) -> None:
        """`str.lower` put every one of these after Z."""
        names = ["Zebra", "Ähre", "Apple", "Ostrich", "Österreich", "Zulu"]
        self.assertEqual(sorted(names, key=collation.sort_key),
                         ["Ähre", "Apple", "Österreich", "Ostrich", "Zebra", "Zulu"])

    def test_the_original_breaks_the_tie(self) -> None:
        """Two titles with the same fold need a defined order, not the caller's."""
        self.assertEqual(sorted(["Ähre", "Ahre"], key=collation.sort_key),
                         ["Ahre", "Ähre"])


class TestLetterOf(unittest.TestCase):
    def test_ascii_is_unchanged(self) -> None:
        self.assertEqual(collation.letter_of("Attack from Mars"), "A")

    def test_digits_and_symbols_share_one_group(self) -> None:
        self.assertEqual(collation.letter_of("300"), "#")
        self.assertEqual(collation.letter_of("'Cuda"), "#")
        self.assertEqual(collation.letter_of(""), "#")

    def test_a_marked_letter_files_under_its_base(self) -> None:
        self.assertEqual(collation.letter_of("Ölçek"), "O")
        self.assertEqual(collation.letter_of("Æon Flux"), "A")

    def test_an_alphabet_of_its_own_gets_a_group_per_letter(self) -> None:
        self.assertEqual(collation.letter_of("Медведь"), "М")

    def test_a_script_with_no_alphabet_gets_one_group(self) -> None:
        """The defect: every kanji is `isalpha()`, so each was its own letter."""
        self.assertEqual(collation.letter_of("東方Project"), "漢")
        self.assertEqual(collation.letter_of("超兄貴"), "漢")

    def test_the_two_kana_answer_with_one_group(self) -> None:
        self.assertEqual(collation.letter_of("ドラゴン"), "あ")
        self.assertEqual(collation.letter_of("がんばれ"), "あ")

    def test_groups_stay_contiguous_under_the_sort(self) -> None:
        """A group the sort splits is a page jump that lands outside the letter."""
        titles = ["Ähre", "Zebra", "東方", "Apple", "ドラゴン", "300", "Österreich"]
        groups = [collation.letter_of(t) for t in sorted(titles, key=collation.sort_key)]
        runs = [g for i, g in enumerate(groups) if i == 0 or g != groups[i - 1]]
        self.assertEqual(len(runs), len(set(runs)),
                         f"a letter group appears in two runs: {groups}")


if __name__ == "__main__":
    unittest.main()

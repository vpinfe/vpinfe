"""A number on screen, written with the separators of the language that is set."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common import i18n

DOT_AND_COMMA = {"number.group": ".", "number.decimal": ","}


def _swapped(key: str, language_name: str | None) -> str:
    return DOT_AND_COMMA[key]


class NumberTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("en")

    def test_english_groups_by_comma_and_marks_decimals_with_a_point(self) -> None:
        self.assertEqual(["0", "999", "1,234", "1,234,567", "-1,234"],
                         [i18n.number(n) for n in (0, 999, 1234, 1234567, -1234)])
        self.assertEqual(["1.5", "2", "1,234.25"],
                         [i18n.number(n) for n in (1.5, 2.0, 1234.25)])

    def test_a_float_keeps_the_digits_it_has_and_no_others(self) -> None:
        self.assertEqual(["1,234,567.891", "10,000,000,000,000,000", "0", "-0.000000000001"],
                         [i18n.number(n) for n in (1234567.891, 1e16, -0.0, -1e-12)])

    def test_an_entry_marks_which_values_are_numbers(self) -> None:
        self.assertEqual("1,234 of 12,345 games",
                         i18n.t("manager.games.games_of", shown=1234, count=12345))

    def test_a_value_that_is_not_a_number_is_left_as_it_is(self) -> None:
        self.assertEqual("1234 of many games",
                         i18n.t("manager.games.games_of", shown="1234", count="many"))
        self.assertEqual("True of 3 games",
                         i18n.t("manager.games.games_of", shown=True, count=3))

    def test_a_slot_nobody_filled_still_reads_as_its_name(self) -> None:
        self.assertEqual("{shown} of 3 games", i18n.t("manager.games.games_of", count=3))

    def test_the_catalogs_separators_decide_the_digits(self) -> None:
        with patch.object(i18n, "_separator", _swapped):
            self.assertEqual("1.234 of 12.345 games",
                             i18n.t("manager.games.games_of", shown=1234, count=12345))
            self.assertEqual("1.234,5", i18n.number(1234.5))

    def test_a_log_line_keeps_the_sources_separators(self) -> None:
        self.assertEqual("1,234 of 12,345 games",
                         i18n.t_source("manager.games.games_of", shown=1234, count=12345))

    def test_a_size_uses_the_decimal_separator(self) -> None:
        with patch.object(i18n, "_separator", _swapped):
            self.assertEqual("1,5 MB", i18n.size(1024**2 + 1024**2 // 2))


if __name__ == "__main__":
    unittest.main()

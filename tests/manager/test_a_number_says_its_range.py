"""What a number's tooltip says it may be."""

from __future__ import annotations

import unittest

from common.apps.contract import Field
from manager.settings.settings import range_words
from manager.workbench.all_settings import _as_option


class RangeWordsTests(unittest.TestCase):
    def test_both_bounds_are_said_together(self) -> None:
        self.assertEqual(range_words(0.0, 100.0), "From 0 to 100")

    def test_one_bound_is_said_alone(self) -> None:
        self.assertEqual(range_words(1, None), "At least 1")
        self.assertEqual(range_words(None, 65535), "At most 65535")

    def test_a_fraction_keeps_its_digits(self) -> None:
        self.assertEqual(range_words(0.001, 1.0), "From 0.001 to 1")

    def test_a_large_bound_is_not_written_as_an_exponent(self) -> None:
        self.assertEqual(range_words(0, 100000000), "From 0 to 100000000")
        self.assertEqual(range_words(0, 16777216.0), "From 0 to 16777216")

    def test_an_unbounded_number_says_nothing(self) -> None:
        self.assertEqual(range_words(None, None), "")

    def test_a_bound_that_is_not_a_finite_number_is_left_out(self) -> None:
        self.assertEqual(range_words("0", "10"), "From 0 to 10")
        self.assertEqual(range_words("low", "10"), "At most 10")
        self.assertEqual(range_words(float("-inf"), float("nan")), "")


class AProgramsBoundsReachTheControlTests(unittest.TestCase):
    def test_a_field_s_declared_range_is_its_option_s(self) -> None:
        option = _as_option(Field(key="Player.Latitude", type="number", minimum=-90.0,
                                  maximum=90.0))

        self.assertEqual((option["min"], option["max"]), (-90.0, 90.0))

    def test_a_field_with_no_range_adds_none(self) -> None:
        option = _as_option(Field(key="Player.Latitude", type="number"))

        self.assertNotIn("min", option)
        self.assertNotIn("max", option)

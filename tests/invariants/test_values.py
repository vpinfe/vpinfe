import unittest

from common.values import is_truthy, newer_version, parse_version, quarter_turn


class IsTruthyTests(unittest.TestCase):
    def test_it_accepts_the_spellings_config_files_actually_use(self) -> None:
        for value in ("1", "true", "TRUE", " Yes ", "on", True):
            with self.subTest(value=value):
                self.assertTrue(is_truthy(value))

    def test_everything_else_is_false(self) -> None:
        for value in ("0", "false", "no", "off", "maybe", 0, False):
            with self.subTest(value=value):
                self.assertFalse(is_truthy(value))

    def test_absent_means_the_default_not_false(self) -> None:
        """A missing setting can still be opted in by its caller."""
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertFalse(is_truthy(value))
                self.assertTrue(is_truthy(value, default=True))

    def test_a_real_bool_ignores_the_default(self) -> None:
        self.assertFalse(is_truthy(False, default=True))


class VersionTests(unittest.TestCase):
    def test_a_suffix_keeps_the_number_it_is_attached_to(self) -> None:
        for value, expected in (("3.0.1-beta.2", (3, 0, 1)), ("v3.0-beta.1", (3, 0)),
                                ("1.2 (fixed)", (1, 2)), ("2.0.1b", (2, 0, 1))):
            with self.subTest(value=value):
                self.assertEqual(expected, parse_version(value))

    def test_what_does_not_start_with_a_number_is_not_a_version(self) -> None:
        for value in ("VP10.1", "beta", "", None):
            with self.subTest(value=value):
                self.assertEqual((), parse_version(value))

    def test_trailing_zeros_do_not_make_a_version_newer(self) -> None:
        self.assertFalse(newer_version((3, 0, 0), (3, 0)))
        self.assertFalse(newer_version((3, 0), (3, 0, 0)))
        self.assertTrue(newer_version((3, 0, 1), (3, 0)))
        self.assertTrue(newer_version((1, 10), (1, 9, 9)))


class QuarterTurnTests(unittest.TestCase):
    def test_a_quarter_turn_is_read_as_a_number(self) -> None:
        for given, expected in (("0", 0), (90, 90), ("180", 180), (" 270 ", 270)):
            with self.subTest(given=given):
                self.assertEqual(expected, quarter_turn(given))

    def test_anything_that_is_no_quarter_turn_is_none(self) -> None:
        for given in (None, "", "auto", "45", 360, -90):
            with self.subTest(given=given):
                self.assertEqual(0, quarter_turn(given))


if __name__ == "__main__":
    unittest.main()

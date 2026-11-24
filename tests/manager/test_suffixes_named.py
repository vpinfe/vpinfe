"""The suffixes a file picker refuses a path for are listed in the language's own words."""

from __future__ import annotations

import unittest

from manager.kit import folder_picker


class SuffixesNamedTests(unittest.TestCase):
    def test_one_suffix_stands_alone(self) -> None:
        self.assertEqual(folder_picker.suffixes_named((".pem",)), ".pem")

    def test_two_are_joined_by_or(self) -> None:
        self.assertEqual(folder_picker.suffixes_named((".pem", ".crt")), ".pem or .crt")

    def test_more_are_a_list_closed_by_or(self) -> None:
        self.assertEqual(folder_picker.suffixes_named((".pem", ".crt", ".cer")),
                         ".pem, .crt or .cer")


if __name__ == "__main__":
    unittest.main()

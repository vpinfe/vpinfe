"""Which names a table's script mentions."""

from __future__ import annotations

import unittest

from common.games.formats.script_names import named_in

SCRIPT = '''
Const cGameName = "taxi_l4"
Sub Table1_Init
    LoadScores "Taxi Scores.txt"
    x = LoadValue("Medieval", "Hi")
    ' "commented.txt" was the old file
    y = "say ""highscores"" aloud"
End Sub
'''


class NamedInTests(unittest.TestCase):
    def test_a_name_in_a_string_is_found_with_or_without_its_extension(self) -> None:
        self.assertEqual(named_in(SCRIPT, ["Taxi Scores", "Medieval"]), {"Taxi Scores", "Medieval"})
        self.assertEqual(named_in(SCRIPT, ["Taxi Scores.txt"]), {"Taxi Scores.txt"})

    def test_case_does_not_matter(self) -> None:
        self.assertEqual(named_in(SCRIPT, ["TAXI SCORES", "medieval"]), {"TAXI SCORES", "medieval"})

    def test_a_name_must_be_a_whole_word(self) -> None:
        self.assertEqual(named_in(SCRIPT, ["scores", "high", "highscores"]),
                         {"scores", "highscores"})
        self.assertEqual(named_in('x = "mybigscores"', ["scores"]), set())

    def test_a_comment_does_not_count(self) -> None:
        self.assertEqual(named_in(SCRIPT, ["commented", "commented.txt"]), set())

    def test_a_name_outside_a_string_does_not_count(self) -> None:
        self.assertEqual(named_in(SCRIPT, ["Table1_Init", "LoadScores"]), set())

    def test_no_script_or_no_names_find_nothing(self) -> None:
        self.assertEqual(named_in("", ["a"]), set())
        self.assertEqual(named_in(SCRIPT, []), set())
        self.assertEqual(named_in(SCRIPT, ["", "  "]), set())

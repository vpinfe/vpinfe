"""A stored value on its way into a control.

The settings a program keeps in its own file are strings, and not every one of them
matches what that program says the setting is. A page that raises on either does not
draw at all - which is what a dead button looks like from the outside.
"""

from __future__ import annotations

import unittest

from manager.settings.settings import value_for

CHOICE = {"type": "choice", "choices": {"0": "Disabled", "1": "Floating"},
          "default": "0"}


class ValueForTests(unittest.TestCase):
    def test_a_closed_set_never_answers_with_something_not_in_it(self) -> None:
        """A setting nobody has touched has an empty effective value, and there is no
        option spelled "". This is the one that made the whole dialog refuse to open."""
        self.assertEqual(value_for(CHOICE, ""), "0")
        self.assertEqual(value_for(CHOICE, None), "0")

    def test_a_stored_answer_outside_the_set_falls_back(self) -> None:
        self.assertEqual(value_for(CHOICE, "7"), "0")

    def test_a_whole_number_written_as_a_fraction_is_still_a_number(self) -> None:
        """An integer written once as `0.0` stays that way in the file."""
        self.assertEqual(value_for({"type": "int", "default": "0"}, "0.0"), 0)
        self.assertEqual(value_for({"type": "int", "default": "0"}, "12"), 12)

    def test_a_number_that_is_not_one_falls_back_to_the_default(self) -> None:
        self.assertEqual(value_for({"type": "number", "default": "1.5"}, "nonsense"),
                         1.5)

    def test_a_switch_reads_the_words_a_file_writes_for_off(self) -> None:
        for said in ("0", "false", "no", "off"):
            with self.subTest(said=said):
                self.assertIs(value_for({"type": "bool", "default": "1"}, said), False)

    def test_and_anything_else_is_on(self) -> None:
        self.assertIs(value_for({"type": "bool", "default": "0"}, "1"), True)

    def test_an_untouched_setting_takes_the_default(self) -> None:
        """The control shows what the program will use, which for a setting nobody has
        touched is the program's own default."""
        self.assertIs(value_for({"type": "bool", "default": "1"}, ""), True)
        self.assertEqual(value_for({"type": "int", "default": "8"}, ""), 8)

    def test_a_string_is_left_alone(self) -> None:
        self.assertEqual(value_for({"type": "string", "default": ""}, "hello"), "hello")


if __name__ == "__main__":
    unittest.main()

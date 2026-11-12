"""What a settings row says about where its value came from.

The program's file is 98% untouched keys, so most of what this surface shows is a value
nobody chose. Saying that in the wire's word - `unset` - would put a term on screen that
nobody outside this project uses, and putting it on almost every row would say nothing
either way.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.i18n import t
from manager.kit import panel
from manager.workbench import all_settings


class _Bool:
    type = "bool"
    choices = ()
    default = "1"
    key = "Backglass.ShowGrill"


class _Choice:
    type = "choice"
    choices = (("0", "Disabled"), ("1", "Floating"))
    default = "0"
    key = "Backglass.BackglassOutput"


class ValueWordTests(unittest.TestCase):
    def test_a_switch_reads_on_and_off_rather_than_one_and_zero(self) -> None:
        self.assertEqual(all_settings._said_value(_Bool, "1"), "On")
        self.assertEqual(all_settings._said_value(_Bool, "0"), "Off")

    def test_a_choice_reads_its_own_label(self) -> None:
        self.assertEqual(all_settings._said_value(_Choice, "1"), "Floating")

    def test_a_value_with_no_label_is_shown_as_it_is(self) -> None:
        self.assertEqual(all_settings._said_value(_Choice, "7"), "7")


class ClearHintTests(unittest.TestCase):
    def test_clearing_names_the_value_it_goes_back_to_and_whose(self) -> None:
        """So nobody has to change a value to find out what it was following."""
        said = all_settings._clear_hint(
            {"fallback_scope": "launcher", "fallback": "0"}, _Bool, "Visual Pinball X")

        self.assertEqual(said, "Back to Off - The Launcher")

    def test_whose_is_said_in_the_scope_words(self) -> None:
        said = all_settings._clear_hint(
            {"fallback_scope": "folder", "fallback": "1"}, _Choice, "Visual Pinball X")

        self.assertEqual(said, "Back to Floating - This Game")

    def test_with_nothing_under_it_the_program_answers(self) -> None:
        self.assertEqual(all_settings._clear_hint({}, _Bool, "Visual Pinball X"),
                         "Back to On - Visual Pinball X's default")

    def test_a_default_with_no_value_names_only_whose(self) -> None:
        class _Text:
            type = "text"
            choices = ()
            default = ""

        self.assertEqual(all_settings._clear_hint({}, _Text, "Visual Pinball X"),
                         "Back to Visual Pinball X's default")

    def test_a_blank_that_has_a_word_goes_back_to_the_authors_setting(self) -> None:
        class _ViewMode(_Choice):
            default = ""
            blank = "As the author set it"

        for held, whose in (({}, "Visual Pinball X's default"),
                            ({"fallback_scope": "folder", "fallback": ""}, "This Game")):
            with self.subTest(whose=whose):
                self.assertEqual(all_settings._clear_hint(held, _ViewMode, "Visual Pinball X"),
                                 "Back to the author's setting")


class SwitchedOffTests(unittest.TestCase):
    ONE = {"launcher_id": "a", "app": "vpx", "enabled": True, "display_name": "Wide"}
    TWO = {"launcher_id": "b", "app": "vpx", "enabled": True, "display_name": "Narrow"}

    def test_it_names_where_the_tables_go(self) -> None:
        self.assertEqual(all_settings._switched_off_goes_to(self.ONE, [self.ONE, self.TWO]),
                         "Narrow")

    def test_the_note_calls_the_name_a_launcher_so_any_name_reads_as_one(self) -> None:
        self.assertEqual(t("manager.workbench.off_note", name="true"),
                         "Off, its tables play with the launcher “true” instead")

    def test_nothing_is_said_where_nothing_could_take_them(self) -> None:
        other_app = dict(self.TWO, app="fp")
        switched_off = dict(self.TWO, enabled=False)
        for held in ([self.ONE], [self.ONE, other_app], [self.ONE, switched_off]):
            with self.subTest(held=held):
                self.assertEqual(all_settings._switched_off_goes_to(self.ONE, held), "")


class MarkTests(unittest.TestCase):
    """The word beside a value. Set here is the dot's to say, so a word is only ever an
    exception."""

    def test_a_value_nobody_has_touched_is_not_marked(self) -> None:
        """Unmarked is the untouched one, so a mark always means somebody did
        something. On 98% of rows a mark would say nothing."""
        self.assertIsNone(all_settings._config_mark(
            {"set_here": False, "in_effect": True, "scope": ""}, "launcher", _Bool))

    def test_a_value_set_at_this_scope_takes_no_word(self) -> None:
        self.assertIsNone(all_settings._config_mark(
            {"set_here": True, "in_effect": True, "scope": "launcher"}, "launcher",
            _Bool))

    def test_at_a_table_following_all_tables_is_silent(self) -> None:
        self.assertIsNone(all_settings._config_mark(
            {"set_here": False, "in_effect": True, "scope": "launcher"}, "entry", _Bool))

    def test_a_value_from_this_game_names_it_at_a_table(self) -> None:
        with patch.object(panel, "state") as chip:
            all_settings._config_mark(
                {"set_here": False, "in_effect": True, "scope": "folder"}, "entry", _Bool)

        self.assertEqual(chip.call_args.args[0], "This Game")


class WhoseValueTests(unittest.TestCase):
    """What hovering a value says."""

    def whose(self, **held: object) -> str:
        return all_settings._whose_value(held, _Bool, "Visual Pinball X")

    def test_nobody_set_it(self) -> None:
        self.assertEqual(self.whose(), "Visual Pinball X's default")

    def test_set_to_something_else_says_what_it_changed_from(self) -> None:
        self.assertEqual(self.whose(set_here=True, in_effect=True, scope="launcher",
                                    value="0"),
                         "Changed from On - Visual Pinball X's default")

    def test_set_to_what_it_would_be_anyway_is_the_default_s(self) -> None:
        """In the file, but nothing was changed, so the hover says what the row says."""
        for value in ("1", "1.0"):
            with self.subTest(value=value):
                self.assertEqual(self.whose(set_here=True, in_effect=True,
                                            scope="launcher", value=value),
                                 "Visual Pinball X's default")

    def test_changed_from_another_scope_s_value_names_that_scope(self) -> None:
        self.assertEqual(self.whose(set_here=True, in_effect=True, scope="folder", value="0",
                                    fallback_scope="launcher", fallback="1"),
                         "Changed from On - The Launcher")

    def test_followed_from_another_scope(self) -> None:
        self.assertEqual(self.whose(scope="launcher", value="0"), "The Launcher")

    def test_left_empty_says_what_its_blank_word_means(self) -> None:
        field = SimpleNamespace(type="int", choices=(), default="", key="Player.PlayfieldWidth",
                                blank="Auto", left_empty="From the screen")
        whose = all_settings._whose_value

        self.assertEqual(whose({"value": ""}, field, "Visual Pinball X"), "From the screen")
        self.assertEqual(whose({"scope": "launcher", "value": "1920"}, field,
                               "Visual Pinball X"), "The Launcher")

    def test_no_word_on_screen_is_the_wire_s(self) -> None:
        said = " ".join(all_settings.CAME_FROM.values()).lower()

        self.assertNotIn("unset", said)
        self.assertNotIn("scope", said)


class IgnoredTests(unittest.TestCase):
    """A value a table's file holds that the program reads only for all tables."""

    def test_it_is_marked_ignored_and_says_where_it_works(self) -> None:
        with patch.object(panel, "state") as chip:
            all_settings._mark_for({"set_here": True, "in_effect": False, "scope": "entry"},
                                "entry", _Bool, offered=False)

        self.assertEqual(chip.call_args.args[:2], ("Ignored", "warn"))
        self.assertEqual(chip.call_args.kwargs["hint"], "Works only for the launcher")

    def test_one_the_table_does_not_hold_takes_no_mark(self) -> None:
        self.assertIsNone(all_settings._mark_for(
            {"set_here": False, "in_effect": True, "scope": "launcher"}, "entry", _Bool,
            offered=False))

    def test_one_the_program_reads_at_table_start_is_not_ignored(self) -> None:
        """Kept for all tables, and still read from the table's file when it starts."""
        self.assertIsNone(all_settings._mark_for(
            {"set_here": True, "in_effect": True, "scope": "entry"}, "entry", _Bool,
            offered=False))


if __name__ == "__main__":
    unittest.main()

"""The field vocabulary an extension declares in: what a settings page and a dialog draw,
and the refusal of anything else at the moment it is declared."""

from __future__ import annotations

import unittest

from common.extensions import fields
from common.extensions.fields import FieldError, dialog_fields, settings_fields

SELECT = {"key": "mode", "type": "select", "choices": [["fast", "Fast"], ["slow", "Slow"]]}


class Vocabulary(unittest.TestCase):
    def test_a_settings_page_draws_seven_types(self) -> None:
        self.assertEqual(fields.SETTINGS_TYPES,
                         {"string", "secret", "switch", "select", "multi", "number", "path"})

    def test_a_dialog_draws_ten_types(self) -> None:
        self.assertEqual(fields.DIALOG_TYPES,
                         {"string", "secret", "path", "select", "multi", "switch", "choice",
                          "number", "checklist", "settle"})

    def test_all_types_is_the_two_together(self) -> None:
        self.assertEqual(fields.ALL_TYPES, fields.DIALOG_TYPES | fields.SETTINGS_TYPES)


class SettingsFields(unittest.TestCase):
    def test_a_field_with_only_a_key_is_a_string_with_no_default(self) -> None:
        self.assertEqual(settings_fields("x", [{"key": "greeting"}]),
                         [{"key": "greeting", "type": "string", "default": "",
                           "choices": []}])

    def test_fields_keep_their_order(self) -> None:
        kept = settings_fields("x", [{"key": "b"}, {"key": "a"}, {"key": "c"}])

        self.assertEqual([one["key"] for one in kept], ["b", "a", "c"])

    def test_a_key_is_stripped(self) -> None:
        self.assertEqual(settings_fields("x", [{"key": "  greeting "}])[0]["key"], "greeting")

    def test_a_default_is_kept_as_text(self) -> None:
        self.assertEqual(settings_fields("x", [{"key": "n", "default": 5}])[0]["default"],
                         "5")

    def test_a_none_default_is_empty_text(self) -> None:
        self.assertEqual(settings_fields("x", [{"key": "n", "default": None}])[0]["default"],
                         "")

    def test_a_field_that_is_not_a_dict_is_refused(self) -> None:
        with self.assertRaisesRegex(FieldError, "not a dict"):
            settings_fields("x", ["greeting"])

    def test_a_field_with_no_key_is_refused(self) -> None:
        for one in ({}, {"key": ""}, {"key": "   "}, {"key": None}):
            with self.subTest(one=one), self.assertRaisesRegex(FieldError, "no key"):
                settings_fields("x", [one])

    def test_a_key_declared_twice_is_refused_and_named(self) -> None:
        with self.assertRaisesRegex(FieldError, "'greeting' twice"):
            settings_fields("x", [{"key": "greeting"}, {"key": "greeting"}])

    def test_a_key_core_keeps_for_itself_is_refused_and_named(self) -> None:
        for key in ("accounts", "declared"):
            with self.subTest(key=key), self.assertRaisesRegex(FieldError, f"named '{key}'"):
                settings_fields("x", [{"key": key}])

    def test_the_extension_is_named_in_the_refusal(self) -> None:
        with self.assertRaisesRegex(FieldError, "^vpinplay "):
            settings_fields("vpinplay", [{}])

    def test_a_type_a_settings_page_does_not_draw_is_refused_with_the_ones_it_does(self):
        with self.assertRaises(FieldError) as raised:
            settings_fields("x", [{"key": "pick", "type": "choice"}])

        said = str(raised.exception)
        self.assertIn("'pick' as 'choice'", said)
        self.assertIn("Drawn: multi, number, path, secret, select, string, switch", said)

    def test_a_type_only_a_dialog_draws_is_refused_in_settings(self) -> None:
        for kind in sorted(fields.DIALOG_TYPES - fields.SETTINGS_TYPES):
            with self.subTest(kind=kind), self.assertRaises(FieldError):
                settings_fields("x", [{"key": "k", "type": kind}])

    def test_every_type_a_settings_page_draws_is_accepted(self) -> None:
        kept = settings_fields("x", [{"key": "a", "type": "string"},
                                     {"key": "b", "type": "secret"},
                                     {"key": "c", "type": "switch"}, {**SELECT, "key": "d"},
                                     {"key": "e", "type": "multi", "choices": [["a", "A"]]},
                                     {"key": "f", "type": "number"},
                                     {"key": "g", "type": "path"}])

        self.assertEqual([one["type"] for one in kept],
                         ["string", "secret", "switch", "select", "multi", "number", "path"])

    def test_a_multi_needs_choices_and_defaults_to_none_ticked(self) -> None:
        with self.assertRaisesRegex(FieldError, "no choices"):
            settings_fields("x", [{"key": "m", "type": "multi"}])
        kept = settings_fields("x", [{"key": "m", "type": "multi", "choices": [["a", "A"]]}])

        self.assertEqual(kept[0]["default"], "[]")

    def test_a_multi_default_is_a_list_of_its_choices_stored_as_json(self) -> None:
        one = {"key": "m", "type": "multi", "choices": [["a", "A"], ["b", "B"]]}

        self.assertEqual(settings_fields("x", [{**one, "default": ["b", "a", "b"]}])[0]["default"],
                         '["b", "a"]')
        self.assertEqual(settings_fields("x", [{**one, "default": '["a"]'}])[0]["default"],
                         '["a"]')
        with self.assertRaisesRegex(FieldError, "'z'"):
            settings_fields("x", [{**one, "default": ["z"]}])

    def test_a_multi_default_that_is_not_a_list_is_refused_and_not_read_as_none_ticked(
            self) -> None:
        one = {"key": "m", "type": "multi", "choices": [["a", "A"]]}
        for default in ("a", 5, '{"a": 1}', "[broken"):
            with self.subTest(default=default):
                with self.assertRaisesRegex(FieldError, "not a list"):
                    settings_fields("x", [{**one, "default": default}])

    def test_a_number_keeps_the_limits_and_unit_it_declared(self) -> None:
        kept = settings_fields("x", [{"key": "n", "type": "number", "min": 1, "max": "10",
                                      "unit": "s", "default": 5}])[0]

        self.assertEqual((kept["min"], kept["max"], kept["unit"], kept["default"]),
                         (1.0, 10.0, "s", "5"))

    def test_a_number_default_outside_its_limits_is_refused(self) -> None:
        with self.assertRaisesRegex(FieldError, "number 'n'"):
            settings_fields("x", [{"key": "n", "type": "number", "max": 3, "default": 5}])

    def test_a_number_limit_that_is_not_a_number_is_refused(self) -> None:
        with self.assertRaisesRegex(FieldError, "min"):
            settings_fields("x", [{"key": "n", "type": "number", "min": "low"}])

    def test_a_path_wants_a_dir_unless_it_says_otherwise(self) -> None:
        kept = settings_fields("x", [{"key": "p", "type": "path"},
                                     {"key": "q", "type": "path", "wants": "exe"}])

        self.assertEqual([one["wants"] for one in kept], ["dir", "exe"])
        with self.assertRaisesRegex(FieldError, "'socket'"):
            settings_fields("x", [{"key": "p", "type": "path", "wants": "socket"}])


class Numbers(unittest.TestCase):
    def test_a_whole_number_is_stored_without_its_point(self) -> None:
        self.assertEqual(fields.number_text("5.0", {}), "5")
        self.assertEqual(fields.number_text(2.5, {}), "2.5")

    def test_text_nan_and_values_outside_the_limits_are_none(self) -> None:
        for value in ("abc", "nan", "inf", "1_0", None, 11, 0):
            with self.subTest(value=value):
                self.assertIsNone(fields.number_text(value, {"min": 1, "max": 10}))


class Ticked(unittest.TestCase):
    def test_a_list_or_its_json_text_gives_the_keys_once_in_order(self) -> None:
        self.assertEqual(fields.ticked(["b", "a", "b"]), ["b", "a"])
        self.assertEqual(fields.ticked('["a", "b"]'), ["a", "b"])

    def test_anything_else_is_none_ticked(self) -> None:
        for value in ("", "nonsense", "{}", None, 5):
            with self.subTest(value=value):
                self.assertEqual(fields.ticked(value), [])

    def test_a_secret_is_kept_with_no_default(self) -> None:
        self.assertEqual(settings_fields("x", [{"key": "token", "type": "secret"}])[0],
                         {"key": "token", "type": "secret", "default": "", "choices": []})

    def test_a_secret_with_a_default_is_refused_and_named(self) -> None:
        with self.assertRaisesRegex(FieldError, "secret 'token' a default"):
            settings_fields("x", [{"key": "token", "type": "secret", "default": "abc"}])

    def test_a_switch_defaults_to_false_as_text(self) -> None:
        self.assertEqual(settings_fields("x", [{"key": "s", "type": "switch"}])[0]["default"],
                         "false")

    def test_a_switch_default_is_normalized_to_true_or_false_text(self) -> None:
        cases = [(True, "true"), ("true", "true"), (False, "false"), ("false", "false"),
                 ("yes", "false"), ("", "false"), (None, "false")]
        for given, kept in cases:
            with self.subTest(given=given):
                one = {"key": "s", "type": "switch", "default": given}
                self.assertEqual(settings_fields("x", [one])[0]["default"], kept)

    def test_a_select_needs_choices(self) -> None:
        for one in ({"key": "m", "type": "select"},
                    {"key": "m", "type": "select", "choices": []}):
            with self.subTest(one=one), self.assertRaisesRegex(FieldError, "no choices"):
                settings_fields("x", [one])

    def test_a_select_with_no_default_takes_its_first_choice(self) -> None:
        self.assertEqual(settings_fields("x", [SELECT])[0]["default"], "fast")

    def test_a_select_default_must_be_one_of_its_choices(self) -> None:
        with self.assertRaises(FieldError) as raised:
            settings_fields("x", [{**SELECT, "default": "medium"}])

        self.assertIn("'medium'", str(raised.exception))
        self.assertIn("fast, slow", str(raised.exception))

    def test_a_select_default_that_is_a_choice_is_kept(self) -> None:
        self.assertEqual(settings_fields("x", [{**SELECT, "default": "slow"}])[0]["default"],
                         "slow")

    def test_choices_are_kept_as_pairs_of_text(self) -> None:
        kept = settings_fields("x", [{"key": "m", "type": "select",
                                      "choices": [(1, "One"), ("2", 2)]}])

        self.assertEqual(kept[0]["choices"], [["1", "One"], ["2", "2"]])
        self.assertEqual(kept[0]["default"], "1")

    def test_an_empty_list_declares_nothing(self) -> None:
        self.assertEqual(settings_fields("x", []), [])


class DialogFields(unittest.TestCase):
    def test_every_type_a_dialog_draws_is_accepted_as_it_was_given(self) -> None:
        asked = [{"key": kind, "type": kind} for kind in sorted(fields.DIALOG_TYPES)
                 if kind != "secret"]

        self.assertEqual(dialog_fields("x", asked), asked)

    def test_a_secret_goes_out_with_set_and_never_its_value(self) -> None:
        kept = dialog_fields("x", [{"key": "token", "type": "secret", "value": "hunter2"}])

        self.assertEqual(kept, [{"key": "token", "type": "secret", "set": True}])
        self.assertEqual(dialog_fields("x", [{"key": "t", "type": "secret"}])[0]["set"], False)

    def test_a_field_with_no_type_is_a_string(self) -> None:
        asked = [{"key": "name"}]

        self.assertEqual(dialog_fields("x", asked), asked)

    def test_a_type_outside_the_dialog_types_is_refused_with_the_ones_it_draws(self) -> None:
        with self.assertRaises(FieldError) as raised:
            dialog_fields("vpinplay", [{"key": "name", "type": "string"},
                                       {"key": "stars", "type": "rating"}])

        said = str(raised.exception)
        self.assertIn("vpinplay asks for 'stars' as 'rating'", said)
        self.assertIn("Drawn: checklist, choice, multi, number, path, secret, select, "
                      "settle, string, switch", said)

    def test_the_first_field_that_is_not_drawn_is_the_one_named(self) -> None:
        with self.assertRaisesRegex(FieldError, "'first' as 'one'"):
            dialog_fields("x", [{"key": "first", "type": "one"},
                                {"key": "second", "type": "two"}])

    def test_no_fields_is_fine(self) -> None:
        self.assertEqual(dialog_fields("x", []), [])


if __name__ == "__main__":
    unittest.main()

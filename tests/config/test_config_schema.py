"""The schema is the same settings the config store already has.

It is introduced against the ini it will replace, so it can be checked rather than
trusted: every section, every key and every default has to match what a new install
writes today. Once this holds, the store can read its defaults from here and the
format underneath can change without either being a guess.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.config import config_access, config_schema
from common.config.config_store import ConfigStore

# What a new install wrote before the schema existed, captured once. The store reads its
# defaults from the schema now, so comparing the two would compare the schema to itself -
# this is the outside witness that keeps the check meaningful. Changing a default here is
# changing it for every user, so it should be a visible line in a diff.
FROZEN = Path(__file__).resolve().parent.parent / "fixtures" / "config_defaults.json"


def _shipped_defaults() -> dict[str, dict[str, str]]:
    with TemporaryDirectory() as tmp:
        return ConfigStore(os.path.join(tmp, "vpinfe.ini")).defaults


class SchemaMatchesTheStoreTests(unittest.TestCase):
    def test_the_schema_declares_the_defaults_we_shipped(self) -> None:
        frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
        self.assertEqual(config_schema.defaults(), frozen,
                         "a default moved; if that is deliberate, update the fixture")

    def test_the_store_fills_a_new_file_from_the_schema(self) -> None:
        self.assertEqual(_shipped_defaults(), config_schema.defaults())

    def test_no_option_is_declared_twice(self) -> None:
        seen = [(entry.section, entry.key) for entry in config_schema.options()]
        duplicates = {pair for pair in seen if seen.count(pair) > 1}
        self.assertEqual(duplicates, set())


class MovedSettingsKeepTheirValueTests(unittest.TestCase):
    """A 2.x setting that changed section must not silently take its default.

    The value is in a section nothing looks at any more, so the new one answers with its
    default and the setting appears to have been forgotten.
    """

    def _reads(self, section, key, stored):
        from configparser import ConfigParser
        parser = ConfigParser()
        for (sec, name), value in stored.items():
            parser.setdefault(sec, {})
            parser[sec][name] = value
        return config_access.cfg_get(parser, section, key)

    def test_the_2x_spelling_still_reaches_the_moved_setting(self):
        for stored, expected in ((("Settings", "MMhideQuitButton"), "true"),
                                 (("Settings", "restorelasttable"), "false")):
            with self.subTest(stored=stored):
                section, key = ("behavior", "hide_quit_button") if "Quit" in stored[1] \
                    else ("behavior", "restore_last_table")
                self.assertEqual(self._reads(section, key, {stored: expected}), expected)


class SchemaShapeTests(unittest.TestCase):
    def test_every_option_states_a_type_we_can_read(self) -> None:
        kinds = {entry.type for entry in config_schema.options()}
        self.assertEqual(
            kinds - {"string", "bool", "int", "choice", "list", "text"}, set())

    def test_a_choice_defaults_to_one_of_its_choices(self) -> None:
        for entry in config_schema.options():
            if entry.type == "choice":
                self.assertIn(entry.default, entry.choices,
                              f"{entry.section}.{entry.key} defaults outside its choices")

    def test_only_choices_carry_choices(self) -> None:
        for entry in config_schema.options():
            if entry.type != "choice":
                self.assertEqual(entry.choices, (),
                                 f"{entry.section}.{entry.key} is not a choice")

    def test_a_bool_defaults_to_a_spelling_the_reader_accepts(self) -> None:
        for entry in config_schema.options():
            if entry.type == "bool":
                self.assertIn(entry.default, ("true", "false"),
                              f"{entry.section}.{entry.key} is not a bool default")

    def test_a_setting_that_moves_files_names_a_kind_a_surface_knows(self) -> None:
        declared = {e.moves_with for e in config_schema.options() if e.moves_with}

        self.assertEqual(declared, {config_schema.MOVES_BACKUPS})
        self.assertLessEqual(declared, set(config_schema.MOVES))

    def test_internal_state_is_not_offered_as_a_setting(self) -> None:
        """An option is internal when it is runtime state that happens to live in the
        config file - a last-played pointer, a cache marker. Nobody sets these, so nothing
        should offer them. "We would rather not show it" is not a reason, which is what
        this list exists to stop.
        """
        internal = {(e.section, e.key) for e in config_schema.options() if e.internal}

        self.assertEqual(internal, {("device", "setup_pending"),
                                    # The `locations` list holds this now.
                                    ("general", "game_root_dir"),
                                    # The extension's own store holds these now.
                                    ("vpinplay", "sync_on_exit"),
                                    ("vpinplay", "api_endpoint"),
                                    ("vpinplay", "user_id"),
                                    ("vpinplay", "initials"),
                                    ("vpinplay", "machine_id")})
        self.assertNotIn(("State", "last_game"),
                         {(e.section, e.key) for e in config_schema.settable()})


class AliasTests(unittest.TestCase):
    """Keys moved to snake_case at schema 2; every old spelling still has to resolve.

    The frozen fixture was regenerated for that rename, so it can no longer witness it.
    These do instead: each alias maps to a canonical key that exists and carries the
    same default, which is what makes an old file and an old call site both safe.
    """

    def test_every_alias_resolves_to_a_declared_option(self) -> None:
        for entry in config_schema.options():
            for alias in entry.aliases:
                self.assertEqual(config_schema.canonical(entry.section, alias), entry.key,
                                 f"{entry.section}.{alias} must resolve to {entry.key}")

    def test_no_alias_collides_with_a_real_key(self) -> None:
        keys = {(e.section, e.key.lower()) for e in config_schema.options()}
        for entry in config_schema.options():
            for alias in entry.aliases:
                self.assertNotIn((entry.section, alias.lower()), keys,
                                 f"{alias} is both an alias and a key")

    def test_the_renamed_keys_are_the_ones_we_meant(self) -> None:
        """A spot check in both directions, so a bad regeneration is visible."""
        self.assertEqual(config_schema.canonical("Settings", "gamerootdir"), "game_root_dir")
        self.assertEqual(config_schema.canonical("presentation", "cabmode"), "cab_mode")
        self.assertEqual(config_schema.canonical("Settings", "game_root_dir"), "game_root_dir")

    def test_input_keys_are_untouched(self) -> None:
        """[Input] renames are the vocabulary work and want decisions first."""
        for entry in config_schema.options():
            if entry.section == "Input":
                self.assertEqual(entry.aliases, (), f"{entry.key} should not have moved yet")


class LookupTests(unittest.TestCase):
    def test_a_key_repeats_only_across_window_sections(self) -> None:
        """Giving each window a section made screen_id mean three different monitors.

        Anywhere else a repeated key would make the key-only lookup ambiguous, so this
        pins the repeats to the place the design intends them.
        """
        keys = [entry.key.lower() for entry in config_schema.options()]
        repeated = {k for k in keys if keys.count(k) > 1}

        self.assertEqual(repeated, {"screen_id", "override", "enabled"})
        for entry in config_schema.options():
            if entry.key.lower() in repeated:
                self.assertIn(entry.section,
                              {"windows.playfield", "windows.backglass",
                               "windows.scoreview", "windows.topper", "dof", "real_dmd"},
                              f"{entry.section}.{entry.key} repeats outside a surface")

    def test_a_repeated_key_is_labelled_for_the_row_not_the_section(self) -> None:
        """Three sections answer `screen_id`, and the heading above the row says which.

        So the label is the row's own word and the surface is printed once, as a heading,
        rather than again on every line beneath it. That holds only while nothing looks a
        repeated key up without its section, which
        `test_no_repeated_key_is_asked_for_without_its_section` is what keeps true.
        """
        self.assertEqual(config_schema.label_for("screen_id", "windows.playfield"),
                         "Screen")
        self.assertEqual(config_schema.label_for("screen_id", "windows.backglass"),
                         "Screen")
        self.assertEqual(config_schema.label_for("override", "windows.scoreview"),
                         "Window Override")
        # `enabled` repeats too, and these two are drawn with no heading over them.
        self.assertEqual(config_schema.label_for("enabled", "dof"), "Enable DOF")
        self.assertEqual(config_schema.label_for("enabled", "real_dmd"),
                         "Enable Real DMD")

    def test_a_setting_that_moved_section_still_resolves(self) -> None:
        """Fourteen settings left [Displays] and [Media] for a window of their own."""
        self.assertEqual(config_schema.locate("Displays", "playfieldscreenid"),
                         ("windows.playfield", "screen_id"))
        self.assertEqual(config_schema.locate("Media", "bgmediapriority"),
                         ("presentation", "backglass_media_priority"),
                         "a media priority is what a theme is told to prefer")
        self.assertEqual(config_schema.locate("Displays", "cabmode"),
                         ("presentation", "cab_mode"),
                         "cab_mode is what the frontend shows, not what a screen is")

    def test_a_key_resolves_whatever_its_casing(self) -> None:
        """configparser lowercases option names, so a caller rarely has the original."""
        self.assertIsNotNone(config_schema.by_key("MMhideQuitButton"))
        self.assertIs(config_schema.by_key("mmhidequitbutton"),
                      config_schema.by_key("MMhideQuitButton"))

    def test_a_label_falls_back_to_a_readable_key(self) -> None:
        self.assertEqual(config_schema.label_for("some_unknown_key"), "Some Unknown Key")

    def test_an_undescribed_setting_says_so_rather_than_guessing(self) -> None:
        self.assertEqual(config_schema.description_for("no_such_setting"), "")


class DocumentationCoverageTests(unittest.TestCase):
    def test_every_settable_option_has_a_label(self) -> None:
        """A label is what a person sees; an option without one cannot be presented."""
        missing = sorted(f"{e.section}.{e.key}" for e in config_schema.settable()
                         if not e.label)

        self.assertEqual(missing, [],
                         "a settable option needs a label - humanizing its key is how "
                         "a person came to read chrome_options_exclude on screen")

    def test_every_choice_of_a_settable_option_has_a_word(self) -> None:
        missing = sorted(f"{e.keys}.choice.{value}" for e in config_schema.settable()
                         for value in e.choices if value not in e.choice_labels)

        self.assertEqual(missing, [], "a choice with no word shows its stored value")


if __name__ == "__main__":
    unittest.main()

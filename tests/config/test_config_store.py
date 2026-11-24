"""Settings are stored as JSON, and an ini is read once and kept.

Two things this has to get right, because both are somebody's install. A fresh one has
to come up with no file and no hand-editing at all - the whole first-run path had no
test before this. And an existing `vpinfe.ini` has to convert without losing a value,
without being deleted, and without converting twice.
"""

from __future__ import annotations

import json
import os
import unittest

from common.config import config_schema, input_registry
from common.config.config_store import (
    CONFIG_SCHEMA,
    SCHEMA_KEY,
    SETTINGS_KEY,
    ConfigStore,
    _flatten,
)
from common.devices import install_setup
from tests.support.library import TempTree


class ConfigStoreTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.ini = self.root / "vpinfe.ini"
        self.json = self.root / "vpinfe.json"

    def _payload(self) -> dict:
        return json.loads(self.json.read_text(encoding="utf-8"))


class FirstRunTests(ConfigStoreTests):
    """The acceptance criterion: a fresh install never needs the file opened."""

    def test_a_fresh_install_writes_a_complete_config(self) -> None:
        store = ConfigStore(str(self.ini))

        self.assertTrue(install_setup.pending(store), "a fresh install has to announce "
                                                       "itself - main.py opens Setup on it")
        self.assertTrue(self.json.exists())
        self.assertFalse(self.ini.exists(), "nothing writes an ini any more")

        # Flattened first: on disk a window is a nested object, in the schema it is a
        # dotted section name. Case-folded because configparser lowercases option names
        # and always did - MMhideQuitButton reached the old ini lowercase too.
        settings = _flatten(self._payload()[SETTINGS_KEY])
        self.assertEqual(
            {s: {k.lower() for k in v} for s, v in settings.items()},
            {s: {k.lower() for k in v} for s, v in config_schema.defaults().items()})

    def test_a_fresh_install_starts_the_manager_on_the_devices_own_look(self) -> None:
        ConfigStore(str(self.ini))
        self.assertEqual(_flatten(self._payload()[SETTINGS_KEY])["manager"]["appearance"], "system")

    def test_an_install_that_was_not_fresh_keeps_the_schema_default(self) -> None:
        """Anything that arrives with a file already written - a hand-made config, a
        converted 2.x install - is an upgrade, and the Manager stays on the palette it
        has always opened in until its owner picks another."""
        self.json.write_text("{}", encoding="utf-8")
        store = ConfigStore(str(self.ini))
        self.assertEqual(store.config.get("manager", "appearance", fallback="synthwave"),
                         "synthwave")

    def test_the_library_root_is_there_for_a_new_install_to_fill_in(self) -> None:
        """It used to check three. The other two named Visual Pinball's program and its
        ini, which are fields of a launcher now rather than settings of the install."""
        store = ConfigStore(str(self.ini))

        self.assertTrue(store.config.has_option("general", "game_root_dir"))

    def test_a_second_run_still_finds_setup_pending(self) -> None:
        """Unlike the momentary flag this replaced: a cabinet rebooted mid-setup has to
        keep landing on Setup, not just the one run that wrote the file."""
        ConfigStore(str(self.ini))
        self.assertTrue(install_setup.pending(ConfigStore(str(self.ini))))

    def test_a_hand_written_config_is_never_sent_through_setup(self) -> None:
        self.json.write_text("{}", encoding="utf-8")
        self.assertFalse(install_setup.pending(ConfigStore(str(self.ini))))

    def test_the_file_carries_a_schema_version(self) -> None:
        ConfigStore(str(self.ini))
        self.assertEqual(self._payload()[SCHEMA_KEY], CONFIG_SCHEMA)


class RereadTests(ConfigStoreTests):
    """The Manager writes through a store of its own; the one a process booted with is
    stale until it looks again."""

    def test_a_write_through_another_store_shows_after_reread(self) -> None:
        booted = ConfigStore(str(self.ini))
        other = ConfigStore(str(self.ini))
        other.set_value("Display", "rotation", "90")
        other.save()
        self.assertNotEqual(booted.value("Display", "rotation"), "90")

        booted.reread()

        self.assertEqual(booted.value("Display", "rotation"), "90")

    def test_a_file_that_cannot_be_read_leaves_what_was_read(self) -> None:
        store = ConfigStore(str(self.ini))
        store.set_value("Display", "rotation", "90")
        store.save()
        self.json.write_text("{ not json", encoding="utf-8")

        store.reread()

        self.assertEqual(store.value("Display", "rotation"), "90")

    def test_staged_changes_are_dropped(self) -> None:
        store = ConfigStore(str(self.ini))
        store.set_value("Display", "rotation", "270")

        store.reread()

        self.assertNotEqual(store.value("Display", "rotation"), 270)


class FreeTextTests(ConfigStoreTests):
    def test_a_percent_sign_in_a_value_is_kept_as_typed(self) -> None:
        store = ConfigStore(str(self.ini))
        store.set_value("theme_source_names", "https://example.test/a", "100% themes")
        store.save()

        again = ConfigStore(str(self.ini))

        self.assertEqual(again.config.get("theme_source_names", "https://example.test/a"),
                         "100% themes")


class TypedValueTests(ConfigStoreTests):
    def test_bools_and_ints_are_stored_as_bools_and_ints(self) -> None:
        ConfigStore(str(self.ini))
        settings = self._payload()[SETTINGS_KEY]

        self.assertIs(settings["presentation"]["cab_mode"], False)
        self.assertEqual(settings["network"]["http_port"], 8001)
        self.assertIsInstance(settings["network"]["http_port"], int)

    def test_a_blank_int_stays_blank_rather_than_becoming_zero(self) -> None:
        """Blank means "no window on this one", which is not the same as screen 0."""
        ConfigStore(str(self.ini))
        self.assertEqual(
            self._payload()[SETTINGS_KEY]["windows"]["backglass"]["screen_id"], "")

    def test_an_empty_list_is_an_empty_list_rather_than_a_blank_string(self) -> None:
        """Unlike a blank int, which means something. A list setting is edited by hand,
        and "" would leave the user guessing what shape to put there."""
        ConfigStore(str(self.ini))
        self.assertEqual(self._payload()[SETTINGS_KEY]["themes"]["repositories"], [])

    def test_a_list_setting_round_trips_through_the_parser(self) -> None:
        first = ConfigStore(str(self.ini))
        first.config.set("themes", "repositories", "https://a.net/one,https://b.net/two")
        first.save()

        self.assertEqual(self._payload()[SETTINGS_KEY]["themes"]["repositories"],
                         ["https://a.net/one", "https://b.net/two"])
        second = ConfigStore(str(self.ini))
        self.assertEqual(second.config.get("themes", "repositories"),
                         "https://a.net/one,https://b.net/two")

    def test_values_survive_the_round_trip_as_text(self) -> None:
        first = ConfigStore(str(self.ini))
        first.config.set("presentation", "cab_mode", "true")
        first.config.set("network", "http_port", "9001")
        first.save()

        second = ConfigStore(str(self.ini))
        self.assertTrue(second.config.getboolean("presentation", "cab_mode"))
        self.assertEqual(second.config.get("network", "http_port"), "9001")


class IniConversionTests(ConfigStoreTests):
    def _write_ini(self, body: str) -> None:
        self.ini.write_text(body, encoding="utf-8")

    def test_an_existing_ini_converts_and_is_kept(self) -> None:
        self._write_ini("[Displays]\nbgwindowoverride = 1x1+0+0\n")

        ConfigStore(str(self.ini))

        self.assertEqual(self._payload()[SETTINGS_KEY]["windows"]["backglass"]["override"],
                         "1x1+0+0")
        self.assertTrue(self.ini.exists(), "a downgrade needs the file 2.x reads")
        backups = [n for n in os.listdir(self.root) if n.startswith("vpinfe.ini.")]
        self.assertEqual(len(backups), 1, "the pre-JSON file is copied aside once")

    def test_a_percent_escaped_for_the_old_ini_parser_is_one_percent(self) -> None:
        self._write_ini("[Displays]\nbgwindowoverride = 100%%\n")

        ConfigStore(str(self.ini))

        self.assertEqual(self._payload()[SETTINGS_KEY]["windows"]["backglass"]["override"],
                         "100%")

    def test_converting_still_applies_the_key_renames(self) -> None:
        """An old ini must not lose a value whose key was renamed."""
        self._write_ini("[Displays]\nbgwindowoverride = 1x1+0+0\n")

        ConfigStore(str(self.ini))

        backglass = self._payload()[SETTINGS_KEY]["windows"]["backglass"]
        self.assertEqual(backglass["override"], "1x1+0+0")
        self.assertNotIn("bgwindowoverride", backglass)

    def test_the_defaults_switch_is_inverted_from_2x(self) -> None:
        """`disabledefaultchromeoptions` said when they were off, `defaults` says on."""
        for written, expected in (("true", False), ("false", True), ("yes", False)):
            with self.subTest(written=written):
                self._write_ini(f"[Settings]\ndisabledefaultchromeoptions = {written}\n")

                ConfigStore(str(self.ini))

                chromium = self._payload()[SETTINGS_KEY]["chromium"]
                self.assertIs(chromium["defaults"], expected)
                self.assertNotIn("disabledefaultchromeoptions",
                                 self._payload()[SETTINGS_KEY].get("general", {}))
                self.json.unlink()

    def test_terminal_logging_keeps_the_value_2x_wrote(self) -> None:
        """`console` is what 2.x called terminal logging, and 2.x wrote
        it into every vpinfe.ini as a default - so this one is owed an upgrade path."""
        self._write_ini("[logger]\nconsole = false\n")

        ConfigStore(str(self.ini))

        logger = self._payload()[SETTINGS_KEY]["logger"]
        self.assertIs(logger["terminal"], False)
        self.assertNotIn("console", logger)

    def test_a_user_value_is_typed_on_the_way_in(self) -> None:
        self._write_ini("[Displays]\nplayfieldscreenid = 2\ncabmode = true\n")

        ConfigStore(str(self.ini))

        settings = self._payload()[SETTINGS_KEY]
        self.assertEqual(settings["windows"]["playfield"]["screen_id"], 2)
        self.assertIs(settings["presentation"]["cab_mode"], True)

    def test_conversion_happens_once(self) -> None:
        self._write_ini("[Settings]\ngamerootdir = /my/tables\n")
        ConfigStore(str(self.ini))
        after_first = sorted(os.listdir(self.root))

        ConfigStore(str(self.ini))

        self.assertEqual(sorted(os.listdir(self.root)), after_first,
                         "a second run must not back the ini up again")

    def test_json_wins_when_both_exist(self) -> None:
        """The ini is left in place, so it must not overwrite newer settings."""
        ConfigStore(str(self.ini))
        self._write_ini("[Settings]\ngamerootdir = /stale\n")

        store = ConfigStore(str(self.ini))

        self.assertNotEqual(store.config.get("general", "game_root_dir"), "/stale")

    def test_a_newer_schema_is_not_stamped_down(self) -> None:
        """A future VPinFE owns that number; claiming it would say we understood it."""
        ConfigStore(str(self.ini))
        payload = self._payload()
        payload[SCHEMA_KEY] = CONFIG_SCHEMA + 5
        self.json.write_text(json.dumps(payload), encoding="utf-8")

        store = ConfigStore(str(self.ini))
        store.save()

        self.assertEqual(self._payload()[SCHEMA_KEY], CONFIG_SCHEMA + 5)


class RetiredValueTests(ConfigStoreTests):
    """A key rename carried the key and left the value in the old vocabulary.

    It resolves either way on read, so this is about what the file says. The file is what
    the settings are served as, and saying `alpha` where the schema publishes
    `('sort', 'count')` is the file contradicting its own contract.
    """

    def test_an_ini_value_is_written_in_the_current_vocabulary(self) -> None:
        self.ini.write_text("[Input]\npagingtype = alpha\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._payload()["settings"]["behavior"]["paging_group"], "sort")

    def test_a_json_written_before_the_rename_is_corrected_in_place(self) -> None:
        """The cab hit this: the key migrated, the value did not, and nothing was
        converting an ini any more so no migration was going to reach it."""
        store = ConfigStore(str(self.ini))
        store.config.set("behavior", "paging_group", "numeric")
        store.save()

        ConfigStore(str(self.ini))

        self.assertEqual(self._payload()["settings"]["behavior"]["paging_group"], "count")

    def test_a_current_value_is_left_alone(self) -> None:
        self.ini.write_text("[behavior]\npaging_group = count\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._payload()["settings"]["behavior"]["paging_group"], "count")


class RetiredInputDefaultsTests(ConfigStoreTests):
    """A 2.x file holds what 2.x shipped for every key nobody changed, so on its own a
    value equal to a shipped one is not a choice. Reading it as one would keep a tap of
    Esc as Quit for every install that came up from 2.x."""

    TWO_X = "\n".join([
        "[Input]", "keyleft = ArrowLeft,ShiftLeft", "keyright = ArrowRight,ShiftRight",
        "keyup = ArrowUp", "keydown = ArrowDown", "keypageup = PageUp",
        "keypagedown = PageDown", "keyselect = Enter", "keymenu = m", "keyback = b",
        "keytutorial = t", "keyexit = Escape,q", "keycollectionmenu = c",
        "joyleft =", "joyselect =", "joyexit =", ""])

    def _migrated(self, text: str, action: str) -> str:
        self.ini.write_text(text, encoding="utf-8")
        return ConfigStore(str(self.ini)).config.get("input", action)

    def test_the_shipped_2x_keys_take_the_current_defaults(self) -> None:
        exit_option = config_schema.option("input", "exit")
        back_option = config_schema.option("input", "back")
        assert exit_option is not None
        assert back_option is not None
        exit_default = exit_option.default

        self.assertEqual(self._migrated(self.TWO_X, "exit"), exit_default)
        self.assertEqual(self._migrated(self.TWO_X, "back"), back_option.default)

    def test_a_key_somebody_changed_is_kept_as_they_wrote_it(self) -> None:
        """Each half is judged alone: the controller buttons nobody mapped still take the
        standard layout's."""
        text = self.TWO_X.replace("keyexit = Escape,q", "keyexit = Escape,q,F4")

        self.assertEqual(self._migrated(text, "exit"),
                         "key:Escape,key:q,key:F4,pad:standard/button:1@hold:1500")

    def test_a_button_somebody_mapped_is_kept_and_the_standard_ones_stay_out(self) -> None:
        text = self.TWO_X.replace("joyselect =", "joyselect = 7")

        self.assertEqual(self._migrated(text, "select"), "key:Enter,pad:0/button:7")

    def test_a_key_somebody_chose_outranks_a_default_on_another_action(self) -> None:
        """Esc kept on Quit by choice stays there; Back, unchanged, gives it up."""
        text = self.TWO_X.replace("keyexit = Escape,q", "keyexit = Escape")
        self.ini.write_text(text, encoding="utf-8")

        config = ConfigStore(str(self.ini)).config

        self.assertEqual(config.get("input", "exit"),
                         "key:Escape,pad:standard/button:1@hold:1500")
        self.assertEqual(config.get("input", "back"), "key:KeyB,pad:standard/button:1")
        self.assertEqual(input_registry.collisions(
            {name: value.split(",") for name, value in config.items("input")}), {})

    def test_converting_twice_does_not_change_it_again(self) -> None:
        first = self._migrated(self.TWO_X, "exit")

        self.assertEqual(ConfigStore(str(self.ini)).config.get("input", "exit"), first)


class RetiredRoleTests(ConfigStoreTests):
    """`device.roles` became `device.features`, and one role expands into two.

    Not a key rename and not a value rename: `hub` meant the library and the device list
    together. An install that said only `device` must land on `frontend` alone - falling
    through to the default would hand a cab a library it never had.
    """

    def _features(self) -> list[str]:
        return self._payload()["settings"]["device"]["features"]

    def test_a_json_written_before_the_rename_is_corrected_in_place(self) -> None:
        store = ConfigStore(str(self.ini))
        store.config.set("device", "roles", "hub,player")
        store.save()

        ConfigStore(str(self.ini))

        self.assertEqual(self._features(), ["library", "frontend", "devices"])

    def test_a_migrating_install_does_not_acquire_overview(self) -> None:
        """Nothing expands into it, and the migration writes the features it found, so an
        install coming from roles keeps exactly those."""
        self.ini.write_text("[device]\nroles = hub,device\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertNotIn("overview", self._features())

    def test_an_install_already_asking_for_overview_keeps_it(self) -> None:
        """The migration reorders what it finds; it is not a filter on what is allowed."""
        self.ini.write_text("[device]\nroles = hub,overview\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._features(), ["library", "devices", "overview"])

    def test_a_cab_does_not_inherit_a_library_it_never_had(self) -> None:
        """The one that matters. `device` alone becomes `frontend` alone, not everything -
        the fallback is the default set, and falling through to it would be silent."""
        self.ini.write_text("[device]\nroles = device\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._features(), ["frontend"])

    def test_the_two_era_spelling_arrives_at_the_same_place(self) -> None:
        """`player` named the launching install before `device` did."""
        self.ini.write_text("[device]\nroles = player\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._features(), ["frontend"])

    def test_one_role_expands_into_two_features(self) -> None:
        """`hub` was the shared library and the list of other installs at once."""
        self.ini.write_text("[device]\nroles = hub\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._features(), ["library", "devices"])

    def test_the_order_is_the_schema_order_however_it_was_written(self) -> None:
        self.ini.write_text("[device]\nroles = player,hub\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._features(), ["library", "frontend", "devices"])

    def test_current_features_are_left_alone(self) -> None:
        self.ini.write_text("[device]\nfeatures = frontend\n", encoding="utf-8")

        ConfigStore(str(self.ini))

        self.assertEqual(self._features(), ["frontend"])

    def test_a_migrated_install_survives_being_read_back(self) -> None:
        """The point of the migration: install_identity drops what it does not know, so
        without this an install reported every feature where it had asked for one."""
        from common.devices import install_identity

        self.ini.write_text("[device]\nroles = device\n", encoding="utf-8")

        store = ConfigStore(str(self.ini))

        self.assertEqual(install_identity.features(store), ["core", "frontend"])


class ConfirmSwitchTests(ConfigStoreTests):
    """`frontend.confirm` was a list of scopes and is a switch.

    Anyone who had named a scope asked to be asked, so they stay asked. Letting the type
    conversion have "app,system" would read it as not-a-boolean and answer no - turning a
    setting off because its shape changed, which is the failure this whole file exists to
    catch.
    """

    def _confirm(self, stored):
        self.json.write_text(json.dumps({"schema": 2, "settings": {"lifecycle": stored}}),
                             encoding="utf-8")
        ConfigStore(str(self.ini))
        return self._payload()["settings"]["behavior"]["confirm"]

    def test_any_scope_named_means_keep_asking(self) -> None:
        for scopes in (["app", "system"], ["system"], ["frontend"]):
            with self.subTest(scopes=scopes):
                self.assertIs(self._confirm({"confirm": scopes}), True)

    def test_no_scopes_means_do_not_ask(self) -> None:
        self.assertIs(self._confirm({"confirm": []}), False)

    def test_a_fresh_install_does_not_ask(self) -> None:
        """Off is how VPinFE has always behaved."""
        ConfigStore(str(self.ini))
        self.assertIs(self._payload()["settings"]["behavior"]["confirm"], False)


class MovedSectionTests(ConfigStoreTests):
    """A setting that changes section takes its value with it, and leaves nothing behind.

    Declaring it in its new home and saying nothing about the old one loses whatever the
    user had: the new key falls back to its default while the real value sits orphaned
    under the old section - which the settings page then renders as a second control
    beside the new one. Both halves of that were live before this.
    """

    def _written(self, settings):
        self.json.write_text(json.dumps({"schema": 2, "settings": settings}),
                             encoding="utf-8")
        ConfigStore(str(self.ini))
        return self._payload()["settings"]

    def test_a_customised_value_moves_with_the_setting(self) -> None:
        after = self._written({"input": {"paging_group": "count", "paging_size": 25}})

        self.assertEqual(after["behavior"]["paging_group"], "count")
        self.assertEqual(after["behavior"]["paging_size"], 25)

    def test_the_old_entries_do_not_linger(self) -> None:
        """A leftover is not harmless: the settings page renders what the file holds."""
        after = self._written({"input": {"paging_group": "count", "paging_size": 25},
                               "lifecycle": {"confirm": ["app"]}})

        self.assertEqual([k for k in after.get("input", {}) if "paging" in k], [])
        self.assertNotIn("lifecycle", after)

    def test_a_setting_that_moved_and_changed_type_arrives(self) -> None:
        """confirm did both at once - lifecycle to frontend, and a scope list to a switch.
        Either step alone would have lost it."""
        after = self._written({"lifecycle": {"confirm": ["app", "system"]}})

        self.assertIs(after["behavior"]["confirm"], True)

    def test_a_file_that_never_had_them_gets_the_defaults(self) -> None:
        after = self._written({"general": {}})

        self.assertEqual(after["behavior"]["paging_size"], 10)
        self.assertIs(after["behavior"]["confirm"], False)


class TwoStoresTests(ConfigStoreTests):
    def test_a_setting_written_elsewhere_survives_a_save_by_a_store_that_never_saw_it(
            self) -> None:
        kept = ConfigStore(str(self.ini))
        other = ConfigStore(str(self.ini))
        other.config.set("device", "name", "cab")
        other.save()

        kept.config.set("network", "http_port", "9001")
        kept.save()

        after = ConfigStore(str(self.ini))
        self.assertEqual(after.config.get("device", "name"), "cab")
        self.assertEqual(after.config.get("network", "http_port"), "9001")

    def test_a_save_with_nothing_changed_writes_nothing_back(self) -> None:
        kept = ConfigStore(str(self.ini))
        other = ConfigStore(str(self.ini))
        other.config.set("general", "game_root_dir", "/elsewhere")
        other.save()

        kept.save()

        self.assertEqual(self._payload()[SETTINGS_KEY]["general"]["game_root_dir"],
                         "/elsewhere")

    def test_a_key_both_stores_changed_takes_the_later_save(self) -> None:
        kept = ConfigStore(str(self.ini))
        other = ConfigStore(str(self.ini))
        other.config.set("network", "http_port", "9001")
        other.save()

        kept.config.set("network", "http_port", "9002")
        kept.save()

        self.assertEqual(self._payload()[SETTINGS_KEY]["network"]["http_port"], 9002)

    def test_a_key_the_store_removed_is_gone_and_the_rest_are_kept(self) -> None:
        kept = ConfigStore(str(self.ini))
        other = ConfigStore(str(self.ini))
        other.config.set("general", "game_root_dir", "/elsewhere")
        other.save()

        kept.config.remove_option("logger", "terminal")
        kept.save()

        settings = self._payload()[SETTINGS_KEY]
        self.assertNotIn("terminal", settings["logger"])
        self.assertEqual(settings["general"]["game_root_dir"], "/elsewhere")

    def test_after_a_save_the_store_holds_what_the_file_holds(self) -> None:
        kept = ConfigStore(str(self.ini))
        other = ConfigStore(str(self.ini))
        other.config.set("general", "game_root_dir", "/elsewhere")
        other.save()

        kept.save()

        self.assertEqual(kept.config.get("general", "game_root_dir"), "/elsewhere")


if __name__ == "__main__":
    unittest.main()

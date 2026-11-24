"""A theme update must not reset the options the user chose.

They used to be written into the installed theme package, and updating a theme deletes
that package - so every update silently restored every default, with no backup and no
warning. The values live outside the theme now. The test that matters is the last one:
install, configure, update, and the choice is still there.
"""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from common import service_errors
from common.config.config_store import ConfigStore
from common.i18n import t
from common.online import theme_ops, theme_service
from common.online.theme_installer import ThemeInstallStore
from common.settings import config_service, theme_options
from tests.support.library import TempTree

SCHEMA = {"title": "Reference", "options": [
    {"key": "showFlags", "name": "Show flags", "type": "boolean", "default": False},
    {"key": "wheelSpan", "name": "Wheel span", "type": "number", "default": 3},
]}


def _installed(root: Path, folder: str = "Reference", *, values: dict | None = None) -> Path:
    """A theme as a pre-3.0 build left it: values written into its own schema file."""
    theme_dir = root / folder
    theme_dir.mkdir(parents=True)
    schema = json.loads(json.dumps(SCHEMA))
    for option in schema["options"]:
        if values and option["key"] in values:
            option["value"] = values[option["key"]]
    (theme_dir / "theme.json").write_text(json.dumps(schema), encoding="utf-8")
    (theme_dir / "manifest.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    return theme_dir


class _Base(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.config_file = self.root / "config" / "vpinfe.json"
        self.config_file.parent.mkdir()
        patcher = mock.patch.object(config_service, "get_ini_config",
                                    lambda: ConfigStore(self.config_file))
        patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self) -> dict:
        return json.loads(self.config_file.read_text(encoding="utf-8"))


class UserOptionStoreTests(_Base):
    def test_values_round_trip(self) -> None:
        theme_options.save("Reference", {"showFlags": True, "wheelSpan": 5})
        self.assertEqual(theme_options.load("Reference"),
                         {"showFlags": True, "wheelSpan": 5})

    def test_a_theme_nobody_configured_has_no_values(self) -> None:
        self.assertEqual(theme_options.load("Reference"), {})

    def test_the_entry_is_keyed_by_folder_name_in_vpinfe_json(self) -> None:
        """Not the registry key - a local or side-loaded theme has no registry entry."""
        theme_options.save("My Local Theme", {"showFlags": True})
        self.assertEqual(self.saved()["theme_options"],
                         {"My Local Theme": {"showFlags": True}})

    def test_nothing_is_written_beside_the_config_file(self) -> None:
        theme_options.save("Reference", {"showFlags": True})
        self.assertEqual([p.name for p in self.config_file.parent.iterdir()],
                         ["vpinfe.json"])

    def test_a_hostile_folder_name_is_only_a_key(self) -> None:
        theme_options.save("../../etc/passwd", {"showFlags": True})
        self.assertEqual(theme_options.load("../../etc/passwd"), {"showFlags": True})
        self.assertEqual([p.name for p in self.root.rglob("passwd")], [])

    def test_saving_a_theme_leaves_the_others_alone(self) -> None:
        theme_options.save("Reference", {"showFlags": True})
        theme_options.save("Other", {"wheelSpan": 4})
        theme_options.save("Reference", {"showFlags": False})
        self.assertEqual(self.saved()["theme_options"],
                         {"Reference": {"showFlags": False}, "Other": {"wheelSpan": 4}})

    def test_saving_nothing_leaves_an_empty_entry(self) -> None:
        theme_options.save("Reference", {"showFlags": True})
        theme_options.save("Reference", {})
        self.assertEqual(self.saved()["theme_options"], {"Reference": {}})

    def test_a_file_that_is_not_an_object_reads_as_nothing_chosen(self) -> None:
        self.config_file.write_text(json.dumps({"schema": 2, "settings": {},
                                                "theme_options": ["x"]}), encoding="utf-8")
        self.assertEqual(theme_options.load("Reference"), {})

    def test_a_folder_name_with_nothing_usable_is_refused_in_words(self) -> None:
        with self.assertRaises(ValueError) as raised:
            theme_options.save("  ", {"showFlags": True})

        self.assertEqual(str(raised.exception),
                         t("error.themes.folder_name_unusable", folder="  "))

    def test_two_stores_saving_different_themes_keep_both(self) -> None:
        first, second = ConfigStore(self.config_file), ConfigStore(self.config_file)
        first.set_theme_options({"Reference": {"showFlags": True}})
        first.save()
        second.set_theme_options({"Other": {"wheelSpan": 4}})
        second.save()
        self.assertEqual(self.saved()["theme_options"],
                         {"Reference": {"showFlags": True}, "Other": {"wheelSpan": 4}})


class RefusedSettingTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        _installed(self.root)
        patcher = mock.patch.object(theme_service, "THEMES_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_value_a_setting_cannot_take_is_refused_in_words(self) -> None:
        with self.assertRaises(ValueError) as raised:
            theme_service.save_theme_option_values("Reference", {"wheelSpan": "lots"})

        self.assertEqual(str(raised.exception),
                         t("error.themes.option_takes_number", name="Wheel span"))

    def test_a_theme_with_no_settings_says_so(self) -> None:
        with self.assertRaises(ValueError) as raised:
            theme_service.save_theme_option_values("Elsewhere", {"wheelSpan": 2})

        self.assertEqual(str(raised.exception),
                         t("error.themes.no_settings", key="Elsewhere"))

    def test_saving_passes_the_refusal_on_as_it_was_said(self) -> None:
        registry = mock.Mock()
        registry.get_installed_folder.return_value = "Reference"
        with mock.patch.object(theme_ops, "_loaded", return_value=registry), \
                self.assertRaises(service_errors.RefusedError) as raised:
            theme_ops.save_options("Reference", {"wheelSpan": "lots"})

        self.assertEqual(str(raised.exception),
                         t("error.themes.option_takes_number", name="Wheel span"))


class MigrationTests(_Base):
    def test_values_are_lifted_out_of_an_installed_package(self) -> None:
        themes = self.root / "themes"
        _installed(themes, values={"showFlags": True, "wheelSpan": 7})

        moved = theme_options.migrate_from_packages(themes)

        self.assertEqual(moved, ["Reference"])
        self.assertEqual(theme_options.load("Reference"),
                         {"showFlags": True, "wheelSpan": 7})

    def test_a_theme_with_no_chosen_values_is_left_alone(self) -> None:
        themes = self.root / "themes"
        _installed(themes)

        self.assertEqual(theme_options.migrate_from_packages(themes), [])

    def test_an_existing_user_file_is_never_overwritten(self) -> None:
        """Second run, or a theme that already moved - what is there wins."""
        themes = self.root / "themes"
        _installed(themes, values={"showFlags": True})
        theme_options.save("Reference", {"showFlags": False})

        theme_options.migrate_from_packages(themes)

        self.assertEqual(theme_options.load("Reference"), {"showFlags": False})

    def test_choices_taken_away_are_not_lifted_back_from_the_package(self) -> None:
        themes = self.root / "themes"
        _installed(themes, values={"showFlags": True})
        theme_options.migrate_from_packages(themes)
        theme_options.save("Reference", {})

        self.assertEqual(theme_options.migrate_from_packages(themes), [])
        self.assertEqual(theme_options.load("Reference"), {})

    def test_migrating_twice_changes_nothing(self) -> None:
        themes = self.root / "themes"
        _installed(themes, values={"wheelSpan": 9})

        theme_options.migrate_from_packages(themes)
        self.assertEqual(theme_options.migrate_from_packages(themes), [])
        self.assertEqual(theme_options.load("Reference"), {"wheelSpan": 9})


class SurvivesAnUpdateTests(_Base):
    def test_updating_a_theme_keeps_what_the_user_chose(self) -> None:
        """The whole point. This is the sequence that used to reset every option."""
        themes = self.root / "themes"
        themes.mkdir()
        _installed(themes, values={"showFlags": True, "wheelSpan": 7})
        theme_options.migrate_from_packages(themes)

        # A newer release of the same theme, exactly as the installer receives it.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Reference-master/manifest.json", json.dumps({"version": "2.0.0"}))
            archive.writestr("Reference-master/theme.json", json.dumps(SCHEMA))
        buffer.seek(0)
        ThemeInstallStore(str(themes)).install_zip(
            "Reference", "https://github.com/someone/Reference", buffer)

        self.assertEqual(ThemeInstallStore(str(themes)).installed_version("Reference"),
                         "2.0.0", "precondition: the theme really was updated")
        self.assertEqual(theme_options.load("Reference"),
                         {"showFlags": True, "wheelSpan": 7})


class SchemaFileTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.theme = _installed(self.root / "themes")
        theme_options._warned_schema.clear()

    def test_contract_1_reads_its_options_from_theme_json(self) -> None:
        self.assertEqual(theme_options.schema_path(self.theme, 1).name, "theme.json")

    def test_contract_2_reads_them_from_options_json(self) -> None:
        self.assertEqual(theme_options.schema_path(self.theme, 2).name, "options.json")

    def test_a_later_contract_reads_options_json_too(self) -> None:
        self.assertEqual(theme_options.schema_path(self.theme, 3).name, "options.json")

    def test_a_contract_2_theme_with_options_only_in_theme_json_is_told_once(self) -> None:
        with self.assertLogs("vpinfe.common.settings.theme_options", level="WARNING") as logged:
            theme_options.schema_path(self.theme, 2)
            theme_options.schema_path(self.theme, 2)

        self.assertEqual(len(logged.records), 1)
        self.assertIn("options.json", logged.output[0])

    def test_a_theme_with_an_options_file_is_not_told_anything(self) -> None:
        (self.theme / "options.json").write_text(json.dumps(SCHEMA), encoding="utf-8")

        with self.assertNoLogs("vpinfe.common.settings.theme_options", level="WARNING"):
            theme_options.schema_path(self.theme, 2)


if __name__ == "__main__":
    unittest.main()

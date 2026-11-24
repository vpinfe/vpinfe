"""The accepted list: what the Owner has said yes to, kept apart from the settings."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.online import theme_sources
from common.security import accepted

STOCK = theme_sources.stock_list()
ELSEWHERE = "https://git.example.net/someone/themes.json"
ANOTHER = "https://git.example.net/other/themes.json"


class Case(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.config = Path(holder.name) / "config"
        self.themes = Path(holder.name) / "themes"
        self.themes.mkdir()
        self.file = self.config / "accepted.json"
        for name, value in (("CONFIG_DIR", self.config), ("ACCEPTED_PATH", self.file),
                            ("SECRETS_PATH", self.config / "secrets.json"),
                            ("THEMES_DIR", self.themes)):
            patched = mock.patch.object(paths, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def install(self, *keys: str) -> None:
        for key in keys:
            (self.themes / key).mkdir()


class ThemeOrigin(Case):
    def test_the_address_decides_never_the_name(self) -> None:
        self.assertEqual(accepted.theme_origin(stock=True), accepted.STOCK)
        self.assertEqual(accepted.theme_origin(stock=False), accepted.SOURCE)
        self.assertEqual(accepted.theme_origin(stock=False, in_folder=True), accepted.IN_FOLDER)

    def test_a_theme_from_the_stock_list_needs_no_entry(self) -> None:
        self.assertTrue(accepted.theme_accepted("Anything", STOCK, stock=True))

    def test_a_theme_from_another_source_is_not_accepted_until_it_is(self) -> None:
        self.assertFalse(accepted.theme_accepted("Mine", ELSEWHERE, stock=False))

        accepted.accept_theme("Mine", ELSEWHERE)

        self.assertTrue(accepted.theme_accepted("Mine", ELSEWHERE, stock=False))

    def test_the_same_theme_from_a_different_address_asks_again(self) -> None:
        accepted.accept_theme("Mine", ELSEWHERE)

        self.assertFalse(accepted.theme_accepted("Mine", ANOTHER, stock=False))

    def test_a_theme_found_in_the_folder_is_accepted_as_found_there(self) -> None:
        accepted.accept_theme("Found", "")

        self.assertTrue(accepted.theme_accepted("Found", "", stock=False, in_folder=True))
        self.assertFalse(accepted.theme_accepted("Found", ELSEWHERE, stock=False))

    def test_a_removed_theme_has_to_be_accepted_again(self) -> None:
        accepted.accept_theme("Mine", ELSEWHERE)

        accepted.forget_theme("Mine")

        self.assertFalse(accepted.theme_accepted("Mine", ELSEWHERE, stock=False))

    def test_only_an_install_from_the_stock_list_is_noted_on_its_own(self) -> None:
        accepted.note_theme_installed("FromStock", STOCK, stock=True)
        accepted.note_theme_installed("FromElsewhere", ELSEWHERE, stock=False)

        self.assertTrue(accepted.theme_runs("FromStock"))
        self.assertFalse(accepted.theme_runs("FromElsewhere"))


class ThemesAlreadyInstalled(Case):
    def test_they_count_as_accepted_the_first_time_the_list_is_read(self) -> None:
        self.install("Old", "Older")

        self.assertTrue(accepted.theme_runs("Old"))
        self.assertTrue(accepted.theme_runs("Older"))

    def test_one_that_arrives_after_that_does_not(self) -> None:
        self.install("Old")
        accepted.theme_runs("Old")
        self.install("Later")

        self.assertFalse(accepted.theme_runs("Later"))
        self.assertEqual(accepted.unaccepted_themes(), ["Later"])

    def test_they_ask_once_for_any_address_they_are_offered_from(self) -> None:
        self.install("Old")
        accepted.theme_runs("Old")

        self.assertTrue(accepted.theme_runs("Old"))
        self.assertTrue(accepted.theme_accepted("Old", "", stock=False, in_folder=True))
        self.assertFalse(accepted.theme_accepted("Old", ELSEWHERE, stock=False))

        accepted.accept_theme("Old", ELSEWHERE)

        self.assertTrue(accepted.theme_accepted("Old", ELSEWHERE, stock=False))
        self.assertFalse(accepted.theme_accepted("Old", ANOTHER, stock=False))

    def test_an_unreadable_list_is_an_empty_one_and_never_a_new_seeding(self) -> None:
        self.install("Old")
        accepted.theme_runs("Old")
        self.file.write_text("{not json", encoding="utf-8")

        self.install("Later")

        self.assertFalse(accepted.theme_runs("Old"))
        self.assertFalse(accepted.theme_runs("Later"))

    def test_a_deleted_list_is_an_empty_one_and_never_a_new_seeding(self) -> None:
        self.install("Old")
        self.assertTrue(accepted.theme_runs("Old"))
        self.file.unlink()

        self.assertFalse(accepted.theme_runs("Old"))
        self.assertEqual(accepted.unaccepted_themes(), ["Old"])
        accepted.accept_theme("Old", "")
        self.assertTrue(accepted.theme_runs("Old"))

    def test_the_first_read_with_no_list_still_starts_one(self) -> None:
        self.install("Old")

        self.assertFalse(self.file.exists())
        self.assertTrue(accepted.theme_runs("Old"))
        self.assertTrue(self.file.exists())

    def test_the_default_theme_always_runs(self) -> None:
        self.assertTrue(accepted.theme_runs(accepted.default_theme()))


class Extensions(Case):
    def accept(self, **changes: object) -> None:
        held: dict = {"folder": "/x", "version": "1.0.0", "scopes": ["games:read"],
                      "capabilities": ["ui:mount"]}
        held.update(changes)
        accepted.accept_extension("tool", accepted.IN_FOLDER, **held)

    def test_what_ships_with_vpinfe_needs_nothing(self) -> None:
        self.assertTrue(accepted.extension_accepted("tool", accepted.SHIPPED, ["games:write"], []))

    def test_one_in_a_folder_is_not_accepted_until_it_is(self) -> None:
        self.assertFalse(accepted.extension_accepted("tool", accepted.IN_FOLDER, [], []))

        self.accept()

        self.assertTrue(accepted.extension_accepted(
            "tool", accepted.IN_FOLDER, ["games:read"], ["ui:mount"]))

    def test_an_update_that_declares_no_more_runs_without_asking(self) -> None:
        self.accept()

        self.assertTrue(accepted.extension_accepted("tool", accepted.IN_FOLDER, ["games:read"], []))

    def test_an_update_that_declares_more_stops_and_says_what_is_new(self) -> None:
        self.accept()

        grown = ["games:read", "games:write"]
        self.assertFalse(accepted.extension_accepted("tool", accepted.IN_FOLDER, grown, []))
        self.assertEqual(accepted.new_access("tool", accepted.IN_FOLDER, grown, ["ui:mount"]),
                         ["games:write"])
        self.assertTrue(accepted.accepted_before("tool", accepted.IN_FOLDER))

    def test_an_update_that_adds_a_surface_a_remote_can_open_stops_and_says_which(self) -> None:
        self.accept(play=["list:scores"])

        added = ["list:scores", "action:reset"]
        self.assertFalse(accepted.extension_accepted(
            "tool", accepted.IN_FOLDER, ["games:read"], [], play=added))
        self.assertEqual(
            accepted.new_access("tool", accepted.IN_FOLDER, ["games:read"], ["ui:mount"],
                                play=added),
            ["play:action:reset"])

    def test_an_acceptance_that_listed_a_surface_covers_it_again(self) -> None:
        self.accept(play=["list:scores"])

        self.assertTrue(accepted.extension_accepted(
            "tool", accepted.IN_FOLDER, ["games:read"], [], play=["list:scores"]))

    def test_an_acceptance_that_listed_none_covers_none(self) -> None:
        self.accept()

        self.assertEqual(
            accepted.new_access("tool", accepted.IN_FOLDER, ["games:read"], [],
                                play=["router"]),
            ["play:router"])

    def test_accepting_again_covers_the_new_access(self) -> None:
        self.accept()
        self.accept(scopes=["games:read", "games:write"])

        self.assertTrue(accepted.extension_accepted(
            "tool", accepted.IN_FOLDER, ["games:write"], []))

    def test_an_acceptance_belongs_to_where_the_extension_was_found(self) -> None:
        """A copy that replaces a built-in is not the folder one that was accepted."""
        self.accept()

        self.assertFalse(accepted.extension_accepted("tool", accepted.REPLACES, [], []))

    def test_an_acceptance_belongs_to_the_folder_it_was_given_for(self) -> None:
        self.accept(folder="/x")

        self.assertTrue(accepted.extension_accepted(
            "tool", accepted.IN_FOLDER, ["games:read"], ["ui:mount"], "/x"))
        self.assertFalse(accepted.extension_accepted(
            "tool", accepted.IN_FOLDER, ["games:read"], ["ui:mount"], "/elsewhere"))
        self.assertFalse(accepted.accepted_before("tool", accepted.IN_FOLDER, "/elsewhere"))
        self.assertEqual(accepted.new_access("tool", accepted.IN_FOLDER, ["games:read"], [],
                                             "/elsewhere"), ["games:read"])

    def test_extensions_are_never_carried_over_on_upgrade(self) -> None:
        self.install("Old")

        accepted.theme_runs("Old")

        self.assertFalse(accepted.extension_accepted("tool", accepted.IN_FOLDER, [], []))


class Keeping(Case):
    def test_the_list_is_its_own_file_and_never_the_settings(self) -> None:
        accepted.accept_theme("Mine", ELSEWHERE)

        self.assertEqual(self.file.name, "accepted.json")
        self.assertNotEqual(self.file, paths.CONFIG_DIR / "vpinfe.json")
        self.assertEqual(json.loads(self.file.read_text())["themes"]["Mine"],
                         {"source": ELSEWHERE})
        self.assertFalse((self.config / "vpinfe.json").exists())

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_the_file_is_for_this_user_alone(self) -> None:
        accepted.accept_theme("Mine", ELSEWHERE)

        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_it_is_still_private_after_a_later_change(self) -> None:
        accepted.accept_theme("Mine", ELSEWHERE)
        os.chmod(self.file, 0o644)

        accepted.accept_theme("Other", ELSEWHERE)

        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

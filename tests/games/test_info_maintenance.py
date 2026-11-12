"""Upgrading a whole library at once, and putting it back.

Both walks exist because the lazy path upgrades a table only when something writes it, so
a library is a mix of shapes and the user cannot tell which is which. What matters here is
that neither walk stops on one bad folder and that nothing is ever destroyed.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from common.config import path_state
from common.games import info_codec
from common.games.collection_store import CollectionStore, restorable_collections_backup
from common.games.game_parser import GameParser
from common.games.info_maintenance import (
    game_dirs,
    restorable_backup,
    restore_library,
    upgrade_library,
)
from common.games.info_migration import INFO_SCHEMA, backup_schema, schema_of
from common.games.library_folders import KIND_GAME, Location
from tests.support.hung_folder import never_answering
from tests.support.library import TempTree, write_game

LEGACY = {
    "Info": {"Title": "Dr. Dude", "Rom": "dd_l2", "Authors": "someone"},
    "User": {"Rating": 4, "StartCount": 12, "Tags": ["fast"]},
    "VPXFile": {"filename": "Dr. Dude.vpx", "filehash": "abc123", "rom": "dd_l2"},
    "Medias": {"wheel": {"Source": "user"}},
}


class LibraryTestCase(TempTree):

    def _game(self, name: str, meta=LEGACY) -> Path:
        return write_game(self.root, name, info=meta)

    def _info(self, game_dir: Path) -> dict:
        return json.loads((game_dir / f"{game_dir.name}.info").read_text(encoding="utf-8"))

    def _backups(self, game_dir: Path) -> list[Path]:
        return sorted(p for p in game_dir.iterdir() if ".vpinfe-" in p.name)


class UpgradeTests(LibraryTestCase):
    def test_it_upgrades_every_game_in_one_pass(self):
        for name in ("Dr. Dude", "Taxi", "Whirlwind"):
            self._game(name)

        result = upgrade_library(self.root)

        self.assertEqual(result["upgraded"], 3)
        self.assertEqual(result["failed"], 0)
        for name in ("Dr. Dude", "Taxi", "Whirlwind"):
            after = self._info(self.root / name)
            self.assertEqual(after["vpinfe"]["schema"], INFO_SCHEMA)
            self.assertNotIn("VPXFile", after)

    def test_it_keeps_a_restore_point_for_each_game(self):
        game = self._game("Dr. Dude")
        original = (game / "Dr. Dude.info").read_text(encoding="utf-8")

        upgrade_library(self.root)

        backups = self._backups(game)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_running_it_twice_upgrades_nothing_the_second_time(self):
        self._game("Dr. Dude")
        upgrade_library(self.root)

        result = upgrade_library(self.root)

        self.assertEqual(result["upgraded"], 0)
        self.assertEqual(result["already_current"], 1)
        self.assertEqual(len(self._backups(self.root / "Dr. Dude")), 1)

    def test_one_unreadable_file_does_not_stop_the_others(self):
        """The state somebody is trying to get out of. Failing the run would withhold the
        fix from every other table in the library."""
        self._game("Dr. Dude")
        broken = self._game("Broken")
        (broken / "Broken.info").write_text("{ not json", encoding="utf-8")

        result = upgrade_library(self.root)

        self.assertEqual(result["upgraded"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["failures"][0][0], "Broken")
        self.assertEqual((broken / "Broken.info").read_text(encoding="utf-8"), "{ not json")

    def test_a_folder_with_no_info_is_left_alone(self):
        self._game("No Meta", meta=None)

        result = upgrade_library(self.root)

        self.assertEqual(result["upgraded"], 0)
        self.assertEqual(result["failed"], 0)

    def test_one_game_can_be_upgraded_on_its_own(self):
        self._game("Dr. Dude")
        self._game("Taxi")

        result = upgrade_library(self.root, game_name="Taxi")

        self.assertEqual(result["upgraded"], 1)
        self.assertNotIn("vpinfe", self._info(self.root / "Dr. Dude"))


class RestoreTests(LibraryTestCase):
    def test_it_puts_back_everything_that_was_upgraded(self):
        for name in ("Dr. Dude", "Taxi"):
            self._game(name)
        upgrade_library(self.root)

        result = restore_library(self.root)

        self.assertEqual(result["restored"], 2)
        for name in ("Dr. Dude", "Taxi"):
            after = self._info(self.root / name)
            self.assertEqual(after["VPXFile"]["filehash"], "abc123")
            self.assertEqual(after["User"]["Rating"], 4)
            self.assertNotIn("vpinfe", after)

    def _upgraded_with_id(self, game_id: str) -> Path:
        game = self._game("Dr. Dude")
        upgrade_library(self.root)
        info = self._info(game)
        info["vpinfe"]["game_id"] = game_id
        (game / "Dr. Dude.info").write_text(json.dumps(info), encoding="utf-8")
        return game

    def test_the_game_keeps_its_id_through_a_restore(self):
        """Everything that points at a game points at it by that id."""
        game = self._upgraded_with_id("g-kept")

        restore_library(self.root)

        after = self._info(game)
        self.assertEqual(after["vpinfe"], {"game_id": "g-kept"})
        self.assertEqual(after["VPXFile"]["filehash"], "abc123")

    def test_a_game_with_no_id_is_put_back_as_it_was_taken(self):
        game = self._game("Dr. Dude")
        original = (game / "Dr. Dude.info").read_bytes()
        upgrade_library(self.root)

        restore_library(self.root)

        self.assertEqual((game / "Dr. Dude.info").read_bytes(), original)

    def test_a_reset_can_ask_for_the_file_exactly_as_it_was(self):
        game = self._upgraded_with_id("g-gone")

        restore_library(self.root, keep_ids=False)

        self.assertNotIn("vpinfe", self._info(game))

    def test_what_a_restore_set_aside_is_named_for_why(self):
        game = self._upgraded_with_id("g-kept")

        restore_library(self.root)

        self.assertEqual([p.name.rsplit("-", 1)[-1] for p in self._backups(game)
                          if p.name.endswith("-restore")], ["restore"])

    def test_a_game_that_never_upgraded_is_left_alone(self):
        self._game("Dr. Dude")

        result = restore_library(self.root)

        self.assertEqual(result["restored"], 0)
        self.assertEqual(result["nothing_to_restore"], 1)
        self.assertIn("VPXFile", self._info(self.root / "Dr. Dude"))

    def test_the_current_file_is_kept_so_the_restore_is_reversible(self):
        game = self._game("Dr. Dude")
        upgrade_library(self.root)
        upgraded = (game / "Dr. Dude.info").read_text(encoding="utf-8")

        restore_library(self.root)

        backups = self._backups(game)
        self.assertEqual(len(backups), 2)
        self.assertIn(upgraded, [b.read_text(encoding="utf-8") for b in backups])

    def test_a_backup_this_build_cannot_read_is_passed_over_not_treated_as_the_end(self):
        """A 2.x build must not restore a schema 3 file, but the unversioned copy sitting
        behind it is still exactly what it wants. Newer backups are stepped over, not a
        dead end."""
        game = self._game("Dr. Dude")
        upgrade_library(self.root)
        restore_library(self.root)          # back to 2.x, and the schema 3 copy is kept

        as_2x = restorable_backup(game, max_schema=0)
        self.assertIsNotNone(as_2x)
        self.assertIsNone(backup_schema(as_2x), "2.x takes the unversioned copy")
        newest = restorable_backup(game, max_schema=INFO_SCHEMA)
        self.assertEqual(backup_schema(newest), INFO_SCHEMA)

    def test_the_newest_readable_backup_wins(self):
        game = self._game("Dr. Dude")
        upgrade_library(self.root)
        restore_library(self.root)
        (game / "Dr. Dude.info").write_text(
            json.dumps({**LEGACY, "User": {"Rating": 1}}), encoding="utf-8")
        upgrade_library(self.root)

        restore_library(self.root, max_schema=0)

        self.assertEqual(self._info(game)["User"]["Rating"], 1)

    def test_an_unreadable_backup_is_skipped_rather_than_restored(self):
        game = self._game("Dr. Dude")
        upgrade_library(self.root)
        (game / "Dr. Dude.info.vpinfe-20990101T000000Z").write_text("{ broken", "utf-8")

        result = restore_library(self.root)

        self.assertEqual(result["restored"], 1)
        self.assertEqual(self._info(game)["User"]["Rating"], 4)

    def test_a_corrupt_current_file_is_still_kept_before_being_replaced(self):
        """The file most likely to need restoring is the one too broken to parse. Refusing
        to keep it would block the rescue to protect a copy nobody wants back."""
        game = self._game("Dr. Dude")
        upgrade_library(self.root)
        (game / "Dr. Dude.info").write_text("{ truncated", encoding="utf-8")

        result = restore_library(self.root)

        self.assertEqual(result["restored"], 1)
        kept = [b.read_text(encoding="utf-8") for b in self._backups(game)]
        self.assertIn("{ truncated", kept)


class CollectionsRestoreTests(TempTree):
    """Collections are put back with the library, from the config folder."""

    INI = "[Favorites]\ntype = vpsid\nvpsids = vps-1\n"

    def setUp(self) -> None:
        super().setUp()
        self.config = self.root / "config"
        self.config.mkdir()
        (self.root / "games").mkdir()

    def _upgraded_from_2x(self) -> None:
        (self.config / "collections.ini").write_text(self.INI, encoding="utf-8")
        CollectionStore(str(self.config / "collections.json")).save()

    def test_a_2x_copy_goes_back_to_the_ini_and_the_json_still_reads(self):
        self._upgraded_from_2x()

        result = restore_library(self.root / "games", config_dir=self.config)

        self.assertTrue(result["collections_restored"])
        self.assertEqual((self.config / "collections.ini").read_text(encoding="utf-8"),
                         self.INI)
        json.loads((self.config / "collections.json").read_text(encoding="utf-8"))
        self.assertIn("Favorites", CollectionStore(
            str(self.config / "collections.json")).get_collections_name())

    def test_the_newest_copy_wins_whichever_file_it_was_taken_of(self):
        (self.config / "collections.json.vpinfe-20260101T000000Z").write_text(
            json.dumps({"schema": 2, "collections": []}), encoding="utf-8")
        (self.config / "collections.ini.vpinfe-20260201T000000Z").write_text(
            self.INI, encoding="utf-8")

        self.assertEqual(Path(restorable_collections_backup(self.config)).name,
                         "collections.ini.vpinfe-20260201T000000Z")


class WalkTests(LibraryTestCase):
    def test_dot_folders_are_not_games(self):
        self._game("Dr. Dude")
        (self.root / ".hidden").mkdir()

        self.assertEqual([d.name for d in game_dirs(self.root)], ["Dr. Dude"])

    def test_a_missing_root_is_not_an_error(self):
        self.assertEqual(game_dirs(self.root / "nope"), [])

    def _library(self):
        first, second = self.root / "first", self.root / "second"
        write_game(first, "Dr. Dude", info=LEGACY)
        write_game(second, "Taxi", info=LEGACY)
        single = write_game(self.root / "single", "Whirlwind", info=LEGACY)
        return patch("common.games.library_folders.configured", return_value=[
            Location("l1", str(first)), Location("l2", str(second)),
            Location("l3", str(single), kind=KIND_GAME)])

    def test_with_no_root_it_walks_every_library_folder(self):
        with self._library():
            self.assertEqual([d.name for d in game_dirs()], ["Dr. Dude", "Taxi", "Whirlwind"])

    def test_a_library_folder_that_does_not_answer_is_left_out_not_waited_for(self):
        with self._library(), patch.object(path_state, "PROBE_SECONDS", 0.5), \
                never_answering(str(self.root / "first")):
            self.assertEqual([d.name for d in game_dirs()], ["Taxi", "Whirlwind"])

    def test_a_root_that_does_not_answer_lists_nothing(self):
        write_game(self.root / "first", "Dr. Dude", info=LEGACY)
        with patch.object(path_state, "PROBE_SECONDS", 0.5), \
                never_answering(str(self.root / "first")):
            self.assertEqual(game_dirs(self.root / "first"), [])

    def test_one_game_is_found_in_whichever_library_folder_holds_it(self):
        with self._library():
            self.assertEqual([d.parent.name for d in game_dirs(game_name="Taxi")], ["second"])
            self.assertEqual([d.name for d in game_dirs(game_name="Whirlwind")],
                             ["Whirlwind"])


class WhatThePageSaysTests(LibraryTestCase):
    """Which of the three things the Tables page tells the user.

    The one that matters is a library a NEWER build upgraded: saying "I upgraded these"
    there is actively wrong, and it is the state every future schema bump creates.
    """

    def _game_at(self, schema, with_backup=True):
        record = {"Info": {}, "User": {"Rating": 2}, "vpinfe": {"game_id": "a"},
                  "tables": {"Dr. Dude.vpx": {"rom": "dd"}}}
        live = info_codec.encode(record)
        live["vpinfe"]["schema"] = schema
        files = {}
        if with_backup:
            older = info_codec.encode(record)
            files["Dr. Dude.info.vpinfe-20260901T000000Z"] = json.dumps(older).encode()
        return write_game(self.root, "Dr. Dude", info=live, files=files)

    def _counts(self):
        games = GameParser(str(self.root)).get_all_games()
        return {
            "pending_upgrade": sum(1 for t in games if t.info_pending_upgrade),
            "restorable": sum(1 for t in games if t.info_restorable),
            "newer_than_us": sum(1 for t in games
                                 if (schema_of(t.meta_config) or 0) > INFO_SCHEMA),
        }

    def test_a_library_a_newer_build_upgraded_is_reported_as_that(self):
        self._game_at(INFO_SCHEMA + 1)

        counts = self._counts()

        self.assertEqual(counts["newer_than_us"], 1)
        self.assertEqual(counts["pending_upgrade"], 0)

    def test_a_library_this_build_upgraded_is_not(self):
        self._game_at(INFO_SCHEMA)

        counts = self._counts()

        self.assertEqual(counts["newer_than_us"], 0)
        self.assertEqual(counts["restorable"], 1)

    def test_a_library_nobody_has_upgraded_says_nothing(self):
        self._game_at(INFO_SCHEMA, with_backup=False)

        counts = self._counts()

        self.assertEqual(counts, {"pending_upgrade": 0, "restorable": 0, "newer_than_us": 0})


if __name__ == "__main__":
    unittest.main()

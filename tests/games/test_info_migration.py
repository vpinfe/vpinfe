"""Turning an unversioned 2.x .info into schema 2.

The one place in this branch that rewrites a file a user already has, so what matters
here is what survives, not what the new shape looks like.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from common.games.info_codec import encode
from common.games.info_file import MetaConfig, record_of
from common.games.info_migration import (
    MATCHED_BY_USER,
    VPS_MATCHED_BY_KEY,
    copied_from,
    is_importer_bak,
    is_versioned,
    migrate,
    needs_migration,
)
from common.kept_copies import (
    IMPORT,
    REASON_2X,
    RESET,
    RESTORE,
    backup_path,
    backup_reason,
    backup_stamp,
    copy_aside,
    keep_newest,
    write_backup,
)
from tests.support.library import TempTree

LEGACY = {
    "Info": {"Title": "Dr. Dude", "VPSId": "vps-1", "Rom": "dd_l2",
             "Authors": ["someone", "someone else"]},
    "User": {"Rating": 7, "Favorite": 1, "LastRun": 1671033801, "StartCount": 12,
             "RunTime": 340, "Tags": ["fast"], "FrontendDOFEvent": "E901",
             "Score": {"rom": "dd_l2", "resolved_rom": "dd_l2", "score_type": "Leaderboard",
                       "entries": [{"section": "HIGH SCORES", "rank": 1, "initials": "ABC",
                                    "score": 1000000}]},
             "Seen": {"by": "another tool"}},
    "VPXFile": {"filename": "Dr. Dude.vpx", "filehash": "abc123", "version": "1.2",
                "releaseDate": "22.06.2019", "saveDate": "Tue Dec 13 16:03:21 2022",
                "saveRev": "4", "vbsHash": "def456", "rom": "dd_l2",
                "detectnfozzy": True, "detectscorebit": True},
    "VPinFE": {"deletedNVRamOnClose": True, "altlauncher": "/opt/vpx",
               "pluginprofile": "no-dmd", "alttitle": "Doctor Dude",
               "altvpsid": "vps-override"},
    "Medias": {"wheel": {"Source": "user"}},
}


class DetectionTests(unittest.TestCase):
    def test_a_2x_file_is_recognized(self):
        self.assertTrue(needs_migration(LEGACY))

    def test_a_current_file_is_left_alone(self):
        self.assertFalse(needs_migration({"vpinfe": {"schema": 3}}))

    def test_a_schema_2_file_from_the_beta_is_migrated(self):
        self.assertTrue(needs_migration({"vpinfe": {"schema": 2}, "tables": {}}))

    def test_a_beta_that_wrote_to_a_current_file_is_migrated_again(self):
        self.assertTrue(needs_migration({"vpinfe": {"schema": 3}, "tables": {}}))

    def test_a_stamped_file_2x_wrote_again_is_migrated_again(self):
        """We never write VPXFile, so finding one in a stamped file means a 2.x build
        rebuilt it after the migration and its tables section is stale. Trusting the stamp
        here is the bug 5db3f3d fixed, one release later."""
        rolled_back = {"vpinfe": {"schema": 2}, "tables": {"X.vpx": {"rom": "old"}},
                       "VPXFile": {"filename": "X.vpx", "rom": "new"}}

        self.assertTrue(needs_migration(rolled_back))

    def test_a_stamped_file_2x_rebuilt_media_into_is_migrated_again(self):
        self.assertTrue(needs_migration({"vpinfe": {"schema": 2}, "Medias": {"wheel": {}}}))

    def test_the_old_sections_own_schema_does_not_count_as_migrated(self):
        """A 3.0 build before the rename stamped VPinFE.schema, which numbered that
        section's shape rather than the file's. Reading it as "already migrated" leaves
        the file half converted - VPXFile still there, no game_files, and every consumer
        of them empty."""
        half = {"VPinFE": {"schema": 2, "altvpsid": "", "id": "f7e3e722"},
                "vpinfe": {"game_id": "mJ8F4RqD8U"},
                "VPXFile": {"filename": "X.vpx", "rom": "afm", "detectssf": True},
                "Medias": {}, "Info": {}, "User": {}}

        self.assertTrue(needs_migration(half))

        after = migrate(half)
        self.assertEqual(after["tables"]["X.vpx"]["rom"], "afm")
        self.assertTrue(after["tables"]["X.vpx"]["detect_ssf"])
        self.assertNotIn("VPXFile", after)
        self.assertEqual(after["vpinfe"]["game_id"], "mJ8F4RqD8U",
                         "the id this build already minted wins over the old one")

    def test_a_file_we_wrote_is_not_migrated_again(self):
        self.assertFalse(needs_migration(encode(migrate(LEGACY))))

    def test_migrating_twice_changes_nothing(self):
        once = encode(migrate(LEGACY))

        self.assertEqual(encode(record_of(once)), once)

    def test_junk_is_not_a_2x_file(self):
        for value in (None, [], "", {}, {"Info": {}}):
            with self.subTest(value=value):
                self.assertFalse(needs_migration(value))


class WhatSurvivesTests(unittest.TestCase):
    def setUp(self):
        self.after = migrate(LEGACY)

    def test_play_history_is_untouched(self):
        """Ratings, counts and tags are the user's, and no part of this reshapes them.
        A 2.x rating is whole stars and comes through as the same stars in halves, held
        to five stars: the file's 7 is out of range."""
        user = self.after["User"]
        for key in ("Favorite", "LastRun", "StartCount", "RunTime", "Tags"):
            self.assertEqual(user[key], LEGACY["User"][key], key)
        self.assertEqual(user["Rating"], 10)

    def test_a_key_outside_the_spec_survives(self):
        self.assertEqual(self.after["User"]["Seen"], {"by": "another tool"})

    def test_a_2x_match_is_the_persons(self):
        """Or it reads as a guess from the folder name, and Match again may replace it."""
        self.assertEqual(self.after["vpinfe"][VPS_MATCHED_BY_KEY], MATCHED_BY_USER)

    def test_a_2x_file_with_no_match_records_none(self):
        unmatched = {**LEGACY, "Info": {**LEGACY["Info"], "VPSId": ""}}

        self.assertNotIn(VPS_MATCHED_BY_KEY, migrate(unmatched)["vpinfe"])

    def test_a_stamped_file_2x_wrote_again_keeps_what_3_0_recorded(self):
        guessed = {"Info": {"VPSId": "vps-1"}, "vpinfe": {"schema": 2},
                   "VPXFile": {"filename": "X.vpx"}}

        self.assertNotIn(VPS_MATCHED_BY_KEY, migrate(guessed)["vpinfe"])

    def test_the_last_reading_moves_under_the_rom_it_names(self):
        """A game held one reading; a ROM is what has a high score table. When 2.x read
        it was never recorded, so the next read has nothing to count new against."""
        held = self.after["User"]["HighScores"]["dd_l2"]

        self.assertNotIn("Score", self.after["User"])
        self.assertEqual(held["entries"], LEGACY["User"]["Score"]["entries"])
        self.assertEqual((held["read_at"], held["score_kind"], held["new"]),
                         (None, "Leaderboard", []))

    def test_a_reading_2x_took_again_replaces_the_one_kept_for_its_rom(self):
        rolled_back = {**migrate(LEGACY), "VPXFile": LEGACY["VPXFile"]}
        rolled_back["User"] = {**rolled_back["User"],
                               "Score": {"rom": "dd_l2", "score_type": "Leaderboard",
                                         "entries": [{"section": "HIGH SCORES", "rank": 1,
                                                      "initials": "OWN", "score": 5}]}}

        again = migrate(rolled_back)["User"]

        self.assertEqual(again["HighScores"]["dd_l2"]["entries"][0]["initials"], "OWN")
        self.assertNotIn("Score", again)

    def test_the_dof_override_moves_and_leaves_nothing_behind(self):
        self.assertEqual(self.after["vpinfe"]["frontend_dof_event"], "E901")
        self.assertNotIn("FrontendDOFEvent", self.after["User"])

    def test_our_settings_keep_their_values_under_new_names(self):
        vpinfe = self.after["vpinfe"]
        self.assertTrue(vpinfe["delete_nvram_on_close"])
        self.assertEqual(vpinfe["alt_title"], "Doctor Dude")
        self.assertEqual(vpinfe["alt_vpsid"], "vps-override")

    def test_the_two_launcher_keys_survive_this_pass_to_be_consumed_later(self) -> None:
        """They are retired, but not here. They name a launcher for a *table*, and a
        table has no id until the minting pass runs over the migrated library - so
        dropping them at this point would destroy the values before anything could
        record which table they belonged to. `launcher_migration` takes them once the
        ids exist."""
        vpinfe = self.after["vpinfe"]

        self.assertEqual(vpinfe["alt_launcher"], "/opt/vpx")
        self.assertEqual(vpinfe["plugin_profile"], "no-dmd")

    def test_rerunning_over_a_built_entry_keeps_what_only_we_record(self):
        """The re-entry case: 2.x rebuilt a file we had already converted. Its VPXFile is
        the fresher parse, but hiding, origins and stats exist only on our side and
        replacing the entry outright would delete them."""
        rolled_back = dict(LEGACY)
        rolled_back["vpinfe"] = {"schema": 2}
        rolled_back["tables"] = {"Dr. Dude.vpx": {
            "hidden": True,
            "source": {"vps_file_id": "f-1"},
            "play": {"start_count": 9},
            "rom": "stale",
        }}

        entry = migrate(rolled_back)["tables"]["Dr. Dude.vpx"]

        self.assertTrue(entry["hidden"])
        self.assertEqual(entry["source"], {"vps_file_id": "f-1"})
        self.assertEqual(entry["play"], {"start_count": 9})
        self.assertEqual(entry["rom"], "dd_l2", "the fresher parse wins on parsed fields")

    def test_the_table_becomes_a_table(self):
        entry = self.after["tables"]["Dr. Dude.vpx"]
        self.assertEqual(entry["file_hash"], "abc123")
        self.assertEqual(entry["vbs_hash"], "def456")
        self.assertEqual(entry["rom"], "dd_l2")
        self.assertEqual(entry["save_rev"], "4")

    def test_dates_are_normalized_on_the_way_through(self):
        entry = self.after["tables"]["Dr. Dude.vpx"]
        self.assertEqual(entry["release_date"], "2019-06-22")
        self.assertEqual(entry["save_date"], "2022-12-13T16:03:21")

    def test_the_scorbit_typo_is_corrected(self):
        self.assertTrue(self.after["tables"]["Dr. Dude.vpx"]["detect_scorbit"])

    def test_camel_case_detect_keys_are_not_dropped(self):
        """Real files carry both detectssf and detectSSF."""
        after = migrate({**LEGACY, "VPXFile": {**LEGACY["VPXFile"],
                                               "detectSSF": True, "detectFleep": True}})
        entry = after["tables"]["Dr. Dude.vpx"]
        self.assertTrue(entry["detect_ssf"])
        self.assertTrue(entry["detect_fleep"])

    def test_authors_land_on_the_table(self):
        """Game-level authors only worked while a folder held one table."""
        self.assertEqual(self.after["tables"]["Dr. Dude.vpx"]["authors"],
                         ["someone", "someone else"])

    def test_no_default_is_recorded(self):
        """2.x described one table, so the resolver picks it anyway - and writing it
        down would freeze an arbitrary pick as a choice, which is what
        `recorded_default` says a rebuild must never do.

        It also made every migrated game report a user-chosen default it never had:
        the id re-key turns a seeded filename into a real table id, after which
        "somebody chose this" is indistinguishable from "the migration wrote it".
        """
        self.assertNotIn("default_table", self.after["vpinfe"])

    def test_a_section_we_do_not_own_is_left_alone(self):
        after = migrate({**LEGACY, "SomeOtherTool": {"note": "keep me"}})
        self.assertEqual(after["SomeOtherTool"], {"note": "keep me"})

    def test_what_is_deliberately_gone(self):
        for section in ("VPXFile", "Medias", "VPinFE"):
            self.assertNotIn(section, self.after, section)
        self.assertNotIn("Rom", self.after["Info"])
        self.assertNotIn("Authors", self.after["Info"])

    def test_a_folder_with_no_table_still_migrates(self):
        """Nine files in the corpus have no VPXFile at all."""
        after = migrate({"Info": {"Title": "x"}, "User": {"Rating": 2},
                         "Medias": {}, "VPinFE": {"altlauncher": "/opt/vpx"}})
        self.assertEqual(after["tables"], {})
        self.assertEqual(after["User"]["Rating"], 4)
        self.assertTrue(is_versioned(after))


class CapTests(TempTree):
    """When VPinFE writes a copy it drops that file's older ones it took itself, keeping
    the newest 2.x copy and the newest of any kind."""

    def setUp(self):
        super().setUp()
        self.info = self.root / "Example.info"
        self.info.write_text(json.dumps(LEGACY), encoding="utf-8")

    def _at(self, day: int) -> datetime:
        return datetime(2026, 7, day, 12, 0, 0, tzinfo=UTC)

    def _names(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if ".vpinfe-" in p.name)

    def test_a_second_restore_leaves_the_newest_2x_copy_and_the_newest(self):
        write_backup(str(self.info), json.dumps(LEGACY), self._at(1))
        copy_aside(self.info, RESTORE, self._at(2))
        copy_aside(self.info, RESTORE, self._at(3))

        self.assertEqual(self._names(), ["Example.info.vpinfe-20260701T120000Z",
                                         "Example.info.vpinfe-20260703T120000Z-restore"])

    def test_the_newest_2x_copy_is_kept_when_it_is_also_the_newest(self):
        write_backup(str(self.info), json.dumps(LEGACY), self._at(1))
        write_backup(str(self.info), json.dumps(LEGACY), self._at(2))

        self.assertEqual(self._names(), ["Example.info.vpinfe-20260702T120000Z"])

    def test_what_is_not_a_copy_of_ours_is_left_alone(self):
        for name in ("Example.info.bak", "Example.info.bak-repro", "Example.info.vpinfe-keep",
                     "vpinfe-backup-20260701T120000Z.zip",
                     "Example.info.vpinfe-20260701T120000Z-restore-twice"):
            (self.root / name).write_text("{}")
        copy_aside(self.info, RESTORE, self._at(2))
        copy_aside(self.info, RESTORE, self._at(3))

        self.assertTrue(all((self.root / n).exists() for n in
                            ("Example.info.bak", "Example.info.bak-repro",
                             "Example.info.vpinfe-keep", "vpinfe-backup-20260701T120000Z.zip",
                             "Example.info.vpinfe-20260701T120000Z-restore-twice")))

    def test_another_file_beside_it_keeps_its_own(self):
        other = self.root / "Other.info"
        other.write_text("{}")
        copy_aside(other, RESTORE, self._at(1))
        copy_aside(self.info, RESTORE, self._at(2))
        copy_aside(self.info, RESTORE, self._at(3))

        self.assertIn("Other.info.vpinfe-20260701T120000Z-restore", self._names())

    def test_the_copy_about_to_be_put_back_is_spared_once(self):
        older = copy_aside(self.info, RESTORE, self._at(1))
        copy_aside(self.info, RESTORE, self._at(2), restoring=older)

        self.assertTrue(Path(older).exists())
        self.assertEqual(keep_newest(self.info), [Path(older).name])

    def test_a_folder_of_its_own_is_capped_the_same_way(self):
        kept = self.root / "kept"
        kept.mkdir()
        copy_aside(self.info, RESTORE, self._at(1), into=kept)
        copy_aside(self.info, RESTORE, self._at(2), into=kept)

        self.assertEqual(sorted(p.name for p in kept.iterdir()),
                         ["Example.info.vpinfe-20260702T120000Z-restore"])
        self.assertEqual(self._names(), [])


class BackupTests(TempTree):
    def setUp(self):
        super().setUp()
        self.info = self.root / "Example.info"
        self.info.write_text(json.dumps(LEGACY), encoding="utf-8")

    def _backups(self):
        return sorted(p for p in self.root.iterdir() if ".vpinfe-" in p.name)

    def test_the_name_carries_a_sortable_utc_stamp(self):
        path = backup_path("/games/X/X.info", datetime(2026, 7, 29, 14, 30, 22, tzinfo=UTC))

        self.assertEqual(path, "/games/X/X.info.vpinfe-20260729T143022Z")
        self.assertNotIn(":", path, "Windows will not have a colon in a filename")

    def test_a_copy_of_a_2x_file_keeps_the_bare_name_2x_looks_for(self):
        path = backup_path("/games/X/X.info", datetime(2026, 7, 29, 14, 30, 22, tzinfo=UTC),
                           REASON_2X)

        self.assertTrue(path.endswith(".info.vpinfe-20260729T143022Z"), path)

    def test_a_copy_of_3_0s_own_says_its_reason_in_one_word_after_the_stamp(self):
        when = datetime(2026, 7, 29, 14, 30, 22, tzinfo=UTC)
        for reason in (RESTORE, IMPORT, RESET):
            path = backup_path("/games/X/X.info", when, reason)

            self.assertTrue(path.endswith(f".info.vpinfe-20260729T143022Z-{reason}"), path)
            self.assertEqual(backup_stamp(path), "20260729T143022Z")
            self.assertEqual(backup_reason(path), reason)
            self.assertEqual(copied_from(path), Path("/games/X/X.info"))
        self.assertEqual(backup_reason("/games/X/X.info.vpinfe-20260729T143022Z"), REASON_2X)

    def test_the_importers_old_single_copy_is_a_copy_of_its_info(self):
        self.assertTrue(is_importer_bak("X.info.bak", "X.info"))
        self.assertFalse(is_importer_bak("Y.info.bak", "X.info"))
        self.assertFalse(is_importer_bak("X.info", "X.info"))
        self.assertEqual(copied_from("/games/X/X.info.bak"), Path("/games/X/X.info"))

    def test_the_stamp_still_sorts_with_a_word_after_it(self):
        names = ["X.info.vpinfe-20260729T143022Z", "X.info.vpinfe-20260730T010101Z-restore"]

        self.assertEqual(sorted(names, key=backup_stamp)[-1], names[1])

    def test_reading_does_not_write_anything(self):
        before = self.info.read_text(encoding="utf-8")

        MetaConfig(str(self.info))

        self.assertEqual(self.info.read_text(encoding="utf-8"), before)
        self.assertEqual(self._backups(), [])

    def test_the_first_write_keeps_the_original_byte_for_byte(self):
        original = self.info.read_text(encoding="utf-8")

        meta = MetaConfig(str(self.info))
        meta.write_config()

        backups = self._backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
        self.assertEqual(json.loads(self.info.read_text(encoding="utf-8"))["vpinfe"]["schema"], 3)

    def test_later_writes_do_not_pile_up_restore_points(self):
        """One per schema bump over the app's lifetime, not one per rebuild."""
        meta = MetaConfig(str(self.info))
        meta.write_config()
        meta.write_config()
        MetaConfig(str(self.info)).write_config()

        self.assertEqual(len(self._backups()), 1)

    def test_a_second_copy_in_the_same_second_gets_a_name_of_its_own(self):
        stamp = datetime(2026, 7, 29, 14, 30, 22, tzinfo=UTC)
        first = write_backup(str(self.info), json.dumps(LEGACY), stamp)
        second = write_backup(str(self.info), json.dumps({"Info": {}}), stamp)

        self.assertNotEqual(first, second)
        self.assertEqual(json.loads(Path(second).read_text(encoding="utf-8")), {"Info": {}})

    def test_an_unreadable_backup_is_refused_rather_than_written(self):
        with self.assertRaises(json.JSONDecodeError):
            write_backup(str(self.info), "{ this is not json")

        self.assertEqual(self._backups(), [])


class CollisionOrderingTests(TempTree):
    """Two backups in the same second still sort in the order they were made.

    The names are the only ordering a restore has: it takes the newest readable one. A
    bump that wrapped 59 to 0 made the newer file sort 59 seconds older, so a restore
    could reach past a good backup to an older one.
    """

    def setUp(self):
        super().setUp()
        self.info = self.root / "Example.info"
        self.info.write_text(json.dumps(LEGACY), encoding="utf-8")

    def _two_at(self, when):
        first = write_backup(str(self.info), json.dumps(LEGACY), when)
        second = write_backup(str(self.info), json.dumps({"Info": {}}), when)
        return Path(first).name, Path(second).name

    def test_a_collision_on_the_last_second_of_a_minute_still_sorts_forward(self):
        first, second = self._two_at(datetime(2026, 8, 1, 12, 30, 59, tzinfo=UTC))

        self.assertGreater(second, first)

    def test_a_collision_at_midnight_rolls_the_date(self):
        first, second = self._two_at(datetime(2026, 8, 1, 23, 59, 59, tzinfo=UTC))

        self.assertGreater(second, first)
        self.assertIn("20260802T000000Z", second)


if __name__ == "__main__":
    unittest.main()

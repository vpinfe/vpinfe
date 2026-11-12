"""Protects: (a, b) ratings, plays and file records through schema 3, and what themes read.

The file on disk is schema 3, and the record in memory keeps the shape and units every
reader was written against. These tests hold the two sides together: what is written reads
back as what was held, a file converts once and stays converted, and what a 2.x or beta
file counted is not lost on the way.
"""

from __future__ import annotations

import copy
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from common.games import file_listing, info_codec
from common.games.info_file import MetaConfig, migration_backup_reason, record_of
from common.games.info_migration import migrate, needs_migration
from common.kept_copies import AGAIN, BETA, REASON_2X, RESTORE, backup_path, keep_newest
from tests.support.library import TempTree
from tests.support.saved import saved_record

RICH: dict[str, Any] = {
    "Info": {"Title": "Dr. Dude", "VPSId": "vps-1", "Rom": "dd_l2"},
    "User": {"Rating": 3.5, "Favorite": True, "LastRun": 1671033801, "StartCount": 12,
             "RunTime": 340, "Tags": ["fast"], "Seen": {"by": "another tool"}},
    "vpinfe": {"schema": 3, "game_id": "g1", "run_time_seconds": 20400, "unrelated": "kept"},
    "tables": {
        "t1": {"id": "t1", "filename": "Dr. Dude.vpx", "file_hash": "abc", "hidden": True,
               "tags": ["mine"]},
        "t2": {"id": "t2", "filename": "Dr. Dude VR.vpx",
               "source": {"vps_file_id": "rel_1"}},
    },
    "assets": {
        "medias/wheel.png": {"source": {"host": "user"}},
        "medias/bg.png": {},
        "Dr. Dude.directb2s": {},
        "medias/rulesheet.pdf": {},
    },
    "guides": [
        {"kind": "tutorial", "origin": "user", "title": "A primer",
         "url": "https://pinballprimer.github.io/x", "youtube_id": "", "authors": ["a"]},
        {"kind": "tutorial", "origin": "vps", "title": "", "url": "", "youtube_id": "abc123XYZ_-",
         "authors": [], "vps_file_id": "rel_9"},
    ],
}


class WhatIsWrittenReadsBackTests(unittest.TestCase):
    def test_a_record_reads_back_as_it_was_held(self) -> None:
        stored = json.loads(json.dumps(info_codec.encode(copy.deepcopy(RICH))))

        self.assertEqual(info_codec.decode(stored), RICH)

    def test_writing_again_changes_nothing(self) -> None:
        once = info_codec.encode(copy.deepcopy(RICH))

        self.assertEqual(info_codec.encode(info_codec.decode(once)), once)

    def test_the_files_sit_in_the_manifest_and_not_at_the_top_of_the_file(self) -> None:
        stored = info_codec.encode(copy.deepcopy(RICH))

        for name in ("tables", "assets", "guides"):
            self.assertNotIn(name, stored)
        manifest = stored["vpinfe"]["manifest"]
        self.assertEqual([one["file"] for one in manifest["tables"]],
                         ["Dr. Dude VR.vpx", "Dr. Dude.vpx"])
        self.assertEqual([one["file"] for one in manifest["media"]],
                         ["medias/bg.png", "medias/wheel.png"])
        self.assertEqual([one["file"] for one in manifest["assets"]], ["Dr. Dude.directb2s"])
        self.assertIn("medias/rulesheet.pdf", [one.get("file") for one in manifest["guides"]])

    def test_the_person_s_numbers_are_written_in_the_units_the_file_layout_names(self) -> None:
        user = info_codec.encode(copy.deepcopy(RICH))["User"]

        self.assertEqual((user["Rating"], user["Favorite"], user["RunTime"]), (7, 1, 20400))
        self.assertEqual(user["LastRun"], "2022-12-14T16:03:21Z")
        self.assertEqual(user["StartCount"], 12)
        self.assertEqual(user["Tags"], ["fast"])
        self.assertEqual(user["Seen"], {"by": "another tool"})

    def test_a_key_nothing_here_knows_survives(self) -> None:
        stored = info_codec.encode(copy.deepcopy(RICH))

        self.assertEqual(stored["vpinfe"]["unrelated"], "kept")
        self.assertEqual(info_codec.decode(stored)["vpinfe"]["unrelated"], "kept")

    def test_a_game_that_never_played_keeps_no_last_run(self) -> None:
        record = copy.deepcopy(RICH)
        record["User"]["LastRun"] = 0

        stored = info_codec.encode(record)

        self.assertIsNone(stored["User"]["LastRun"])
        self.assertEqual(info_codec.decode(stored)["User"]["LastRun"], None)

    def test_a_manifest_line_with_no_file_is_not_a_file(self) -> None:
        stored = info_codec.encode(copy.deepcopy(RICH))
        stored["vpinfe"]["manifest"]["assets"].append({"source": {"host": "user"}})
        stored["vpinfe"]["manifest"]["media"].append({"file": ""})

        record = info_codec.decode(stored)

        self.assertEqual(sorted(record["assets"]), sorted(RICH["assets"]))

    def test_a_run_time_that_is_not_a_number_is_left_alone_and_counts_for_nothing(self) -> None:
        for value in ("never", float("inf"), float("nan"), None):
            with self.subTest(value=value):
                stored = info_codec.encode(copy.deepcopy(RICH))
                stored["User"]["RunTime"] = value
                stored["vpinfe"].pop("run_time_seconds", None)

                record = info_codec.decode(stored)

                self.assertNotIn("run_time_seconds", record["vpinfe"])

    def test_a_schema_2_file_reads_as_it_is(self) -> None:
        schema_2 = copy.deepcopy(RICH)
        schema_2["vpinfe"]["schema"] = 2

        self.assertIs(info_codec.decode(schema_2), schema_2)


class WhatThemesAndTheApiReadTests(unittest.TestCase):
    """The record is the same whichever schema the file was in."""

    LEGACY = {
        "Info": {"Title": "Dr. Dude", "VPSId": "vps-1", "Rom": "dd_l2"},
        "User": {"Rating": 3, "Favorite": 1, "LastRun": 1671033801, "StartCount": 12,
                 "RunTime": 340, "Tags": ["fast"]},
        "VPXFile": {"filename": "Dr. Dude.vpx", "filehash": "abc123", "version": "1.2",
                    "rom": "dd_l2"},
        "VPinFE": {"altvpsid": "vps-override"},
        "Medias": {"wheel": {"Source": "user"}},
    }

    def test_a_2x_file_gives_the_same_record_before_and_after_it_is_written(self) -> None:
        held = migrate(self.LEGACY)
        written = json.loads(json.dumps(info_codec.encode(held)))

        read_back = record_of(written)

        self.assertEqual(
            {key: {k: v for k, v in one.items() if k != "filename"}
             for key, one in read_back["tables"].items()}, held["tables"])
        self.assertEqual(read_back["User"]["Rating"], held["User"]["Rating"])
        self.assertEqual(read_back["User"]["Favorite"], held["User"]["Favorite"])
        self.assertEqual(read_back["Info"], held["Info"])
        self.assertEqual(read_back["User"]["RunTime"], held["User"]["RunTime"])
        self.assertEqual(read_back["User"]["LastRun"], held["User"]["LastRun"])

    def test_a_rating_in_stars_keeps_its_stars(self) -> None:
        for stars in (0, 1, 2.5, 3, 4.5, 5):
            with self.subTest(stars=stars):
                stored = info_codec.encode({"User": {"Rating": stars}})

                self.assertEqual(info_codec.decode(stored)["User"]["Rating"], stars)

    def test_the_run_time_in_minutes_keeps_its_seconds(self) -> None:
        stored = info_codec.encode({"User": {"RunTime": 3}, "vpinfe": {"run_time_seconds": 200}})

        self.assertEqual(stored["User"]["RunTime"], 200)
        record = info_codec.decode(stored)
        self.assertEqual(record["User"]["RunTime"], 3)
        self.assertEqual(record["vpinfe"]["run_time_seconds"], 200)


class AFileConvertsOnceTests(unittest.TestCase):
    def test_a_2x_file_converts_and_converts_again_to_the_same_file(self) -> None:
        once = info_codec.encode(migrate(WhatThemesAndTheApiReadTests.LEGACY))

        self.assertFalse(needs_migration(once))
        self.assertEqual(info_codec.encode(record_of(once)), once)

    def test_a_schema_2_file_converts_and_converts_again_to_the_same_file(self) -> None:
        beta = {"Info": {"Title": "T"}, "User": {"Rating": 4, "RunTime": 7},
                "vpinfe": {"schema": 2, "game_id": "g"},
                "tables": {"t1": {"id": "t1", "filename": "T.vpx"}},
                "assets": {"medias/bg.png": {}}, "guides": []}
        self.assertTrue(needs_migration(beta))

        once = info_codec.encode(migrate(beta))

        self.assertFalse(needs_migration(once))
        self.assertEqual(info_codec.encode(record_of(once)), once)
        self.assertEqual(once["User"]["Rating"], 8)

    def test_a_game_with_one_table_keeps_the_rating_its_table_held_alone(self) -> None:
        beta = {"Info": {}, "User": {"Rating": 0, "Tags": ["a"]},
                "vpinfe": {"schema": 2, "game_id": "g"},
                "tables": {"t1": {"id": "t1", "filename": "T.vpx",
                                  "user": {"rating": 4, "tags": ["a", "b"]}}}}

        record = migrate(beta)

        self.assertEqual(record["User"]["Rating"], 4)
        self.assertEqual(record["User"]["Tags"], ["a", "b"])
        self.assertNotIn("user", record["tables"]["t1"])

    def test_a_games_own_rating_is_not_replaced_by_its_tables(self) -> None:
        beta = {"Info": {}, "User": {"Rating": 5},
                "vpinfe": {"schema": 2, "game_id": "g"},
                "tables": {"t1": {"id": "t1", "filename": "T.vpx", "user": {"rating": 1}}}}

        self.assertEqual(migrate(beta)["User"]["Rating"], 5)

    def test_two_tables_keep_a_rating_each(self) -> None:
        beta = {"Info": {}, "User": {},
                "vpinfe": {"schema": 2, "game_id": "g"},
                "tables": {"t1": {"id": "t1", "filename": "A.vpx", "user": {"rating": 4}},
                           "t2": {"id": "t2", "filename": "B.vpx", "user": {"rating": 2}}}}

        record = migrate(beta)

        self.assertEqual(record["tables"]["t1"]["user"]["rating"], 4)
        self.assertEqual(record["tables"]["t2"]["user"]["rating"], 2)
        self.assertFalse(record["User"].get("Rating"))


class ABetaBackInAFileTests(unittest.TestCase):
    def _written_by_us(self) -> dict:
        record = {"Info": {}, "User": {},
                  "vpinfe": {"game_id": "g"},
                  "tables": {"t1": {"id": "t1", "filename": "T.vpx",
                                    "user": {"start_count": 3, "run_time_seconds": 100,
                                             "last_run": "2026-01-01T00:00:00Z"}}}}
        return info_codec.encode(record)

    def test_a_top_level_list_in_a_current_file_means_a_beta_wrote_to_it(self) -> None:
        stored = self._written_by_us()
        stored["tables"] = {"t1": {"id": "t1", "filename": "T.vpx"}}

        self.assertTrue(needs_migration(stored))

    def test_what_the_beta_counted_is_taken_in_and_the_larger_wins(self) -> None:
        stored = self._written_by_us()
        stored["tables"] = {"t1": {"id": "t1", "filename": "T.vpx",
                                   "user": {"start_count": 5, "run_time_seconds": 50,
                                            "last_run": "2026-03-01T00:00:00Z"}}}

        user = migrate(stored)["tables"]["t1"]["user"]

        self.assertEqual(user["start_count"], 5)
        self.assertEqual(user["run_time_seconds"], 100)
        self.assertEqual(user["last_run"], "2026-03-01T00:00:00Z")

    def test_a_beta_entry_is_matched_by_its_file_when_its_id_is_not_ours(self) -> None:
        stored = self._written_by_us()
        stored["tables"] = {"other": {"filename": "t.VPX", "user": {"start_count": 5}}}

        user = migrate(stored)["tables"]["t1"]["user"]

        self.assertEqual(user["start_count"], 5)

    def test_the_copy_before_a_beta_s_file_is_rewritten_is_worded_for_what_wrote_it(self) -> None:
        beta = {"Info": {}, "vpinfe": {"schema": 2}, "tables": {}}
        again = self._written_by_us()
        again["tables"] = {"t1": {"id": "t1", "filename": "T.vpx"}}

        self.assertEqual(migration_backup_reason(beta), BETA)
        self.assertEqual(migration_backup_reason(again), AGAIN)
        self.assertEqual(migration_backup_reason({"VPXFile": {}}), REASON_2X)

    def test_folding_twice_never_counts_twice(self) -> None:
        stored = self._written_by_us()
        stored["tables"] = {"t1": {"id": "t1", "filename": "T.vpx",
                                   "user": {"start_count": 5}}}

        once = info_codec.encode(migrate(stored))
        again = info_codec.encode(record_of(once))

        self.assertEqual(again, once)
        self.assertFalse(needs_migration(once))

    def test_the_top_level_lists_are_gone_after_the_file_is_written_again(self) -> None:
        stored = self._written_by_us()
        stored["tables"] = {"t1": {"id": "t1", "filename": "T.vpx"}}

        written = info_codec.encode(migrate(stored))

        self.assertNotIn("tables", written)


class TheCapOnKeptCopiesTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.info = self.root / "Example.info"
        self.info.write_text("{}", encoding="utf-8")
        self.start = datetime(2026, 7, 1, tzinfo=UTC)

    def _copy(self, days: int, reason: str) -> str:
        path = backup_path(self.info, self.start + timedelta(days=days), reason)
        with open(path, "w", encoding="utf-8") as one:
            one.write("{}")
        return Path(path).name

    def _kept(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if ".vpinfe-" in p.name)

    def test_the_newest_2x_copy_the_newest_beta_copy_and_the_newest_of_any_kind_stay(self) -> None:
        old_2x = self._copy(1, REASON_2X)
        newer_2x = self._copy(2, REASON_2X)
        old_beta = self._copy(3, BETA)
        newer_beta = self._copy(4, BETA)
        newest = self._copy(5, RESTORE)
        self._copy(0, RESTORE)

        keep_newest(self.info)

        self.assertEqual(self._kept(), sorted([newer_2x, newer_beta, newest]))
        for gone in (old_2x, old_beta):
            self.assertNotIn(gone, self._kept())

    def test_a_copy_of_a_file_the_beta_wrote_again_does_not_replace_the_beta_s_own(self) -> None:
        beta = self._copy(1, BETA)
        again = self._copy(2, AGAIN)
        newest = self._copy(3, RESTORE)

        keep_newest(self.info)

        self.assertEqual(self._kept(), sorted([beta, newest]))
        self.assertNotIn(again, self._kept())

    def test_a_copy_that_is_nobody_s_is_left_where_it_is(self) -> None:
        mine = self.root / "Example.info.vpinfe-notes-from-me"
        mine.write_text("x", encoding="utf-8")
        self._copy(1, RESTORE)

        keep_newest(self.info)

        self.assertTrue(mine.exists())


class TheFilesTheManifestListsTests(unittest.TestCase):
    WHEN = "2026-11-06T12:00:00Z"

    def _sync(self, held: dict, listing: set[str], present: set[str] | None = None):
        on_disk = present or set()
        return file_listing.sync(held, listing, self.WHEN, lambda path: path in on_disk)

    def test_a_file_the_scan_sees_is_listed_with_nothing_in_the_line(self) -> None:
        held, news = self._sync({}, {"medias/bg.png"})

        self.assertEqual(held, {"medias/bg.png": {}})
        self.assertEqual(news["listed"], 1)

    def test_a_line_of_the_person_s_own_for_a_file_that_is_gone_is_dropped(self) -> None:
        held, news = self._sync({"medias/bg.png": {}}, set())

        self.assertEqual(held, {})
        self.assertEqual(news["dropped"], 1)

    def test_a_line_with_a_source_for_a_file_that_is_gone_is_kept_and_marked(self) -> None:
        line = {"source": {"host": "vpinmediadb"}}

        held, news = self._sync({"medias/bg.png": line}, set())

        self.assertEqual(held["medias/bg.png"]["absent_since"], self.WHEN)
        self.assertEqual(held["medias/bg.png"]["source"], line["source"])
        self.assertEqual(news["absent"], 1)

    def test_a_marked_line_is_not_moved_while_the_file_stays_gone(self) -> None:
        marked = {"source": {"host": "x"}, "absent_since": "2026-10-01T00:00:00Z"}

        held, news = self._sync({"medias/bg.png": marked}, set())

        self.assertEqual(held["medias/bg.png"]["absent_since"], "2026-10-01T00:00:00Z")
        self.assertEqual(news["absent"], 0)

    def test_a_file_that_comes_back_loses_the_mark(self) -> None:
        marked = {"source": {"host": "x"}, "absent_since": "2026-10-01T00:00:00Z"}

        held, news = self._sync({"medias/bg.png": marked}, {"medias/bg.png"})

        self.assertNotIn("absent_since", held["medias/bg.png"])
        self.assertEqual(news["returned"], 1)

    def test_a_file_the_scan_does_not_read_is_not_dropped_while_it_is_on_disk(self) -> None:
        held, news = self._sync({"rom.zip": {"source": {"host": "x"}}}, set(), {"rom.zip"})

        self.assertEqual(held["rom.zip"], {"source": {"host": "x"}})
        self.assertEqual(sum(news.values()), 0)

    def test_the_names_are_matched_whatever_the_case(self) -> None:
        held, news = self._sync({"Medias/BG.png": {}}, {"medias/bg.png"})

        self.assertEqual(list(held), ["Medias/BG.png"])
        self.assertEqual(sum(news.values()), 0)


class AFolderThatCouldNotBeReadTests(TempTree):
    def test_a_failed_walk_of_the_media_folder_gives_no_listing(self) -> None:
        self.assertIsNone(file_listing.listing_of(self.root, ["a.vpx"], {}, None))

    def test_a_media_folder_read_in_full_gives_its_files(self) -> None:
        listing = file_listing.listing_of(self.root, [], {"medias": "medias"},
                                          ["bg.png", "notes.txt"])

        self.assertEqual(listing, frozenset({"medias/bg.png"}))

    def test_a_pack_folder_is_one_line(self) -> None:
        (self.root / "PUPVideos" / "pack").mkdir(parents=True)
        (self.root / "PUPVideos" / "pack" / "a.mp4").write_bytes(b"x")

        listing = file_listing.listing_of(self.root, [], {"pupvideos": "PUPVideos"}, [])

        self.assertEqual(listing, frozenset({"PUPVideos/pack"}))


class TheFirstWriteKeepsTheOldFileTests(TempTree):
    def test_a_2x_file_is_kept_beside_the_schema_3_file_that_replaces_it(self) -> None:
        info = self.root / "Example.info"
        original = json.dumps(WhatThemesAndTheApiReadTests.LEGACY)
        info.write_text(original, encoding="utf-8")

        MetaConfig(str(info)).write_config()

        kept = [p for p in self.root.iterdir() if ".vpinfe-" in p.name]
        self.assertEqual([p.read_text(encoding="utf-8") for p in kept], [original])
        self.assertEqual(info_codec.stamped_schema(json.loads(info.read_text())), 3)
        self.assertEqual(saved_record(info)["tables"]["Dr. Dude.vpx"]["rom"], "dd_l2")

    def test_a_current_file_is_not_copied_again_when_it_is_written(self) -> None:
        info = self.root / "Example.info"
        info.write_text(json.dumps(info_codec.encode(copy.deepcopy(RICH))), encoding="utf-8")

        MetaConfig(str(info)).write_config()

        self.assertEqual([p for p in self.root.iterdir() if ".vpinfe-" in p.name], [])


if __name__ == "__main__":
    unittest.main()

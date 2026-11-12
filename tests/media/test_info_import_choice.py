"""Protects: (a) an imported .info never replaces a game's details unless the person chose it."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from common.uploads import asset_import_service, upload_ops
from tests.support.library import TempTree
from tests.support.saved import saved_record
from tests.support.swaps import library_of

FILE = "Foo (Bar 1999).info"


class _Drop(TempTree):
    def setUp(self) -> None:
        super().setUp()
        library_of(self)
        self.game = self.root / "Foo (Bar 1999)"
        self.game.mkdir()
        (self.game / "Foo.vpx").write_bytes(b"x")
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()
        (self.incoming / "Foo.vpx").write_bytes(b"x")
        (self.incoming / FILE).write_text(json.dumps(
            {"Info": {"VPSId": "foreign-id"}, "User": {"Rating": 5, "StartCount": 99}}))
        roots = [{"path": str(self.root.resolve()), "name": "root", "source": "library"}]
        patcher = mock.patch("common.games.media_browse.roots", return_value=roots)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _begun(self) -> str:
        upload = upload_ops.begin_from(str(self.incoming))["id"]
        self.addCleanup(upload_ops.abort, upload)
        return upload

    def _has_details(self) -> None:
        (self.game / FILE).write_text(json.dumps(
            {"Info": {"VPSId": "local-id"}, "User": {"Rating": 3, "StartCount": 12}}))

    def _import(self, request: dict) -> dict:
        upload = self._begun()
        row = next(one for one in upload_ops.plan_for(upload, {"game_dir": str(self.game)})
                   ["items"] if one["action"] == "write_info")
        with mock.patch.object(asset_import_service, "refresh_game"):
            upload_ops.execute(upload, {
                "game_dir": str(self.game), "selected": [row["index"]],
                **{key: ({row["member"]: value} if key == "info_modes" else value)
                   for key, value in request.items()}})
        return saved_record(self.game / FILE)

    def _row(self, request: dict) -> dict:
        plan = upload_ops.plan_for(self._begun(), {"game_dir": str(self.game), **request})
        return next(one for one in plan["items"] if one["action"] == "write_info")


class ThePlanAsksOnlyWhereThereIsSomethingToMergeInto(_Drop):
    def test_a_game_with_details_is_offered_the_choice_starting_on_fill(self) -> None:
        self._has_details()

        self.assertEqual({"existing": True, "mode": "fill", "modes": ["fill", "facts", "all"]},
                         self._row({})["info"])

    def test_a_game_with_no_details_is_asked_what_to_bring_in_starting_on_facts(self) -> None:
        self.assertEqual({"existing": False, "mode": "facts", "modes": ["facts", "all"]},
                         self._row({})["info"])

    def test_fill_asked_of_a_game_with_no_details_comes_back_as_facts(self) -> None:
        row = self._row({})

        again = self._row({"info_modes": {row["member"]: "fill"}})

        self.assertEqual("facts", again["info"]["mode"])

    def test_the_mode_sent_comes_back_on_the_row(self) -> None:
        self._has_details()
        row = self._row({})

        again = self._row({"info_modes": {row["member"]: "all"}})

        self.assertEqual("all", again["info"]["mode"])


class TheImportDoesWhatWasChosen(_Drop):
    def test_no_choice_fills_and_keeps_the_games_plays(self) -> None:
        self._has_details()

        data = self._import({})

        self.assertEqual("local-id", data["Info"]["VPSId"])
        self.assertEqual(12, data["User"]["StartCount"])

    def test_update_everything_takes_the_files_plays(self) -> None:
        self._has_details()

        data = self._import({"info_modes": "all"})

        self.assertEqual("foreign-id", data["Info"]["VPSId"])
        self.assertEqual(99, data["User"]["StartCount"])

    def test_a_file_left_out_of_the_selection_leaves_the_details_alone(self) -> None:
        self._has_details()
        before = (self.game / FILE).read_text()
        upload = self._begun()
        with mock.patch.object(asset_import_service, "refresh_game"):
            self.assertRaises(upload_ops.NothingImportableError, upload_ops.execute, upload,
                              {"game_dir": str(self.game), "selected": []})

        self.assertEqual(before, (self.game / FILE).read_text())
        self.assertEqual([], list(self.game.glob("*.vpinfe-*-import")))


class AFirstImportTakesWhatTheSenderSaid(_Drop):
    SENT = {"Info": {"VPSId": "sent-id", "Title": "Foo"},
            "User": {"Rating": 5, "Favorite": 1, "Tags": ["flippers"], "StartCount": 99,
                     "RunTime": 600, "LastRun": "2026-01-02T03:04:05Z"},
            "VPX": {"RunCount": 12},
            "vpinfe": {"alt_launcher": "/nonexistent/launcher", "alt_title": "Foo Mod"}}

    def _first(self) -> dict:
        (self.incoming / FILE).write_text(json.dumps(self.SENT))
        return self._import({})

    def test_the_rating_favorite_tags_and_info_come_in(self) -> None:
        data = self._first()

        self.assertEqual("sent-id", data["Info"]["VPSId"])
        self.assertEqual(10, data["User"]["Rating"])
        self.assertTrue(data["User"]["Favorite"])
        self.assertEqual(["flippers"], data["User"]["Tags"])
        self.assertEqual("Foo Mod", data["vpinfe"]["alt_title"])

    def test_play_counts_and_vpx_numbers_do_not(self) -> None:
        data = self._first()

        self.assertFalse(data["User"].get("StartCount"))
        self.assertFalse(data["User"].get("RunTime"))
        self.assertFalse(data["User"].get("LastRun"))
        self.assertNotIn("VPX", data)

    def test_a_launcher_that_is_not_here_is_not_taken(self) -> None:
        self.assertFalse(self._first()["vpinfe"].get("alt_launcher"))

    def test_everything_brings_the_plays_and_vpx_numbers_too(self) -> None:
        (self.incoming / FILE).write_text(json.dumps(self.SENT))

        data = self._import({"info_modes": "all"})

        self.assertEqual(99, data["User"]["StartCount"])
        self.assertEqual(600, data["User"]["RunTime"])
        self.assertEqual(12, data["VPX"]["RunCount"])
        self.assertFalse(data["vpinfe"].get("alt_launcher"))

    def test_fill_on_a_first_import_leaves_the_plays_behind(self) -> None:
        (self.incoming / FILE).write_text(json.dumps(self.SENT))

        data = self._import({"info_modes": "fill"})

        self.assertFalse(data["User"].get("StartCount"))
        self.assertEqual(10, data["User"]["Rating"])


class ATablesOwnFileOnAFirstImport(unittest.TestCase):
    SENT = {"User": {"StartCount": 4}, "VPX": {"RunCount": 2},
            "vpinfe": {"alt_launcher": "/nonexistent/launcher", "table": {"id": "t"}}}

    def test_facts_leaves_the_plays_and_a_missing_launcher_behind(self) -> None:
        kept = asset_import_service.first_import_table(self.SENT, "facts")

        self.assertNotIn("VPX", kept)
        self.assertNotIn("StartCount", kept["User"])
        self.assertEqual({"table": {"id": "t"}}, kept["vpinfe"])

    def test_all_keeps_the_plays_but_still_not_a_missing_launcher(self) -> None:
        kept = asset_import_service.first_import_table(self.SENT, "all")

        self.assertEqual(4, kept["User"]["StartCount"])
        self.assertEqual({"RunCount": 2}, kept["VPX"])
        self.assertNotIn("alt_launcher", kept["vpinfe"])


class AGameKeepsItsOwnId(_Drop):
    def _sent(self, game_id: str) -> None:
        (self.incoming / FILE).write_text(json.dumps(
            {"Info": {"VPSId": "sent"}, "vpinfe": {"game_id": game_id}}))

    def test_a_first_import_keeps_the_senders_id_when_no_game_here_has_it(self) -> None:
        self._sent("0123456789abcdef0123456789abcdef")

        data = self._import({})

        self.assertEqual("0123456789abcdef0123456789abcdef", data["vpinfe"]["game_id"])

    def test_a_first_import_does_not_take_an_id_another_game_here_holds(self) -> None:
        self._sent("0123456789abcdef0123456789abcdef")
        with mock.patch.object(asset_import_service, "_ids_held_here",
                               return_value={"0123456789abcdef0123456789abcdef"}):
            data = self._import({})

        self.assertFalse(data.get("vpinfe", {}).get("game_id"))

    def test_a_game_that_has_details_never_takes_an_id_from_the_file(self) -> None:
        for mode in ("fill", "facts", "all"):
            with self.subTest(mode=mode):
                (self.game / FILE).write_text(json.dumps(
                    {"Info": {"VPSId": "local"}, "vpinfe": {"game_id": "mine"}}))
                self._sent("theirs")

                data = self._import({"info_modes": mode})

                self.assertEqual("mine", data["vpinfe"]["game_id"])

    def test_a_game_with_details_but_no_id_does_not_take_one_from_the_file(self) -> None:
        self._has_details()
        self._sent("theirs")

        data = self._import({"info_modes": "all"})

        self.assertFalse(data["vpinfe"].get("game_id"))


class UpdateEverythingKeepsTheRunTimeInStepWithTheRestOfTheFile(unittest.TestCase):
    def test_the_seconds_kept_beside_the_minutes_do_not_outlive_them(self) -> None:
        merged = asset_import_service.merge_info(
            {"User": {"RunTime": 0}},
            {"User": {"RunTime": 5}, "vpinfe": {"run_time_seconds": 300}}, "all")

        self.assertNotIn("run_time_seconds", merged["vpinfe"])

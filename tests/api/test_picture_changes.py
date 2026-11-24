"""The pictures a source has replaced, kept by the daily check and read by a page."""

from __future__ import annotations

import configparser
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.online import picture_changes, sources, vps_marks
from tests.support.library import TempTree


def _change(folder: Path, *, game_id: str = "g1", kind: str = "wheel", md5: str = "new-md5",
            have: str = "", path: str = "medias/wheel.png") -> dict:
    return {"game_id": game_id, "vps_id": "E1", "name": "Alpha", "kind": kind,
            "source": "vpinmediadb", "size": "", "md5": md5, "folder": str(folder),
            "path": path, "have": have}


class _Kept(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.config = configparser.ConfigParser()
        self.config.read_dict({"updates": {"update_downloaded_media": "false"}})
        self.folder = self.root / "Alpha"
        (self.folder / "medias").mkdir(parents=True)
        self.file = self.folder / "medias" / "wheel.png"
        self.file.write_bytes(b"as placed")
        self.placed = picture_changes.file_md5(self.file)
        for patcher in (
                patch.object(picture_changes, "ANSWER_PATH", self.root / "picture-changes.json"),
                patch.object(picture_changes, "get_ini_config", return_value=self.config),
                patch.object(vps_marks, "PATH", self.root / "marks.json"),
                patch.object(sources, "enabled", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def keep(self, *found: dict) -> None:
        picture_changes.record(found)


class AnswerTests(_Kept):
    def test_before_any_check_there_is_nothing_and_no_time(self) -> None:
        self.assertEqual(picture_changes.pending(), [])
        self.assertEqual(picture_changes.checked_at(), "")

    def test_what_a_check_kept_is_read_back_with_the_id_its_marks_use(self) -> None:
        self.keep(_change(self.folder, have=self.placed))

        [found] = picture_changes.pending()

        self.assertEqual(found["id"], "E1:wheel")
        self.assertEqual((found["game_id"], found["kind"], found["md5"]),
                         ("g1", "wheel", "new-md5"))
        self.assertNotIn("folder", found)
        self.assertNotIn("have", found)
        self.assertTrue(picture_changes.checked_at())

    def test_one_game_s_pictures_are_asked_for_alone(self) -> None:
        self.keep(_change(self.folder, have=self.placed),
                  _change(self.folder, game_id="g2", have=self.placed))

        self.assertEqual([one["game_id"] for one in picture_changes.pending("g2")], ["g2"])
        self.assertEqual(len(picture_changes.pending()), 2)

    def test_a_file_that_is_gone_or_changed_is_no_longer_a_change(self) -> None:
        self.keep(_change(self.folder, have=self.placed))
        self.file.write_bytes(b"edited")
        self.assertEqual(picture_changes.pending(), [])

        self.file.unlink()
        self.assertEqual(picture_changes.pending(), [])

    def test_an_entry_with_no_recorded_hash_is_never_offered(self) -> None:
        self.keep(_change(self.folder, have=""))

        self.assertEqual(picture_changes.pending(), [])

    def test_an_answer_from_another_schema_reads_as_none(self) -> None:
        self.keep(_change(self.folder, have=self.placed))
        with patch.object(picture_changes, "SCHEMA", 2):
            self.assertEqual(picture_changes.pending(), [])

    def test_the_replacement_being_automatic_offers_nothing(self) -> None:
        self.keep(_change(self.folder, have=self.placed))
        self.config.read_dict({"updates": {"update_downloaded_media": "true"}})

        self.assertEqual(picture_changes.pending(), [])

    def test_a_changes_list_that_is_not_a_list_reads_as_none(self) -> None:
        for broken in ("a string", {"g1": 1}, 7, None):
            with self.subTest(changes=broken):
                picture_changes.ANSWER_PATH.write_text(
                    json.dumps({"schema": picture_changes.SCHEMA, "changes": broken}))

                self.assertEqual(picture_changes.pending(), [])

    def test_an_entry_that_is_not_a_record_is_passed_over(self) -> None:
        picture_changes.ANSWER_PATH.write_text(
            json.dumps({"schema": picture_changes.SCHEMA, "changes": ["x", 3, None]}))

        self.assertEqual(picture_changes.pending(), [])

    def test_every_games_changes_are_read_without_hashing_a_file_that_is_as_it_was(self) -> None:
        self.keep({**_change(self.folder, have=self.placed),
                   "stamp": picture_changes.signature(self.file)})

        with patch.object(picture_changes, "file_md5") as hashed:
            self.assertEqual(len(picture_changes.pending()), 1)

        hashed.assert_not_called()

    def test_one_games_changes_are_read_against_the_file_itself(self) -> None:
        self.keep({**_change(self.folder, have=self.placed),
                   "stamp": picture_changes.signature(self.file)})

        with patch.object(picture_changes, "file_md5", return_value="other") as hashed:
            self.assertEqual(picture_changes.pending("g1"), [])

        hashed.assert_called_once()

    def test_a_file_touched_since_is_hashed_and_kept_when_its_content_is_the_same(self) -> None:
        self.keep({**_change(self.folder, have=self.placed),
                   "stamp": picture_changes.signature(self.file)})
        later = self.file.stat().st_mtime + 100
        os.utime(self.file, (later, later))

        with patch.object(picture_changes, "file_md5", wraps=picture_changes.file_md5) as hashed:
            self.assertEqual(len(picture_changes.pending()), 1)

        hashed.assert_called_once()


    def test_what_a_game_is_offered_is_the_hash_and_the_source_of_the_slot(self) -> None:
        self.keep(_change(self.folder, have=self.placed))

        self.assertEqual(picture_changes.upgrades({"game_id": "g1"}, "wheel"),
                         {"source": "vpinmediadb", "size": "", "md5": "new-md5"})
        self.assertEqual(picture_changes.upgrades({"game_id": "g1"}, "flyer"), {})
        self.assertEqual(picture_changes.upgrades({"game_id": "g2"}, "wheel"), {})

    def test_a_game_with_no_change_is_answered_without_reading_the_settings(self) -> None:
        self.keep(_change(self.folder, have=self.placed))

        with patch.object(picture_changes, "get_ini_config") as read, \
                patch.object(sources, "enabled") as enabled:
            picture_changes.upgrades({"game_id": "g2"}, "wheel")
            picture_changes.upgrades({"game_id": "g1"}, "flyer")

        read.assert_not_called()
        enabled.assert_not_called()

    def test_a_rewritten_answer_is_read_again(self) -> None:
        self.keep(_change(self.folder, have=self.placed))
        self.assertEqual(1, len(picture_changes.pending()))

        self.keep()

        self.assertEqual([], picture_changes.pending())


class RouteTests(_Kept):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.keep(_change(self.folder, have=self.placed),
                  _change(self.folder, game_id="g2", kind="flyer", have=self.placed))

    def test_it_answers_every_picture_and_when_they_were_found(self) -> None:
        body = self.client.get("/discovery/pictures").json()

        self.assertEqual(sorted(one["game_id"] for one in body["pictures"]), ["g1", "g2"])
        self.assertTrue(body["checked_at"])
        self.assertEqual(set(body["pictures"][0]), {"id", "game_id", "vps_id", "name", "kind",
                                                    "source", "size", "md5"})

    def test_it_answers_one_game_s(self) -> None:
        body = self.client.get("/discovery/pictures", params={"game_id": "g2"}).json()

        self.assertEqual([(one["game_id"], one["kind"]) for one in body["pictures"]],
                         [("g2", "flyer")])

    def test_a_picture_set_aside_is_not_answered(self) -> None:
        vps_marks.mark(vps_marks.IGNORED, vps_marks.UPDATES, "E1:wheel")
        vps_marks.mark(vps_marks.IGNORED, vps_marks.VERSION, "E1:flyer", "new-md5")

        self.assertEqual(self.client.get("/discovery/pictures").json()["pictures"], [])

    def test_a_game_it_does_not_know_has_none(self) -> None:
        body = self.client.get("/discovery/pictures", params={"game_id": "nope"}).json()

        self.assertEqual(body["pictures"], [])


if __name__ == "__main__":
    unittest.main()

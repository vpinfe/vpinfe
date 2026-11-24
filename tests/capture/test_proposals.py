"""Choose After Recording: proposals kept, used and discarded; and Replace deleting what
it replaced."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import kept_copies, service_errors
from common.backups import folder as backup_folder
from common.capture import proposals, run, slots
from common.games import asset_origin
from tests.capture.test_run import FOLDER, GAME_ID, INFO, MOD, PLAIN, _Library


class _Held(_Library):
    def setUp(self) -> None:
        super().setUp()
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        patcher = patch("common.capture.proposals.ROOT", Path(held.name) / "proposals")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = self.root / FOLDER
        self.recording = Path(held.name) / "playfield_video.mp4"
        self.recording.write_bytes(b"recorded" * 100)

    def row(self, planned: dict, kind: str) -> dict:
        return next(one for one in planned["targets"][0]["kinds"] if one["kind"] == kind)


class ChooseTests(_Held):
    def test_choose_proposes_where_a_file_is_and_fills_where_none_is(self) -> None:
        planned = self.plan(kinds=["playfield", "playfield_video", "backglass"],
                            existing=run.CHOOSE)

        self.assertEqual(self.does(planned), {
            "playfield": ("fill", None), "playfield_video": ("propose", "vpinmediadb"),
            "backglass": ("propose", "user")})
        self.assertEqual((planned["recording"], planned["replacing"]),
                         (["playfield", "playfield_video", "backglass"], 0))

    def test_review_proposes_even_an_empty_slot(self) -> None:
        planned = self.plan(kinds=["playfield", "playfield_video"], review=True)

        self.assertEqual({row["does"] for row in planned["targets"][0]["kinds"]}, {"propose"})


class ReplaceTests(_Held):
    def test_a_games_recording_deletes_the_catalogs_file_under_it(self) -> None:
        planned = self.plan(kinds=["playfield_video"], existing=run.REPLACE_DOWNLOADED)

        row = self.row(planned, "playfield_video")
        self.assertEqual((row["file"], row["goes"]), ("medias/table.mp4", True))
        self.assertEqual(planned["replacing"], 1)

    def test_a_tables_recording_leaves_the_shared_file_its_siblings_use(self) -> None:
        planned = self.plan(games=[], tables=[(GAME_ID, PLAIN)], kinds=["backglass"],
                            existing=run.REPLACE_ALL)

        row = self.row(planned, "backglass")
        self.assertEqual((row["does"], row["source"], row["goes"]), ("replace", "user", False))
        self.assertEqual((planned["replacing"], planned["replacing_by_source"]), (0, {}))

    def test_a_shared_file_only_this_table_used_goes_with_its_recording(self) -> None:
        planned = self.plan(games=[], tables=[(GAME_ID, PLAIN)], kinds=["playfield_video"],
                            existing=run.REPLACE_ALL)

        self.assertTrue(self.row(planned, "playfield_video")["goes"])
        self.assertEqual(planned["replacing"], 1)

    def test_placing_deletes_the_file_it_replaced(self) -> None:
        from common.games import media_ops

        row = slots.serving(GAME_ID, "", "playfield_video")
        written = media_ops.place_file(GAME_ID, "playfield_video", "", self.recording,
                                       asset_origin.RECORDED, "")

        self.assertEqual(slots.remove(GAME_ID, row, "", written["written"]),
                         ["medias/table.mp4"])
        self.assertFalse((self.folder / "medias" / "table.mp4").exists())
        self.assertTrue((self.folder / "medias" / written["written"]).is_file())


class KeptTests(_Held):
    """What a recording replaces is copied into Backups first, with the reason Replace."""

    def setUp(self) -> None:
        super().setUp()
        info = {**INFO, "vpinfe": {"schema": 2, "game_id": GAME_ID}}
        (self.folder / f"{FOLDER}.info").write_text(json.dumps(info), encoding="utf-8")
        kept = tempfile.TemporaryDirectory()
        self.addCleanup(kept.cleanup)
        self.backups = Path(kept.name)
        patcher = patch.object(backup_folder, "for_writing", return_value=self.backups)
        patcher.start()
        self.addCleanup(patcher.stop)

    def kept(self) -> list[tuple[str, bytes]]:
        return sorted((one.relative_to(self.backups).as_posix(), one.read_bytes())
                      for one in self.backups.rglob("*") if one.is_file())

    def record(self, table_id: str) -> tuple[Path, dict]:
        from common.games import media_ops

        row = slots.serving(GAME_ID, table_id, "playfield_video")
        slots.can_keep(GAME_ID, row, table_id)
        written = media_ops.place_file(GAME_ID, "playfield_video", table_id, self.recording,
                                       asset_origin.RECORDED, "")
        slots.remove(GAME_ID, row, table_id, written["written"])
        return self.folder / "medias" / written["written"], written

    def test_a_tables_recording_keeps_the_shared_file_it_removes(self) -> None:
        self.record(PLAIN)

        self.assertFalse((self.folder / "medias" / "table.mp4").exists())
        [(name, data)] = self.kept()
        self.assertEqual(data, b"v")
        self.assertTrue(name.startswith(f"media/{GAME_ID}/medias/table.mp4"))
        self.assertEqual(kept_copies.backup_reason(name), "replace")

    def test_a_file_replaced_at_its_own_level_is_kept_once(self) -> None:
        landed, _written = self.record(MOD)

        self.assertEqual(landed.read_bytes(), b"recorded" * 100)
        [(name, _data)] = self.kept()
        self.assertTrue(name.startswith(f"media/{GAME_ID}/medias/(Playfield) Mod.mp4"))

    def test_a_backups_folder_that_cannot_be_written_to_leaves_the_file_in_place(self) -> None:
        refusal = service_errors.BlockedError("the backups folder cannot be written to")
        row = slots.serving(GAME_ID, PLAIN, "playfield_video")

        with patch.object(backup_folder, "for_writing", side_effect=refusal):
            with self.assertRaises(service_errors.BlockedError):
                slots.can_keep(GAME_ID, row, PLAIN)
            with self.assertRaises(service_errors.BlockedError):
                slots.remove(GAME_ID, row, PLAIN, "(Playfield) tbl.mp4")

        self.assertEqual((self.folder / "medias" / "table.mp4").read_bytes(), b"v")
        self.assertEqual(self.kept(), [])

    def test_a_folder_with_no_game_id_removes_the_file_and_keeps_nothing(self) -> None:
        (self.folder / f"{FOLDER}.info").write_text(json.dumps(INFO), encoding="utf-8")

        self.record(PLAIN)

        self.assertFalse((self.folder / "medias" / "table.mp4").exists())
        self.assertEqual(self.kept(), [])

    def test_a_slot_with_nothing_in_it_keeps_nothing(self) -> None:
        for one in (self.folder / "medias").glob("*.mp4"):
            one.unlink()
        row = slots.serving(GAME_ID, "", "playfield_video")

        with patch.object(backup_folder, "for_writing") as asked:
            slots.can_keep(GAME_ID, row, "")
            self.assertEqual(slots.remove(GAME_ID, row, "", "x.mp4"), [])

        asked.assert_not_called()
        self.assertEqual(self.kept(), [])


class ProposalTests(_Held):
    def test_a_proposal_waits_with_what_it_would_replace(self) -> None:
        kept = proposals.keep(GAME_ID, "", "playfield_video", self.recording)

        held = proposals.listing()

        self.assertEqual((held["count"], held["bytes"]), (1, 800))
        row = held["proposals"][0]
        self.assertEqual((row["id"], row["kind"], row["name"]),
                         (kept["id"], "playfield_video", FOLDER))
        self.assertEqual(row["replaces"], {"path": "medias/table.mp4",
                                           "source": "vpinmediadb", "goes": True})
        self.assertEqual(row["url"], f"/api/v1/capture/proposals/{kept['id']}/file")
        self.assertEqual(proposals.file_of(kept["id"]).read_bytes(), b"recorded" * 100)

    def test_using_one_places_it_and_deletes_what_it_replaces(self) -> None:
        kept = proposals.keep(GAME_ID, "", "playfield_video", self.recording)

        used = proposals.use(kept["id"])

        self.assertEqual(used["removed"], ["medias/table.mp4"])
        self.assertFalse((self.folder / "medias" / "table.mp4").exists())
        self.assertTrue((self.folder / "medias" / f"(Playfield) {FOLDER}.mp4").is_file())
        self.assertEqual(proposals.listing()["count"], 0)

    def test_a_tables_proposal_lands_as_the_tables_own(self) -> None:
        kept = proposals.keep(GAME_ID, MOD, "playfield_video", self.recording)

        proposals.use(kept["id"])

        self.assertEqual((self.folder / "medias" / "(Playfield) Mod.mp4").read_bytes(),
                         b"recorded" * 100)
        self.assertTrue((self.folder / "medias" / "table.mp4").exists())

    def test_discarding_forgets_it_and_touches_nothing(self) -> None:
        one = proposals.keep(GAME_ID, "", "playfield_video", self.recording)
        proposals.keep(GAME_ID, "", "backglass_video", self.recording)

        proposals.discard(one["id"])
        self.assertEqual(proposals.listing()["count"], 1)
        self.assertEqual(proposals.discard_all(), {"discarded": 1})
        self.assertEqual(proposals.listing(), {"count": 0, "bytes": 0, "proposals": []})
        self.assertTrue((self.folder / "medias" / "table.mp4").exists())

    def test_one_that_is_not_there_is_not_found(self) -> None:
        for proposal_id in ("abc123", "../escape"):
            with self.subTest(proposal_id), self.assertRaises(service_errors.NotFoundError):
                proposals.use(proposal_id)


class ProposalRouteTests(_Held):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_proposals_are_served_played_used_and_discarded(self) -> None:
        kept = proposals.keep(GAME_ID, "", "playfield_video", self.recording)
        other = proposals.keep(GAME_ID, "", "backglass_video", self.recording)

        listed = self.client.get("/capture/proposals")
        played = self.client.get(f"/capture/proposals/{kept['id']}/file")
        skipped = self.client.post(f"/capture/proposals/{other['id']}", json={"use": False})
        used = self.client.post(f"/capture/proposals/{kept['id']}", json={"use": True})

        self.assertEqual(listed.json()["count"], 2, listed.text)
        self.assertEqual(played.content, b"recorded" * 100)
        self.assertEqual(skipped.json(), {"placed": None, "removed": []})
        self.assertEqual(used.json(), {"placed": "playfield_video",
                                       "removed": ["medias/table.mp4"]}, used.text)
        self.assertEqual(self.client.get("/capture/proposals/nope/file").status_code, 404)

    def test_discard_all(self) -> None:
        proposals.keep(GAME_ID, "", "playfield_video", self.recording)

        response = self.client.delete("/capture/proposals")

        self.assertEqual(response.json(), {"discarded": 1})

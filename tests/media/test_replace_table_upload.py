"""An upload that takes the place of a table, asked for over the API, and the routes that
swap it back or delete it."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.games import table_ops
from common.uploads import asset_import_service, upload_ops
from tests.support.library import TempTree, fake_game, game_info
from tests.support.swaps import library_of

GAME_ID = "Replaced0001"
FOLDER = "Foo (Bar 1999)"


class ReplaceCase(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.game = self.root / FOLDER
        self.game.mkdir()
        (self.game / "Foo 1.1.vpx").write_bytes(b"old")
        (self.game / "Foo 1.1.ini").write_bytes(b"ini")
        (self.game / "Foo 1.1.vbs").write_bytes(b"script")
        (self.game / "Other.vpx").write_bytes(b"other")
        self.info = self.game / f"{FOLDER}.info"
        record = game_info("Foo", game_id=GAME_ID, tables={
            "t-old": {"id": "t-old", "filename": "Foo 1.1.vpx", "rating": 4, "version": "1.1"},
            "t-other": {"id": "t-other", "filename": "Other.vpx"}})
        record["vpinfe"]["default_table"] = "t-old"
        self.info.write_text(json.dumps(record))
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "Foo 1.2.vpx").write_bytes(b"new")
        library_of(self, {GAME_ID: fake_game(self.game, FOLDER, meta=record)},
                   parsed={"version": "1.2"})
        roots = [{"path": str(self.root.resolve()), "name": "root", "source": "library"}]
        for patched in (mock.patch("common.games.media_browse.roots", return_value=roots),
                        mock.patch.object(asset_import_service, "refresh_game")):
            patched.start()
            self.addCleanup(patched.stop)
        self.upload = upload_ops.begin_from(str(incoming))["id"]
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.asked = {"game_dir": str(self.game)}

    def plan(self, **asked) -> dict:
        response = self.client.post(f"/uploads/{self.upload}/plan",
                                    json={**self.asked, **asked})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def table_item(self, plan: dict) -> dict:
        return next(one for one in plan["items"] if one["kind"] == "table")


class ReplaceTableRouteTests(ReplaceCase):
    def test_a_drop_takes_the_place_of_the_games_default_table(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        plan = self.plan()

        item = self.table_item(plan)
        self.assertEqual("replace_vpx", item["action"])
        self.assertEqual("Foo 1.1.vpx", item["swap"]["filename"])
        self.assertEqual({"t-old", True}, {plan["choice"]["table"], plan["choice"]["replace"]})

    def test_the_plan_offers_each_table_that_can_be_replaced_and_names_it(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        offered = self.plan()["choice"]["tables"]

        self.assertEqual({"t-old", "t-other"}, {one["id"] for one in offered})
        self.assertEqual({"t-old": True, "t-other": False},
                         {one["id"]: one["default"] for one in offered})
        self.assertTrue(all(one["label"] for one in offered))

    def test_the_plan_says_what_follows_the_table(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        files = self.table_item(self.plan())["swap"]["files"]

        self.assertEqual({"ini": True, "script": False},
                         {one["kind"]: one["carry"] for one in files})

    def test_another_table_can_be_named(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        plan = self.plan(replace_table="t-other")

        self.assertEqual("Other.vpx", self.table_item(plan)["swap"]["filename"])
        self.assertEqual("t-other", plan["choice"]["table"])

    def test_a_table_the_game_does_not_hold_is_refused(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        response = self.client.post(f"/uploads/{self.upload}/plan",
                                    json={**self.asked, "replace_table": "nothing"})

        self.assertEqual(409, response.status_code, response.text)

    def test_keeping_both_has_nothing_to_replace_and_still_offers_the_choice(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        plan = self.plan(add_table=True)

        item = self.table_item(plan)
        self.assertEqual(("add_table", None), (item["action"], item["swap"]))
        self.assertFalse(plan["choice"]["replace"])

    def test_what_is_not_ticked_is_not_carried(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        files = self.table_item(self.plan(carry=[]))["swap"]["files"]

        self.assertEqual({"ini": False, "script": False},
                         {one["kind"]: one["carry"] for one in files})

    def test_the_import_replaces_the_table_and_keeps_its_entry(self) -> None:
        response = self.client.post(f"/uploads/{self.upload}/import", json=self.asked)

        self.assertEqual(200, response.status_code, response.text)
        report = response.json()
        self.assertEqual([], report["added_tables"])
        swapped = report["replaced_tables"][0]
        self.assertEqual("t-old", swapped["table"]["id"])
        self.assertTrue(swapped["kept"])
        saved = json.loads(self.info.read_text())["tables"]["t-old"]
        self.assertEqual(("Foo 1.2.vpx", 4), (saved["filename"], saved["rating"]))
        self.assertFalse((self.game / "Foo 1.1.vpx").exists())
        self.assertTrue((self.game / "Foo 1.2.ini").exists())

    def test_the_games_other_tables_are_left_alone(self) -> None:
        self.client.post(f"/uploads/{self.upload}/import", json=self.asked)

        self.assertEqual(b"other", (self.game / "Other.vpx").read_bytes())

    def test_keeping_both_adds_a_table_and_replaces_none(self) -> None:
        response = self.client.post(f"/uploads/{self.upload}/import",
                                    json={**self.asked, "add_table": True})

        report = response.json()
        self.assertEqual((1, []), (len(report["added_tables"]), report["replaced_tables"]))
        self.assertEqual(b"old", (self.game / "Foo 1.1.vpx").read_bytes())


class CompanionsTests(ReplaceCase):
    """A drop that brings an ini beside its table."""

    def setUp(self) -> None:
        super().setUp()
        upload_ops.abort(self.upload)
        (self.root / "incoming" / "Foo 1.2.ini").write_bytes(b"new ini")
        self.upload = upload_ops.begin_from(str(self.root / "incoming"))["id"]
        self.addCleanup(upload_ops.abort, self.upload)

    def kinds(self, plan: dict) -> list[str]:
        return sorted(one["kind"] for one in plan["items"])

    def test_a_kind_the_drop_brings_is_not_carried_unless_ticked(self) -> None:
        self.assertEqual(["ini", "table"], self.kinds(self.plan()))

    def test_a_carried_kind_leaves_the_drops_own_file_out(self) -> None:
        self.assertEqual(["table"], self.kinds(self.plan(carry=["ini"])))

    def test_with_nothing_carried_the_drops_companions_stay_in_the_plan(self) -> None:
        plan = self.plan(carry=[])

        self.assertEqual(["ini", "table"], self.kinds(plan))
        self.assertTrue(all(one["default_enabled"] for one in plan["items"]))

    def test_a_failure_after_the_swap_ran_says_what_was_swapped(self) -> None:
        real = asset_import_service._extract_replace

        def refuse_the_ini(source, entry, dest):
            if dest.suffix == ".ini":
                raise ValueError("no room")
            real(source, entry, dest)

        with mock.patch.object(asset_import_service, "_extract_replace", refuse_the_ini):
            response = self.client.post(f"/uploads/{self.upload}/import",
                                        json={**self.asked, "carry": []})

        self.assertEqual(400, response.status_code, response.text)
        error = response.json()["error"]
        self.assertIn("no room", error["message"])
        swapped = error["details"]["replaced_tables"][0]
        self.assertEqual("t-old", swapped["table"]["id"])
        self.assertTrue(swapped["kept"])
        self.assertTrue((self.game / "Foo 1.2.vpx").exists())
        self.assertFalse((self.game / "Foo 1.1.vpx").exists())

    def test_a_failure_after_the_swap_ran_in_a_job_is_a_result_that_names_the_swap(self) -> None:
        real = asset_import_service._extract_replace

        def refuse_the_ini(source, entry, dest):
            if dest.suffix == ".ini":
                raise ValueError("no room")
            real(source, entry, dest)

        with mock.patch.object(asset_import_service, "_extract_replace", refuse_the_ini):
            started = self.client.post(f"/uploads/{self.upload}/import/start",
                                       json={**self.asked, "carry": []})
            self.assertEqual(202, started.status_code, started.text)
            for _ in range(100):
                finished = self.client.get(f"/jobs/{started.json()['id']}").json()
                if finished["state"] != "running":
                    break
                time.sleep(0.1)

        self.assertEqual("done", finished["state"], finished)
        result = finished["result"]
        self.assertTrue(result["failed"])
        self.assertIn("no room", result["error"])
        self.assertEqual("t-old", result["replaced_tables"][0]["table"]["id"])

    def test_a_failure_with_no_swap_carries_no_details(self) -> None:
        with mock.patch.object(asset_import_service, "_extract_replace",
                               side_effect=ValueError("no room")):
            response = self.client.post(f"/uploads/{self.upload}/import",
                                        json={**self.asked, "add_table": True})

        self.assertEqual(400, response.status_code, response.text)
        self.assertNotIn("replaced_tables", response.json()["error"].get("details") or {})


class SwapRoutesTests(ReplaceCase):
    path = f"/games/{GAME_ID}/tables/t-old"

    def setUp(self) -> None:
        super().setUp()
        row = mock.patch.object(table_ops, "row_or_refuse", side_effect=lambda game, table_id: {
            "id": table_id, "format": "vpx", "app": "vpx", "filename": "Foo 1.1.vpx",
            "default": True, "hidden": False, "available": True, "assets": {}})
        row.start()
        self.addCleanup(row.stop)

    def swapped(self) -> dict:
        response = self.client.post(f"/uploads/{self.upload}/import", json=self.asked)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["replaced_tables"][0]

    def test_what_a_swap_moves_is_asked_for_before_it_happens(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        response = self.client.get(f"{self.path}/swap")

        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual(("t-old", "Foo 1.1.vpx"), (body["table"], body["filename"]))
        self.assertEqual({"ini", "script"}, {one["kind"] for one in body["files"]})
        self.assertTrue(body["label"])

    def test_a_swap_is_put_back_from_the_copies_it_kept(self) -> None:
        copies = [one["id"] for one in self.swapped()["kept"]]

        response = self.client.post(f"{self.path}/swap_back", json={"copies": copies})

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(b"old", (self.game / "Foo 1.1.vpx").read_bytes())
        self.assertEqual("t-old", response.json()["table"]["id"])

    def test_a_table_is_deleted_from_disk_with_a_copy_of_each_file_and_its_record(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        response = self.client.delete(f"{self.path}/file")

        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual(("t-old", True), (body["table"], body["deleted"]))
        self.assertEqual(
            ["Foo 1.1.ini", "Foo 1.1.vbs", "Foo 1.1.vpx"],
            sorted(Path(one["path"]).name.split(".vpinfe-")[0] for one in body["kept"]))
        table = next(one for one in body["kept"] if one["file"] == "Foo 1.1.vpx")
        self.assertEqual("1.1", table["label"])
        self.assertFalse((self.game / "Foo 1.1.vpx").exists())
        self.assertNotIn("t-old", json.loads(self.info.read_text())["tables"])

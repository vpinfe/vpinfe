"""The plan and import routes take the table a drop landed on, the files a person set by hand
and the one picked of several, and a request that leaves them out plans exactly as before."""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import httpapi
from common.games import media_placement
from tests.support.drops import png_bytes
from tests.support.library import fake_game, game_info
from tests.support.swaps import library_of

GAME = "Multi00000001"


def _archive(members: dict[str, bytes]) -> bytes:
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return held.getvalue()


class _Api(unittest.TestCase):
    PARSED: dict | None = None

    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.folder = Path(held.name) / "Multi (Bar 1999)"
        self.folder.mkdir()
        stems = ["Alpha", "Bravo", "Charlie"]
        tables = {f"t{n}": {"id": f"t{n}", "filename": f"{stem}.vpx"}
                  for n, stem in enumerate(stems, start=1)}
        for stem in stems:
            (self.folder / f"{stem}.vpx").write_bytes(b"x")
        info = game_info("Alpha", game_id=GAME, tables=tables)
        info["vpinfe"]["default_table"] = "t1"
        (self.folder / f"{self.folder.name}.info").write_text(json.dumps(info))
        library_of(self, {GAME: fake_game(self.folder, self.folder.name, meta=info)},
                   parsed=self.PARSED)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _session(self, name: str, content: bytes) -> str:
        begun = self.client.post("/uploads")
        upload_id = begun.json()["id"]
        self.addCleanup(self.client.delete, f"/uploads/{upload_id}")
        self.client.post(f"/uploads/{upload_id}/files", data={"relpath": name},
                         files={"file": (name, content)})
        return upload_id

    def _drop(self, members: dict[str, bytes]) -> str:
        return self._session("drop.zip", _archive(members))

    def _plan(self, upload_id: str, **body):
        return self.client.post(f"/uploads/{upload_id}/plan",
                                json={"game_dir": str(self.folder), **body})

    def _import(self, upload_id: str, **body):
        return self.client.post(f"/uploads/{upload_id}/import",
                                json={"game_dir": str(self.folder), **body})

    def _files(self) -> set[str]:
        return {path.relative_to(self.folder).as_posix()
                for path in self.folder.rglob("*") if path.is_file()}


class UploadPlanTableTests(_Api):
    def test_a_table_names_what_a_loose_file_is_called(self) -> None:
        upload_id = self._session("Other.pov", b"x" * 16)

        plan = self._plan(upload_id, asset_kind="pov", table_id="t2")

        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertEqual(Path(plan.json()["items"][0]["destination"]).name, "Bravo.pov")
        self.assertEqual(plan.json()["tables"], [])

    def test_a_scope_and_a_page_name_where_a_picture_goes(self) -> None:
        upload_id = self._session("IMG_0001.png", b"x" * 16)

        plan = self._plan(upload_id, media_kind="flyer", table_id="t2", scope="table", page=2)

        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertIn("Bravo", Path(plan.json()["items"][0]["destination"]).name)

    def test_a_file_named_for_no_table_lists_the_tables_it_could_have_been_for(self) -> None:
        upload_id = self._session("Other.pov", b"x" * 16)

        plan = self._plan(upload_id, asset_kind="pov")

        self.assertEqual([(one["id"], one["default"]) for one in plan.json()["tables"]],
                         [("t1", True), ("t2", False), ("t3", False)])

    def test_a_request_that_leaves_the_new_fields_out_plans_as_it_did(self) -> None:
        upload_id = self._session("IMG_0001.png", b"x" * 16)

        plan = self._plan(upload_id, media_kind="wheel")

        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertIn("Multi (Bar 1999)", Path(plan.json()["items"][0]["destination"]).name)
        self.assertEqual(plan.json()["tables"], [])

    def test_an_empty_body_plans_the_upload_as_it_stands(self) -> None:
        upload_id = self._session("Other.directb2s", b"x" * 16)

        plan = self.client.post(f"/uploads/{upload_id}/plan", json={})

        self.assertEqual(plan.status_code, 200, plan.text)
        for field in ("groups", "unclaimed", "left_out"):
            self.assertEqual(plan.json()[field], [], field)

    def test_a_table_the_game_does_not_hold_is_refused(self) -> None:
        upload_id = self._session("IMG_0001.png", b"x" * 16)

        plan = self._plan(upload_id, media_kind="wheel", table_id="t9")

        self.assertEqual(plan.status_code, 400, plan.text)

    def test_a_scope_outside_the_two_is_rejected_at_the_boundary(self) -> None:
        upload_id = self._session("IMG_0001.png", b"x" * 16)

        plan = self._plan(upload_id, media_kind="wheel", scope="everything")

        self.assertEqual(plan.status_code, 422, plan.text)


class ChooseOneTests(_Api):
    WHEELS = {"(Wheel) One.png": png_bytes(64, 64), "(Wheel) Two.png": png_bytes(80, 80)}

    def test_an_import_with_a_group_unanswered_is_refused_and_writes_nothing(self) -> None:
        upload_id = self._drop(self.WHEELS)
        before = self._files()

        refused = self._import(upload_id)

        self.assertEqual(refused.status_code, 422, refused.text)
        error = refused.json()["error"]
        self.assertEqual(error["code"], "choose_one")
        self.assertEqual([group["key"] for group in error["details"]["groups"]], ["media:wheel"])
        self.assertEqual(self._files(), before)

    def test_the_plan_returns_the_group_instead_of_refusing(self) -> None:
        upload_id = self._drop(self.WHEELS)

        plan = self._plan(upload_id)

        self.assertEqual(plan.status_code, 200, plan.text)
        groups = plan.json()["groups"]
        self.assertEqual([group["key"] for group in groups], ["media:wheel"])
        self.assertEqual(sorted(one["member"] for one in groups[0]["candidates"]),
                         sorted(self.WHEELS))

    def test_a_pick_writes_the_one_file_and_says_what_it_left(self) -> None:
        upload_id = self._drop(self.WHEELS)

        report = self._import(upload_id, picks={"media:wheel": "(Wheel) Two.png"})

        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()["written"], ["(Wheel) Two.png"])
        self.assertEqual(report.json()["not_written"], ["(Wheel) One.png"])
        wheel = media_placement.target_name("wheel", self.folder.name, ".png")
        self.assertIn(f"medias/{wheel}", self._files())

    def test_a_pick_for_a_group_the_plan_does_not_have_is_ignored(self) -> None:
        upload_id = self._drop({"(Wheel) One.png": png_bytes(64, 64)})

        plan = self._plan(upload_id, picks={"media:backglass": "(Wheel) One.png",
                                            "nowhere": ""})

        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertEqual(len(plan.json()["items"]), 1)
        self.assertEqual(plan.json()["groups"], [])

    def test_a_pick_that_is_not_in_its_group_is_refused(self) -> None:
        upload_id = self._drop(self.WHEELS)

        plan = self._plan(upload_id, picks={"media:wheel": "(Wheel) Nine.png"})

        self.assertEqual(plan.status_code, 400, plan.text)


class AssignmentTests(_Api):
    DROP = {"(Wheel) One.png": png_bytes(64, 64), "photo.png": png_bytes(90, 90),
            "notes.txt": b"x" * 16}

    def _kinds(self, plan) -> dict[str, str]:
        return {item["member"]: item["media_kind"] or item["kind"]
                for item in plan.json()["items"]}

    def test_an_assignment_places_a_file_the_drop_left_unclaimed(self) -> None:
        upload_id = self._drop(self.DROP)
        unplaced = self._plan(upload_id).json()
        self.assertEqual([one["member"] for one in unplaced["unclaimed"]], ["photo.png"])

        placed = self._plan(upload_id, assignments=[
            {"member": "photo.png", "kind": "media", "media_kind": "backglass"}])

        self.assertEqual(placed.status_code, 200, placed.text)
        self.assertEqual(self._kinds(placed)["photo.png"], "backglass")
        self.assertEqual(placed.json()["unclaimed"], [])
        item = next(one for one in placed.json()["items"] if one["member"] == "photo.png")
        self.assertEqual(item["basis"], "declared")

    def test_an_assignment_can_move_a_claimed_file_and_name_its_table(self) -> None:
        upload_id = self._drop(self.DROP)

        plan = self._plan(upload_id, assignments=[
            {"member": "(Wheel) One.png", "kind": "media", "media_kind": "logo",
             "table_id": "t2"}])

        self.assertEqual(self._kinds(plan)["(Wheel) One.png"], "logo")
        destination = next(one["destination"] for one in plan.json()["items"]
                           if one["member"] == "(Wheel) One.png")
        self.assertIn("Bravo", Path(destination).name)

    def test_a_skipped_file_is_left_out_with_its_reason(self) -> None:
        upload_id = self._drop(self.DROP)

        plan = self._plan(upload_id, assignments=[{"member": "(Wheel) One.png", "skip": True}])

        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertEqual([one["member"] for one in plan.json()["items"]], ["notes.txt"])
        left = {one["member"]: one for one in plan.json()["left_out"]}
        self.assertEqual(left["(Wheel) One.png"]["reason_key"], "arrival.left_out.skipped")

    def test_a_kind_the_file_cannot_be_is_refused_with_its_reason(self) -> None:
        upload_id = self._drop(self.DROP)

        plan = self._plan(upload_id, assignments=[
            {"member": "notes.txt", "kind": "media", "media_kind": "wheel"}])

        self.assertEqual(plan.status_code, 400, plan.text)
        self.assertIn("notes.txt", plan.json()["error"]["message"])

    def test_a_destination_is_not_a_thing_an_assignment_can_name(self) -> None:
        upload_id = self._drop(self.DROP)
        before = self._files()

        plan = self._plan(upload_id, assignments=[
            {"member": "photo.png", "kind": "media", "media_kind": "backglass",
             "destination": "../../outside.png"}])
        report = self._import(upload_id, assignments=[
            {"member": "photo.png", "kind": "media", "media_kind": "backglass",
             "destination": "../../outside.png"}])

        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertNotIn("outside", plan.json()["items"][0]["destination"])
        self.assertEqual(report.status_code, 200, report.text)
        self.assertFalse((self.folder.parent / "outside.png").exists())
        self.assertFalse((self.folder.parent.parent / "outside.png").exists())
        self.assertTrue(self._files() > before)

    def test_a_member_that_climbs_out_of_the_game_writes_nothing(self) -> None:
        upload_id = self._drop(self.DROP)
        before = self._files()

        for member in ("../outside.png", "/etc/passwd", "photo.png/../../x.png"):
            with self.subTest(member=member):
                plan = self._plan(upload_id, assignments=[
                    {"member": member, "kind": "media", "media_kind": "backglass"}])
                report = self._import(upload_id, assignments=[
                    {"member": member, "kind": "media", "media_kind": "backglass"}])

                self.assertEqual(plan.status_code, 400, plan.text)
                self.assertEqual(report.status_code, 400, report.text)

        self.assertEqual(self._files(), before)
        self.assertFalse((self.folder.parent / "outside.png").exists())

    def test_a_table_the_game_does_not_hold_is_refused(self) -> None:
        upload_id = self._drop(self.DROP)

        plan = self._plan(upload_id, assignments=[
            {"member": "photo.png", "kind": "media", "media_kind": "backglass",
             "table_id": "t9"}])

        self.assertEqual(plan.status_code, 400, plan.text)


class ImportReportTests(_Api):
    PARSED = {"version": "1.2"}
    DROP = {"(Wheel) One.png": png_bytes(64, 64), "photo.png": png_bytes(90, 90),
            "Multi.directb2s": b"x" * 16, "notes.txt": b"x" * 16, "mystery.xyz": b"x" * 16}

    @staticmethod
    def _shown(plan: dict) -> set[str]:
        shown = {one["member"] for one in plan["items"]}
        shown |= {one["member"] for group in plan["groups"] for one in group["candidates"]}
        shown |= {one["member"] for one in plan["unclaimed"]}
        shown |= {one["member"] for one in plan["left_out"]}
        return shown

    def test_what_was_written_and_what_was_not_are_what_the_plan_showed(self) -> None:
        upload_id = self._drop(self.DROP)
        shown = self._shown(self._plan(upload_id).json())

        report = self._import(upload_id).json()

        self.assertEqual(set(report["written"]) | set(report["not_written"]), shown)
        self.assertEqual(set(report["written"]) & set(report["not_written"]), set())
        self.assertIn("(Wheel) One.png", report["written"])
        self.assertIn("photo.png", report["not_written"])

    def test_a_file_set_by_hand_counts_as_written(self) -> None:
        upload_id = self._drop(self.DROP)
        assigned = [{"member": "photo.png", "kind": "media", "media_kind": "backglass"}]
        shown = self._shown(self._plan(upload_id, assignments=assigned).json())

        report = self._import(upload_id, assignments=assigned).json()

        self.assertIn("photo.png", report["written"])
        self.assertEqual(set(report["written"]) | set(report["not_written"]), shown)

    def test_a_table_file_on_a_slot_is_offered_unticked_and_not_written(self) -> None:
        upload_id = self._drop({"(Wheel) One.png": png_bytes(64, 64), "Other.vpx": b"x" * 16})

        plan = self._plan(upload_id, media_kind="wheel")
        report = self._import(upload_id, media_kind="wheel")

        self.assertEqual(plan.status_code, 200, plan.text)
        extra =next(one for one in plan.json()["items"] if one["member"] == "Other.vpx")
        self.assertFalse(extra["default_enabled"])
        self.assertEqual(report.json()["written"], ["(Wheel) One.png"])
        self.assertNotIn("Other.vpx", self._files())


class WholeFolderReportTests(_Api):
    PARSED = {"version": "1.2"}

    def test_every_file_of_a_folder_kind_counts_as_written(self) -> None:
        upload_id = self._drop({"a.ogg": b"x" * 16, "b.ogg": b"x" * 16})

        plan = self._plan(upload_id, asset_kind="altsound", rom_name="mm")
        report = self._import(upload_id, asset_kind="altsound", rom_name="mm")

        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(sorted(report.json()["written"]), ["a.ogg", "b.ogg"])
        self.assertEqual(report.json()["not_written"], [])

    def test_a_page_given_for_a_kind_with_no_pages_is_a_bad_request(self) -> None:
        upload_id = self._drop({"(Wheel) One.png": png_bytes(64, 64)})
        assigned = [{"member": "(Wheel) One.png", "kind": "media", "media_kind": "wheel",
                     "page": 2}]

        refused = self._plan(upload_id, assignments=assigned)

        self.assertEqual(refused.status_code, 400, refused.text)


class ContractTests(_Api):
    def test_the_plan_schema_declares_what_it_returns(self) -> None:
        spec = self.client.get("/openapi.json").json()["components"]["schemas"]

        for field in ("tables", "groups", "unclaimed", "left_out"):
            self.assertIn(field, spec["ImportPlanResource"]["properties"], field)
        for field in ("member", "basis"):
            self.assertIn(field, spec["PlanItem"]["properties"], field)
        for field in ("table_id", "scope", "page", "assignments", "picks"):
            self.assertIn(field, spec["PlanRequest"]["properties"], field)
            self.assertIn(field, spec["ImportRequest"]["properties"], field)
        for field in ("written", "not_written"):
            self.assertIn(field, spec["ImportReport"]["properties"], field)

    def test_nothing_the_engine_returns_is_dropped_by_the_response_model(self) -> None:
        from common.uploads import upload_ops
        from httpapi import models

        upload_id = self._drop({"(Wheel) One.png": png_bytes(64, 64),
                                "(Wheel) Two.png": png_bytes(80, 80),
                                "photo.png": png_bytes(90, 90), "later.png": png_bytes(70, 70),
                                "notes.txt": b"x" * 16, "Other.pov": b"x" * 16})
        raw = upload_ops.plan_for(upload_id, {
            "game_dir": str(self.folder),
            "assignments": [{"member": "later.png", "skip": True}]})

        shaped = models.ImportPlanResource(**raw).model_dump()

        for field in ("groups", "unclaimed", "left_out", "tables"):
            self.assertTrue(raw[field] or field == "tables", field)
            self.assertEqual(len(shaped[field]), len(raw[field]), field)
        group = shaped["groups"][0]
        for field in ("key", "candidates"):
            self.assertTrue(group[field], field)
        for field in ("member", "name", "size", "width", "height", "basis"):
            self.assertIn(field, group["candidates"][0], field)
        for field in ("member", "basis"):
            self.assertIn(field, shaped["items"][0], field)
        for field in ("member", "name", "reason_key", "reason"):
            self.assertIn(field, shaped["left_out"][0], field)
        for field in ("member", "name", "size", "choices"):
            self.assertIn(field, shaped["unclaimed"][0], field)


if __name__ == "__main__":
    unittest.main()

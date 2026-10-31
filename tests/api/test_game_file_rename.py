"""Protects: (a, b) a changed file keeps its bytes and record, and nothing goes unasked."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import asset_origin, media_placement
from common.games.info_file import MetaConfig
from httpapi import auth, scopes
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Rename0001"
FOLDER = "Attack from Mars (Bally 1995)"
LEDGER = {"extras/notes.txt": {"source": {"host": "capture"}}}


class GameFileChanges(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Name": "Attack from Mars"}, "VPinFE": {"game_id": GAME_ID},
                "tables": {"a1": {"id": "a1", "filename": f"{FOLDER}.vpx"}},
                "assets": LEDGER}
        self.folder = write_game(self.root, FOLDER, info=info,
                                 files={"Manual.docx": b"manual",
                                        f"{FOLDER}.directb2s": b"backglass",
                                        "extras/notes.txt": b"notes",
                                        "extras/Notes.csv": b"markdown",
                                        "docs/a.txt": b"a",
                                        "empty/.keep": b"",
                                        "pinmame/roms/amars.zip": b"rom"})
        (self.folder / "empty" / ".keep").unlink()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        game = fake_game(self.folder, FOLDER, meta=info)
        for patcher in (patch("common.games.game_repository.catalog",
                              return_value={GAME_ID: game}),
                        patch("common.backups.folder.current", return_value=self.backups)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _rename(self, path: str, name: str, **body):
        return self.client.patch(f"/games/{GAME_ID}/files", params={"path": path},
                                 json={"name": name, **body})

    def _change(self, route: str, path: str, **body):
        return self.client.patch(f"/games/{GAME_ID}/{route}", params={"path": path}, json=body)

    def _remove(self, route: str, path: str, **params):
        return self.client.delete(f"/games/{GAME_ID}/{route}", params={"path": path, **params})

    def _kept(self) -> list[pathlib.Path]:
        return [one for one in self.backups.rglob("*") if one.is_file()]


class RenamingAFile(GameFileChanges):
    def test_a_file_takes_its_new_name_in_its_own_folder_with_its_bytes(self) -> None:
        response = self._rename("extras/notes.txt", "read me.txt")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"path": "extras/read me.txt", "was": "extras/notes.txt",
                          "name": "read me.txt"}, response.json())
        self.assertFalse((self.folder / "extras" / "notes.txt").exists())
        self.assertEqual(b"notes", (self.folder / "extras" / "read me.txt").read_bytes())

    def test_where_a_file_came_from_goes_with_it(self) -> None:
        self._rename("extras/notes.txt", "read me.txt")

        self.assertEqual(["extras/read me.txt"], sorted(asset_origin.sources(self.folder)))

    def test_a_change_of_case_alone_is_a_rename(self) -> None:
        response = self._rename("Manual.docx", "MANUAL.docx")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(["MANUAL.docx"],
                         [one.name for one in self.folder.iterdir() if one.suffix == ".docx"])

    def test_the_games_record_is_not_changed_and_a_table_is_not_removed_here(self) -> None:
        self.assertEqual(400, self._rename(f"{FOLDER}.info", "other.txt").status_code)
        for path in (f"{FOLDER}.vpx", f"{FOLDER}.info"):
            with self.subTest(path=path):
                self.assertEqual(400, self._remove("files", path).status_code)
                self.assertTrue((self.folder / path).exists())

    def test_a_path_typed_in_another_case_is_the_file_and_its_record_follows(self) -> None:
        response = self._rename("Extras/Notes.TXT", "read me.txt")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("extras/read me.txt", response.json()["path"])
        self.assertEqual(["extras/read me.txt"], sorted(asset_origin.sources(self.folder)))

    def test_a_change_of_case_in_another_case_is_not_a_name_that_is_taken(self) -> None:
        response = self._rename("extras/Notes.csv", "notes.CSV")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("extras/notes.CSV", response.json()["path"])

    def test_nothing_is_renamed_into_a_table(self) -> None:
        self.assertEqual(400, self._rename("Manual.docx", "Manual.vpx").status_code)
        self.assertTrue((self.folder / "Manual.docx").exists())

    def test_a_name_that_is_not_one_name_is_refused(self) -> None:
        for name in ("", "..", "a/b.txt", "a\\b.txt", "ends.", " padded.txt", "what?.txt"):
            with self.subTest(name=name):
                self.assertEqual(400, self._rename("Manual.docx", name).status_code)
        self.assertTrue((self.folder / "Manual.docx").exists())

    def test_a_path_that_is_not_a_file_of_the_game_is_refused(self) -> None:
        self.assertEqual(404, self._rename("missing.txt", "a.txt").status_code)
        self.assertEqual(404, self._rename("extras", "more").status_code)
        self.assertEqual(400, self._rename("../Manual.docx", "a.txt").status_code)

    def test_nothing_is_changed_while_a_table_of_the_game_is_playing(self) -> None:
        with patch("common.games.file_move.playing", return_value=True):
            response = self._rename("Manual.docx", "Guide.docx")

        self.assertEqual(409, response.status_code)
        self.assertTrue((self.folder / "Manual.docx").exists())

    def test_a_request_that_changes_nothing_is_refused(self) -> None:
        response = self._change("files", "Manual.docx")

        self.assertEqual(400, response.status_code)


class ANameAnotherFileHas(GameFileChanges):
    def test_it_is_refused_in_any_case_with_what_it_would_replace_and_nothing_changes(self) -> None:
        response = self._rename("extras/notes.txt", "NOTES.CSV")

        self.assertEqual(409, response.status_code)
        impacts = response.json()["error"]["details"]["impacts"]
        self.assertEqual([{"code": "name_taken", "path": "extras/Notes.csv",
                           "answers": ["replace"]}], impacts)
        self.assertEqual(b"notes", (self.folder / "extras" / "notes.txt").read_bytes())
        self.assertEqual(b"markdown", (self.folder / "extras" / "Notes.csv").read_bytes())
        self.assertEqual([], self._kept())

    def test_replace_keeps_a_copy_of_the_file_it_takes_the_place_of(self) -> None:
        response = self._rename("extras/notes.txt", "NOTES.CSV", resolve=["replace"])

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(b"notes", (self.folder / "extras" / "NOTES.CSV").read_bytes())
        self.assertEqual([b"markdown"], [one.read_bytes() for one in self._kept()])

    def test_a_folder_is_never_replaced(self) -> None:
        response = self._rename("Manual.docx", "docs")

        self.assertEqual(409, response.status_code)
        self.assertEqual(b"manual", (self.folder / "Manual.docx").read_bytes())


class MovingAFile(GameFileChanges):
    def test_a_file_goes_into_another_folder_with_its_record(self) -> None:
        response = self._change("files", "extras/notes.txt", to="docs")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("docs/notes.txt", response.json()["path"])
        self.assertEqual(b"notes", (self.folder / "docs" / "notes.txt").read_bytes())
        self.assertEqual(["docs/notes.txt"], sorted(asset_origin.sources(self.folder)))

    def test_a_file_can_be_moved_and_renamed_at_once(self) -> None:
        response = self._change("files", "extras/notes.txt", to="docs", name="b.txt")

        self.assertEqual("docs/b.txt", response.json()["path"])

    def test_a_file_in_the_folder_of_the_scores_is_refused_until_answered(self) -> None:
        (self.folder / "pinmame" / "nvram").mkdir()
        (self.folder / "pinmame" / "nvram" / "amars.nv").write_bytes(b"scores")

        response = self._change("files", "pinmame/nvram/amars.nv", to="extras")

        self.assertEqual("fixed_place", response.json()["error"]["details"]["impacts"][0]["code"])
        self.assertTrue((self.folder / "pinmame" / "nvram" / "amars.nv").exists())

    def test_the_games_own_folder_is_where_an_empty_destination_means(self) -> None:
        response = self._change("files", "extras/notes.txt", to="")

        self.assertEqual("notes.txt", response.json()["path"])
        self.assertTrue((self.folder / "notes.txt").exists())

    def test_a_folder_that_is_not_there_is_refused(self) -> None:
        self.assertEqual(404, self._change("files", "Manual.docx", to="nowhere").status_code)
        self.assertEqual(400, self._change("files", "Manual.docx", to="../out").status_code)
        self.assertTrue((self.folder / "Manual.docx").exists())


class WhatAChangeWouldStopWorking(GameFileChanges):
    def _companion(self):
        return self._rename(f"{FOLDER}.directb2s", "other.directb2s")

    def test_a_file_a_table_uses_is_refused_until_the_loss_is_answered(self) -> None:
        response = self._companion()

        self.assertEqual(409, response.status_code)
        impact = response.json()["error"]["details"]["impacts"][0]
        self.assertEqual(("companion", f"{FOLDER}.directb2s", "", ["break"]),
                         (impact["code"], impact["was"], impact["becomes"], impact["answers"]))
        self.assertTrue((self.folder / f"{FOLDER}.directb2s").exists())

    def test_break_goes_ahead(self) -> None:
        response = self._rename(f"{FOLDER}.directb2s", "other.directb2s", resolve=["break"])

        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue((self.folder / "other.directb2s").exists())

    def test_a_file_in_a_folder_vpx_reads_by_its_place_is_refused_until_answered(self) -> None:
        path = "pinmame/roms/amars.zip"
        response = self._rename(path, "renamed.zip")

        self.assertEqual("fixed_place", response.json()["error"]["details"]["impacts"][0]["code"])
        self.assertTrue((self.folder / path).exists())
        self.assertEqual(200, self._rename(path, "renamed.zip", resolve=["break"]).status_code)

    def test_a_value_that_is_not_a_way_to_go_on_is_refused(self) -> None:
        self.assertEqual(400, self._rename("Manual.docx", "x.docx", resolve=["maybe"]).status_code)

    def test_the_preview_says_so_and_changes_nothing(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/files/impacts",
                                   params={"path": f"{FOLDER}.directb2s", "name": "x.directb2s"})

        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual("x.directb2s", body["becomes"])
        self.assertEqual(["companion"], [one["code"] for one in body["impacts"]])
        self.assertTrue((self.folder / f"{FOLDER}.directb2s").exists())

    def test_a_file_nothing_reads_has_no_effects(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/files/impacts",
                                   params={"path": "Manual.docx", "remove": "true"})

        self.assertEqual({"path": "Manual.docx", "becomes": "", "impacts": []}, response.json())


class RemovingAFile(GameFileChanges):
    def test_it_goes_after_a_copy_is_kept_and_its_record_goes(self) -> None:
        response = self._remove("files", "extras/notes.txt")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"removed": ["extras/notes.txt"]}, response.json())
        self.assertFalse((self.folder / "extras" / "notes.txt").exists())
        self.assertEqual([b"notes"], [one.read_bytes() for one in self._kept()])
        self.assertEqual([], sorted(asset_origin.sources(self.folder)))

    def test_a_file_a_table_uses_is_refused_until_the_loss_is_answered(self) -> None:
        path = f"{FOLDER}.directb2s"

        self.assertEqual(409, self._remove("files", path).status_code)
        self.assertTrue((self.folder / path).exists())
        self.assertEqual(200, self._remove("files", path, resolve="break").status_code)
        self.assertEqual([b"backglass"], [one.read_bytes() for one in self._kept()])

    def test_a_path_that_is_not_a_file_is_not_found(self) -> None:
        self.assertEqual(404, self._remove("files", "missing.txt").status_code)
        self.assertEqual(404, self._remove("files", "docs").status_code)

    def test_the_copy_is_an_option_and_is_kept_unless_it_is_turned_off(self) -> None:
        response = self._remove("files", "extras/notes.txt", keep_copy="false")

        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse((self.folder / "extras" / "notes.txt").exists())
        self.assertEqual([], self._kept())
        self.assertEqual(200, self._remove("files", "Manual.docx").status_code)
        self.assertEqual([b"manual"], [one.read_bytes() for one in self._kept()])

    def test_a_link_is_not_a_file_of_the_game(self) -> None:
        (self.folder / "link.txt").symlink_to(self.folder / "Manual.docx")

        self.assertEqual(400, self._remove("files", "link.txt").status_code)
        self.assertEqual(400, self._rename("link.txt", "other.txt").status_code)
        self.assertTrue((self.folder / "link.txt").is_symlink())


class Folders(GameFileChanges):
    def test_a_folder_is_made_with_the_ones_above_it(self) -> None:
        response = self.client.post(f"/games/{GAME_ID}/folders", json={"path": "docs/new/deep"})

        self.assertEqual(201, response.status_code, response.text)
        self.assertEqual({"path": "docs/new/deep"}, response.json())
        self.assertTrue((self.folder / "docs" / "new" / "deep").is_dir())

    def test_a_name_taken_in_any_case_or_a_bad_name_makes_nothing(self) -> None:
        for path, status in (("DOCS", 409), ("Manual.docx", 409), ("a/what?", 400), ("..", 400)):
            with self.subTest(path=path):
                response = self.client.post(f"/games/{GAME_ID}/folders", json={"path": path})
                self.assertEqual(status, response.status_code, response.text)

    def test_a_folder_is_renamed_and_the_records_of_the_files_in_it_follow(self) -> None:
        response = self._change("folders", "extras", name="more")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"path": "more", "was": "extras", "name": "more"}, response.json())
        self.assertEqual(b"notes", (self.folder / "more" / "notes.txt").read_bytes())
        self.assertEqual(["more/notes.txt"], sorted(asset_origin.sources(self.folder)))

    def test_a_folder_is_moved_into_another(self) -> None:
        response = self._change("folders", "extras", to="docs")

        self.assertEqual("docs/extras", response.json()["path"])
        self.assertEqual(["docs/extras/notes.txt"], sorted(asset_origin.sources(self.folder)))

    def test_a_folder_does_not_go_into_itself(self) -> None:
        self.assertEqual(400, self._change("folders", "docs", to="docs").status_code)
        (self.folder / "docs" / "inner").mkdir()
        self.assertEqual(400, self._change("folders", "docs", to="docs/inner").status_code)

    def test_a_folder_does_not_take_a_name_that_is_taken(self) -> None:
        self.assertEqual(409, self._change("folders", "extras", name="DOCS").status_code)
        self.assertTrue((self.folder / "extras").is_dir())

    def test_the_games_own_folder_and_a_file_are_not_a_folder_to_change(self) -> None:
        self.assertEqual(404, self._change("folders", "Manual.docx", name="x").status_code)
        self.assertEqual(400, self._change("folders", ".", name="x").status_code)

    def test_a_folder_that_holds_a_kind_by_its_place_is_refused_until_answered(self) -> None:
        response = self._change("folders", "pinmame", name="pm")

        self.assertEqual("fixed_place", response.json()["error"]["details"]["impacts"][0]["code"])
        self.assertTrue((self.folder / "pinmame").is_dir())


class RemovingAFolder(GameFileChanges):
    def test_an_empty_one_goes_without_being_asked(self) -> None:
        response = self._remove("folders", "empty")

        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse((self.folder / "empty").exists())

    def test_one_with_files_is_refused_with_how_many_until_it_is_answered(self) -> None:
        response = self._remove("folders", "extras")

        self.assertEqual(409, response.status_code)
        self.assertEqual([{"code": "holds", "path": "extras", "files": 2,
                           "answers": ["keep", "break"]}],
                         [{key: one[key] for key in ("code", "path", "files", "answers")}
                          for one in response.json()["error"]["details"]["impacts"]])
        self.assertTrue((self.folder / "extras" / "notes.txt").exists())

    def test_keep_moves_what_is_in_it_up_with_its_records(self) -> None:
        response = self._remove("folders", "extras", resolve="keep")

        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse((self.folder / "extras").exists())
        self.assertEqual(b"notes", (self.folder / "notes.txt").read_bytes())
        self.assertEqual(["notes.txt"], sorted(asset_origin.sources(self.folder)))

    def test_keep_that_would_take_a_taken_name_puts_everything_back(self) -> None:
        (self.folder / "notes.txt").write_bytes(b"already here")

        response = self._remove("folders", "extras", resolve="keep")

        self.assertEqual(409, response.status_code)
        self.assertEqual(b"notes", (self.folder / "extras" / "notes.txt").read_bytes())
        self.assertEqual(b"markdown", (self.folder / "extras" / "Notes.csv").read_bytes())
        self.assertEqual(b"already here", (self.folder / "notes.txt").read_bytes())
        self.assertFalse((self.folder / "Notes.csv").exists())
        self.assertEqual(["extras/notes.txt"], sorted(asset_origin.sources(self.folder)))

    def test_a_folder_that_would_be_read_by_its_place_when_it_moves_up_is_asked_about(self) -> None:
        (self.folder / "pick" / "medias").mkdir(parents=True)
        (self.folder / "pick" / "medias" / "a.png").write_bytes(b"picture")

        refused = self._remove("folders", "pick", resolve="keep")

        self.assertEqual(409, refused.status_code, refused.text)
        self.assertIn("fixed_place",
                      [one["code"] for one in refused.json()["error"]["details"]["impacts"]])
        self.assertTrue((self.folder / "pick" / "medias" / "a.png").exists())

    def test_a_file_that_would_be_a_table_when_it_moves_up_is_refused(self) -> None:
        (self.folder / "pick").mkdir()
        (self.folder / "pick" / "Extra.vpx").write_bytes(b"not a table of the game")

        refused = self._remove("folders", "pick", resolve="keep")

        self.assertEqual(400, refused.status_code, refused.text)
        self.assertTrue((self.folder / "pick" / "Extra.vpx").exists())

    def test_break_removes_it_with_what_is_in_it_and_keeps_no_copy(self) -> None:
        response = self._remove("folders", "extras", resolve="break")

        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse((self.folder / "extras").exists())
        self.assertEqual([], self._kept())
        self.assertEqual([], sorted(asset_origin.sources(self.folder)))


class WhoMayGoAheadWithAnEffect(GameFileChanges):
    def _grant(self, *granted: str) -> None:
        class Policy(auth.SignInPolicy):
            name = "test"

            def identify(self, request) -> auth.Identity:
                return auth.Identity(name="test", scopes=frozenset(granted))

        auth.set_policy(Policy())
        self.addCleanup(auth.set_policy, auth.SignInPolicy())

    def test_a_token_that_writes_games_may_answer_replace_and_keep(self) -> None:
        self._grant(scopes.GAMES_WRITE)

        self.assertEqual(200, self._rename("extras/notes.txt", "Notes.csv",
                                           resolve=["replace"]).status_code)
        self.assertEqual(200, self._remove("folders", "docs", resolve="keep").status_code)

    def test_it_may_not_answer_break(self) -> None:
        self._grant(scopes.GAMES_WRITE)

        self.assertEqual(403, self._rename("pinmame/roms/amars.zip", "x.zip",
                                           resolve=["break"]).status_code)
        self.assertEqual(403, self._remove("folders", "docs", resolve="break").status_code)
        self.assertTrue((self.folder / "pinmame" / "roms" / "amars.zip").exists())
        self.assertTrue((self.folder / "docs" / "a.txt").exists())

    def test_reading_what_a_change_would_do_needs_only_to_read(self) -> None:
        self._grant(scopes.GAMES_READ)

        found = self.client.get(f"/games/{GAME_ID}/files/impacts",
                                params={"path": "Manual.docx", "remove": "true"})

        self.assertEqual(200, found.status_code, found.text)
        self.assertEqual(403, self._remove("files", "Manual.docx").status_code)


class RenamingATable(GameFileChanges):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Name": "Attack from Mars"}, "VPinFE": {"game_id": GAME_ID},
                "tables": {"a1": {"id": "a1", "filename": f"{FOLDER}.vpx"},
                           "a2": {"id": "a2", "filename": "Other.vpx"}},
                "assets": {**LEDGER, "Other.ini": {"source": {"host": "capture"}},
                           "Other.directb2s": {"source": {"host": "capture"}}}}
        (self.folder / f"{FOLDER}.info").write_text(json.dumps(info), encoding="utf-8")
        for name in ("Other.vpx", "Other.ini", "Other.directb2s"):
            (self.folder / name).write_bytes(name.encode())
        self.picture = media_placement.target_name("wheel", "Other", ".png")
        (self.folder / "medias").mkdir(exist_ok=True)
        (self.folder / "medias" / self.picture).write_bytes(b"picture")
        patcher = patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: fake_game(self.folder, FOLDER, meta=info)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _filename(self) -> str:
        meta = MetaConfig(str(self.folder / f"{FOLDER}.info"))
        return meta.data["tables"]["a2"]["filename"]

    def test_it_is_refused_until_what_is_named_for_it_is_answered(self) -> None:
        response = self._rename("Other.vpx", "New.vpx")

        self.assertEqual(409, response.status_code, response.text)
        named = [(one["path"], one["becomes"], one["answers"])
                 for one in response.json()["error"]["details"]["impacts"]
                 if one["code"] == "named_file"]
        self.assertEqual([("Other.directb2s", "New.directb2s", ["fix", "break"]),
                          ("Other.ini", "New.ini", ["fix", "break"]),
                          (f"medias/{self.picture}",
                           f"medias/{self.picture.replace('Other', 'New')}", ["fix", "break"])],
                         sorted(named))
        self.assertTrue((self.folder / "Other.vpx").exists())
        self.assertEqual("Other.vpx", self._filename())

    def test_fix_takes_the_files_named_for_it_along_and_the_record_keeps_the_table(self) -> None:
        response = self._rename("Other.vpx", "New.vpx", resolve=["fix"])

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("New.vpx", self._filename())
        for name in ("New.vpx", "New.ini", "New.directb2s",
                     f"medias/{self.picture.replace('Other', 'New')}"):
            self.assertTrue((self.folder / name).exists(), name)
        for name in ("Other.vpx", "Other.ini", "Other.directb2s", f"medias/{self.picture}"):
            self.assertFalse((self.folder / name).exists(), name)

    def test_what_the_game_records_about_the_carried_files_goes_with_them(self) -> None:
        self._rename("Other.vpx", "New.vpx", resolve=["fix"])

        self.assertEqual(["New.directb2s", "New.ini", "extras/notes.txt"],
                         sorted(asset_origin.sources(self.folder)))

    def test_break_leaves_them_behind(self) -> None:
        response = self._rename("Other.vpx", "New.vpx", resolve=["break"])

        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue((self.folder / "New.vpx").exists())
        self.assertTrue((self.folder / "Other.ini").exists())
        self.assertFalse((self.folder / "New.ini").exists())

    def test_a_name_a_carried_file_would_take_needs_replace_and_keeps_a_copy(self) -> None:
        (self.folder / "New.ini").write_bytes(b"in the way")

        refused = self._rename("Other.vpx", "New.vpx", resolve=["fix"])
        self.assertEqual(409, refused.status_code)
        taken = [one for one in refused.json()["error"]["details"]["impacts"]
                 if one["path"] == "Other.ini"]
        self.assertEqual(["replace", "break"], taken[0]["answers"])
        done = self._rename("Other.vpx", "New.vpx", resolve=["fix", "replace"])

        self.assertEqual(200, done.status_code, done.text)
        self.assertEqual(b"Other.ini", (self.folder / "New.ini").read_bytes())
        self.assertEqual([b"in the way"], [one.read_bytes() for one in self._kept()])

    def test_a_table_stays_in_the_games_folder_and_the_kind_of_file_it_is(self) -> None:
        self.assertEqual(400, self._change("files", "Other.vpx", to="docs").status_code)
        self.assertEqual(400, self._rename("Other.vpx", "Other.txt").status_code)
        self.assertEqual(409, self._rename("Other.vpx", f"{FOLDER}.vpx").status_code)
        self.assertTrue((self.folder / "Other.vpx").exists())
        self.assertEqual("Other.vpx", self._filename())

    def test_a_table_that_is_not_removed_here_has_a_route_of_its_own(self) -> None:
        self.assertEqual(400, self._remove("files", "Other.vpx").status_code)
        self.assertTrue((self.folder / "Other.vpx").exists())

    def test_what_moves_up_is_checked_for_what_a_table_would_use_instead(self) -> None:
        (self.folder / "Other.ini").unlink()
        (self.folder / f"{FOLDER}.ini").write_bytes(b"shared")
        (self.folder / "pick").mkdir()
        (self.folder / "pick" / "Other.ini").write_bytes(b"its own")

        refused = self._remove("folders", "pick", resolve="keep")

        self.assertEqual(409, refused.status_code, refused.text)
        self.assertEqual(["holds", "companion"],
                         [one["code"] for one in refused.json()["error"]["details"]["impacts"]])
        self.assertTrue((self.folder / "pick" / "Other.ini").exists())

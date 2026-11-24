"""Every kept copy, listed in one place and put back by its id.

What is worth pinning is the id: a client holds only what the listing gave it, so an id
built by hand, or one whose file is no longer a copy, has to reach nothing.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common.backups import archive, copies, pending, places
from common.backups import folder as backup_folder
from common.config import paths
from common.config.config_access import cfg_set
from common.config.config_store import ConfigStore
from common.devices.device_registry import DeviceRegistry
from common.games import (
    game_repository,
    launchers,
    library_folders,
)
from common.kept_copies import RESET, RESTORE, backup_path
from common.runtime import jobs
from httpapi import config as config_api
from tests.support.launchers_api import put_launcher
from tests.support.library import game_info, own_launchers, write_game

OLD = datetime(2026, 10, 1, 1, 2, 3, tzinfo=UTC)
NEWER = datetime(2026, 10, 2, 1, 2, 3, tzinfo=UTC)
NEWEST = datetime(2026, 10, 3, 1, 2, 3, tzinfo=UTC)


class BackupsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.library = root / "library"
        self.config = root / "config"
        self.themes = root / "themes"
        self.state = root / "state"
        for folder in (self.library, self.config, self.themes, self.state):
            folder.mkdir()
        self.store = own_launchers(self, str(root))
        self.store.save([], {})
        self.playing = False
        for patcher in (
                patch.object(library_folders, "configured",
                             return_value=[library_folders.Location("l1", str(self.library))]),
                patch.object(places, "CONFIG_DIR", self.config),
                patch.object(places, "STATE_DIR", self.state),
                patch.object(pending, "STATE_DIR", self.state),
                patch.object(archive, "STATE_DIR", self.state),
                patch.object(pending, "CONFIG_DIR", self.config),
                patch.object(archive, "CONFIG_DIR", self.config),
                patch.object(paths, "DEFAULT_BACKUPS_DIR", self.config / "backups"),
                patch.object(backup_folder, "get_ini_config",
                             return_value=ConfigStore(str(root / "vpinfe.ini"))),
                patch.object(game_repository, "all_games", side_effect=self.library_games),
                patch.object(game_repository, "game_folder", side_effect=self.folder_of),
                patch.object(game_repository, "by_folder_name", side_effect=self.named),
                patch.object(places, "INI_2X_DIR", self.config / "backups" / "vpx_ini"),
                patch.object(places, "get_themes_dir", return_value=self.themes),
                patch.object(launchers, "get_launcher_store", return_value=self.store),
                patch("common.backups.ops.game_repository.refresh_games"),
                patch("common.backups.ops.launch_state.current",
                      side_effect=lambda: SimpleNamespace(launching=self.playing))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def library_games(self) -> list[SimpleNamespace]:
        found = []
        for folder in sorted(self.library.iterdir()):
            try:
                meta = json.loads((folder / f"{folder.name}.info").read_text())
            except (OSError, ValueError):
                meta = {}
            found.append(SimpleNamespace(game_dir_name=folder.name,
                                         full_path_game=str(folder), meta_config=meta))
        return found

    def folder_of(self, game_id: str) -> pathlib.Path | None:
        return next((pathlib.Path(game.full_path_game) for game in self.library_games()
                     if game.meta_config.get("vpinfe", {}).get("game_id") == game_id), None)

    def named(self, name: str) -> SimpleNamespace | None:
        return next((game for game in self.library_games() if game.game_dir_name == name),
                    None)

    def game(self, name: str = "Taxi", info: dict | None = None) -> pathlib.Path:
        return write_game(self.library, name, info=info or game_info(
            name, game_id="id-" + name))

    def kept(self, folder: pathlib.Path, when: datetime, reason: str = "",
             info: dict | None = None) -> pathlib.Path:
        path = pathlib.Path(backup_path(folder / f"{folder.name}.info", when, reason))
        path.write_text(json.dumps(info or {"Info": {"Title": "was"}}), encoding="utf-8")
        return path

    def listing(self) -> list[dict]:
        return self.client.get("/backups").json()["copies"]

    def id_of(self, path: pathlib.Path) -> str:
        return next(one["id"] for one in self.listing() if one["path"] == str(path))

    def restore(self, path: pathlib.Path):
        return self.client.post(f"/backups/{self.id_of(path)}/restore")


class ListingTests(BackupsCase):
    def test_a_games_copies_say_what_they_are_of_and_why_they_were_taken(self) -> None:
        folder = self.game()
        self.kept(folder, OLD)
        self.kept(folder, NEWER, RESTORE)
        self.kept(folder, NEWEST, RESET)
        importer = folder / "Taxi.info.bak"
        importer.write_text("{}", encoding="utf-8")

        found = {one["file"]: one for one in self.listing()}

        self.assertEqual(
            {name: one["reason"] for name, one in found.items()},
            {"Taxi.info.vpinfe-20261001T010203Z": "upgrade",
             "Taxi.info.vpinfe-20261002T010203Z-restore": "restore",
             "Taxi.info.vpinfe-20261003T010203Z-reset": "reset",
             "Taxi.info.bak": "import"})
        one = found["Taxi.info.vpinfe-20261001T010203Z"]
        self.assertEqual((one["subject_kind"], one["subject_id"], one["subject_name"], one["kind"]),
                         ("game", "id-Taxi", "Taxi", "game_metadata"))
        self.assertEqual((one["taken"], one["written_by"], one["gone"]),
                         ("2026-10-01T01:02:03Z", "2.x", False))
        self.assertEqual(found["Taxi.info.bak"]["subject_name"], "Taxi")

    def test_a_copy_of_a_game_whose_info_is_gone_says_so(self) -> None:
        folder = self.game()
        self.kept(folder, OLD)
        (folder / "Taxi.info").unlink()

        self.assertTrue(self.listing()[0]["gone"])

    def test_the_config_folder_copies_are_the_settings_and_the_records(self) -> None:
        for name in ("vpinfe.ini", "collections.json", "devices.json", "players.json",
                     "extras.json"):
            (self.config / f"{name}.vpinfe-20261001T010203Z").write_text("x")
        (self.config / "vpinfe.ini").write_text("live")

        found = {one["file"]: one for one in self.listing()}

        self.assertEqual(
            {name.split(".vpinfe")[0]: one["kind"] for name, one in found.items()},
            {"vpinfe.ini": "settings", "collections.json": "collections",
             "devices.json": "devices", "players.json": "players",
             "extras.json": "records"})
        self.assertEqual({one["subject_kind"] for one in found.values()}, {"vpinfe"})

    def test_a_copy_of_the_device_registry_is_found_in_the_state_folder(self) -> None:
        copy = self.state / "devices.json.vpinfe-20261001T010203Z"
        copy.write_text("{}")

        found = self.listing()

        self.assertEqual([(one["file"], one["kind"]) for one in found],
                         [(copy.name, "devices")])

    def test_putting_back_a_device_registry_keeps_the_running_install(self) -> None:
        live = self.state / "devices.json"
        here = DeviceRegistry(live)
        own = here.ensure_self()
        copy = self.state / "devices.json.vpinfe-20261001T010203Z"
        there = DeviceRegistry(copy)
        other = there.ensure_self()
        there.record("Pppp444444", display_name="cab", address="192.168.1.50")

        self.assertTrue(self.restore(copy).json()["pending"])
        self.assertEqual(pending.apply(self.config), 1)

        restored = DeviceRegistry(live)
        self.assertEqual(restored.self_id(), own)
        self.assertTrue(restored.knows("Pppp444444"))
        self.assertTrue(restored.knows(other))
        self.assertEqual(DeviceRegistry(copy).self_id(), other, "the copy is left as it was")

    def test_a_registry_aimed_at_the_config_folder_is_not_put_back(self) -> None:
        staged = self.config / pending.STAGED_DIR
        staged.mkdir()
        copy = staged / "devices.json"
        DeviceRegistry(copy).ensure_self()
        pending.add(self.config / "devices.json", copy, self.config, copy_id="staged")

        self.assertEqual(pending.apply(self.config), 0)
        self.assertFalse((self.config / "devices.json").exists())

    def test_a_theme_set_aside_is_a_copy_of_that_theme(self) -> None:
        (self.themes / "Classic.previous").mkdir()
        (self.themes / "Classic.previous" / "index.html").write_text("old")
        (self.themes / "Classic").mkdir()

        found = self.listing()

        self.assertEqual([(one["subject_kind"], one["subject_id"], one["reason"], one["gone"],
                           one["size"]) for one in found],
                         [("theme", "Classic", "update", False, 3)])

    def test_a_folder_that_is_not_a_game_has_no_copies_listed(self) -> None:
        stray = self.library.parent / "elsewhere"
        stray.mkdir()
        (stray / "elsewhere.info.vpinfe-20261001T010203Z").write_text("{}")

        self.assertEqual(self.listing(), [])

    def test_the_listing_narrows_to_one_game(self) -> None:
        taxi = self.game("Taxi")
        self.kept(taxi, OLD)
        self.kept(self.game("Bally"), NEWER)

        mine = self.client.get("/backups", params={"subject_kind": "game", "subject_id": "id-Taxi"})

        self.assertEqual([one["subject_id"] for one in mine.json()["copies"]], ["id-Taxi"])

    def test_the_listing_narrows_to_one_theme(self) -> None:
        for name in ("Classic", "Modern"):
            (self.themes / f"{name}.previous").mkdir()
            (self.themes / f"{name}.previous" / "index.html").write_text("old")

        mine = self.client.get("/backups", params={"subject_kind": "theme", "subject_id": "Modern"})

        self.assertEqual([one["subject_id"] for one in mine.json()["copies"]], ["Modern"])

    def test_a_kind_without_an_id_or_an_id_without_a_kind_is_refused(self) -> None:
        for params in ({"subject_kind": "game"}, {"subject_id": "id-Taxi"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/backups", params=params).status_code, 422)

    def test_a_kind_that_is_not_one_is_refused(self) -> None:
        found = self.client.get("/backups", params={"subject_kind": "cab", "subject_id": "x"})

        self.assertEqual(found.status_code, 422)

    def test_newest_comes_first(self) -> None:
        folder = self.game()
        self.kept(folder, OLD)
        self.kept(folder, NEWEST)

        self.assertEqual([one["taken"] for one in self.listing()],
                         ["2026-10-03T01:02:03Z", "2026-10-01T01:02:03Z"])


class LauncherListingTests(BackupsCase):
    def setUp(self) -> None:
        super().setUp()
        self.ini = pathlib.Path(self.tmp.name) / "VPinballX.ini"
        self.ini.write_text("[Player]\nFXAA = 1\n")
        put = put_launcher(self.client, "l1", json={
            "app": "vpx", "display_name": "Main VPX",
            "settings": {"bin_path": "/opt/vpx", "ini_path": str(self.ini)}})
        self.assertEqual(put.status_code, 200, put.text)

    def test_vpxs_first_change_copy_beside_its_ini_is_listed_for_that_launcher(self) -> None:
        first = self.ini.with_name("VPinballX.ini.vpinfe-20261001T010203Z")
        first.write_text("[Player]\nFXAA = 0\n")

        found = self.listing()

        self.assertEqual([(one["subject_kind"], one["subject_id"], one["subject_name"],
                           one["reason"])
                          for one in found],
                         [("launcher", "l1", "Main VPX", "first_change")])

    def test_a_backup_of_the_launcher_is_listed_with_its_note_and_its_file(self) -> None:
        taken = self.client.post("/backups", json={
            "scope": "launcher", "launcher": "l1", "note": "before tuning"})
        self.assertEqual(taken.status_code, 200, taken.text)

        found = self.listing()

        self.assertEqual({(one["subject_kind"], one["subject_id"], one["note"]) for one in found
                          if one["kind"] == "backup"}, {("archive", "", "before tuning")})
        self.assertIn("VPinballX.ini", {one["file"] for one in found})

    def test_the_old_launcher_routes_are_gone(self) -> None:
        for method, path in (("get", "/launchers/l1/config/backups"),
                             ("post", "/launchers/l1/config/backups"),
                             ("post", "/launchers/l1/config/backups/x/restore")):
            with self.subTest(path=path, method=method):
                self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_the_2x_folder_of_settings_copies_belongs_to_the_default_vpx(self) -> None:
        folder = self.config / "backups" / "vpx_ini"
        folder.mkdir(parents=True)
        (folder / "VPinballX-manual-tuning-20261001-010203.ini").write_text("x")
        (folder / "VPinballX-pre-restore-20261002-010203.ini").write_text("x")
        (folder / "notes.txt").write_text("x")

        found = {one["file"]: one for one in self.listing()}

        self.assertEqual(
            {name: (one["reason"], one["note"], one["subject_id"]) for name, one in found.items()},
            {"VPinballX-manual-tuning-20261001-010203.ini": ("yours", "tuning", "l1"),
             "VPinballX-pre-restore-20261002-010203.ini": ("restore", "", "l1")})

    def test_a_copy_beside_the_ini_goes_back_over_it_and_the_ini_is_kept(self) -> None:
        first = self.ini.with_name("VPinballX.ini.vpinfe-20261001T010203Z")
        first.write_text("[Player]\nFXAA = 0\n")

        response = self.restore(first)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.ini.read_text(), "[Player]\nFXAA = 0\n")
        kept = pathlib.Path(response.json()["kept"][0]["path"])
        self.assertEqual(kept.read_text(encoding="utf-8"), "[Player]\nFXAA = 1\n")

    def test_a_launcher_copy_is_not_put_back_while_a_table_plays(self) -> None:
        first = self.ini.with_name("VPinballX.ini.vpinfe-20261001T010203Z")
        first.write_text("x")
        self.playing = True

        response = self.restore(first)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.ini.read_text(), "[Player]\nFXAA = 1\n")

    def test_a_copy_beside_an_ini_two_launchers_share_is_listed_once(self) -> None:
        second = put_launcher(self.client, "l2", json={
            "app": "vpx", "display_name": "Second VPX",
            "settings": {"bin_path": "/opt/vpx", "ini_path": str(self.ini)}})
        self.assertEqual(second.status_code, 200, second.text)
        self.ini.with_name("VPinballX.ini.vpinfe-20261001T010203Z").write_text("x")

        self.assertEqual(len(self.listing()), 1)

    def test_a_copy_beside_the_ini_goes_back_for_a_launcher_renamed_since(self) -> None:
        first = self.ini.with_name("VPinballX.ini.vpinfe-20261001T010203Z")
        first.write_text("[Player]\nFXAA = 0\n")
        copy = self.listing()[0]
        put_launcher(self.client, "l1", json={
            "app": "vpx", "display_name": "Renamed",
            "settings": {"bin_path": "/opt/vpx", "ini_path": str(self.ini)}})

        response = self.client.post(f"/backups/{copy['id']}/restore")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.ini.read_text(), "[Player]\nFXAA = 0\n")

    def test_the_listing_narrows_to_one_launcher(self) -> None:
        self.ini.with_name("VPinballX.ini.vpinfe-20261001T010203Z").write_text("x")
        put_launcher(self.client, "l2", json={
            "app": "vpx", "display_name": "Second VPX",
            "settings": {"bin_path": "/opt/vpx",
                         "ini_path": str(self.ini.with_name("Other.ini"))}})
        self.ini.with_name("Other.ini").write_text("y")
        self.ini.with_name("Other.ini.vpinfe-20261002T010203Z").write_text("y")

        mine = self.client.get("/backups", params={"subject_kind": "launcher", "subject_id": "l2"})

        self.assertEqual([(one["subject_id"], one["file"]) for one in mine.json()["copies"]],
                         [("l2", "Other.ini.vpinfe-20261002T010203Z")])


class RestoreTests(BackupsCase):
    def test_a_games_copy_goes_back_and_what_it_replaced_is_kept(self) -> None:
        folder = self.game()
        old = self.kept(folder, OLD, info={"Info": {"Title": "Taxi (old)"},
                                           "vpinfe": {"schema": 2, "game_id": "other"}})

        response = self.restore(old)

        body = response.json()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(body["pending"])
        now = json.loads((folder / "Taxi.info").read_text())
        self.assertEqual(now["Info"]["Title"], "Taxi (old)")
        self.assertEqual(now["vpinfe"]["game_id"], "id-Taxi")
        self.assertEqual([one["reason"] for one in body["kept"]], ["restore"])
        self.assertTrue(old.exists())

    def test_a_game_copy_that_cannot_be_read_is_refused_and_nothing_is_changed(self) -> None:
        folder = self.game()
        before = (folder / "Taxi.info").read_text()
        broken = pathlib.Path(backup_path(folder / "Taxi.info", OLD, ""))
        broken.write_text("{not json", encoding="utf-8")

        response = self.restore(broken)

        self.assertIn(response.status_code, (400, 409, 422))
        self.assertEqual((folder / "Taxi.info").read_text(), before)
        self.assertEqual(len(list(folder.glob("Taxi.info.vpinfe-*"))), 1)

    def test_putting_back_what_a_restore_kept_undoes_it(self) -> None:
        folder = self.game()
        before = (folder / "Taxi.info").read_text()
        old = self.kept(folder, OLD)

        kept = self.restore(old).json()["kept"][0]
        undone = self.client.post(f"/backups/{kept['id']}/restore")

        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(json.loads((folder / "Taxi.info").read_text()),
                         json.loads(before))

    def test_a_config_folder_copy_waits_for_the_close_and_is_then_put_in(self) -> None:
        live = self.config / "vpinfe.ini"
        live.write_text("live")
        copy = self.config / "vpinfe.ini.vpinfe-20261001T010203Z"
        copy.write_text("older")

        response = self.restore(copy)

        self.assertTrue(response.json()["pending"])
        self.assertEqual(live.read_text(), "live")
        self.assertEqual(self.client.get("/backups").json()["pending"],
                         [response.json()["copy"]["id"]])

        self.assertEqual(pending.apply(self.config), 1)

        self.assertEqual(live.read_text(), "older")
        kept = [p for p in self.config.iterdir() if p.name.endswith("-restore")]
        self.assertEqual([p.read_text() for p in kept], ["live"])
        self.assertEqual(self.client.get("/backups").json()["pending"], [])

    def test_a_waiting_restore_that_is_not_a_copy_of_its_target_is_not_carried_out(self) -> None:
        live = self.config / "vpinfe.ini"
        live.write_text("live")
        stranger = self.config / "players.json.vpinfe-20261001T010203Z"
        stranger.write_text("other")
        pending.add(live, stranger, self.config, copy_id="")

        self.assertEqual(pending.apply(self.config), 0)

        self.assertEqual(live.read_text(), "live")

    def test_a_second_pending_copy_of_the_same_file_replaces_the_first(self) -> None:
        (self.config / "vpinfe.ini").write_text("live")
        first = self.config / "vpinfe.ini.vpinfe-20261001T010203Z"
        second = self.config / "vpinfe.ini.vpinfe-20261002T010203Z"
        first.write_text("one")
        second.write_text("two")

        self.restore(first)
        self.restore(second)
        pending.apply(self.config)

        self.assertEqual((self.config / "vpinfe.ini").read_text(), "two")

    def test_deleting_a_copy_that_is_waiting_cancels_the_wait(self) -> None:
        (self.config / "vpinfe.ini").write_text("live")
        copy = self.config / "vpinfe.ini.vpinfe-20261001T010203Z"
        copy.write_text("older")
        self.restore(copy)

        self.client.delete(f"/backups/{self.id_of(copy)}")

        self.assertEqual(pending.held(self.config), {})

    def test_a_theme_swaps_with_its_previous_and_swapping_again_comes_back(self) -> None:
        for name, text in (("Classic", "new"), ("Classic.previous", "old")):
            (self.themes / name).mkdir()
            (self.themes / name / "index.html").write_text(text)

        first = self.restore(self.themes / "Classic.previous").json()

        self.assertEqual((self.themes / "Classic" / "index.html").read_text(), "old")
        self.assertEqual((self.themes / "Classic.previous" / "index.html").read_text(),
                         "new")
        self.client.post(f"/backups/{first['kept'][0]['id']}/restore")
        self.assertEqual((self.themes / "Classic" / "index.html").read_text(), "new")

    def test_a_previous_theme_with_nothing_beside_it_just_comes_back(self) -> None:
        (self.themes / "Classic.previous").mkdir()

        response = self.restore(self.themes / "Classic.previous")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue((self.themes / "Classic").is_dir())
        self.assertEqual(response.json()["kept"], [])


class DeleteAndForgeryTests(BackupsCase):
    def test_deleting_removes_the_file_and_the_row(self) -> None:
        folder = self.game()
        old = self.kept(folder, OLD)

        response = self.client.delete(f"/backups/{self.id_of(old)}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(old.exists())
        self.assertEqual(self.listing(), [])

    def test_deleting_a_theme_removes_its_folder(self) -> None:
        (self.themes / "Classic.previous").mkdir()
        (self.themes / "Classic.previous" / "index.html").write_text("old")

        self.client.delete(f"/backups/{self.id_of(self.themes / 'Classic.previous')}")

        self.assertFalse((self.themes / "Classic.previous").exists())

    def test_an_id_that_is_not_one_reaches_nothing(self) -> None:
        for bad in ("nope", "", "e30", copies.make_id("game", "")):
            with self.subTest(bad=bad):
                response = self.client.post(f"/backups/{bad or 'x'}/restore")
                self.assertEqual(response.status_code, 404)

    def test_an_id_for_a_file_that_is_not_a_copy_reaches_nothing(self) -> None:
        folder = self.game()
        victim = folder / "Taxi.info"
        forged = copies.make_id(copies.PLACE_GAME, victim)

        self.assertEqual(self.client.delete(f"/backups/{forged}").status_code, 404)
        self.assertTrue(victim.exists())

    def test_an_id_naming_a_path_outside_every_place_reaches_nothing(self) -> None:
        outside = pathlib.Path(self.tmp.name) / "outside.vpinfe-20261001T010203Z"
        outside.write_text("x")
        for place in (copies.PLACE_GAME, copies.PLACE_RECORDS,
                      copies.PLACE_BESIDE, copies.PLACE_VPX_2X, copies.PLACE_THEME):
            with self.subTest(place=place):
                forged = copies.make_id(place, outside)
                self.assertEqual(self.client.delete(f"/backups/{forged}").status_code, 404)
        self.assertTrue(outside.exists())

    def test_an_id_with_dots_in_its_path_reaches_nothing(self) -> None:
        folder = self.game()
        old = self.kept(folder, OLD)
        wandering = str(old.parent / ".." / old.parent.name / old.name)

        for place in (copies.PLACE_GAME, copies.PLACE_THEME):
            with self.subTest(place=place):
                forged = copies.make_id(place, wandering)
                self.assertEqual(self.client.delete(f"/backups/{forged}").status_code, 404)
        self.assertTrue(old.exists())

    def test_a_folder_named_only_previous_is_not_a_theme_copy(self) -> None:
        for name in (".previous", "..previous"):
            (self.themes / name).mkdir()

        self.assertEqual(self.listing(), [])

    def test_a_copy_that_is_gone_says_so(self) -> None:
        folder = self.game()
        old = self.kept(folder, OLD)
        copy_id = self.id_of(old)
        os.unlink(old)

        self.assertEqual(self.client.post(f"/backups/{copy_id}/restore").status_code, 404)


class ChangeFolderTests(BackupsCase):
    def setUp(self) -> None:
        super().setUp()
        (self.config / "tags.json").write_text("{}", encoding="utf-8")

    def test_the_change_says_what_is_in_the_old_folder_and_the_new_one(self) -> None:
        archive.take(archive.SCOPE_RECORDS)
        other = self.config.parent / "other"
        other.mkdir()

        found = self.client.get("/backups/folder/change", params={"to": str(other)}).json()

        self.assertEqual(found, {"old": str(self.config / "backups"), "new": str(other),
                                 "moving": 1, "already": 0, "problem": ""})

    def test_moving_sets_the_folder_and_starts_a_job_that_carries_the_backups(self) -> None:
        taken = archive.take(archive.SCOPE_RECORDS)
        other = self.config.parent / "other"
        other.mkdir()
        saved: list[dict] = []
        store = backup_folder.get_ini_config()

        def put(patch_: dict) -> None:
            saved.append(patch_)
            cfg_set(store, backup_folder.SECTION, backup_folder.FOLDER, str(other))

        with patch.object(config_api, "apply_patch", side_effect=put):
            response = self.client.post("/backups/folder/move", json={"folder": str(other)})

        self.assertEqual(response.status_code, 202)
        job_id = response.json()["id"]
        self.assertEqual(response.headers["Location"], f"/api/v1/jobs/{job_id}")
        self.assertEqual(saved, [{"settings": {"backups": {"folder": str(other)}}}])
        for _ in range(100):
            job = jobs.get(job_id)
            if job is not None and job.state != jobs.RUNNING:
                break
            time.sleep(0.05)
        self.assertEqual(job.state, jobs.DONE)  # type: ignore[union-attr]
        self.assertTrue((other / taken.name).is_file())

    def test_a_new_folder_that_is_not_there_is_refused_and_nothing_is_saved(self) -> None:
        archive.take(archive.SCOPE_RECORDS)

        with patch.object(config_api, "apply_patch") as put:
            response = self.client.post("/backups/folder/move",
                                        json={"folder": str(self.config.parent / "gone")})

        self.assertEqual(response.status_code, 409)
        put.assert_not_called()


if __name__ == "__main__":
    unittest.main()

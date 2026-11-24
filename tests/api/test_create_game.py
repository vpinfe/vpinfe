"""Creating an entry, and putting a game file into one, over the wire.

Both exist because everything else that writes a game needs an id that already exists,
and the only way to get one was to upload a bundle through a browser. An importer
converting a foreign library has the files on a share already.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response

import httpapi
from common.games import game_repository, library_vps_state
from common.games.info_file import GAME_ID_KEY
from common.i18n import t
from tests.support.library import own_locations, write_game
from tests.support.skips import needs_posix_permissions


class CreateGameCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()

        self.store = own_locations(self, self.root / "config")

        # A read allowlist of this test's own. Unpatched it is built from this machine's
        # configuration, so `within_roots` would be answering about a real library.
        # Resolved, the way the real one answers: /var is a symlink on macOS, and a root
        # that has not been through realpath matches nothing under it.
        roots = patch("common.games.media_browse.roots",
                      side_effect=lambda game_dir="": [
                          {"path": str(self.elsewhere.resolve()), "name": "elsewhere",
                           "source": "configured"},
                          {"path": str(self.library.resolve()), "name": "library",
                           "source": "library"}])
        roots.start()
        self.addCleanup(roots.stop)

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.addCleanup(game_repository.all_games, True)

    def put_location(self, path: Path, location_id: str) -> None:
        """Held, and where new games go."""
        self.client.put(f"/config/locations/{location_id}", json={"path": str(path)})
        self.client.put("/config", json={
            "settings": {"updates": {"new_games_location": location_id}}})

    def add_location(self, path: Path, location_id: str = "one") -> None:
        self.put_location(path, location_id)
        game_repository.all_games(reload=True)

    def create(self, name: str, **body) -> Response:
        return self.client.post("/games", json={"name": name, **body})


class CreateTests(CreateGameCase):
    def test_a_created_game_has_a_folder_a_record_and_an_id(self) -> None:
        self.add_location(self.library)

        made = self.create("Attack from Mars (Bally 1995)")

        self.assertEqual(made.status_code, 201, made.text)
        folder = self.library / "Attack from Mars (Bally 1995)"
        self.assertTrue(folder.is_dir())
        record = folder / "Attack from Mars (Bally 1995).info"
        self.assertTrue(record.is_file())
        held = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(held["vpinfe"][GAME_ID_KEY], made.json()["id"])

    def test_it_is_in_the_library_immediately(self) -> None:
        """A caller that just made one has to be able to address it."""
        self.add_location(self.library)
        made = self.create("Medieval Madness")

        found = self.client.get(f"/games/{made.json()['id']}")

        self.assertEqual(found.status_code, 200, found.text)

    def test_it_lands_in_the_location_new_games_go_to(self) -> None:
        second = self.root / "second"
        second.mkdir()
        self.add_location(self.library, "one")
        self.add_location(second, "two")

        self.create("Taxi")

        self.assertTrue((second / "Taxi").is_dir())
        self.assertFalse((self.library / "Taxi").is_dir())

    def test_a_named_location_overrides_where_it_goes(self) -> None:
        second = self.root / "second"
        second.mkdir()
        self.add_location(self.library, "one")
        self.add_location(second, "two")

        self.create("Taxi", location="one")

        self.assertTrue((self.library / "Taxi").is_dir())

    def test_it_is_found_in_a_location_that_already_holds_games(self) -> None:
        """The folder is looked up canonically rather than by spelling.

        Re-reading one folder resolves the path it is given, so the game that has just
        been created carries the real path while a game found by a scan carries the
        location's own spelling. On macOS every path under /var differs between those
        two, and matching the spelling meant a game could be created and then not found.

        An empty library never sees it: re-reading one folder is skipped where the
        location has loaded nothing, and the whole-library fallback keeps the spelling.
        """
        write_game(self.library, "Already Here")
        self.add_location(self.library)

        made = self.create("Taxi")

        self.assertEqual(made.status_code, 201, made.text)
        self.assertEqual(self.client.get(f"/games/{made.json()['id']}").status_code, 200)

    def test_a_folder_that_is_already_there_is_a_conflict(self) -> None:
        self.add_location(self.library)
        (self.library / "Taxi").mkdir()

        found = self.create("Taxi")

        self.assertEqual(found.status_code, 409)
        self.assertEqual((found.json()["error"]["code"], found.json()["error"]["details"]),
                         ("conflict", {"path": str(self.library / "Taxi")}))

    def test_a_name_that_is_only_reserved_characters_is_refused(self) -> None:
        self.add_location(self.library)

        found = self.create("///")

        self.assertEqual(found.status_code, 400)
        self.assertEqual(found.json()["error"]["message"], t("error.games.name_needed"))

    def test_with_nowhere_to_write_it_says_so_rather_than_failing_late(self) -> None:
        found = self.create("Taxi")

        self.assertEqual(found.status_code, 400)
        self.assertEqual(found.json()["error"]["message"],
                         t("error.library_folders.nowhere_to_create"))

    @needs_posix_permissions
    def test_a_refusal_names_the_locations_that_would_have_worked(self) -> None:
        """The answer somebody needs is not only that it failed."""
        self.add_location(self.library, "one")
        read_only = self.root / "readonly"
        read_only.mkdir()
        self.put_location(read_only, "two")
        os.chmod(read_only, 0o500)
        self.addCleanup(os.chmod, read_only, 0o700)

        found = self.create("Taxi")

        self.assertEqual(found.status_code, 400)
        named = [one["location_id"]
                 for one in found.json()["error"]["details"]["alternatives"]]
        self.assertIn("one", named)


class ImportTableTests(CreateGameCase):
    def _game(self, name: str = "Taxi") -> str:
        self.add_location(self.library)
        return self.create(name).json()["id"]

    def _source(self, name: str = "Taxi (Williams 1988).vpx") -> Path:
        path = self.elsewhere / name
        path.write_bytes(b"not really a table")
        return path

    def test_a_file_is_copied_in_and_becomes_a_table(self) -> None:
        game_id = self._game()
        source = self._source()

        made = self.client.post(f"/games/{game_id}/tables/import",
                                json={"path": str(source)})

        self.assertEqual(made.status_code, 201, made.text)
        self.assertTrue((self.library / "Taxi" / source.name).is_file())
        self.assertTrue(source.is_file(), "a copy, not a move")
        self.assertTrue(made.json()["id"])

    def test_an_imported_table_settles_the_order_of_its_game(self) -> None:
        game_id = self._game()

        with patch("common.games.backglass_order.settle") as settle:
            self.client.post(f"/games/{game_id}/tables/import",
                             json={"path": str(self._source())})

        settle.assert_called_once_with(game_id)

    def test_the_table_can_be_addressed_by_the_id_it_came_back_with(self) -> None:
        """A minting pass that runs at startup answers too late to be told to."""
        game_id = self._game()
        table_id = self.client.post(f"/games/{game_id}/tables/import",
                                    json={"path": str(self._source())}).json()["id"]

        listed = self.client.get(f"/games/{game_id}/tables").json()["tables"]

        self.assertEqual([one["id"] for one in listed], [table_id])

    def test_a_file_outside_the_allowlist_is_refused(self) -> None:
        game_id = self._game()
        outside = self.root / "outside.vpx"
        outside.write_bytes(b"not really a table")

        found = self.client.post(f"/games/{game_id}/tables/import",
                                 json={"path": str(outside)})

        self.assertEqual(found.status_code, 400)
        self.assertFalse((self.library / "Taxi" / "outside.vpx").exists())

    def test_the_pick_of_a_browser_on_this_device_is_copied_from_anywhere(self) -> None:
        game_id = self._game()
        outside = self.root / "outside.vpx"
        outside.write_bytes(b"not really a table")

        made = self.client.post(f"/folders/games/{game_id}/tables",
                                json={"path": str(outside)})

        self.assertEqual(made.status_code, 201, made.text)
        self.assertTrue((self.library / "Taxi" / "outside.vpx").is_file())
        self.assertTrue(outside.is_file(), "a copy, not a move")

    def test_the_route_for_this_device_still_wants_a_file_a_program_plays(self) -> None:
        game_id = self._game()
        notes = self._source("notes.txt")

        found = self.client.post(f"/folders/games/{game_id}/tables", json={"path": str(notes)})

        self.assertEqual(found.status_code, 400)
        self.assertFalse((self.library / "Taxi" / "notes.txt").exists())

    def test_the_route_for_this_device_refuses_a_network_caller(self) -> None:
        game_id = self._game()

        with patch("httpapi.folders.caller_is_local", return_value=False):
            found = self.client.post(f"/folders/games/{game_id}/tables",
                                     json={"path": str(self._source())})

        self.assertEqual(found.status_code, 403)

    def test_a_file_nothing_plays_is_refused(self) -> None:
        game_id = self._game()
        source = self._source("notes.txt")

        found = self.client.post(f"/games/{game_id}/tables/import",
                                 json={"path": str(source)})

        self.assertEqual(found.status_code, 400)
        self.assertIn("notes.txt", found.json()["error"]["message"])

    def test_a_second_file_by_the_same_name_is_a_conflict(self) -> None:
        game_id = self._game()
        source = self._source()
        self.client.post(f"/games/{game_id}/tables/import", json={"path": str(source)})

        again = self.client.post(f"/games/{game_id}/tables/import",
                                 json={"path": str(source)})

        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["error"]["details"], {"filename": source.name})


    def test_a_file_the_folder_lost_comes_back_under_its_own_table(self) -> None:
        game_id = self._game()
        source = self._source()
        first = self.client.post(f"/games/{game_id}/tables/import",
                                 json={"path": str(source)}).json()["id"]
        (self.library / "Taxi" / source.name).unlink()

        again = self.client.post(f"/games/{game_id}/tables/import",
                                 json={"path": str(source)})

        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.json()["id"], first)
        self.assertTrue((self.library / "Taxi" / source.name).is_file())

class DetailsTests(CreateGameCase):
    def _game(self, name: str = "Taxi") -> str:
        self.add_location(self.library)
        return self.create(name).json()["id"]

    def _put(self, game_id: str, **body):
        return self.client.put(f"/games/{game_id}/details", json=body)

    def test_a_game_no_catalog_matched_can_still_say_what_it_is(self) -> None:
        game_id = self._game()

        found = self._put(game_id, title="Taxi", manufacturer="Williams",
                          year="1988", type="SS", themes=["Cars", "City"])

        self.assertEqual(found.status_code, 200, found.text)
        row = found.json()
        self.assertEqual(row["manufacturer"], "Williams")
        self.assertEqual(row["year"], "1988")
        self.assertEqual(row["themes"], ["Cars", "City"])

    def test_it_lands_in_our_own_section_not_in_info(self) -> None:
        game_id = self._game()
        self._put(game_id, manufacturer="Williams")

        held = json.loads((self.library / "Taxi" / "Taxi.info")
                          .read_text(encoding="utf-8"))

        self.assertEqual(held["vpinfe"]["alt_manufacturer"], "Williams")
        self.assertEqual(held["Info"].get("Manufacturer", ""), "")

    def test_clearing_a_field_drops_the_answer(self) -> None:
        game_id = self._game()
        self._put(game_id, manufacturer="Williams")

        self._put(game_id, manufacturer="")

        held = json.loads((self.library / "Taxi" / "Taxi.info")
                          .read_text(encoding="utf-8"))
        self.assertNotIn("alt_manufacturer", held["vpinfe"])

    def test_a_field_left_out_is_left_alone(self) -> None:
        """An importer filling in a year should not have to restate a title it never
        knew, which a whole-value write would make it do."""
        game_id = self._game()
        self._put(game_id, title="Taxi", manufacturer="Williams")

        self._put(game_id, year="1988")

        row = self.client.get(f"/games/{game_id}").json()
        self.assertEqual(row["manufacturer"], "Williams")
        self.assertEqual(row["year"], "1988")

    def test_a_field_sent_empty_is_cleared(self) -> None:
        """Left alone and cleared have to be different, or nothing can undo a typo."""
        game_id = self._game()
        self._put(game_id, manufacturer="Willaims")

        self._put(game_id, manufacturer="")

        self.assertEqual(self.client.get(f"/games/{game_id}").json()["manufacturer"], "")

    def test_a_game_that_is_not_here_is_a_404(self) -> None:
        self.add_location(self.library)

        self.assertEqual(self._put("nosuchgame", title="x").status_code, 404)


class FromCatalogTests(CreateGameCase):
    ENTRY = {"id": "vps-taxi", "name": "Taxi", "manufacturer": "Williams", "year": 1988,
             "type": "SS", "theme": ["Cars"]}

    def setUp(self) -> None:
        super().setUp()
        catalog = patch("common.games.game_service.load_vpsdb", return_value=[self.ENTRY])
        catalog.start()
        self.addCleanup(catalog.stop)
        self.fill = patch("common.games.media_fill.request")
        self.requested = self.fill.start()
        self.addCleanup(self.fill.stop)
        self.add_location(self.library)
        self.rollup = self.root / "vps-rollup.json"
        stored = patch("common.games.library_vps_state.ROLLUP_PATH", self.rollup)
        stored.start()
        self.addCleanup(stored.stop)

    def add(self, vps_id: str = "vps-taxi", **body):
        return self.client.post("/games/from_vps", json={"vps_id": vps_id, **body})

    def record(self) -> dict:
        return json.loads((self.library / "Taxi (Williams 1988)" / "Taxi (Williams 1988).info")
                          .read_text(encoding="utf-8"))

    def test_the_game_is_named_as_an_import_names_it_and_has_no_table(self) -> None:
        made = self.add()

        self.assertEqual(made.status_code, 201, made.text)
        self.assertEqual(made.json()["table_count"], 0)
        self.assertEqual([one.name for one in
                          (self.library / "Taxi (Williams 1988)").iterdir()],
                         ["Taxi (Williams 1988).info"])

    def test_the_entry_is_what_the_catalog_said_and_not_a_person_overriding_it(self) -> None:
        self.add()

        held = self.record()

        self.assertEqual(held["Info"]["VPSId"], "vps-taxi")
        self.assertEqual(held["vpinfe"]["alt_vpsid"], "")

    def test_the_match_is_recorded_as_made_on_import(self) -> None:
        made = self.add()

        self.assertEqual(made.json()["vps_matched_by"], "import")
        self.assertEqual(made.json()["vps_id"], "vps-taxi")

    def test_media_is_requested_for_the_new_folder_and_nothing_is_downloaded(self) -> None:
        self.add()

        self.requested.assert_called_once_with([str(self.library / "Taxi (Williams 1988)")])

    def test_a_folder_that_is_already_there_is_a_conflict_and_is_left_alone(self) -> None:
        (self.library / "Taxi (Williams 1988)").mkdir()

        found = self.add()

        self.assertEqual(found.status_code, 409)
        self.assertEqual(list((self.library / "Taxi (Williams 1988)").iterdir()), [])
        self.requested.assert_not_called()

    def test_a_match_that_fails_takes_the_new_folder_back_so_a_retry_can_succeed(self) -> None:
        with patch("common.games.auto_match.adopt_guess", side_effect=OSError("disk")):
            failed = self.add()

        self.assertEqual(failed.status_code, 409)
        self.assertFalse((self.library / "Taxi (Williams 1988)").exists())
        self.requested.assert_not_called()
        self.assertEqual(self.client.get("/games").json()["total"], 0)
        self.assertEqual(self.add().status_code, 201)

    def test_a_counted_library_reads_the_new_game_as_in_it_before_the_next_count(self) -> None:
        self.rollup.write_text(json.dumps({"schema": library_vps_state.SCHEMA,
                                           "computed": "2026-10-01T00:00:00Z",
                                           "entries": {}}))

        with patch("common.games.library_vps_state.load_vpsdb", return_value=[self.ENTRY]):
            self.add()
            found = self.client.get("/library/discovery").json()

        rows = {row["vps_id"]: row for row in found["rows"]}
        self.assertTrue(rows["vps-taxi"]["in_library"])
        self.assertEqual(rows["vps-taxi"]["held"], [])

    def test_a_library_never_counted_stays_never_counted(self) -> None:
        self.add()

        self.assertFalse(self.rollup.exists())

    def test_an_entry_the_catalog_does_not_hold_is_a_404(self) -> None:
        found = self.add("nope")

        self.assertEqual(found.status_code, 404)
        self.assertEqual(found.json()["error"]["message"], t("error.games.unknown_entry"))
        self.assertEqual(list(self.library.iterdir()), [])

    def test_a_named_location_overrides_where_it_goes(self) -> None:
        second = self.root / "second"
        second.mkdir()
        self.add_location(second, "two")

        self.add(location="one")

        self.assertTrue((self.library / "Taxi (Williams 1988)").is_dir())


if __name__ == "__main__":
    unittest.main()

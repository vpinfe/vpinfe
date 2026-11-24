"""Locations over the wire.

The reachable and writable flags come back on every read rather than being stored, which
is the part worth pinning: a stored answer is wrong the moment a mount drops, and a late
mount is the case this whole resource exists to report well.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common import mounts
from common.config import path_state
from common.games import library_folders
from tests.support.hung_folder import never_answering
from tests.support.library import own_locations
from tests.support.skips import needs_posix_permissions

NAS = mounts.Origin(mounts.NFS, "nas.lan", "/export/pinball", "/mnt/nas")


def _client() -> TestClient:
    return TestClient(httpapi.create_api_app(), raise_server_exceptions=False)


class LocationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = own_locations(self, self.root / "config")
        self.client = _client()

    def _folder(self, name: str) -> str:
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        return str(folder)

    def _put(self, location_id: str, path: str, **body):
        return self.client.put(f"/config/locations/{location_id}",
                               json={"path": path, **body})

    def _choose(self, location_id: str):
        return self.client.put("/config", json={
            "settings": {"updates": {"new_games_location": location_id}}})

    def test_an_install_with_none_answers_an_empty_list(self) -> None:
        body = self.client.get("/locations").json()

        self.assertEqual(body["locations"], [])
        self.assertEqual(body["write_to"], "")

    def test_a_location_comes_back_with_what_the_disk_says(self) -> None:
        self._put("one", self._folder("share"))

        row = self.client.get("/locations").json()["locations"][0]

        self.assertTrue(row["reachable"])
        self.assertTrue(row["writable"])
        self.assertEqual(row["reason"], "")

    def test_an_unreachable_location_says_why(self) -> None:
        """"Unavailable" on its own leaves a person nothing to act on."""
        self._put("gone", str(self.root / "never-mounted"))

        row = self.client.get("/locations").json()["locations"][0]

        self.assertFalse(row["reachable"])
        self.assertTrue(row["reason"])

    @needs_posix_permissions
    def test_a_read_only_location_is_reachable_and_not_writable(self) -> None:
        path = self._folder("readonly")
        self._put("ro", path)
        os.chmod(path, 0o500)
        self.addCleanup(os.chmod, path, 0o700)

        row = self.client.get("/locations").json()["locations"][0]

        self.assertTrue(row["reachable"])
        self.assertFalse(row["writable"])

    def test_the_same_folder_under_a_new_id_replaces_the_row(self) -> None:
        """Two rows for one place would each report their own state."""
        path = self._folder("share")
        self._put("first", path)
        refused = self._put("second", path)

        self.assertEqual(refused.status_code, 400)
        self.assertEqual([row["location_id"] for row
                          in self.client.get("/locations").json()["locations"]], ["first"])

    def test_a_location_needs_a_path(self) -> None:
        self.assertEqual(self._put("one", "  ").status_code, 400)

    def test_a_relative_path_is_refused(self) -> None:
        """It would name a different folder depending on where the install started."""
        response = self._put("one", "testbed/tables")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/locations").json()["locations"], [])

    def test_a_new_path_under_a_held_id_changes_that_row(self) -> None:
        self._put("one", self._folder("before"))
        self._put("two", self._folder("other"))
        after = self._folder("after")

        self._put("one", after)

        held = self.client.get("/locations").json()["locations"]
        self.assertEqual([(row["location_id"], row["path"]) for row in held],
                         [("one", after), ("two", str(self.root / "other"))])

    def test_a_held_id_moved_onto_another_row_names_that_row(self) -> None:
        self._put("one", self._folder("before"))
        taken = self._folder("taken")
        self._put("two", taken)

        response = self._put("one", taken)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.client.get("/locations").json()["locations"]), 2)

    def test_a_kind_this_build_does_not_know_is_refused(self) -> None:
        response = self._put("one", self._folder("share"), kind="mounted-elsewhere")

        self.assertEqual(response.status_code, 400)
        self.assertIn("root", response.json()["error"]["message"])

    def test_the_write_target_is_named_rather_than_left_to_be_derived(self) -> None:
        self._put("one", self._folder("share"))
        self._put("two", self._folder("other"))
        self._choose("two")

        body = self.client.get("/locations").json()

        self.assertEqual(body["write_to"], "two")
        self.assertEqual([r["location_id"] for r in body["locations"] if r["write_to"]],
                         ["two"])

    def test_a_chosen_folder_that_has_gone_away_is_still_the_one_named(self) -> None:
        """`destination()` refuses rather than falling back, so the listing names the
        folder it refuses and not the one a fallback would have picked."""
        away = str(self.root / "never-mounted")
        self._put("one", self._folder("share"))
        self._put("two", away)
        self._choose("two")

        body = self.client.get("/locations").json()

        self.assertEqual(body["write_to"], "two")
        self.assertEqual({row["location_id"]: row["write_to"] for row in body["locations"]},
                         {"one": False, "two": True})

    def test_with_nothing_chosen_the_first_writable_folder_is_named(self) -> None:
        self._put("gone", str(self.root / "never-mounted"))
        self._put("one", self._folder("share"))

        self.assertEqual(self.client.get("/locations").json()["write_to"], "one")

    def test_a_folder_with_no_games_counts_none(self) -> None:
        self._put("one", self._folder("share"))

        self.assertEqual(self.client.get("/locations").json()["locations"][0]["games"], 0)

    def test_a_chosen_location_that_was_removed_falls_back_to_the_first_writable(self) -> None:
        self._put("one", self._folder("share"))
        self._put("two", self._folder("other"))
        self._choose("two")

        self.client.delete("/config/locations/two")

        self.assertEqual(self.client.get("/locations").json()["write_to"], "one")

    def test_forgetting_one_that_is_not_there_is_refused(self) -> None:
        self.assertEqual(self.client.delete("/config/locations/nothing").status_code, 404)

    def test_forgetting_one_removes_it(self) -> None:
        self._put("one", self._folder("share"))

        self.assertEqual(self.client.delete("/config/locations/one").status_code, 204)
        self.assertEqual(self.client.get("/locations").json()["locations"], [])

    def test_the_listing_says_whether_an_import_asks_where_a_new_game_goes(self) -> None:
        with patch("common.games.library_folder_ops._asks", return_value=False):
            body = self.client.get("/locations").json()

        self.assertIs(body["ask_where_new_games_go"], False)

    def test_a_folder_on_this_device_says_so(self) -> None:
        self._put("one", self._folder("share"))

        row = self.client.get("/locations").json()["locations"][0]

        self.assertEqual((row["where"], row["origin"]), ("local", None))

    def test_a_folder_on_a_share_names_it(self) -> None:
        self._put("one", self._folder("share"))
        with patch.object(mounts, "where", return_value=mounts.Where(NAS)):
            row = self.client.get("/locations").json()["locations"][0]

        self.assertEqual(row["where"], "system")
        self.assertEqual(row["origin"], {**NAS.as_dict(), "source": NAS.source})

    def test_a_share_that_is_not_answering_is_still_named(self) -> None:
        folder = self._folder("share")
        self.store.put(library_folders.Location("one", folder))
        self.store.record_origins({"one": NAS})
        with patch.object(path_state, "PROBE_SECONDS", 0.2), \
                patch.object(path_state, "MOUNT_SECONDS", 0.2), never_answering(folder):
            row = self.client.get("/locations").json()["locations"][0]

        self.assertEqual(row["state"], path_state.NOT_ANSWERING)
        self.assertEqual(row["origin"]["server"], "nas.lan")


if __name__ == "__main__":
    unittest.main()

"""Which location outranks which, and what happens to the folders that lose.

A game folder carries its id, so one library reached through two locations holds every
id twice. Nothing is written to settle that: the higher location answers, the rest are
reported, and the only write is the one somebody asks for.
"""

from __future__ import annotations

import unittest
from pathlib import PurePath
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import game_identity, library_folders
from tests.support.library import TempTree, fake_game, own_locations, write_game

# Lowercase `vpinfe` is where the id lives; `VPinFE` is the legacy section and does not
# carry it.
INFO = {"Info": {"Name": "Attack from Mars"}, "vpinfe": {"game_id": "gid0000001"}}


class _Priority(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.here = self.root / "tables"
        self.there = self.root / "backup"
        for folder in (self.here, self.there):
            folder.mkdir(parents=True, exist_ok=True)
            write_game(folder, "Attack from Mars", info=INFO, vpx=False)

        self.store = own_locations(self, self.root / "config")
        self.store.save([
            library_folders.Location(location_id="loc1", path=str(self.here), kind="root"),
            library_folders.Location(location_id="loc2", path=str(self.there), kind="root"),
        ])
        patch.object(library_folders, "configured",
                     side_effect=lambda: self.store.locations()).start()
        self.addCleanup(patch.stopall)

        self.games = [
            fake_game(self.here / "Attack from Mars", "Attack from Mars", meta=INFO),
            fake_game(self.there / "Attack from Mars", "Attack from Mars", meta=INFO),
        ]
        self.games[0].location_id = "loc1"
        self.games[1].location_id = "loc2"
        patch("common.games.game_repository.all_games",
              return_value=self.games).start()
        patch("httpapi.locations.all_games", create=True,
              return_value=self.games).start()

        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def _reorder(self, *order):
        held = {row["id"]: row for row in self.client.get("/config/locations").json()}
        return self.client.put("/config/locations", json=[held[one] for one in order])

    def _rows(self):
        got = self.client.get("/locations")
        self.assertEqual(got.status_code, 200, got.text)
        return {row["location_id"]: row for row in got.json()["locations"]}


class ReportTests(_Priority):
    def test_the_lower_location_reports_what_it_is_holding(self) -> None:
        rows = self._rows()

        self.assertEqual(rows["loc1"]["shadowed"], 0)
        self.assertEqual(rows["loc2"]["shadowed"], 1)

    def test_each_folder_counts_the_games_in_it_the_shadowed_one_too(self) -> None:
        rows = self._rows()

        self.assertEqual(rows["loc1"]["games"], 1)
        self.assertEqual(rows["loc2"]["games"], 1)

    def test_a_folder_changed_in_place_answers_with_its_own_counts(self) -> None:
        self.client.put("/config/locations/loc2", json={"path": str(self.there)})

        row = self._rows()["loc2"]

        self.assertEqual((row["games"], row["shadowed"]), (1, 1))

    def test_the_list_says_both_sides(self) -> None:
        """The question somebody brings here is which copy they meant to keep, and that
        cannot be answered by naming only one of them."""
        got = self.client.get("/locations/loc2/shadowed")

        one = got.json()["shadowed"][0]
        self.assertEqual(one["game_id"], "gid0000001")
        # Compared as path parts: the separator is the host's, and a string tail
        # hard-codes one platform's.
        self.assertEqual(PurePath(one["path"]).parts[-2:], ("backup", "Attack from Mars"))
        self.assertEqual(PurePath(one["used_path"]).parts[-2:],
                         ("tables", "Attack from Mars"))
        self.assertEqual(one["used_location_id"], "loc1")

    def test_the_list_is_answered_fresh_rather_than_stored(self) -> None:
        """Whether a folder is shadowed depends on the other locations, so a stored
        answer is wrong the moment one is added, removed or reordered."""
        self._reorder("loc2", "loc1")

        rows = self._rows()

        self.assertEqual(rows["loc2"]["shadowed"], 0)
        self.assertEqual(rows["loc1"]["shadowed"], 1)


class OrderTests(_Priority):
    def test_reordering_changes_which_folder_answers(self) -> None:
        self._reorder("loc2", "loc1")

        found = game_identity.resolve_ids(self.games)

        self.assertIs(found.by_id["gid0000001"], self.games[1])

    def test_the_list_order_is_the_priority(self) -> None:
        self._reorder("loc2", "loc1")

        self.assertEqual([one.location_id for one in self.store.locations()],
                         ["loc2", "loc1"])


class NothingIsWrittenTests(_Priority):
    def test_reading_the_library_never_settles_a_clash(self) -> None:
        """The behaviour this replaced: a read used to re-mint the loser, which is a
        write to somebody's file to decide something only they can."""
        with patch.object(game_identity, "ensure_id") as wrote:
            self._rows()
            self.client.get("/locations/loc2/shadowed")

        wrote.assert_not_called()

    def test_adopting_one_is_the_write_and_it_is_asked_for(self) -> None:
        with patch.object(game_identity, "ensure_id",
                          return_value="gid0000099") as wrote:
            got = self.client.post(
                "/locations/loc2/shadowed/adopt",
                json={"path": str(self.there / "Attack from Mars")})

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["game_id"], "gid0000099")
        self.assertTrue(wrote.call_args.kwargs["force_new"])

    def test_adopting_something_that_is_not_shadowed_is_refused(self) -> None:
        said = self.client.post("/locations/loc2/shadowed/adopt",
                                json={"path": str(self.here / "Attack from Mars")})

        self.assertEqual(said.status_code, 404)

    def test_and_so_is_a_location_this_install_does_not_have(self) -> None:
        self.assertEqual(
            self.client.get("/locations/nope/shadowed").status_code, 404)


if __name__ == "__main__":
    unittest.main()

"""Media over HTTP, at both levels: what the folder shares and what one build owns.

`/games/{id}/media` used to resolve against whichever build launches and call the answer
the game's, so a folder's two tables were indistinguishable through the API. It now means
what it says - the shared art - and each table answers for itself under its own path.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "TwoBuilds1"
FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
        "tbl0000002": {"id": "tbl0000002", "filename": VR},
    },
}


class TableMediaTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(
            self.root, FOLDER, info=INFO, vpx=False,
            files={DESKTOP: b"vpx", VR: b"vpx"},
            # A wheel the folder shares, and a playfield the VR build alone is named for.
            medias={"wheel.png": b"\x89PNG",
                    f"(Playfield) {FOLDER} - VR.png": b"\x89PNG"})
        game = fake_game(folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _media(self, table_id=None):
        path = (f"/games/{GAME_ID}/tables/{table_id}/media" if table_id
                else f"/games/{GAME_ID}/media")
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["media"]

    def test_the_shared_view_skips_a_file_named_for_one_build(self) -> None:
        media = self._media()

        self.assertFalse(media["playfield"]["present"])
        self.assertTrue(media["wheel"]["present"])
        self.assertEqual(media["wheel"]["via"], "default")

    def test_the_build_it_is_named_for_reports_it_as_its_own(self) -> None:
        media = self._media("tbl0000002")

        self.assertTrue(media["playfield"]["present"])
        self.assertEqual(media["playfield"]["via"], "table")
        self.assertEqual(media["playfield"]["file"], f"(Playfield) {FOLDER} - VR.png")

    def test_the_other_build_does_not_see_it(self) -> None:
        self.assertFalse(self._media("tbl0000001")["playfield"]["present"])

    def test_both_builds_still_share_the_folder_s_wheel(self) -> None:
        for table_id in ("tbl0000001", "tbl0000002"):
            self.assertEqual(self._media(table_id)["wheel"]["via"], "default")

    def test_a_table_link_addresses_that_table(self) -> None:
        link = self._media("tbl0000002")["playfield"]["links"]["self"]

        self.assertEqual(
            link, f"/api/v1/games/{GAME_ID}/tables/tbl0000002/media/playfield")

    def test_the_file_route_serves_the_build_s_own_art(self) -> None:
        response = self.client.get(
            f"/games/{GAME_ID}/tables/tbl0000002/media/playfield")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\x89PNG")

    def test_the_file_route_is_a_404_for_the_build_without_it(self) -> None:
        response = self.client.get(
            f"/games/{GAME_ID}/tables/tbl0000001/media/playfield")

        self.assertEqual(response.status_code, 404)

    def test_a_table_that_is_not_this_game_s_is_a_404(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/tables/tbl0000009/media")

        self.assertEqual(response.status_code, 404)

    def _shared(self):
        response = self.client.get("/media/shared")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["media"]

    def test_it_lists_what_the_full_listing_calls_shared(self) -> None:
        full = self.client.get("/media").json()["media"]
        want = {(row["game_id"], row["kind"], row["page"]) for row in full if not row["table"]}

        got = {(row["game_id"], row["kind"], row["page"]) for row in self._shared()}

        self.assertEqual(got, want)

    def test_a_row_carries_only_the_fields_a_count_reads(self) -> None:
        row = next(row for row in self._shared() if row["kind"] == "wheel")

        self.assertEqual(set(row), {"game_id", "kind", "page", "present", "file", "path",
                                    "via", "origin", "matched_to", "version"})
        self.assertTrue(row["present"])
        self.assertEqual(row["via"], "default")

    def test_a_file_named_for_one_build_is_not_listed(self) -> None:
        self.assertFalse(any(row["file"] == f"(Playfield) {FOLDER} - VR.png"
                             for row in self._shared()))


class WheelSetsTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                            files={DESKTOP: b"vpx"},
                            medias={"wheel.png": b"\x89PNG",
                                    "wheels/Classic/wheel.png": b"\x89PNG"})
        game = fake_game(folder, FOLDER, meta=INFO)
        patcher = patch("common.games.media_service.all_games", return_value=[game])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_sets_are_the_folders_under_wheels_and_the_logo_set(self) -> None:
        response = self.client.get("/media/wheel-sets")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"sets": ["Classic", "logo"]})


if __name__ == "__main__":
    unittest.main()

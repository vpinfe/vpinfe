"""A flyer's pages through the API: read, add, replace and remove by number.

`?page=n` is the same address with a page on it, and page 1 is no page at all. A page
past page 1 follows page 1 - same tier, same stem - because it is a page of that flyer
and not a flyer of its own.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Paged000001"
FOLDER = "Doom (Bally 1994)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

INFO = {
    "Info": {"Name": "Doom"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
               "tbl0000002": {"id": "tbl0000002", "filename": VR}},
}


def _png(marker: bytes = b"x") -> dict:
    return {"file": ("art.png", io.BytesIO(b"\x89PNG" + marker), "image/png")}


class MediaPagesTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(
            self.root, FOLDER, info=INFO, vpx=False, files={DESKTOP: b"vpx", VR: b"vpx"},
            medias={f"(Flyer) {FOLDER}.png": b"\x89PNGone",
                    f"(Flyer 2) {FOLDER}.png": b"\x89PNGtwo",
                    f"(Flyer 3) {FOLDER}.png": b"\x89PNGthree"})
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _medias(self) -> set[str]:
        return {p.name for p in (self.folder / "medias").iterdir()}

    def test_the_list_counts_the_pages_a_kind_has(self) -> None:
        media = self.client.get(f"/games/{GAME_ID}/media").json()["media"]

        self.assertEqual(media["flyer"]["pages"], 3)
        self.assertEqual(media["playfield"]["pages"], 0)

    def test_the_library_list_has_a_row_for_each_later_page(self) -> None:
        rows = self.client.get("/media", params={"game": GAME_ID, "kind": "flyer",
                                                 "limit": 100}).json()["media"]
        shared = [r for r in rows if not r["table"]]

        self.assertEqual([(r["page"], r["label"], r["named"]) for r in shared],
                         [(1, "Flyer", "Flyer"), (2, "Flyer", "Flyer, page 2"),
                          (3, "Flyer", "Flyer, page 3")])
        self.assertEqual([r["id"].rsplit(":", 1)[-1] for r in shared[1:]], ["2", "3"])
        self.assertEqual([r["file"] for r in shared[1:]],
                         [f"(Flyer 2) {FOLDER}.png", f"(Flyer 3) {FOLDER}.png"])

    def test_a_page_is_its_own_file(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/media/flyer", params={"page": 3}).content

        self.assertEqual(body, b"\x89PNGthree")

    def test_page_one_is_the_kind_without_a_page(self) -> None:
        plain = self.client.get(f"/games/{GAME_ID}/media/flyer").content
        first = self.client.get(f"/games/{GAME_ID}/media/flyer", params={"page": 1}).content

        self.assertEqual(plain, first)

    def test_a_page_past_the_last_is_not_found(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/media/flyer", params={"page": 4})

        self.assertEqual(response.status_code, 404)

    def test_the_detail_of_a_page_names_it_and_counts_the_rest(self) -> None:
        detail = self.client.get(f"/games/{GAME_ID}/media/flyer/detail",
                                 params={"page": 2}).json()

        self.assertEqual((detail["page"], detail["pages"]), (2, 3))
        self.assertEqual(detail["file"], f"(Flyer 2) {FOLDER}.png")
        self.assertEqual(detail["after_remove"], f"(Flyer 3) {FOLDER}.png")
        self.assertTrue(detail["links"]["self"].endswith("/flyer?page=2"))

    def test_the_next_page_is_added_at_the_tier_of_page_one(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/media/flyer", files=_png(),
                                   params={"page": 4})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["written"], f"(Flyer 4) {FOLDER}.png")
        self.assertEqual(response.json()["media"]["flyer"]["pages"], 4)

    def test_a_page_replaces_the_one_it_names(self) -> None:
        self.client.put(f"/games/{GAME_ID}/media/flyer", files=_png(b"new"), params={"page": 2})

        self.assertEqual((self.folder / "medias" / f"(Flyer 2) {FOLDER}.png").read_bytes(),
                         b"\x89PNGnew")
        self.assertEqual(len(self._medias()), 3)

    def test_a_page_with_a_gap_before_it_is_refused(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/media/flyer", files=_png(),
                                   params={"page": 9})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self._medias()), 3)

    def test_removing_a_page_closes_the_gap(self) -> None:
        response = self.client.delete(f"/games/{GAME_ID}/media/flyer", params={"page": 2})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._medias(), {f"(Flyer) {FOLDER}.png", f"(Flyer 2) {FOLDER}.png"})
        self.assertEqual((self.folder / "medias" / f"(Flyer 2) {FOLDER}.png").read_bytes(),
                         b"\x89PNGthree")

    def test_a_table_cannot_remove_a_page_that_belongs_to_the_game(self) -> None:
        response = self.client.delete(
            f"/games/{GAME_ID}/tables/tbl0000002/media/flyer", params={"page": 2})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self._medias()), 3)

    def test_the_game_removes_a_page_of_the_default_when_it_has_none_of_its_own(self) -> None:
        for path in (self.folder / "medias").iterdir():
            path.rename(path.with_name(
                "flyer.png" if path.name.startswith("(Flyer) ") else
                "flyer 2.png" if path.name.startswith("(Flyer 2)") else "flyer 3.png"))

        response = self.client.delete(f"/games/{GAME_ID}/media/flyer", params={"page": 2})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["removed"], ["medias/flyer 2.png"])
        self.assertEqual(self._medias(), {"flyer.png", "flyer 2.png"})
        self.assertEqual((self.folder / "medias" / "flyer 2.png").read_bytes(),
                         b"\x89PNGthree")

    def test_a_page_of_the_default_stays_while_the_game_has_pages_of_its_own(self) -> None:
        (self.folder / "medias" / "flyer 4.png").write_bytes(b"\x89PNGdefault")

        response = self.client.delete(f"/games/{GAME_ID}/media/flyer")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._medias(), {"flyer 4.png"})

    def test_a_page_number_too_long_to_count_is_not_found(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/media/flyer", params={"page": "9" * 5000})

        self.assertEqual(response.status_code, 422, response.text)

    def test_removing_a_kind_takes_its_pages_with_it(self) -> None:
        self.client.delete(f"/games/{GAME_ID}/media/flyer")

        self.assertEqual(self._medias(), set())

    def test_a_kind_that_does_not_page_refuses_a_page(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/media/playfield", files=_png(),
                                   params={"page": 2})

        self.assertEqual(response.status_code, 400, response.text)

    def test_the_import_adds_a_page_from_a_file(self) -> None:
        source = self.root / "downloads" / "art.png"
        source.parent.mkdir()
        source.write_bytes(b"\x89PNGimported")
        with patch("common.games.media_browse.within_roots", return_value=source):
            response = self.client.post(f"/games/{GAME_ID}/media/flyer/import",
                                        json={"path": str(source), "page": 4})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["media"]["flyer"]["pages"], 4)
        self.assertIn(f"(Flyer 4) {FOLDER}.png", self._medias())

    def _body(self, number: int) -> bytes:
        name = f"(Flyer) {FOLDER}.png" if number == 1 else f"(Flyer {number}) {FOLDER}.png"
        return (self.folder / "medias" / name).read_bytes()

    def test_moving_a_page_earlier_swaps_it_with_the_one_before(self) -> None:
        response = self.client.post(f"/games/{GAME_ID}/media/flyer/move",
                                    json={"page": 3, "to": 2})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([self._body(1), self._body(2), self._body(3)],
                         [b"\x89PNGone", b"\x89PNGthree", b"\x89PNGtwo"])
        self.assertEqual(len(self._medias()), 3)

    def test_moving_a_page_onto_page_one_makes_it_the_first(self) -> None:
        self.client.post(f"/games/{GAME_ID}/media/flyer/move", json={"page": 2, "to": 1})

        self.assertEqual(self._body(1), b"\x89PNGtwo")
        self.assertEqual(self._body(2), b"\x89PNGone")

    def test_a_page_cannot_move_past_the_last(self) -> None:
        response = self.client.post(f"/games/{GAME_ID}/media/flyer/move",
                                    json={"page": 3, "to": 4})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self._body(3), b"\x89PNGthree")

    def test_a_table_moves_the_pages_of_the_flyer_it_shows(self) -> None:
        response = self.client.post(f"/games/{GAME_ID}/media/flyer/move",
                                    json={"table": "tbl0000002", "page": 3, "to": 2})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._body(2), b"\x89PNGthree")

    def test_a_kind_that_does_not_page_cannot_move_a_page(self) -> None:
        response = self.client.post(f"/games/{GAME_ID}/media/playfield/move",
                                    json={"page": 2, "to": 1})

        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()

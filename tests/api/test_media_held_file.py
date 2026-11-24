"""Protects: (a) the path a slot lists for a file it holds removes that file and not the one
in use; (b) a client can fetch any file a slot holds by its name.

`?file=` on the media GET shows a file that is not the one in use.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Held0000002"
FOLDER = "Cactus Canyon (Bally 1998)"
WINNER = f"(Playfield) {FOLDER}.png"
FALLBACK = "table.png"

INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}},
}


class HeldFileTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                                 files={f"{FOLDER}.vpx": b"vpx"},
                                 medias={FALLBACK: b"\x89PNGfallback",
                                         WINNER: b"\x89PNGwinner"})
        (self.root / "secret.png").write_bytes(b"\x89PNGsecret")
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _get(self, file: str):
        return self.client.get(f"/games/{GAME_ID}/media/playfield", params={"file": file})

    def test_without_a_name_the_file_in_use_is_served(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/media/playfield")

        self.assertEqual(response.content, b"\x89PNGwinner")

    def test_a_name_serves_that_file_though_another_is_in_use(self) -> None:
        response = self._get(FALLBACK)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"\x89PNGfallback")

    def test_a_name_that_is_not_held_serves_nothing(self) -> None:
        self.assertEqual(self._get("nothing.png").status_code, 404)

    def test_a_path_out_of_the_folder_is_not_followed(self) -> None:
        for name in ("../secret.png", "../../secret.png", "/etc/hosts",
                     f"../{FOLDER}/medias/{FALLBACK}"):
            with self.subTest(name=name):
                self.assertEqual(self._get(name).status_code, 404)

    def test_each_file_the_detail_lists_says_where_it_is(self) -> None:
        tiers = self.client.get(f"/games/{GAME_ID}/media/playfield/detail").json()["tiers"]

        self.assertEqual({item["file"]: item["path"] for item in tiers},
                         {WINNER: f"medias/{WINNER}", FALLBACK: f"medias/{FALLBACK}"})

    def test_the_path_a_tier_lists_removes_that_file_with_a_copy_and_leaves_the_one_in_use(
            self) -> None:
        backups = self.root / "backups"
        backups.mkdir()
        tiers = self.client.get(f"/games/{GAME_ID}/media/playfield/detail").json()["tiers"]
        path = next(item["path"] for item in tiers if not item["wins"])
        with patch("common.backups.folder.current", return_value=backups):
            asked = self.client.delete(f"/games/{GAME_ID}/files", params={"path": path})
            self.assertEqual(asked.status_code, 409, asked.text)
            self.assertTrue((self.folder / path).exists())

            gone = self.client.delete(f"/games/{GAME_ID}/files",
                                      params={"path": path, "resolve": "break"})

        self.assertEqual(gone.status_code, 200, gone.text)
        self.assertFalse((self.folder / path).exists())
        self.assertTrue((self.folder / "medias" / WINNER).exists())
        self.assertTrue([one for one in backups.rglob("*") if one.is_file()])


if __name__ == "__main__":
    unittest.main()

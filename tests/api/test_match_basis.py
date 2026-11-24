"""A game says who made its match, and who made the one underneath an override."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Basis00001"
FOLDER = "The Addams Family (Bally 1992)"
FOUND = "aT_GONvw"
PICKED = "P12wTlyY"


def _info(**vpinfe: Any) -> dict[str, Any]:
    return {
        "Info": {"Title": "The Addams Family", "VPSId": FOUND, "Manufacturer": "Bally"},
        "vpinfe": {"game_id": GAME_ID, **vpinfe},
        "tables": {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}},
    }


class MatchBasisTests(TempTree):
    def _game(self, info: dict[str, Any]) -> dict[str, Any]:
        folder = write_game(self.root, FOLDER, info=info, vpx=False,
                            files={f"{FOLDER}.vpx": b"vpx"})
        game = fake_game(folder, FOLDER, meta=info)
        with patch("common.games.game_repository.catalog",
                   return_value={GAME_ID: game}):
            client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
            response = client.get(f"/games/{GAME_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return body.get("game", body)

    def test_a_guess_is_nobodys(self) -> None:
        game = self._game(_info())

        self.assertEqual(game["vps_matched_by"], "")
        self.assertEqual(game["discovered"]["vps_matched_by"], "")

    def test_a_pick_on_import_says_so(self) -> None:
        game = self._game(_info(vps_matched_by="import"))

        self.assertEqual(game["vps_matched_by"], "import")
        self.assertEqual(game["discovered"]["vps_matched_by"], "import")

    def test_a_pick_over_an_import_is_the_persons_and_the_import_is_still_said(self) -> None:
        game = self._game(_info(vps_matched_by="import", alt_vpsid=PICKED))

        self.assertEqual(game["vps_matched_by"], "user")
        self.assertEqual(game["discovered"]["vps_matched_by"], "import")

    def test_a_pick_over_a_guess_leaves_the_guess_nobodys(self) -> None:
        game = self._game(_info(alt_vpsid=PICKED))

        self.assertEqual(game["vps_matched_by"], "user")
        self.assertEqual(game["discovered"]["vps_matched_by"], "")

    def test_a_declared_no_match_is_the_persons(self) -> None:
        game = self._game(_info(alt_vpsid=None))

        self.assertEqual(game["vps_id"], "")
        self.assertEqual(game["vps_matched_by"], "user")

    def test_the_game_list_says_it_too(self) -> None:
        info = _info(vps_matched_by="import")
        folder = write_game(self.root, FOLDER, info=info, vpx=False,
                            files={f"{FOLDER}.vpx": b"vpx"})
        game = fake_game(folder, FOLDER, meta=info)
        with patch("common.games.game_lens.catalog", return_value={GAME_ID: game}):
            client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
            response = client.get("/games")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["games"][0]["vps_matched_by"], "import")


if __name__ == "__main__":
    unittest.main()

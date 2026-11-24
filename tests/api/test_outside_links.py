"""Where extensions say a game, a table or a file is elsewhere, over HTTP."""

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.extensions import catalogs
from tests.support.library import TempTree, fake_game, game_info, write_game

GAME_ID = "Links00001"
NAME = "Attack from Mars (Bally 1995)"


class OutsideLinks(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = game_info("Attack from Mars", vps_id="vps-afm", game_id=GAME_ID,
                         tables={"t1": {"id": "t1", "filename": f"{NAME}.vpx"}})
        info["vpinfe"]["alt_vpsid"] = "vps-corrected"
        folder = write_game(self.root, NAME, info=info, medias={"wheel.png": b"png"})
        game = fake_game(folder, NAME, meta=info)
        patcher = patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        catalogs.clear()
        self.addCleanup(catalogs.clear)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _urls(self, **query: str) -> list[str]:
        response = self.client.get(f"/games/{GAME_ID}/links", params=query)
        self.assertEqual(200, response.status_code, response.text)
        return [one["url"] for one in response.json()["links"]]

    def test_a_game_is_described_by_the_match_the_user_chose(self) -> None:
        catalogs.register("ext", "site", "Site", "game",
                          lambda game: f"https://site.example/{game['vps_id']}")

        self.assertEqual(["https://site.example/vps-corrected"], self._urls())

    def test_a_table_is_described_with_its_file(self) -> None:
        catalogs.register("ext", "site", "Site", "table",
                          lambda one: f"https://site.example/{one['filename']}")

        self.assertEqual([f"https://site.example/{NAME}.vpx"], self._urls(table="t1"))

    def test_a_file_is_described_by_its_path(self) -> None:
        catalogs.register("ext", "site", "Site", "file",
                          lambda one: f"https://site.example/{one['path']}")

        self.assertEqual(["https://site.example/medias/wheel.png"],
                         self._urls(path="medias/wheel.png"))

    def test_a_table_the_game_does_not_have_is_not_found(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/links", params={"table": "nope"})

        self.assertEqual(404, response.status_code)

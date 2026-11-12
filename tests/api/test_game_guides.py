"""Setting a game's guides over HTTP."""

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.saved import saved_record

GAME_ID = "Guides0001"
PRIMER = "https://pinballprimer.github.io/afm.html"


def _info() -> dict:
    info = game_info("Attack from Mars", game_id=GAME_ID)
    info["guides"] = [{"kind": "tutorial", "origin": "vps", "title": "AFM",
                       "authors": [], "url": PRIMER, "youtube_id": ""}]
    return info


class SettingGuides(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(self.root, "Attack from Mars (Bally 1995)", info=_info())
        self.info_path = folder / "Attack from Mars (Bally 1995).info"
        self.game = fake_game(folder, "Attack from Mars (Bally 1995)", meta=_info())
        patcher = patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: self.game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _put(self, guides: list[dict]):
        return self.client.put(f"/games/{GAME_ID}/guides", json={"guides": guides})

    def _on_disk(self) -> list[dict]:
        return saved_record(self.info_path).get("guides") or []

    def test_the_list_reaches_the_info_file(self) -> None:
        response = self._put([{"url": "https://rules.example/", "kind": "rule_sheet",
                               "title": "Rules"},
                              {"url": PRIMER, "hidden": True}])

        self.assertEqual(200, response.status_code)
        self.assertEqual([("https://rules.example/", "user", None), (PRIMER, "vps", True)],
                         [(one["url"], one["origin"], one.get("hidden"))
                          for one in self._on_disk()])

    def test_the_answer_says_whose_each_one_is_and_whether_it_is_hidden(self) -> None:
        said = self._put([{"url": PRIMER, "hidden": True}]).json()["guides"]

        self.assertEqual([("vps", True, "Pinball Primer")],
                         [(one["origin"], one["hidden"], one["source"]) for one in said])

    def test_leaving_out_a_guide_vps_lists_is_refused_and_changes_nothing(self) -> None:
        response = self._put([])

        self.assertEqual(400, response.status_code)
        self.assertEqual([PRIMER], [one["url"] for one in self._on_disk()])

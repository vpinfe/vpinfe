"""A write to a game whose `.info` cannot be read says what is wrong with the file.

The file is damaged after the library has listed the game, which is how it happens: an
editor or a sync client leaves half a file between one read and the next write.
"""

from __future__ import annotations

from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.games import game_repository
from common.games.library_folders import KIND_ROOT, Location
from tests.support.library import TempTree, game_info, write_game

NAME = "Sample Game (Original 2024)"


class BrokenInfoTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, NAME, info=game_info("Sample Game"))
        held = mock.patch.object(
            game_repository.library_folders, "configured",
            return_value=[Location(location_id="test", path=str(self.root),
                                   kind=KIND_ROOT)])
        held.start()
        self.addCleanup(held.stop)
        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        listed = self.client.get("/games")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.game_id = listed.json()["games"][0]["id"]
        self.info = self.root / NAME / f"{NAME}.info"

    def _put(self, route: str, body: dict) -> tuple[int, str]:
        answer = self.client.put(f"/games/{self.game_id}/{route}", json=body)
        return answer.status_code, str(answer.json().get("error", {}).get("message", ""))

    def test_a_file_cut_short_names_the_line(self) -> None:
        self.info.write_text('{"Info": {\n  "Title": ', encoding="utf-8")

        status, said = self._put("overrides", {"alt_title": "Other"})

        self.assertEqual(status, 409, said)
        self.assertIn("Line 2", said)
        self.assertNotIn("char", said)

    def test_the_details_write_says_it_the_same_way(self) -> None:
        self.info.write_text('{"Info": {\n  "Title": ', encoding="utf-8")

        status, said = self._put("details", {"year": "1999"})

        self.assertEqual(status, 409, said)
        self.assertIn("Line 2", said)

    def test_an_empty_file_says_it_is_empty(self) -> None:
        self.info.write_text("", encoding="utf-8")

        status, said = self._put("overrides", {"alt_title": "Other"})

        self.assertEqual(status, 409, said)
        self.assertNotIn("Expecting", said)

    def test_a_file_gone_since_the_listing_names_its_path(self) -> None:
        self.info.unlink()

        answer = self.client.put(f"/games/{self.game_id}/details", json={"year": "1999"})

        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(answer.json()["error"]["details"]["path"], str(self.info))

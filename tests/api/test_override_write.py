"""An override that cannot be written says why."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.skips import needs_posix_permissions

GAME_ID = "Override001"
FOLDER = "Medieval Madness (Williams 1997)"
TABLE_ID = "tbl0000001"

INFO = game_info("Medieval Madness", game_id=GAME_ID,
                 tables={TABLE_ID: {"id": TABLE_ID, "filename": f"{FOLDER}.vpx"}})


@needs_posix_permissions
class OverrideWriteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                                 files={f"{FOLDER}.vpx": b"vpx"})
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.info = self.folder / f"{FOLDER}.info"
        self.info.chmod(0o400)
        self.addCleanup(self.info.chmod, 0o600)

    def _refused(self, response) -> tuple[int, str]:
        return response.status_code, response.json()["error"]["message"]

    def test_a_game_override_it_may_not_write(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/overrides",
                                   json={"alt_title": "Medieval"})

        self.assertEqual(self._refused(response),
                         (409, f"VPinFE does not have permission for {self.info}"))

    def test_saying_there_is_no_match_where_it_may_not_write(self) -> None:
        response = self.client.delete(f"/games/{GAME_ID}/vps_match")

        self.assertEqual(self._refused(response),
                         (409, f"VPinFE does not have permission for {self.info}"))

    def test_a_table_override_it_may_not_write(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/tables/{TABLE_ID}/overrides",
                                   json={"delete_nvram_on_close": True})

        self.assertEqual(self._refused(response),
                         (409, f"VPinFE does not have permission for {self.info}"))


if __name__ == "__main__":
    unittest.main()

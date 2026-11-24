"""Where a media file came from, beside why it is the one being used.

Tier is derived from a filename every run. Origin can only be known because something
wrote it down when it placed the file, so most files honestly answer "unknown" - and
saying so is the point, rather than leaving a caller to infer it from the tier.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games.asset_origin import ledger, origin_of
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Origins001"
FOLDER = "Cactus Canyon (Bally 1998)"

INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}},
    # The ledger records the path it wrote, and only who placed it.
    "assets": {
        "medias/wheel.png": {"source": {"host": "vpinmediadb", "hash": "abc"}},
        "medias/bg.png": {"source": {"host": "user"}},
    },
}


class MediaOriginTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(
            self.root, FOLDER, info=INFO, vpx=False,
            files={f"{FOLDER}.vpx": b"vpx"},
            # A third file nothing recorded: the ordinary case in a real library.
            medias={"wheel.png": b"\x89PNG", "bg.png": b"\x89PNG",
                    "table.png": b"\x89PNG"})
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _media(self) -> dict:
        response = self.client.get(f"/games/{GAME_ID}/media")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["media"]

    def test_a_downloaded_file_says_so(self) -> None:
        self.assertEqual(self._media()["wheel"]["origin"], "vpinmediadb")

    def test_a_file_you_placed_says_so(self) -> None:
        self.assertEqual(self._media()["backglass"]["origin"], "user")

    def test_a_file_nobody_recorded_is_unknown_not_blank(self) -> None:
        self.assertEqual(self._media()["playfield"]["origin"], "unknown")

    def test_a_kind_with_no_file_claims_no_origin(self) -> None:
        entry = self._media()["topper"]

        self.assertFalse(entry["present"])
        self.assertIsNone(entry["origin"])

    def test_origin_says_nothing_about_the_tier(self) -> None:
        """Both files sit at the fixed name, so both resolve at `default` - and they
        came from different places. Deriving one from the other would be wrong."""
        media = self._media()

        self.assertEqual(media["wheel"]["via"], "default")
        self.assertEqual(media["backglass"]["via"], "default")
        self.assertNotEqual(media["wheel"]["origin"], media["backglass"]["origin"])

    def test_a_folder_with_no_ledger_reads_as_empty_rather_than_failing(self) -> None:
        self.assertEqual(ledger(self.root / "not-a-game"), {})
        self.assertEqual(origin_of({}, self.folder, None), "")


if __name__ == "__main__":
    unittest.main()

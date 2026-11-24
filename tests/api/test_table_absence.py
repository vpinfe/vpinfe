"""A table the metadata describes and the disk does not have.

Discovery records when it first could not find a file. Reporting that is the whole
point of recording it - without this the stamp exists and nobody can see it.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Vanished001"
FOLDER = "Cactus Canyon (Bally 1998)"
HERE = f"{FOLDER}.vpx"
GONE = f"{FOLDER} - VR.vpx"
STAMP = "2026-08-01T09:30:00Z"

INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": HERE},
        "tbl0000002": {"id": "tbl0000002", "filename": GONE, "absent_since": STAMP},
    },
}


class TableAbsenceTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        # Only one of the two described tables is actually there.
        folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                            files={HERE: b"vpx"})
        game = fake_game(folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _by_name(self) -> dict:
        response = self.client.get(f"/games/{GAME_ID}/tables")
        self.assertEqual(response.status_code, 200, response.text)
        return {t["filename"]: t for t in response.json()["tables"]}

    def test_a_missing_table_reports_when_it_went(self) -> None:
        self.assertEqual(self._by_name()[GONE]["absent_since"], STAMP)

    def test_a_table_that_is_there_reports_nothing(self) -> None:
        self.assertIsNone(self._by_name()[HERE]["absent_since"])

    def test_absence_and_availability_are_both_reported(self) -> None:
        """They answer different questions - is it there now, and for how long has it
        not been - so neither replaces the other."""
        tables = self._by_name()

        self.assertFalse(tables[GONE]["available"])
        self.assertTrue(tables[HERE]["available"])

    def test_the_missing_table_is_still_listed(self) -> None:
        self.assertIn(GONE, self._by_name())


if __name__ == "__main__":
    unittest.main()

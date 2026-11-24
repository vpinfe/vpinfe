"""A game's DOF event is a letter and a number, or empty, and anything else is refused unwritten."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.i18n import t
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "DofEvent001"
FOLDER = "Medieval Madness (Williams 1997)"

INFO = {
    "Info": {"Title": "Medieval Madness"},
    "VPinFE": {"game_id": GAME_ID},
}


class GameDofEventTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False)
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.info = self.folder / f"{FOLDER}.info"

    def _put(self, value: Any) -> Any:
        return self.client.put(f"/games/{GAME_ID}/overrides",
                               json={"frontend_dof_event": value})

    def _written(self) -> Any:
        return json.loads(self.info.read_text(encoding="utf-8")).get("vpinfe", {}).get(
            "frontend_dof_event")

    def test_a_letter_and_a_number_is_written_as_the_helper_is_told_it(self) -> None:
        for said, written in (("E905", "E905"), ("S27", "S27"), (" e905 ", "E905")):
            with self.subTest(said=said):
                self.assertEqual(self._put(said).status_code, 200)

                self.assertEqual(self._written(), written)

    def test_anything_else_is_refused_and_leaves_the_file_alone(self) -> None:
        for said in ("905", "E9x", "EE905", "E 905"):
            with self.subTest(said=said):
                answer = self._put(said)

                self.assertEqual(answer.status_code, 400)
                self.assertEqual(answer.json()["error"]["message"], t("dof.event.malformed"))
                self.assertIsNone(self._written())

    def test_empty_clears_it(self) -> None:
        self._put("E905")

        self.assertEqual(self._put("").status_code, 200)

        self.assertFalse(self._written())


if __name__ == "__main__":
    unittest.main()

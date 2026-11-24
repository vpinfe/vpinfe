"""A corrected match is the game's id everywhere, and the discovered one is still said.

`4100685` set the contract: every top-level field on a game resource is the value in
force, and `discovered` carries what something outside VPinFE supplied so a surface can
name what an undo reverts to. `name` implemented it. `vps_id` reported the discovered id
on both sides, so a match somebody fixed by hand was stored, echoed back under
`overrides`, and then ignored by everything that reads an id - the entry shown, the
release count, the media offered.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Effective1"
FOLDER = "The Addams Family (Bally 1992)"
FOUND = "aT_GONvw"
CORRECTED = "P12wTlyY"

INFO: dict[str, Any] = {
    "Info": {"Title": "The Addams Family", "VPSId": FOUND, "Manufacturer": "Bally"},
    "vpinfe": {"game_id": GAME_ID, "alt_vpsid": CORRECTED, "alt_title": "Addams"},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}},
}


class EffectiveVpsIdTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                            files={f"{FOLDER}.vpx": b"vpx"})
        game = fake_game(folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def _game(self) -> dict:
        response = self.client.get(f"/games/{GAME_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return body.get("game", body)

    def test_the_id_reported_is_the_one_in_force(self) -> None:
        self.assertEqual(self._game()["vps_id"], CORRECTED)

    def test_what_vps_supplied_is_still_reported(self) -> None:
        self.assertEqual(self._game()["discovered"]["vps_id"], FOUND)

    def test_the_two_are_not_the_same_field(self) -> None:
        """They were, which is what hid this: `discovered` duplicating the effective
        value looks correct until an override exists to tell them apart."""
        game = self._game()

        self.assertNotEqual(game["vps_id"], game["discovered"]["vps_id"])

    def test_the_override_is_still_reported_as_an_override(self) -> None:
        """Resolving through it must not stop a surface offering to undo it."""
        self.assertEqual(self._game()["overrides"]["alt_vps_id"], CORRECTED)

    def test_a_game_with_no_correction_reports_what_was_found(self) -> None:
        plain = dict(INFO)
        plain["vpinfe"] = {k: v for k, v in INFO["vpinfe"].items() if k != "alt_vpsid"}
        folder = write_game(self.root, "Attack from Mars (Bally 1995)", info=plain,
                            vpx=False, files={"Attack from Mars (Bally 1995).vpx": b"v"})
        game = fake_game(folder, "Attack from Mars (Bally 1995)", meta=plain)
        with patch("common.games.game_repository.catalog", return_value={GAME_ID: game}):
            body = self.client.get(f"/games/{GAME_ID}").json()
        found = body.get("game", body)

        self.assertEqual(found["vps_id"], FOUND)
        self.assertEqual(found["discovered"]["vps_id"], FOUND)

    def _lens_ids(self) -> set[str]:
        """What Get art asks a catalog for, from each media and asset row."""
        from common.games import asset_lens, media_lens
        rows = (media_lens.listing(game=GAME_ID)["media"]
                + asset_lens.listing(game=GAME_ID)["assets"])
        self.assertTrue(rows)
        return {row["vps_id"] for row in rows}

    def test_a_media_or_asset_row_asks_for_the_entry_a_person_picked(self) -> None:
        self.assertEqual(self._lens_ids(), {CORRECTED})

    def test_a_media_or_asset_row_asks_for_nothing_on_a_declared_no_match(self) -> None:
        declared = {**INFO, "vpinfe": {**INFO["vpinfe"], "alt_vpsid": None}}
        game = fake_game(self.root / FOLDER, FOLDER, meta=declared)
        with patch("common.games.game_repository.catalog", return_value={GAME_ID: game}):
            self.assertEqual(self._lens_ids(), {""})


if __name__ == "__main__":
    unittest.main()

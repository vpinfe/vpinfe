"""Saying which VPS record one media or asset file is, and taking it back.

The assets ledger's twin of `test_table_source`. Same producer - a person picking from
a list nothing ordered - and so the same `confirmed_by: user`, because the ranker that
would fill this automatically was measured at chance.

What differs is the address. A table is named by its id; a file is named by its path,
because the ledger is keyed by path and one folder can hold several files of a kind.
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games.game_metadata import set_asset_source
from common.games.info_file import INFO_SCHEMA
from tests.support.library import TempTree, fake_game, write_game
from tests.support.saved import saved_record

GAME_ID = "Bound00001"
FOLDER = "Medieval Madness (Williams 1997)"
VPX = f"{FOLDER}.vpx"
BACKGLASS = f"{FOLDER}.directb2s"
RECORD = "b2s_KtY1"

INFO = {
    "Info": {"Title": "Medieval Madness", "VPSId": "mm_1997x"},
    "vpinfe": {"game_id": GAME_ID, "schema": INFO_SCHEMA},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": VPX}},
    # Placed by us and hashed, which is a fact about the file - not a claim about
    # which upstream record it is.
    "assets": {BACKGLASS: {"source": {"host": "vpinmediadb", "hash": "d41d8c"}}},
}

CATALOG = [{
    "id": "mm_1997x", "name": "Medieval Madness",
    "b2sFiles": [
        {"id": RECORD, "version": "3.0", "authors": ["wildman"],
         "updatedAt": 1719187200000},
        {"id": "b2s_Other", "version": "1.1", "authors": ["hauntfreaks"]},
    ],
    "tableFiles": [{"id": "tbl_x", "version": "1.0"}],
}]


class AssetSourceTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        meta = copy.deepcopy(INFO)
        self.folder = write_game(self.root, FOLDER, info=meta, vpx=False,
                                 files={VPX: b"vpx", BACKGLASS: b"b2s"})
        self.game = fake_game(self.folder, FOLDER, meta=copy.deepcopy(meta))
        for target in ("common.games.game_service.load_vpsdb",):
            patcher = patch(target, return_value=list(CATALOG))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: self.game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def _ledger(self) -> dict:
        return saved_record(self.folder / f"{FOLDER}.info").get("assets") or {}

    def _bind(self, path: str, record: str) -> dict:
        response = self.client.put(f"/games/{GAME_ID}/asset_source",
                                   json={"path": path, "vps_file_id": record})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_binding_records_who_said_so(self) -> None:
        self._bind(BACKGLASS, RECORD)

        self.assertEqual(self._ledger()[BACKGLASS]["source"]["vps_file_id"], RECORD)
        self.assertEqual(self._ledger()[BACKGLASS]["source"]["confirmed_by"], "user")

    def test_the_match_sits_beside_where_the_file_came_from(self) -> None:
        """Who placed a file and which record it is are different questions, and the
        answers come apart in both directions."""
        self._bind(BACKGLASS, RECORD)
        stored = self._ledger()[BACKGLASS]["source"]

        self.assertEqual(stored["host"], "vpinmediadb")
        self.assertEqual(stored["hash"], "d41d8c")

    def test_a_file_nothing_had_recorded_can_be_bound(self) -> None:
        """Silence means nobody looked, not that the file is ineligible. Most files in
        a real library have no ledger entry at all."""
        self._bind("medias/wheel.png", RECORD)

        self.assertEqual(self._ledger()["medias/wheel.png"]["source"],
                         {"vps_file_id": RECORD, "confirmed_by": "user",
                          "vps_version": "3.0"})

    def test_unbinding_keeps_where_the_file_came_from(self) -> None:
        self._bind(BACKGLASS, RECORD)
        self._bind(BACKGLASS, "")

        self.assertEqual(self._ledger()[BACKGLASS]["source"],
                         {"host": "vpinmediadb", "hash": "d41d8c"})

    def test_unbinding_a_file_nothing_else_knew_leaves_no_entry(self) -> None:
        """An entry holding nothing would read as "examined, found nothing", which is
        a state only a matcher can produce."""
        self._bind("medias/wheel.png", RECORD)
        self._bind("medias/wheel.png", "")

        self.assertNotIn("medias/wheel.png", self._ledger())

    def test_a_windows_path_keys_the_same_entry(self) -> None:
        """The ledger is forward-slashed wherever it was written, so the same file sent
        from two clients must not become two entries."""
        self._bind("medias/wheel.png", RECORD)
        self._bind("medias\\wheel.png", "b2s_Other")

        self.assertEqual(sorted(self._ledger()), sorted([BACKGLASS, "medias/wheel.png"]))
        self.assertEqual(self._ledger()["medias/wheel.png"]["source"]["vps_file_id"],
                         "b2s_Other")

    def test_a_path_that_says_nothing_is_refused(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/asset_source",
                                   json={"path": "  ", "vps_file_id": RECORD})

        self.assertEqual(response.status_code, 400, response.text)

    def test_the_writer_answers_with_what_it_stored(self) -> None:
        self.assertEqual(set_asset_source(self.game, BACKGLASS, RECORD),
                         {"host": "vpinmediadb", "hash": "d41d8c",
                          "vps_file_id": RECORD, "confirmed_by": "user",
                          "vps_version": "3.0"})


    # --- the records the picker chooses from, in VPS's own vocabulary ----------

    def test_a_kind_is_asked_for_by_the_name_vps_uses(self) -> None:
        found = self.client.get("/vps/entry/mm_1997x/releases",
                                params={"listed_as": "b2sFiles"})

        self.assertEqual([item["vps_file_id"] for item in found.json()["releases"]],
                         [RECORD, "b2s_Other"])

    def test_table_builds_stay_the_default(self) -> None:
        found = self.client.get("/vps/entry/mm_1997x/releases")

        self.assertEqual([item["vps_file_id"] for item in found.json()["releases"]],
                         ["tbl_x"])

    def test_a_kind_vps_does_not_list_is_refused(self) -> None:
        """Rather than answering with an empty list, which reads as "this machine has
        no backglasses" when the caller simply asked for something that is not a kind."""
        found = self.client.get("/vps/entry/mm_1997x/releases",
                                params={"listed_as": "backglass"})

        self.assertEqual(found.status_code, 400, found.text)


if __name__ == "__main__":
    unittest.main()

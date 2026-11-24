"""What the catalog lists for one game, kind by kind, against what the game holds.

State rather than findings: which of it is worth telling somebody about is a judgement
the surface makes with the library in front of it. A producer deciding that here would
bake in exactly the judgement the measurements say we make badly.
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "State00001"
FOLDER = "Attack from Mars (Bally 1995)"
ENTRY = "9Paf7-CL"

INFO = {
    "Info": {"Title": "Attack from Mars", "VPSId": ENTRY},
    "vpinfe": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}},
}

FILE = "https://vpuniverse.com/files/file/{}-a-thing"
FOLDER_LINK = "https://mega.nz/#F!p3hREDgB!pEWMJvVQ7t3Sv_TmrxdU-w"

CATALOG = [{
    "id": ENTRY, "name": "Attack from Mars", "manufacturer": "Bally", "year": 1995,
    "b2sFiles": [{"id": "b1", "urls": [{"url": FILE.format(1)}]}],
    "romFiles": [{"id": "r1", "urls": [{"url": FILE.format(2)}]}],
    # Both of this one's links are the same folder, which is the pov case at scale.
    "povFiles": [{"id": "p1", "urls": [{"url": FOLDER_LINK}]},
                 {"id": "p2", "urls": [{"url": FOLDER_LINK}]}],
    "wheelArtFiles": [{"id": "w1", "urls": [{"url": FILE.format(3)}]}],
    "topperFiles": [],
}]


class VpsStateTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = folder = write_game(
            self.root, FOLDER, info=copy.deepcopy(INFO), vpx=False,
            files={f"{FOLDER}.vpx": b"vpx", f"{FOLDER}.directb2s": b"b2s"},
            medias={"wheel.png": b"\x89PNG"})
        game = fake_game(folder, FOLDER, meta=copy.deepcopy(INFO))
        for target, value in (("common.games.game_service.load_vpsdb", CATALOG),):
            patcher = patch(target, return_value=copy.deepcopy(value))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Keyed on the catalog's length, and every test here uses one entry.
        from common.games.library_vps_state import _crowded_links_for
        _crowded_links_for.cache_clear()
        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def test_the_entry_the_game_is_matched_to_is_named(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/vps_state").json()

        self.assertEqual(body["vps_id"], ENTRY)

    def _state(self) -> dict[str, dict]:
        response = self.client.get(f"/games/{GAME_ID}/vps_state")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["matched"])
        return {item["kind"]: item for item in body["kinds"]}

    def test_every_mapped_kind_is_reported(self) -> None:
        """Including the ones the entry lists nothing for: a consumer can only say
        "you own no toppers" if it can see the kind at all."""
        state = self._state()

        self.assertEqual(state["topperFiles"]["listed"], 0)
        self.assertIn("altSoundFiles", state)

    def test_what_the_game_holds_is_reported(self) -> None:
        state = self._state()

        self.assertTrue(state["b2sFiles"]["held"])
        self.assertTrue(state["wheelArtFiles"]["held"])
        self.assertFalse(state["romFiles"]["held"])

    def test_a_folder_is_listed_but_not_obtainable(self) -> None:
        """The whole point. Two records, both a folder, so the catalog lists two and
        offers none - reporting them as gettable would send somebody rummaging."""
        pov = self._state()["povFiles"]

        self.assertEqual(pov["listed"], 2)
        self.assertEqual(pov["obtainable"], 0)
        self.assertEqual(pov["why_not"], ["collection"])

    def test_a_file_is_obtainable_and_says_nothing_more(self) -> None:
        rom = self._state()["romFiles"]

        self.assertEqual((rom["listed"], rom["obtainable"]), (1, 1))
        self.assertEqual(rom["why_not"], [])

    def test_one_of_theirs_can_be_two_of_ours(self) -> None:
        self.assertEqual(self._state()["altColorFiles"]["ours"],
                         ["altcolor_serum", "altcolor_vni"])

    def _bind(self, path: str, record: str) -> None:
        response = self.client.put(f"/games/{GAME_ID}/asset_source",
                                   json={"path": path, "vps_file_id": record})
        self.assertEqual(response.status_code, 200, response.text)

    def test_nothing_is_identified_until_somebody_says_so(self) -> None:
        """Holding a backglass and knowing which published backglass it is are
        different facts, and only the second can produce an exact update."""
        state = self._state()

        self.assertTrue(state["b2sFiles"]["held"])
        self.assertFalse(state["b2sFiles"]["identified"])

    def test_binding_a_file_identifies_its_kind(self) -> None:
        self._bind(f"{FOLDER}.directb2s", "b1")

        self.assertTrue(self._state()["b2sFiles"]["identified"])

    def test_a_binding_identifies_only_the_kind_that_lists_it(self) -> None:
        """A record id is in one kind's list, so the id alone places it - nothing has
        to know which file in the folder was bound."""
        self._bind(f"{FOLDER}.directb2s", "b1")
        state = self._state()

        self.assertTrue(state["b2sFiles"]["identified"])
        self.assertFalse(state["romFiles"]["identified"])
        self.assertFalse(state["wheelArtFiles"]["identified"])

    def test_unbinding_takes_the_identification_back(self) -> None:
        self._bind(f"{FOLDER}.directb2s", "b1")
        self._bind(f"{FOLDER}.directb2s", "")

        self.assertFalse(self._state()["b2sFiles"]["identified"])

    def test_each_held_file_is_named_as_it_is_on_disk(self) -> None:
        state = self._state()

        self.assertEqual([one["name"] for one in state["b2sFiles"]["files"]],
                         [f"{FOLDER}.directb2s"])
        self.assertEqual(state["romFiles"]["files"], [])

    def _with_rom(self, name: str = "afm_113b.zip") -> str:
        roms = self.folder / "pinmame" / "roms"
        roms.mkdir(parents=True, exist_ok=True)
        (roms / name).write_bytes(b"PK")
        return name

    def test_a_rom_zip_in_the_roms_folder_is_held(self) -> None:
        name = self._with_rom()

        rom = self._state()["romFiles"]

        self.assertTrue(rom["held"])
        self.assertEqual(rom["files"], [{"name": name, "vps_file_id": ""}])

    def test_a_file_that_is_not_a_zip_in_the_roms_folder_is_not_a_rom(self) -> None:
        self._with_rom("notes.txt")

        self.assertFalse(self._state()["romFiles"]["held"])

    def test_a_rom_bound_to_a_record_names_it_and_identifies_the_kind(self) -> None:
        name = self._with_rom()
        self._bind(f"pinmame/roms/{name}", "r1")

        rom = self._state()["romFiles"]

        self.assertEqual(rom["files"], [{"name": name, "vps_file_id": "r1"}])
        self.assertTrue(rom["identified"])

    def test_a_held_file_names_the_record_it_is_tied_to(self) -> None:
        self._bind(f"{FOLDER}.directb2s", "b1")

        (held,) = self._state()["b2sFiles"]["files"]

        self.assertEqual(held["vps_file_id"], "b1")

    def test_a_file_tied_to_no_record_says_so(self) -> None:
        (held,) = self._state()["b2sFiles"]["files"]

        self.assertEqual(held["vps_file_id"], "")

    def test_the_tables_on_disk_are_listed_beside_the_kinds(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/vps_state").json()

        self.assertEqual([one["name"] for one in body["tables"]], [f"{FOLDER}.vpx"])

    def test_a_game_matched_to_nothing_says_so(self) -> None:
        with patch("common.games.game_service.load_vpsdb", return_value=[]):
            body = self.client.get(f"/games/{GAME_ID}/vps_state").json()

        self.assertFalse(body["matched"])
        self.assertEqual(sum(item["listed"] for item in body["kinds"]), 0)
        # Still every kind, so a consumer's rollup does not silently lose a column.
        self.assertTrue(body["kinds"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, game_info

CATALOG = [{"id": "vps-afm", "tableFiles": [
                {"id": "rel-afm-vpw", "urls": [{"url": "https://example.test/afm-vpw"}]},
                {"id": "rel-afm-new", "urls": [{"url": "https://example.test/afm-new"}]}]},
           {"id": "vps-mm", "tableFiles": [{"id": "rel-mm-new", "urls": []}]},
           {"id": "vps-taf", "name": "The Addams Family", "manufacturer": "Bally", "year": 1992,
            "tableFiles": [{"id": "rel-taf", "version": "1.0", "authors": ["Ann", "Bob"],
                            "urls": []}]}]


def _game(game_id: str, vps_id: str, release: str = ""):
    tables = {"t1": {"id": "t1", "filename": f"{game_id}.vpx", "version": "1.2",
                     **({"source": {"vps_file_id": release}} if release else {})}}
    info = game_info(game_id, vps_id=vps_id, game_id=game_id, tables=tables)
    return fake_game(f"/nowhere/{game_id}", game_id, meta=info)


class Owned(TempTree):
    def setUp(self) -> None:
        super().setUp()
        games = [_game("afm", "vps-afm", release="rel-afm-vpw"), _game("mm", "vps-mm")]
        patcher = patch("common.games.game_repository.all_games", lambda: games)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("common.games.game_service.load_vpsdb", lambda: CATALOG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _said(self, ids: list[str], **asked: str) -> dict:
        response = self.client.post("/library/owned", json={"ids": ids, **asked})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def _owned(self, ids: list[str]) -> dict:
        return self._said(ids)["owned"]

    def test_an_entry_answers_with_its_game(self) -> None:
        self.assertEqual({"game_id": "mm", "table_id": "", "name": "mm"},
                         self._owned(["vps-mm"])["vps-mm"])

    def test_a_release_answers_with_its_table(self) -> None:
        self.assertEqual(("afm", "t1"), tuple(self._owned(["rel-afm-vpw"])["rel-afm-vpw"]
                                              [key] for key in ("game_id", "table_id")))

    def test_what_is_not_here_is_left_out(self) -> None:
        self.assertEqual({"vps-afm"}, set(self._owned(["vps-afm", "vps-taf", ""])))


    def test_a_release_of_a_machine_held_as_another_says_which_it_has(self) -> None:
        said = self._said(["rel-afm-new", "rel-mm-new"])

        self.assertEqual({}, said["owned"])
        self.assertEqual({"game_id": "afm", "table_id": "t1", "name": "afm",
                          "version": "1.2", "url": "https://example.test/afm-new"},
                         said["other_versions"]["rel-afm-new"])
        self.assertEqual("https://virtualpinballspreadsheet.github.io/?game=vps-mm",
                         said["other_versions"]["rel-mm-new"]["url"])

    def test_a_release_held_or_of_a_machine_not_held_is_no_other_version(self) -> None:
        self.assertEqual({}, self._said(["rel-afm-vpw", "rel-taf"])["other_versions"])

    def test_vpinfe_ids_find_a_game_or_a_table_by_the_library_s_own(self) -> None:
        games = self._said(["afm", "t1", "zz"], keys="vpinfe", kind="game")["owned"]
        tables = self._said(["afm", "t1", "zz"], keys="vpinfe", kind="table")["owned"]

        self.assertEqual({"afm": ""}, {one: held["table_id"] for one, held in games.items()})
        self.assertEqual({"t1"}, set(tables))
        self.assertEqual("afm", tables["t1"]["game_id"])

    def test_a_kind_keeps_a_table_from_answering_for_a_game(self) -> None:
        self.assertEqual(set(), set(self._said(["vps-afm"], kind="table")["owned"]))
        self.assertEqual({"rel-afm-vpw"},
                         set(self._said(["vps-afm", "rel-afm-vpw"], kind="table")["owned"]))
        self.assertEqual({"vps-afm"},
                         set(self._said(["vps-afm", "rel-afm-vpw"], kind="game")["owned"]))

    def test_an_id_the_library_lacks_is_named_by_vpsdb(self) -> None:
        game = self._said(["vps-taf"], kind="game")
        table = self._said(["rel-taf"], kind="table")

        self.assertEqual({"vps-taf": {"name": "The Addams Family", "manufacturer": "Bally",
                                      "year": "1992", "version": "", "authors": []}},
                         game["known"])
        self.assertEqual({"rel-taf": {"name": "The Addams Family", "manufacturer": "",
                                      "year": "", "version": "1.0",
                                      "authors": ["Ann", "Bob"]}},
                         table["known"])

    def test_what_the_library_holds_is_not_also_known(self) -> None:
        self.assertEqual({}, self._said(["vps-afm"], kind="game")["known"])

    def test_known_is_asked_only_of_vps_ids_with_a_kind(self) -> None:
        self.assertEqual({}, self._said(["vps-taf"])["known"])
        self.assertEqual({}, self._said(["vps-taf"], keys="vpinfe", kind="game")["known"])

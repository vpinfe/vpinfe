"""What a release is a mod of, read off a fixture catalog, and where the library holds it."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import game_service, mods
from tests.support.library import TempTree, fake_game, game_info, write_game

TAF_PAGE = "https://virtualpinballspreadsheet.github.io/?game=vps-taf"

CATALOG = [
    {"id": "vps-taf", "name": "The Addams Family", "tableFiles": [
        {"id": "taf-base", "version": "1.2", "authors": ["VPW"]},
        {"id": "taf-fss", "version": "2.0", "authors": ["Modder"], "parentId": "taf-base",
         "features": ["MOD"], "comment": "FSS MOD"},
        {"id": "taf-room", "version": "1.0", "authors": ["Room builder"],
         "parentId": "taf-fss"},
        {"id": "taf-gone", "version": "0.9", "parentId": "nothing-by-this-id"},
        {"id": "taf-noted", "version": "2.1", "features": ["MOD"], "comment": " Reskin "},
        {"id": "taf-quiet", "version": "2.2", "features": ["VR", "MOD"]},
        {"id": "taf-self", "version": "3.0", "parentId": "taf-self"},
        {"id": "taf-self-noted", "version": "3.1", "parentId": "taf-self-noted",
         "features": ["MOD"], "comment": "Own"},
        {"id": "taf-plain", "version": "4.0", "features": ["VR"], "comment": "Plain"},
    ]},
    {"id": "vps-mm", "name": "Medieval Madness", "tableFiles": [
        {"id": "mm-over-taf", "version": "1.4", "parentId": "taf-base"},
        {"id": "mm-one", "version": "1", "parentId": "mm-two"},
        {"id": "mm-two", "version": "2", "parentId": "mm-one"},
    ]},
]


class Catalog(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("common.games.game_service.load_vpsdb", return_value=CATALOG)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_link_in_the_same_game_names_the_release_and_not_the_game(self) -> None:
        self.assertEqual({"vps_file_id": "taf-base", "version": "1.2", "authors": ["VPW"],
                          "game": "", "url": TAF_PAGE, "note": "", "game_id": "",
                          "table_id": ""},
                         mods.mod_of("taf-fss"))

    def test_a_link_across_games_names_that_game(self) -> None:
        said = mods.mod_of("mm-over-taf") or {}
        self.assertEqual(("taf-base", "The Addams Family", TAF_PAGE),
                         (said["vps_file_id"], said["game"], said["url"]))

    def test_a_mod_of_a_mod_names_only_the_one_it_is_based_on(self) -> None:
        self.assertEqual(("taf-fss", "2.0"),
                         tuple((mods.mod_of("taf-room") or {})[key]
                               for key in ("vps_file_id", "version")))

    def test_a_link_the_catalog_cannot_find_is_a_mod_of_something_unknown(self) -> None:
        said = mods.mod_of("taf-gone") or {}
        self.assertEqual(("", ""), (said["vps_file_id"], said["note"]))

    def test_a_tag_with_no_link_carries_the_note_as_vps_wrote_it(self) -> None:
        said = mods.mod_of("taf-noted") or {}
        self.assertEqual(("", "Reskin"), (said["vps_file_id"], said["note"]))

    def test_a_tag_with_no_link_and_no_note_is_a_mod_of_something_unknown(self) -> None:
        said = mods.mod_of("taf-quiet")
        self.assertIsNotNone(said)
        self.assertEqual(("", ""), ((said or {})["vps_file_id"], (said or {})["note"]))

    def test_a_release_neither_linked_nor_tagged_is_not_a_mod(self) -> None:
        self.assertIsNone(mods.mod_of("taf-plain"))
        self.assertIsNone(mods.mod_of("taf-base"))

    def test_a_link_to_itself_is_no_link(self) -> None:
        self.assertIsNone(mods.mod_of("taf-self"))
        self.assertEqual(("", "Own"), tuple((mods.mod_of("taf-self-noted") or {})[key]
                                           for key in ("vps_file_id", "note")))

    def test_two_releases_linked_to_each_other_each_name_the_other(self) -> None:
        self.assertEqual("mm-two", (mods.mod_of("mm-one") or {})["vps_file_id"])
        self.assertEqual("mm-one", (mods.mod_of("mm-two") or {})["vps_file_id"])

    def test_an_id_the_catalog_lacks_is_not_a_mod(self) -> None:
        self.assertIsNone(mods.mod_of("nothing-by-this-id"))


class Index(unittest.TestCase):
    def test_a_reloaded_catalog_is_read_rather_than_the_one_indexed_before(self) -> None:
        before = [{"id": "e", "tableFiles": [{"id": "r", "version": "1"}]}]
        after = [{"id": "e", "tableFiles": [{"id": "r", "version": "2"}]}]
        with patch.object(game_service, "_vpsdb_cache", before):
            self.assertEqual("1", game_service.find_vps_release("r")["version"])
            built = game_service._release_index
            game_service.find_vps_release("r")
            self.assertIs(built, game_service._release_index)
        with patch.object(game_service, "_vpsdb_cache", after):
            self.assertEqual("2", game_service.find_vps_release("r")["version"])

    def test_the_release_comes_with_the_game_it_is_listed_under(self) -> None:
        with patch("common.games.game_service.load_vpsdb", return_value=CATALOG):
            release, entry = game_service.find_vps_release_and_entry("mm-one")
        self.assertEqual(("mm-one", "vps-mm"), (release["id"], entry["id"]))


def _info(game_id: str, vps_id: str, release: str) -> dict:
    return game_info(game_id, vps_id=vps_id, game_id=game_id, tables={
        "t1": {"id": "t1", "filename": f"{game_id}.vpx", "source": {"vps_file_id": release}}})


class Held(TempTree):
    """A library holding The Addams Family's base and Medieval Madness's mod of it."""

    def setUp(self) -> None:
        super().setUp()
        games = []
        for game_id, vps_id, release in (("taf", "vps-taf", "taf-base"),
                                         ("mm", "vps-mm", "mm-over-taf")):
            info = _info(game_id, vps_id, release)
            folder = write_game(self.root, game_id, info=copy.deepcopy(info))
            games.append(fake_game(folder, game_id, meta=info))
        for target, value in (("common.games.game_repository.all_games", lambda: games),
                              ("common.games.game_service.load_vpsdb", lambda: CATALOG)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _get(self, path: str) -> dict:
        response = self.client.get(path)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_a_table_says_where_the_table_it_is_based_on_is_held(self) -> None:
        said = self._get("/games/mm/tables")["tables"][0]["source"]["mod_of"]
        self.assertEqual(("The Addams Family", "taf", "t1"),
                         (said["game"], said["game_id"], said["table_id"]))

    def test_the_library_list_says_the_same(self) -> None:
        rows = {row["game_id"]: row for row in self._get("/tables")["tables"]}
        self.assertEqual("t1", rows["mm"]["source"]["mod_of"]["table_id"])
        self.assertIsNone(rows["taf"]["source"]["mod_of"])

    def test_a_release_in_the_list_of_releases_says_it_too(self) -> None:
        listed = {one["vps_file_id"]: one
                  for one in self._get("/vps/entry/vps-mm/releases")["releases"]}
        self.assertEqual(("taf", "t1"), (listed["mm-over-taf"]["mod_of"]["game_id"],
                                         listed["mm-over-taf"]["mod_of"]["table_id"]))
        self.assertEqual(("", ""), (listed["mm-one"]["mod_of"]["game_id"],
                                    listed["mm-one"]["mod_of"]["table_id"]))


if __name__ == "__main__":
    unittest.main()

"""An extension reads a VPS entry and its releases as an HTTP client does.

It is the service the `/vps/entry` routes call, handed over by name, so the two cannot
answer differently. Compared against the routes here for that reason, and the one thing a
caller of releases depends on is held down: every link of a file is kept.
"""

from __future__ import annotations

import unittest
from typing import cast
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.extensions.context import ExtensionFiles
from common.extensions.contract import ContractError
from common.extensions.games import ExtensionGames, withdraw_all

NO_FILES = cast(ExtensionFiles, None)

TAXI = "aaaaaaaaaa"
MODS = "bbbbbbbbbb"

ENTRIES = [
    {"id": TAXI, "name": "Taxi", "manufacturer": "Williams", "year": 1988, "type": "SS",
     "tableFiles": [
         {"id": "t1", "version": "1.2", "authors": ["Ada", "Ben"], "tableFormat": "VPX",
          "features": ["VR"], "comment": "Plays well", "updatedAt": 1700000000000,
          "urls": [{"url": "https://gone.example/t1", "broken": True},
                   {"url": "https://host.example/t1"},
                   {"url": "https://other.example/t1"},
                   {"url": "not a web address"}]},
         {"id": "t2", "version": "2", "urls": [{"url": "https://gone.example/t2",
                                                "broken": True}]},
         {"id": "t3", "version": "3"},
     ],
     "b2sFiles": [{"id": "b1", "version": "4",
                   "urls": [{"url": "https://host.example/b1"}]}]},
    {"id": MODS, "name": "Pinbot", "manufacturer": "Williams", "year": 1986, "type": "SS",
     "tableFiles": [
         {"id": "m1", "version": "1", "authors": ["Cy"]},
         {"id": "m2", "version": "2", "parentId": "m1", "features": ["MOD"]},
         {"id": "m3", "version": "3", "features": ["MOD"], "comment": "tagged only"},
         {"id": "m4", "version": "4", "parentId": "nowhere"},
         {"id": "m5", "version": "5", "parentId": "t1"},
         {"id": "m6", "version": "6"},
     ]},
]


class VpsOfferedTests(unittest.TestCase):
    def setUp(self) -> None:
        withdraw_all()
        self.addCleanup(withdraw_all)
        patcher = patch("common.games.game_service.load_vpsdb",
                        side_effect=lambda *a, **k: list(ENTRIES))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.reader = ExtensionGames("reader", ("games:read",), NO_FILES)

    def _routed(self, path: str, **params: str) -> dict:
        response = self.client.get(path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_catalog_is_read_with_games_read_alone(self) -> None:
        self.assertIn("vps_entry", self.reader.reaches())
        self.assertIn("vps_releases", self.reader.reaches())

    def test_an_extension_without_it_cannot_read_the_catalog(self) -> None:
        bare = ExtensionGames("bare", ("games:write",), NO_FILES)

        for name in ("vps_entry", "vps_releases"):
            with self.subTest(name=name), self.assertRaises(ContractError):
                getattr(bare, name)

    def test_an_entry_is_what_the_route_reports(self) -> None:
        self.assertEqual(self.reader.vps_entry(TAXI), self._routed(f"/vps/entry/{TAXI}"))

    def test_an_entry_says_what_the_catalog_lists(self) -> None:
        said = self.reader.vps_entry(TAXI)

        self.assertEqual(
            {key: said[key] for key in ("vps_id", "name", "manufacturer", "year", "type",
                                        "releases", "img_url")},
            {"vps_id": TAXI, "name": "Taxi", "manufacturer": "Williams", "year": 1988,
             "type": "SS", "releases": 3, "img_url": ""})
        self.assertTrue(said["url"].endswith(TAXI))
        self.assertTrue(said["folder_name"].startswith("Taxi"))

    def test_a_file_says_what_the_catalog_lists_of_it(self) -> None:
        first = self.reader.vps_releases(TAXI)["releases"][0]

        self.assertEqual(
            {key: first[key] for key in ("vps_file_id", "version", "authors", "format",
                                         "features", "comment", "updated_at", "mod_of")},
            {"vps_file_id": "t1", "version": "1.2", "authors": ["Ada", "Ben"],
             "format": "VPX", "features": ["VR"], "comment": "Plays well",
             "updated_at": "2023-11-14T22:13:20Z", "mod_of": None})

    def test_a_mod_says_which_file_it_is_based_on_and_where_the_library_holds_it(self) -> None:
        held = {"m1": {"game_id": "g1", "table_id": "tb1", "name": "Pinbot"}}
        with patch("common.games.owned.held", return_value=held):
            listed = {one["vps_file_id"]: one["mod_of"]
                      for one in self.reader.vps_releases(MODS)["releases"]}

        self.assertEqual(listed["m2"], {
            "vps_file_id": "m1", "version": "1", "authors": ["Cy"], "game": "",
            "url": listed["m2"]["url"], "note": "", "game_id": "g1", "table_id": "tb1"})
        self.assertTrue(listed["m2"]["url"].endswith(MODS))
        self.assertEqual(listed["m5"]["game"], "Taxi")
        self.assertEqual(listed["m5"]["vps_file_id"], "t1")
        self.assertEqual(listed["m5"]["game_id"], "")
        self.assertIsNone(listed["m1"])
        self.assertIsNone(listed["m6"])

    def test_a_mod_of_nothing_the_catalog_names_has_no_file_id(self) -> None:
        listed = {one["vps_file_id"]: one["mod_of"]
                  for one in self.reader.vps_releases(MODS)["releases"]}

        self.assertEqual(listed["m3"]["vps_file_id"], "")
        self.assertEqual(listed["m3"]["note"], "tagged only")
        self.assertEqual(listed["m4"]["vps_file_id"], "")
        self.assertEqual(listed["m4"]["note"], "")

    def test_releases_are_what_the_route_reports(self) -> None:
        self.assertEqual(self.reader.vps_releases(TAXI),
                         self._routed(f"/vps/entry/{TAXI}/releases"))
        self.assertEqual(self.reader.vps_releases(TAXI, "b2sFiles"),
                         self._routed(f"/vps/entry/{TAXI}/releases", listed_as="b2sFiles"))

    def test_every_link_of_a_file_comes_across(self) -> None:
        first = self.reader.vps_releases(TAXI)["releases"][0]

        self.assertEqual(first["urls"], [
            {"url": "https://gone.example/t1", "broken": True},
            {"url": "https://host.example/t1", "broken": False},
            {"url": "https://other.example/t1", "broken": False},
        ])

    def test_url_is_the_first_link_not_marked_broken(self) -> None:
        listed = self.reader.vps_releases(TAXI)["releases"]

        self.assertEqual(listed[0]["url"], "https://host.example/t1")
        self.assertEqual(listed[1]["url"], "https://gone.example/t2")
        self.assertEqual(listed[2]["url"], "")
        self.assertEqual(listed[2]["urls"], [])

    def test_releases_come_in_the_order_the_catalog_holds_them(self) -> None:
        listed = self.reader.vps_releases(TAXI)["releases"]

        self.assertEqual([one["vps_file_id"] for one in listed], ["t1", "t2", "t3"])

    def test_a_kind_is_named_as_the_catalog_names_it(self) -> None:
        listed = self.reader.vps_releases(TAXI, "b2sFiles")["releases"]

        self.assertEqual([one["vps_file_id"] for one in listed], ["b1"])

    def test_an_unknown_entry_is_refused(self) -> None:
        for name in ("vps_entry", "vps_releases"):
            with self.subTest(name=name), \
                    self.assertRaises(service_errors.NotFoundError) as caught:
                getattr(self.reader, name)("zzzzzzzzzz")
            self.assertEqual(caught.exception.details, {"vps_id": "zzzzzzzzzz"})

    def test_an_unknown_kind_is_refused_and_the_known_ones_listed(self) -> None:
        with self.assertRaises(service_errors.RefusedError) as caught:
            self.reader.vps_releases(TAXI, "tutorialFiles")

        self.assertEqual(caught.exception.details["listed_as"], "tutorialFiles")
        self.assertIn("tableFiles", caught.exception.details["known"])
        self.assertIn("b2sFiles", caught.exception.details["known"])


if __name__ == "__main__":
    unittest.main()

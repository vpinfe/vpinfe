"""The release index: who made each release, what it is, and which one the library holds."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import library_vps_state as rollup
from common.online import vps_releases

CATALOG = [
    {"id": "E1", "name": "Alpha", "tableFiles": [
        {"id": "t1", "authors": ["guet35", "VPW"], "tableFormat": "VPX",
         "features": ["MOD", "VR"], "createdAt": 1760313600000},
        {"id": "t2", "authors": ["Guet35"], "tableFormat": "FP", "parentId": "t1"},
        {"id": "t3", "authors": [" VPW Team", "VPW Testers"], "parentId": "t3"}],
     "b2sFiles": [{"id": "b1", "authors": ["Wildman", "Guet35"], "features": ["2Screens"]}]},
    {"id": "E2", "name": "Beta", "romFiles": [{"id": "r1", "authors": ["Destruk"]},
                                              {"id": "r2", "authors": ["Guet35", "guet35"]}],
     "tutorialFiles": [{"id": "x", "authors": ["Someone"]}]},
    {"id": "E3", "name": "Gamma", "tableFiles": []},
]


class ReleaseIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(vps_releases, "load_vpsdb", return_value=CATALOG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, vps_releases, "_built", None)
        vps_releases._built = None
        self.built = vps_releases.index()
        self.people = self.built["people"]

    def _names(self, release: dict) -> list[str]:
        return [self.people[one]["name"] for one in release["by"]]

    def test_spellings_of_one_name_are_one_person_and_a_release_counts_once(self) -> None:
        names = [one["name"] for one in self.people]
        self.assertEqual(names.count("Guet35"), 1)
        self.assertNotIn("guet35", names)
        guet = next(one for one in self.people if one["name"] == "Guet35")
        self.assertEqual((guet["aliases"], guet["releases"]), (["guet35"], 4))

    def test_the_workshop_credits_are_one_person_named_vpw(self) -> None:
        vpw = next(one for one in self.people if one["name"] == "VPW")
        self.assertEqual(vpw["aliases"], ["VPinWorkshop", "VPW Team", "VPW Testers"])
        by_id = {one["id"]: one for one in self.built["entries"]["E1"]["tableFiles"]}
        self.assertEqual(self._names(by_id["t3"]), ["VPW"])

    def test_a_release_names_each_person_once_in_the_order_vps_credits_them(self) -> None:
        by_id = {one["id"]: one for one in self.built["entries"]["E1"]["tableFiles"]}
        self.assertEqual(self._names(by_id["t1"]), ["Guet35", "VPW"])
        self.assertEqual(self._names(self.built["entries"]["E1"]["b2sFiles"][0]),
                         ["Wildman", "Guet35"])
        self.assertEqual(self._names(self.built["entries"]["E2"]["romFiles"][1]), ["Guet35"])

    def test_mod_is_a_tag_or_a_link_to_another_release(self) -> None:
        by_id = {one["id"]: one for one in self.built["entries"]["E1"]["tableFiles"]}
        self.assertEqual({key: one["mod"] for key, one in by_id.items()},
                         {"t1": True, "t2": True, "t3": False})

    def test_format_features_and_the_date_added_are_carried(self) -> None:
        first, second, _third = self.built["entries"]["E1"]["tableFiles"]
        self.assertEqual((first["format"], first["features"], first["added"]),
                         ("VPX", ["MOD", "VR"], "2025-10-13T00:00:00Z"))
        self.assertEqual((second["format"], second["added"]), ("FP", ""))

    def test_the_version_is_carried_trimmed_and_blank_where_vps_has_none(self) -> None:
        with patch.object(vps_releases, "load_vpsdb", return_value=[
                {"id": "V1", "name": "V", "tableFiles": [
                    {"id": "a", "version": " 1.2 "}, {"id": "b"}]}]):
            self.assertEqual([one["version"] for one
                              in vps_releases.index()["entries"]["V1"]["tableFiles"]],
                             ["1.2", ""])

    def test_only_listings_that_have_releases_are_kept(self) -> None:
        self.assertEqual(sorted(self.built["entries"]["E1"]), ["b2sFiles", "tableFiles"])
        self.assertEqual(sorted(self.built["entries"]["E2"]), ["romFiles"])
        self.assertNotIn("E3", self.built["entries"])

    def test_a_new_catalog_rebuilds_it(self) -> None:
        with patch.object(vps_releases, "load_vpsdb", return_value=[CATALOG[1]]):
            self.assertEqual(sorted(vps_releases.index()["entries"]), ["E2"])

    def test_held_follows_the_ids_bound_to_the_entry(self) -> None:
        held = vps_releases.of_entry(vps_releases.index(), "E1", {"b1", "t2"})
        self.assertEqual({one["id"]: one["held"] for one in held["tableFiles"]},
                         {"t1": False, "t2": True, "t3": False})
        self.assertEqual([one["held"] for one in held["b2sFiles"]], [True])


class RollupRecordsWhatIsHeldTests(unittest.TestCase):
    def test_the_count_keeps_the_release_ids_files_and_tables_are_bound_to(self) -> None:
        class Game:
            meta_config: dict = {"tables": {}}

        state = {"matched": True, "vps_id": "E1",
                 "tables": [{"name": "a.vpx", "vps_file_id": "t1"},
                            {"name": "b.vpx", "vps_file_id": ""}],
                 "kinds": [{"kind": "b2sFiles", "held": True,
                            "files": [{"name": "a.directb2s", "vps_file_id": "b1"}]}]}
        counted = rollup.compute({"g": Game()}, lambda game, game_id: state)

        self.assertEqual(counted["entries"]["E1"]["bound"], ["b1", "t1"])


class DiscoveryRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.addCleanup(setattr, vps_releases, "_built", None)
        vps_releases._built = None

    def _get(self, counted: dict) -> dict:
        with patch.object(rollup, "stored", return_value=counted), \
                patch.object(rollup, "load_vpsdb", return_value=CATALOG), \
                patch.object(vps_releases, "load_vpsdb", return_value=CATALOG):
            return self.client.get("/library/discovery").json()

    def test_rows_carry_their_releases_and_the_response_its_people(self) -> None:
        body = self._get({"computed": "x", "entries": {"E1": {"tables": 1, "held": [],
                                                             "bound": ["t2"]}}})

        rows = {one["vps_id"]: one for one in body["rows"]}
        held = {one["id"]: one["held"] for one in rows["E1"]["releases"]["tableFiles"]}
        self.assertEqual(held, {"t1": False, "t2": True, "t3": False})
        self.assertEqual(rows["E3"]["releases"], {})
        self.assertIn("Wildman", [one["name"] for one in body["people"]])

    def test_a_count_from_before_the_index_holds_nothing_rather_than_failing(self) -> None:
        body = self._get({"computed": "x", "entries": {"E1": {"tables": 1, "held": []}}})

        rows = {one["vps_id"]: one for one in body["rows"]}
        self.assertFalse(any(one["held"] for one in rows["E1"]["releases"]["tableFiles"]))


if __name__ == "__main__":
    unittest.main()

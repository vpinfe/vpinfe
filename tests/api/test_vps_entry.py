"""A VPSdb entry reads the same whether you searched for it or asked for it by id.

Two routes answer with one response model, and they built their field lists separately.
That pair drifts a field at a time and the shared model does not catch it, because a
field the builder never sets just takes its default.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi

# A machine pictured itself, one pictured only by its backglasses, one only by its
# tables, and one with no picture at all. The older record comes first where there are
# two, so taking the first would pick the wrong one.
ENTRIES = [
    {"id": "aaaaaaaaaa", "name": "Attack from Mars", "manufacturer": "Bally",
     "year": 1995, "type": "SS", "imgUrl": "https://example.invalid/afm.png",
     "b2sFiles": [{"id": "b1", "createdAt": 2, "imgUrl": "https://example.invalid/b1.png"}],
     "tableFiles": [{"id": "f1"}, {"id": "f2"}]},
    {"id": "bbbbbbbbbb", "name": "Space Invaders", "manufacturer": "Bally",
     "year": 1980, "type": "EM", "tableFiles": []},
    {"id": "cccccccccc", "name": "Centaur", "manufacturer": "Bally", "year": 1981,
     "type": "SS",
     "b2sFiles": [{"id": "b2", "createdAt": 1, "imgUrl": "https://example.invalid/old.png"},
                  {"id": "b3", "createdAt": 3, "imgUrl": "https://example.invalid/new.png"},
                  {"id": "b4", "createdAt": None, "imgUrl": "https://example.invalid/b4.png"}],
     "tableFiles": [{"id": "f3", "createdAt": 9, "imgUrl": "https://example.invalid/f3.png"}]},
    {"id": "dddddddddd", "name": "Check", "manufacturer": "Recel", "year": 1975,
     "type": "EM", "b2sFiles": [{"id": "b5", "createdAt": 8}],
     "tableFiles": [{"id": "f4", "createdAt": 4, "imgUrl": "https://example.invalid/f4.png"},
                    {"id": "f5", "createdAt": 5, "imgUrl": "https://example.invalid/f5.png"}]},
]


class VpsEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        for name in ("load_vpsdb", "search_vpsdb"):
            patcher = patch(f"common.games.game_service.{name}",
                            side_effect=lambda *a, **k: list(ENTRIES))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def _searched(self, vps_id: str) -> dict:
        response = self.client.get("/vps/search", params={"q": "a"})
        self.assertEqual(response.status_code, 200, response.text)
        return next(r for r in response.json()["results"] if r["vps_id"] == vps_id)

    def _looked_up(self, vps_id: str) -> dict:
        response = self.client.get(f"/vps/entry/{vps_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_both_routes_report_the_same_entry(self) -> None:
        self.assertEqual(self._looked_up("aaaaaaaaaa"), self._searched("aaaaaaaaaa"))

    def test_the_machines_own_picture_leads(self) -> None:
        self.assertEqual(self._looked_up("aaaaaaaaaa")["img_url"],
                         "https://example.invalid/afm.png")

    def test_without_one_the_newest_backglass_stands_in(self) -> None:
        self.assertEqual(self._looked_up("cccccccccc")["img_url"],
                         "https://example.invalid/new.png")

    def test_without_a_backglass_picture_the_newest_table_stands_in(self) -> None:
        self.assertEqual(self._looked_up("dddddddddd")["img_url"],
                         "https://example.invalid/f5.png")

    def test_a_search_reports_the_same_stand_in(self) -> None:
        self.assertEqual(self._searched("cccccccccc")["img_url"],
                         "https://example.invalid/new.png")

    def test_an_entry_with_no_photograph_says_so_with_a_blank(self) -> None:
        """Not null: a surface that lays out around art needs one falsy thing to test,
        and the model would have to widen to carry two of them."""
        self.assertEqual(self._looked_up("bbbbbbbbbb")["img_url"], "")

    def test_releases_counts_the_builds(self) -> None:
        self.assertEqual(self._looked_up("aaaaaaaaaa")["releases"], 2)
        self.assertEqual(self._looked_up("bbbbbbbbbb")["releases"], 0)


if __name__ == "__main__":
    unittest.main()

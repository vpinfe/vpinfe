"""Which Overview rows are set aside, and over what.

A dismissal is what only a person can say, so it is kept apart from anything the library
derives. A row stays away while every id it counts is in its list.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import dismissals


class DismissalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "dismissed.json"
        patcher = patch.object(dismissals, "PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_a_row_nobody_dismissed_is_absent(self) -> None:
        self.assertEqual({"rows": []}, self.client.get("/library/dismissed").json())

    def test_a_dismissal_replaces_what_the_row_held_and_none_takes_it_back(self) -> None:
        self.client.put("/library/dismissed/no_year", json={"ids": ["a", "b"]})
        self.client.put("/library/dismissed/no_year", json={"ids": ["b", "c", "c"]})

        self.assertEqual({"rows": [{"row": "no_year", "ids": ["b", "c"]}]},
                         self.client.get("/library/dismissed").json())

        self.client.put("/library/dismissed/no_year", json={"ids": []})

        self.assertEqual({"rows": []}, self.client.get("/library/dismissed").json())

    def test_a_store_from_another_schema_is_ignored(self) -> None:
        self.path.write_text(json.dumps({"schema": 99, "rows": {"no_year": ["a"]}}),
                             encoding="utf-8")

        self.assertEqual({}, dismissals.dismissed())

    def test_a_store_that_cannot_be_read_is_empty(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")

        self.assertEqual({}, dismissals.dismissed())

    def test_the_old_watching_file_is_not_read(self) -> None:
        """Dismissals used to live beside the catalog baseline; nothing carries over."""
        (self.path.parent / "watching.json").write_text(
            json.dumps({"schema": 1, "dismissed": {"no_year": ["a"]}}), encoding="utf-8")

        self.assertEqual({}, dismissals.dismissed())


if __name__ == "__main__":
    unittest.main()

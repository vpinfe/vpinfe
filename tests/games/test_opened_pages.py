"""The release pages somebody opened: kept in the install's state, for a day, newest first."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from common.games import opened_pages


def _at(**ago: float) -> str:
    then = datetime.now(UTC) - timedelta(**ago)
    return then.isoformat(timespec="seconds").replace("+00:00", "Z")


class OpenedPages(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "state" / "opened_pages.json"
        self.enterContext(mock.patch.object(opened_pages, "OPENED_PAGES_PATH", self.path))

    def test_nothing_was_opened_before_anything_is_noted(self) -> None:
        self.assertEqual([], opened_pages.recent("g1"))

    def test_a_page_opened_is_found_for_its_game_and_kind(self) -> None:
        opened_pages.note("g1", "tableFiles", "f1", "https://vps/f1")

        (found,) = opened_pages.recent("g1", "tableFiles")

        self.assertEqual(("f1", "https://vps/f1"), (found["vps_file_id"], found["page"]))
        self.assertEqual([], opened_pages.recent("g1", "b2sFiles"))
        self.assertEqual([], opened_pages.recent("g2"))

    def test_opening_a_page_again_moves_it_to_the_front_instead_of_listing_it_twice(self) -> None:
        opened_pages.note("g1", "tableFiles", "f1", "p1")
        opened_pages.note("g1", "tableFiles", "f2", "p2")

        opened_pages.note("g1", "tableFiles", "f1", "p1")

        self.assertEqual(["f1", "f2"], [one["vps_file_id"] for one in opened_pages.recent("g1")])

    def test_the_same_release_opened_for_another_game_is_a_page_of_its_own(self) -> None:
        opened_pages.note("g1", "tableFiles", "f1", "p1")
        opened_pages.note("g2", "tableFiles", "f1", "p1")

        self.assertEqual(1, len(opened_pages.recent("g1")))
        self.assertEqual(1, len(opened_pages.recent("g2")))

    def test_only_the_most_recent_fifty_are_kept(self) -> None:
        for number in range(opened_pages.KEEP + 5):
            opened_pages.note("g1", "tableFiles", f"f{number}", "p")

        found = opened_pages.recent("g1")

        self.assertEqual(opened_pages.KEEP, len(found))
        self.assertEqual(f"f{opened_pages.KEEP + 4}", found[0]["vps_file_id"])

    def test_a_page_opened_over_a_day_ago_is_gone(self) -> None:
        old = _at(hours=25)
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            '{"schema": 1, "opened": [{"game_id": "g1", "kind": "tableFiles", '
            f'"vps_file_id": "f1", "page": "p", "at": "{old}"}}]}}', encoding="utf-8")

        self.assertEqual([], opened_pages.recent("g1"))

        opened_pages.note("g1", "tableFiles", "f2", "p")

        self.assertEqual(["f2"], [one["vps_file_id"] for one in opened_pages.recent("g1")])

    def test_every_page_opened_inside_the_day_is_listed_newest_first_across_games(self) -> None:
        opened_pages.note("g1", "tableFiles", "f1", "p1")
        opened_pages.note("g2", "b2sFiles", "f2", "p2")
        kept = json.loads(self.path.read_text(encoding="utf-8"))
        kept["opened"].append({"game_id": "g3", "kind": "tableFiles", "vps_file_id": "f9",
                               "page": "p", "at": _at(hours=25)})
        self.path.write_text(json.dumps(kept), encoding="utf-8")

        self.assertEqual([("g2", "f2"), ("g1", "f1")],
                         [(one["game_id"], one["vps_file_id"])
                          for one in opened_pages.all_recent()])

    def test_forgetting_a_release_leaves_the_rest_of_the_game(self) -> None:
        opened_pages.note("g1", "tableFiles", "f1", "p1")
        opened_pages.note("g1", "b2sFiles", "f2", "p2")

        opened_pages.forget("g1", "f1")

        self.assertEqual(["f2"], [one["vps_file_id"] for one in opened_pages.recent("g1")])

    def test_forgetting_a_game_drops_every_page_of_it_and_none_of_another(self) -> None:
        opened_pages.note("g1", "tableFiles", "f1", "p1")
        opened_pages.note("g1", "b2sFiles", "f2", "p2")
        opened_pages.note("g2", "tableFiles", "f3", "p3")

        opened_pages.forget("g1")

        self.assertEqual([], opened_pages.recent("g1"))
        self.assertEqual(1, len(opened_pages.recent("g2")))

    def test_a_file_that_cannot_be_read_is_an_empty_list(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")

        self.assertEqual([], opened_pages.recent("g1"))

    def test_a_file_of_another_schema_is_an_empty_list(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"schema": 99, "opened": [{"game_id": "g1"}]}', encoding="utf-8")

        self.assertEqual([], opened_pages.recent("g1"))


if __name__ == "__main__":
    unittest.main()

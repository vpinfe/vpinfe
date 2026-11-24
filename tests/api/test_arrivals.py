"""What is kept about when a source's items were first seen.

Two jobs only: dating what a source gives no date for, and saying what a re-dated release
was. The mistakes worth a test are the ones nobody would notice, such as a source's whole
backlog reading as news the day it is first watched.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.online import arrivals
from common.online.sources import Listed


def _item(item_id: str, version: str = "1.0", date: str = "") -> Listed:
    return Listed(item_id, "vps-1", "tableFiles", version, date)


class ArrivalsTests(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.path = Path(held.name) / "arrivals.json"
        patcher = patch.object(arrivals, "PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_first_look_at_a_source_finds_nothing_new(self) -> None:
        """A source added today has years of backlog; none of it arrived today."""
        self.assertEqual([], arrivals.stamp("site", [_item("a"), _item("b")]))
        self.assertTrue(arrivals.seeded("site"))
        self.assertEqual("", arrivals.first_seen("site", "a"))

    def test_an_item_that_appears_later_has_arrived(self) -> None:
        arrivals.stamp("site", [_item("a")])

        self.assertEqual(["b"], arrivals.stamp("site", [_item("a"), _item("b")]))
        self.assertNotEqual("", arrivals.first_seen("site", "b"))
        self.assertEqual("", arrivals.first_seen("site", "a"))

    def test_an_item_seen_again_is_not_new_again(self) -> None:
        arrivals.stamp("site", [_item("a")])
        arrivals.stamp("site", [_item("a"), _item("b")])

        self.assertEqual([], arrivals.stamp("site", [_item("a"), _item("b")]))

    def test_a_source_that_lists_nothing_yet_is_not_seeded(self) -> None:
        """A catalog still downloading would otherwise turn its whole backlog into news
        the moment it finished."""
        arrivals.stamp("site", [])

        self.assertFalse(arrivals.seeded("site"))
        self.assertEqual([], arrivals.stamp("site", [_item("a")]))

    def test_an_item_that_stops_being_listed_is_remembered(self) -> None:
        arrivals.stamp("site", [_item("a")])
        arrivals.stamp("site", [_item("a"), _item("b")])
        arrivals.stamp("site", [_item("a")])

        self.assertEqual([], arrivals.stamp("site", [_item("a"), _item("b")]))

    def test_a_release_re_dated_says_what_it_was(self) -> None:
        arrivals.stamp("site", [_item("a", "1.0")])

        arrivals.stamp("site", [_item("a", "1.1")])

        self.assertEqual("1.0", arrivals.was("site", "a"))
        self.assertEqual("", arrivals.first_seen("site", "a"))

    def test_a_release_never_changed_was_nothing(self) -> None:
        arrivals.stamp("site", [_item("a", "1.0")])

        self.assertEqual("", arrivals.was("site", "a"))
        self.assertEqual("", arrivals.was("site", "unknown"))

    def test_a_re_dated_release_is_not_an_arrival(self) -> None:
        arrivals.stamp("site", [_item("a", "1.0")])

        self.assertEqual([], arrivals.stamp("site", [_item("a", "1.1")]))

    def test_each_source_keeps_its_own(self) -> None:
        arrivals.stamp("one", [_item("a")])

        self.assertFalse(arrivals.seeded("two"))
        self.assertEqual([], arrivals.stamp("two", [_item("a")]))

    def test_the_date_a_source_gives_wins(self) -> None:
        arrivals.stamp("site", [_item("a")])
        arrivals.stamp("site", [_item("a"), _item("b")])

        self.assertEqual("2026-01-01", arrivals.dated("site", _item("b", date="2026-01-01")))

    def test_a_source_with_no_dates_is_dated_by_when_it_arrived(self) -> None:
        arrivals.stamp("site", [_item("a")])
        arrivals.stamp("site", [_item("a"), _item("b")])

        self.assertEqual(arrivals.first_seen("site", "b"), arrivals.dated("site", _item("b")))
        self.assertEqual("", arrivals.dated("site", _item("a")))

    def test_a_store_from_another_schema_is_ignored(self) -> None:
        self.path.write_text(json.dumps({"schema": 99, "sources": {"site": {"items": {}}}}),
                             encoding="utf-8")

        self.assertFalse(arrivals.seeded("site"))

    def test_a_store_that_cannot_be_read_is_empty(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")

        self.assertFalse(arrivals.seeded("site"))
        self.assertEqual([], arrivals.stamp("site", [_item("a")]))

    def test_a_pass_over_many_releases_reads_the_file_once(self) -> None:
        arrivals.stamp("site", [_item(str(n)) for n in range(50)])
        arrivals.stamp("site", [_item(str(n)) for n in range(51)])
        reads = patch.object(Path, "read_text", autospec=True, side_effect=Path.read_text)

        with reads as read:
            for n in range(51):
                arrivals.first_seen("site", str(n))
                arrivals.was("site", str(n))
                arrivals.dated("site", _item(str(n)))

        self.assertLessEqual(read.call_count, 1)

    def test_what_is_stamped_is_read_back_at_once(self) -> None:
        arrivals.stamp("site", [_item("a")])
        self.assertEqual("", arrivals.first_seen("site", "b"))

        arrivals.stamp("site", [_item("a"), _item("b")])

        self.assertNotEqual("", arrivals.first_seen("site", "b"))

    def test_a_file_changed_by_another_process_is_read_again(self) -> None:
        arrivals.stamp("site", [_item("a")])
        self.assertTrue(arrivals.seeded("site"))

        self.path.write_text(json.dumps({"schema": arrivals.SCHEMA, "sources": {}}) + "  ",
                             encoding="utf-8")

        self.assertFalse(arrivals.seeded("site"))


if __name__ == "__main__":
    unittest.main()

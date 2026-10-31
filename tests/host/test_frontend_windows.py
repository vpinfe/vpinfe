"""Where each window says it opened: what is kept of a report, and what is refused."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.host import frontend_windows as fw

REPORT = {"window": "backglass", "x": 3840, "y": 0, "width": 1920, "height": 1080,
          "scale": 1}


class RecordTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name) / "cache" / "frontend_windows.json"
        patch = mock.patch.object(fw.paths, "FRONTEND_WINDOWS_PATH", self.store)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_window_s_report_is_kept_by_name(self) -> None:
        fw.record(REPORT)
        fw.record({**REPORT, "window": "playfield", "x": 0})
        held = fw.read()
        self.assertEqual(sorted(held), ["backglass", "playfield"])
        self.assertEqual((held["backglass"]["x"], held["backglass"]["width"]), (3840, 1920))
        self.assertTrue(held["backglass"]["reported_at"])

    def test_a_second_report_replaces_the_first(self) -> None:
        fw.record(REPORT)
        fw.record({**REPORT, "x": 0})
        self.assertEqual(fw.read()["backglass"]["x"], 0)

    def test_a_name_no_window_can_have_is_refused(self) -> None:
        for name in ("", "../x", "a b", "x" * 33, None, 7):
            fw.record({**REPORT, "window": name})
        self.assertEqual(fw.read(), {})

    def test_a_number_that_is_not_one_is_not_kept(self) -> None:
        fw.record({**REPORT, "x": "left", "width": 10**9, "scale": 99, "height": True})
        held = fw.read()["backglass"]
        self.assertEqual([held[key] for key in ("x", "width", "scale", "height")],
                         [None, None, None, None])

    def _stamped(self) -> None:
        count = iter(range(1, 1000))
        patch = mock.patch.object(fw, "utc_now_iso", lambda: f"2026-10-25T00:00:{next(count):02d}Z")
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_page_cannot_fill_the_file_with_windows(self) -> None:
        self._stamped()
        for number in range(fw.MOST + 5):
            fw.record({**REPORT, "window": f"w{number:02d}"})
        self.assertEqual(len(fw.read()), fw.MOST)

    def test_a_new_name_past_the_limit_takes_the_place_of_the_oldest(self) -> None:
        self._stamped()
        for number in range(fw.MOST):
            fw.record({**REPORT, "window": f"w{number:02d}"})
        fw.record({**REPORT, "window": "w00", "x": 5})
        fw.record({**REPORT, "window": "playfield"})
        held = fw.read()
        self.assertEqual(len(held), fw.MOST)
        self.assertIn("playfield", held)
        self.assertEqual(held["w00"]["x"], 5)
        self.assertNotIn("w01", held)

    def test_something_that_is_not_a_report_is_refused(self) -> None:
        self.assertEqual(fw.record("not a report"), {})

    def test_a_file_that_is_not_json_reads_as_nothing(self) -> None:
        self.store.parent.mkdir(parents=True)
        self.store.write_text("{", encoding="utf-8")
        self.assertEqual(fw.read(), {})
        self.store.write_text(json.dumps([1]), encoding="utf-8")
        self.assertEqual(fw.read(), {})


if __name__ == "__main__":
    unittest.main()

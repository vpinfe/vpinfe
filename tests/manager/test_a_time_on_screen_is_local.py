"""A time drawn in the Manager is the reader's, and the cell behind it still sorts."""

from __future__ import annotations

import ast
import pathlib
import unittest
from datetime import UTC, datetime, timedelta

from common import i18n
from common.i18n import t
from manager.kit import when
from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"


class LocalTime(unittest.TestCase):
    def test_a_utc_stamp_becomes_local(self) -> None:
        at = datetime.fromisoformat("2026-09-22T12:46:11+00:00").astimezone()
        self.assertEqual(f"{when.day(at)} {when.clock(at)}", when.local("2026-09-22T12:46:11Z"))

    def test_an_offset_is_read_as_well_as_a_z(self) -> None:
        self.assertEqual(when.local("2026-09-22T12:46:11Z"),
                         when.local("2026-09-22T12:46:11+00:00"))

    def test_nothing_in_becomes_nothing_out(self) -> None:
        self.assertEqual("", when.local(""))

    def test_an_unreadable_stamp_comes_back_whole(self) -> None:
        self.assertEqual("not a time", when.local("not a time"))

    def test_the_zone_is_resolved_not_read_once(self) -> None:
        winter = when.local("2026-01-15T12:00:00Z")
        summer = when.local("2026-07-15T12:00:00Z")
        if datetime(2026, 1, 15, tzinfo=UTC).astimezone().utcoffset() == \
                datetime(2026, 7, 15, tzinfo=UTC).astimezone().utcoffset():
            self.skipTest("this machine's zone has no summer time")
        self.assertNotEqual(winter.split()[-1], summer.split()[-1])


class HowLongAgo(unittest.TestCase):
    NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)

    def _ago(self, **delta: float) -> str:
        return when.ago((self.NOW - timedelta(**delta)).isoformat(), self.NOW)

    def test_under_a_minute_is_just_now(self) -> None:
        self.assertEqual(t("date.just_now"), self._ago(seconds=20))

    def test_minutes_hours_and_days_take_their_plural(self) -> None:
        self.assertEqual(t("date.minutes_ago", count=1), self._ago(minutes=1))
        self.assertEqual(t("date.minutes_ago", count=9), self._ago(minutes=9))
        self.assertEqual(t("date.hours_ago", count=3), self._ago(hours=3))
        self.assertEqual(t("date.days_ago", count=12), self._ago(days=12))

    def test_a_month_on_it_is_the_date(self) -> None:
        stamp = self.NOW - timedelta(days=45)
        self.assertEqual(i18n.date(stamp.astimezone()),
                         when.ago(stamp.isoformat(), self.NOW))

    def test_a_stamp_ahead_of_the_clock_says_the_time(self) -> None:
        ahead = (self.NOW + timedelta(minutes=5)).isoformat()
        self.assertEqual(when.local(ahead), when.ago(ahead, self.NOW))

    def test_nothing_is_nothing(self) -> None:
        self.assertEqual("", when.ago(""))

    def test_a_row_carries_the_words_and_the_exact_time(self) -> None:
        stamp = (self.NOW - timedelta(minutes=9)).isoformat()
        row = when.said({"id": "a", "last_seen": stamp}, "last_seen", self.NOW)
        self.assertEqual(stamp, row["last_seen"])
        self.assertEqual(t("date.minutes_ago", count=9), row["last_seen_ago"])
        self.assertEqual(when.local(stamp), row["last_seen_at"])


class TheCellKeepsTheValue(unittest.TestCase):
    def test_devices_puts_a_sortable_stamp_in_the_row(self) -> None:
        tree = trees.tree_for(MANAGER / "devices" / "devices.py")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "last_seen"
                    for k in node.keys if k is not None)):
                continue
            value = next(v for k, v in zip(node.keys, node.values, strict=False)
                         if isinstance(k, ast.Constant) and k.value == "last_seen")
            # A bare `_when(...)` is a Name, `when.local(...)` an Attribute.
            called = {getattr(sub.func, "attr", "") or getattr(sub.func, "id", "")
                      for sub in ast.walk(value) if isinstance(sub, ast.Call)}
            self.assertEqual(
                set(), called & {"local", "_when"},
                "last_seen is drawn into the row rather than the column")

    def test_the_column_draws_it(self) -> None:
        source = (MANAGER / "devices" / "devices.py").read_text(encoding="utf-8")
        self.assertIn('when.cell("last_seen")', source,
                      "the Last Seen column has no formatter")

    def test_the_drawing_holds_no_words_of_its_own(self) -> None:
        """The formatter reads a field; the words come from the catalog."""
        options = when.cell("last_seen")
        self.assertEqual("last_seen_at", options["tooltipField"])
        self.assertNotRegex(options[":valueFormatter"], r"\b(ago|minute|hour|day|now)\b")

    def test_iso_stamps_sort_chronologically_as_text(self) -> None:
        base = datetime(2026, 9, 22, 12, 46, 11, tzinfo=UTC)
        stamps = [(base + timedelta(seconds=n)).isoformat(timespec="seconds")
                  .replace("+00:00", "Z")
                  for n in (0, 59, 60, 3600, 86400, 86400 * 400)]
        self.assertEqual(stamps, sorted(stamps))


if __name__ == "__main__":
    unittest.main()

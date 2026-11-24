"""Clean up: which copies each group holds, and what a ticked pair would delete."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from common import i18n
from common.backups import copies
from common.i18n import t
from manager.backups import backup_cleanup, backups

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
AGES = {backup_cleanup.YOURS: 3, backup_cleanup.AUTOMATIC: 3}


def _row(copy_id: str, **over) -> dict:
    found = {"id": copy_id, "subject_kind": copies.SUBJECT_GAME, "subject_id": "g1",
             "subject_name": "Attack from Mars",
             "kind": copies.KIND_GAME, "file": "a.info", "path": f"/library/{copy_id}",
             "taken": "2026-10-01T10:00:00Z", "written_by": copies.BY_3X,
             "reason": copies.REASON_UPGRADE, "note": "", "size": 1000, "gone": False, **over}
    return backups.rows([found], parts=True)[0]


def _ids(found: dict, group: str) -> list[str]:
    return sorted(row["id"] for row in found[group])


class MonthsBefore(unittest.TestCase):
    def test_a_month_back_is_the_same_day(self) -> None:
        self.assertEqual(datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
                         backup_cleanup.months_before(NOW, 3))

    def test_a_shorter_month_keeps_the_last_day(self) -> None:
        end = datetime(2026, 5, 31, tzinfo=UTC)

        self.assertEqual(datetime(2026, 2, 28, tzinfo=UTC), backup_cleanup.months_before(end, 3))

    def test_it_crosses_a_year(self) -> None:
        self.assertEqual(datetime(2025, 4, 6, 12, 0, tzinfo=UTC),
                         backup_cleanup.months_before(NOW, 18))


class Groups(unittest.TestCase):
    def test_a_copy_written_by_2x_is_in_the_2x_group_whatever_its_age(self) -> None:
        rows = [_row("old", written_by=copies.BY_2X, taken="2020-01-01T00:00:00Z"),
                _row("new", written_by=copies.BY_2X), _row("mine", written_by=copies.BY_3X)]

        found = backup_cleanup.members(rows, AGES, NOW)

        self.assertEqual(["new", "old"], _ids(found, backup_cleanup.TWO_X))

    def test_a_copy_of_something_gone_is_in_the_gone_group(self) -> None:
        found = backup_cleanup.members([_row("a", gone=True), _row("b")], AGES, NOW)

        self.assertEqual(["a"], _ids(found, backup_cleanup.GONE))

    def test_a_backup_is_gone_only_as_the_listing_says(self) -> None:
        archive = {"subject_kind": copies.SUBJECT_ARCHIVE, "kind": copies.KIND_ARCHIVE}
        zips = [_row("every", **archive, gone=True), _row("some", **archive, gone=False)]

        found = backup_cleanup.members(zips, AGES, NOW)

        self.assertEqual(["every"], _ids(found, backup_cleanup.GONE))

    def test_a_part_of_a_backup_is_in_no_group(self) -> None:
        part = _row("p", within="z", gone=True, written_by=copies.BY_2X,
                    taken="2020-01-01T00:00:00Z")

        found = backup_cleanup.members([part], AGES, NOW)

        self.assertEqual({group: [] for group in backup_cleanup.GROUPS}, found)

    def test_yours_and_automatic_each_take_their_own_age(self) -> None:
        old = "2026-06-01T00:00:00Z"
        rows = [_row("yours", reason=copies.REASON_YOURS, taken=old),
                _row("auto", reason=copies.REASON_UPGRADE, taken=old),
                _row("fresh-yours", reason=copies.REASON_YOURS), _row("fresh-auto")]

        found = backup_cleanup.members(rows, AGES, NOW)

        self.assertEqual(["yours"], _ids(found, backup_cleanup.YOURS))
        self.assertEqual(["auto"], _ids(found, backup_cleanup.AUTOMATIC))

    def test_the_age_is_the_one_asked_for(self) -> None:
        rows = [_row("a", taken="2026-08-01T00:00:00Z")]

        three = backup_cleanup.members(rows, AGES, NOW)
        one = backup_cleanup.members(rows, {**AGES, backup_cleanup.AUTOMATIC: 1}, NOW)

        self.assertEqual([], three[backup_cleanup.AUTOMATIC])
        self.assertEqual(["a"], _ids(one, backup_cleanup.AUTOMATIC))

    def test_a_copy_with_no_date_is_in_no_aged_group(self) -> None:
        found = backup_cleanup.members([_row("a", taken="")], AGES, NOW)

        self.assertEqual([], found[backup_cleanup.YOURS] + found[backup_cleanup.AUTOMATIC])


class Union(unittest.TestCase):
    def test_a_copy_in_two_ticked_groups_is_counted_once(self) -> None:
        rows = [_row("a", gone=True, written_by=copies.BY_2X), _row("b", gone=True)]
        found = backup_cleanup.members(rows, AGES, NOW)

        chosen = backup_cleanup.union(found, {backup_cleanup.TWO_X, backup_cleanup.GONE})

        self.assertEqual(["a", "b"], sorted(row["id"] for row in chosen))

    def test_an_unticked_group_adds_nothing(self) -> None:
        found = backup_cleanup.members([_row("a", gone=True)], AGES, NOW)

        self.assertEqual([], backup_cleanup.union(found, {backup_cleanup.TWO_X}))


class Counted(unittest.TestCase):
    def test_none_is_said_as_none(self) -> None:
        self.assertEqual(t("word.none"), backup_cleanup.counted([]))

    def test_a_group_says_how_many_and_how_big(self) -> None:
        said = backup_cleanup.counted([_row("a", size=1024), _row("b", size=2048)])

        self.assertEqual(t("manager.backups.summary", count=2, size=i18n.size(3072)), said)


if __name__ == "__main__":
    unittest.main()

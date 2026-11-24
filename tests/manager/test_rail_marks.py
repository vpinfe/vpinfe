"""The Jobs entry counts the jobs worth reading, and a closed group says the most urgent
thing under it."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from common.i18n import t
from common.runtime import jobs
from manager import rail_marks
from manager.rail_marks import Mark
from manager.system import jobs_list

SINCE = 1000.0


def _job(state: str, *, at: float = SINCE + 5, kind: str = "import", problems: int = 0,
         stopped: bool = False) -> dict:
    return {"id": f"{state}-{at}", "kind": kind, "state": state, "finished_at": at,
            "problems": problems, "stopped": stopped}


def _finding(section: str, key: str, reason: str) -> SimpleNamespace:
    return SimpleNamespace(section=section, key=key, reason=reason)


class WhichJobsAreCounted(unittest.TestCase):
    def test_failed_and_done_with_errors_after_the_time_are_counted(self) -> None:
        found = [_job(jobs.FAILED), _job(jobs.DONE, problems=2)]

        self.assertEqual(2, len(jobs_list.unseen(found, SINCE)))

    def test_done_clean_and_stopped_and_running_are_not(self) -> None:
        found = [_job(jobs.DONE), _job(jobs.DONE, stopped=True, problems=3),
                 _job(jobs.RUNNING, at=0)]

        self.assertEqual([], jobs_list.unseen(found, SINCE))

    def test_a_job_that_ended_before_the_time_is_not(self) -> None:
        found = [_job(jobs.FAILED, at=SINCE - 1), _job(jobs.FAILED, at=SINCE)]

        self.assertEqual([], jobs_list.unseen(found, SINCE))


class WhatTheTooltipSays(unittest.TestCase):
    def test_each_job_is_named_in_the_words_of_the_grid(self) -> None:
        said = jobs_list.unseen_said([_job(jobs.DONE, problems=1)])

        self.assertEqual(t("manager.page.job_outcome", what=jobs_list.what_ran("import"),
                           outcome=t("manager.jobs.outcome.with_errors")), said)

    def test_up_to_three_are_named_and_past_three_they_are_counted(self) -> None:
        three = [_job(jobs.FAILED, at=SINCE + n) for n in range(3)]
        five = [_job(jobs.FAILED, at=SINCE + n) for n in range(5)]

        self.assertEqual(2, jobs_list.unseen_said(three).count(";"))
        self.assertEqual(t("manager.page.jobs_unseen", count=5),
                         jobs_list.unseen_said(five))


class WhatAClosedGroupShows(unittest.TestCase):
    def test_nothing_under_it_shows_nothing(self) -> None:
        self.assertIsNone(rail_marks.group_mark([None, None]))

    def test_trouble_wins_over_a_warm_mark_and_is_counted_alone(self) -> None:
        group = rail_marks.group_mark([Mark(2, "Update waiting"),
                                       Mark(3, "Needs a path", trouble=True)])

        self.assertEqual(Mark(3, "Needs a path", trouble=True), group)

    def test_warm_marks_are_summed_with_their_tooltips_joined(self) -> None:
        group = rail_marks.group_mark([Mark(2, "Update waiting: Cab"), None,
                                       Mark(1, "Import, Failed")])

        self.assertEqual(Mark(3, "Update waiting: Cab; Import, Failed"), group)

    def test_trouble_from_several_entries_is_summed(self) -> None:
        group = rail_marks.group_mark([Mark(1, "a", trouble=True), Mark(2, "b", trouble=True)])

        self.assertEqual(Mark(3, "a b", trouble=True), group)


class WhatAnEntryShows(unittest.TestCase):
    def test_the_findings_make_a_red_count_with_each_reason_once(self) -> None:
        items = [_finding("paths", "tables", "No path"), _finding("paths", "media", "No path"),
                 _finding("paths", "backups", "")]

        self.assertEqual(Mark(3, "No path", trouble=True), rail_marks.entry_mark(items, None))

    def test_one_problem_needed_by_two_features_is_counted_once(self) -> None:
        items = [_finding("folders", "", "No folder"), _finding("folders", "", "No folder")]

        self.assertEqual(Mark(1, "No folder", trouble=True), rail_marks.entry_mark(items, None))

    def test_trouble_is_shown_over_a_warm_mark(self) -> None:
        warm = Mark(1, "Update waiting")

        self.assertEqual(Mark(1, "x", trouble=True),
                         rail_marks.entry_mark([_finding("a", "b", "x")], warm))
        self.assertEqual(warm, rail_marks.entry_mark([], warm))


if __name__ == "__main__":
    unittest.main()

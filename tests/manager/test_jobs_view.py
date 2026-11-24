"""The Jobs view: what ran since startup, newest first, in words a person reads."""

from __future__ import annotations

import time
import unittest
from datetime import datetime
from urllib.parse import parse_qs

from common.i18n import t
from common.runtime import jobs
from manager.kit import deeplink, when
from manager.system import jobs_list

NOW = 1_800_000_000.0


def _job(kind: str = "library.scan", state: str = "done", started: float = NOW - 90,
         took: float | None = 5, **more) -> dict:
    finished = None if took is None else started + took
    return {"id": f"{kind}-{started}", "kind": kind, "state": state, "started_at": started,
            "finished_at": finished, "error": None, "message": "", "detail": "",
            "stopped": False, **more}


class Outcome(unittest.TestCase):
    def test_a_stopped_job_is_stopped_not_done(self) -> None:
        self.assertEqual(jobs_list.STOPPED, jobs_list.outcome_of(_job(stopped=True)))

    def test_done_failed_and_running_read_as_the_state(self) -> None:
        for state in (jobs.DONE, jobs.FAILED, jobs.RUNNING):
            self.assertEqual(state, jobs_list.outcome_of(_job(state=state)))

    def test_a_done_job_with_problems_is_done_with_errors(self) -> None:
        self.assertEqual(jobs_list.WITH_ERRORS, jobs_list.outcome_of(_job(problems=3)))

    def test_a_stopped_job_stays_stopped_whatever_it_skipped(self) -> None:
        self.assertEqual(jobs_list.STOPPED,
                         jobs_list.outcome_of(_job(stopped=True, problems=3)))

    def test_a_failed_job_stays_failed(self) -> None:
        self.assertEqual(jobs.FAILED,
                         jobs_list.outcome_of(_job(state=jobs.FAILED, problems=3)))

    def test_a_row_says_whether_it_has_a_report_to_open(self) -> None:
        self.assertTrue(jobs_list.row(_job(report=True), NOW)["report"])
        self.assertFalse(jobs_list.row(_job(), NOW)["report"])

    def test_every_column_opens_the_report_and_detail_carries_the_link(self) -> None:
        for column in jobs_list.COLUMNS:
            for key in jobs_list.OPENS_REPORT:
                self.assertIn(key, column, column["field"])
        detail = next(one for one in jobs_list.COLUMNS if one["field"] == "detail")
        self.assertIn(t("manager.jobs.view_report"), detail[":cellRenderer"])

    def test_done_with_errors_can_be_asked_for(self) -> None:
        clean, rough = _job(started=NOW - 500), _job(started=NOW - 50, problems=2)

        found = jobs_list.rows([clean, rough], NOW, jobs_list.WITH_ERRORS)

        self.assertEqual([rough["id"]], [one["id"] for one in found])

    def test_every_outcome_has_a_word(self) -> None:
        for outcome in jobs_list.OUTCOMES:
            key = f"manager.jobs.outcome.{outcome}"
            self.assertNotEqual(key, t(key))


class Named(unittest.TestCase):
    def test_a_title_the_job_carries_is_what_it_is_called(self) -> None:
        job = {"kind": jobs.KIND_LIBRARY_SCAN, "title": "Auto-match"}

        self.assertEqual("Auto-match", jobs_list.named(job))

    def test_a_job_with_no_title_is_called_what_its_kind_is(self) -> None:
        for job in ({"kind": jobs.KIND_LIBRARY_SCAN}, {"kind": jobs.KIND_LIBRARY_SCAN,
                                                       "title": ""}):
            self.assertEqual(jobs_list.what_ran(jobs.KIND_LIBRARY_SCAN),
                             jobs_list.named(job))

    def test_a_row_and_the_unseen_phrase_use_the_title(self) -> None:
        job = {"id": "j", "kind": jobs.KIND_LIBRARY_SCAN, "title": "Auto-match",
               "state": jobs.DONE, "started_at": 1.0, "finished_at": 2.0}

        self.assertEqual("Auto-match", jobs_list.row(job, 3.0)["what"])
        self.assertIn("Auto-match", jobs_list.unseen_said([job]))


class WhatRan(unittest.TestCase):
    def test_a_known_kind_is_named_in_words(self) -> None:
        self.assertEqual(t("manager.jobs.kind.spreadsheet_download"),
                         jobs_list.what_ran(jobs.KIND_SPREADSHEET_DOWNLOAD))

    def test_every_kind_the_registry_names_has_words(self) -> None:
        kinds = [value for name, value in vars(jobs).items()
                 if name.startswith("KIND_") and isinstance(value, str)]
        for kind in kinds:
            self.assertNotEqual(kind, jobs_list.what_ran(kind), kind)

    def test_an_extension_s_kind_is_shown_as_it_is_written(self) -> None:
        self.assertEqual("acme.sync", jobs_list.what_ran("acme.sync"))


class Rows(unittest.TestCase):
    def test_newest_first(self) -> None:
        older, newer = _job(started=NOW - 500), _job(started=NOW - 50)

        found = jobs_list.rows([older, newer], NOW)

        self.assertEqual([newer["id"], older["id"]], [one["id"] for one in found])

    def test_an_outcome_narrows_the_list(self) -> None:
        found = jobs_list.rows([_job(), _job(state="failed", started=NOW - 10),
                                _job(stopped=True, started=NOW - 20)], NOW, "failed")

        self.assertEqual(["failed"], [one["outcome"] for one in found])

    def test_stopped_can_be_asked_for(self) -> None:
        found = jobs_list.rows([_job(), _job(stopped=True, started=NOW - 20)], NOW,
                               jobs_list.STOPPED)

        self.assertEqual(1, len(found))

    def test_a_failure_shows_its_error_and_a_success_its_counts(self) -> None:
        failed = jobs_list.row(_job(state="failed", error="No route", detail="x"), NOW)
        done = jobs_list.row(_job(detail="12 games"), NOW)

        self.assertEqual("No route", failed["detail"])
        self.assertEqual("12 games", done["detail"])

    def test_a_running_job_shows_what_it_is_doing_and_runs_until_now(self) -> None:
        running = jobs_list.row(_job(state="running", took=None, message="Reading"), NOW)

        self.assertEqual("Reading", running["detail"])
        self.assertEqual(90, running["took"])

    def test_the_cell_keeps_a_number_to_sort_by(self) -> None:
        one = jobs_list.row(_job(took=65), NOW)

        self.assertEqual(NOW - 90, one["started"])
        self.assertEqual(65, one["took"])
        self.assertEqual(when.took(65), one["took_said"])
        self.assertEqual(when.at_second(NOW - 90), one["started_said"])


class TimesOnScreen(unittest.TestCase):
    def test_a_start_reads_to_the_second_in_local_time(self) -> None:
        at = datetime.fromtimestamp(NOW).astimezone()

        self.assertIn(at.strftime("%H:%M:%S"), when.at_second(NOW))

    def test_how_long_names_the_two_largest_units(self) -> None:
        self.assertEqual(t("date.took_instant"), when.took(0.4))
        self.assertEqual(t("date.took_seconds", seconds=7), when.took(7.9))
        self.assertEqual(t("date.took_minutes", minutes=2, seconds=5), when.took(125))
        self.assertEqual(t("date.took_hours", hours=3, minutes=1), when.took(3 * 3600 + 61))


class TheLink(unittest.TestCase):
    def test_jobs_has_an_address_of_its_own(self) -> None:
        self.assertEqual({"view": ["jobs"]}, parse_qs(deeplink.query({"view": "jobs"})))

    def test_an_address_opens_the_page(self) -> None:
        state: dict = {}
        deeplink.apply(state, {"view": "jobs"}, views=["logs", "jobs"], sections=[])

        self.assertEqual("jobs", state["view"])

    def test_the_old_jobs_view_of_logs_is_not_a_view_any_more(self) -> None:
        state: dict = {}
        deeplink.apply(state, {"view": "logs", "log": "jobs"}, views=["logs"], sections=[])

        self.assertNotIn("log_view", state)

    def test_the_stream_is_the_default_and_leaves_no_mark(self) -> None:
        self.assertNotIn("log", parse_qs(deeplink.query({"view": "logs"})))

    def test_the_digest_is_named_in_the_address(self) -> None:
        self.assertEqual(["digest"],
                         parse_qs(deeplink.query({"view": "logs", "log_view": "digest"}))["log"])

    def test_an_unknown_view_is_dropped(self) -> None:
        state: dict = {}
        deeplink.apply(state, {"view": "logs", "log": "nonsense"}, views=["logs"], sections=[])

        self.assertNotIn("log_view", state)


class TheRegistryKeepsAHistory(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)

    def test_a_job_that_ran_is_a_row_with_its_start(self) -> None:
        before = time.time()
        with jobs.track(jobs.KIND_SPREADSHEET_DOWNLOAD):
            pass

        found = jobs_list.rows([job.snapshot() for job in jobs.recent(jobs_list.LIMIT)],
                               time.time())

        self.assertEqual(1, len(found))
        self.assertEqual("done", found[0]["outcome"])
        self.assertGreaterEqual(found[0]["started"], before - 1)


if __name__ == "__main__":
    unittest.main()

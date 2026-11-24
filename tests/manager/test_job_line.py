"""What the drawer's footer line keeps: the failed job, and a recording run left waiting."""

from __future__ import annotations

import unittest

from common.capture import run
from common.runtime.jobs import (
    KIND_BACKGLASS_ORDER,
    KIND_LIBRARY_SCAN,
    KIND_MEDIA_FILL,
    KIND_VPS_ROLLUP,
)
from manager.library import record
from manager.page import (
    comparisons_ended_since,
    failed_since,
    imports_ended_since,
    list_is_idle,
    orders_unset_since,
    reads_ended_since,
)
from manager.settings.library_folders import KIND_IMPORT

OPENED = 1000.0


def _job(job_id: str, state: str, finished_at: float | None = None) -> dict:
    return {"id": job_id, "state": state, "finished_at": finished_at,
            "message": "Reading the library", "error": "It did not answer in time."}


class FailedSinceTests(unittest.TestCase):
    def test_one_that_failed_after_the_page_opened_is_kept(self) -> None:
        failed = _job("j2", "failed", 1010.0)

        self.assertIs(failed_since([failed, _job("j1", "done", 1005.0)], OPENED, set()),
                      failed)

    def test_one_that_failed_before_the_page_opened_is_not_news(self) -> None:
        self.assertIsNone(failed_since([_job("j1", "failed", 990.0)], OPENED, set()))

    def test_a_later_job_that_finished_clears_it(self) -> None:
        jobs = [_job("j2", "done", 1020.0), _job("j1", "failed", 1010.0)]

        self.assertIsNone(failed_since(jobs, OPENED, set()))

    def test_one_clicked_away_stays_away(self) -> None:
        self.assertIsNone(failed_since([_job("j1", "failed", 1010.0)], OPENED, {"j1"}))

    def test_a_running_job_is_passed_over_for_the_last_to_end(self) -> None:
        failed = _job("j1", "failed", 1010.0)

        self.assertIs(failed_since([_job("j2", "running"), failed], OPENED, set()),
                      failed)

    def test_nothing_run_keeps_nothing(self) -> None:
        self.assertIsNone(failed_since([], OPENED, set()))


def _read(job_id: str, state: str = "done", finished_at: float | None = 1010.0,
          kind: str = KIND_LIBRARY_SCAN) -> dict:
    return {**_job(job_id, state, finished_at), "kind": kind}


def _pass(job_id: str, problems: int = 1, state: str = "done",
          finished_at: float | None = 1010.0) -> dict:
    return {**_job(job_id, state, finished_at), "kind": KIND_BACKGLASS_ORDER,
            "problems": problems}


class OrdersUnsetSinceTests(unittest.TestCase):
    """A pass over the tables' DMD order is told of only when it left a table unwritten."""

    def test_one_that_ended_after_the_page_opened_with_a_table_unwritten_is_told(self) -> None:
        self.assertEqual(orders_unset_since([_pass("j1")], OPENED, set()), ["j1"])

    def test_one_that_wrote_every_table_is_not_told(self) -> None:
        self.assertEqual(orders_unset_since([_pass("j1", problems=0)], OPENED, set()), [])

    def test_one_older_than_the_page_or_already_told_is_not_told(self) -> None:
        jobs = [_pass("j1", finished_at=990.0), _pass("j2")]

        self.assertEqual(orders_unset_since(jobs, OPENED, {"j2"}), [])

    def test_one_running_or_failed_is_not_told(self) -> None:
        jobs = [_pass("j2", state="running", finished_at=None), _pass("j1", state="failed")]

        self.assertEqual(orders_unset_since(jobs, OPENED, set()), [])

    def test_another_kind_with_problems_is_not_told(self) -> None:
        other = {**_pass("j1"), "kind": KIND_MEDIA_FILL}

        self.assertEqual(orders_unset_since([other], OPENED, set()), [])


class ReadsEndedSinceTests(unittest.TestCase):
    """The library reads the line tells of once they end, wherever they started."""

    def test_one_that_ended_after_the_page_opened_is_told(self) -> None:
        self.assertEqual(reads_ended_since([_read("j1")], OPENED, set()), ["j1"])

    def test_one_that_ended_before_the_page_opened_is_not_news(self) -> None:
        self.assertEqual(reads_ended_since([_read("j1", finished_at=990.0)], OPENED, set()),
                         [])

    def test_one_told_is_not_told_again(self) -> None:
        self.assertEqual(reads_ended_since([_read("j1")], OPENED, {"j1"}), [])

    def test_one_running_or_failed_is_not_told(self) -> None:
        """A failed one has the failed line."""
        jobs = [_read("j2", "running", None), _read("j1", "failed")]

        self.assertEqual(reads_ended_since(jobs, OPENED, set()), [])

    def test_every_one_that_ended_is_told(self) -> None:
        jobs = [_read("j2", finished_at=1020.0), _read("j1")]

        self.assertEqual(reads_ended_since(jobs, OPENED, set()), ["j2", "j1"])

    def test_other_work_is_not_a_read(self) -> None:
        self.assertEqual(reads_ended_since([_read("j1", kind=KIND_MEDIA_FILL)], OPENED, set()),
                         [])


def _comparison(job_id: str, state: str = "done", finished_at: float | None = 1010.0,
                kind: str = KIND_VPS_ROLLUP) -> dict:
    return {**_job(job_id, state, finished_at), "kind": kind}


class ComparisonsEndedSinceTests(unittest.TestCase):
    """The comparisons with VPS the rail's count is read again after."""

    def test_one_that_ended_after_the_page_opened_is_news(self) -> None:
        self.assertEqual(["j1"], comparisons_ended_since([_comparison("j1")], OPENED, set()))

    def test_one_that_ended_before_the_page_opened_is_not(self) -> None:
        self.assertEqual([], comparisons_ended_since(
            [_comparison("j1", finished_at=990.0)], OPENED, set()))

    def test_one_already_told_is_not_told_again(self) -> None:
        self.assertEqual([], comparisons_ended_since([_comparison("j1")], OPENED, {"j1"}))

    def test_one_still_running_or_failed_is_not_news(self) -> None:
        jobs = [_comparison("j1", "running", None), _comparison("j2", "failed")]

        self.assertEqual([], comparisons_ended_since(jobs, OPENED, set()))

    def test_a_job_of_another_kind_is_not_one(self) -> None:
        self.assertEqual([], comparisons_ended_since(
            [_comparison("j1", kind=KIND_LIBRARY_SCAN)], OPENED, set()))


def _import(job_id: str, state: str = "done", finished_at: float | None = 1010.0,
            report: bool = True) -> dict:
    return {**_job(job_id, state, finished_at), "kind": KIND_IMPORT, "report": report}


class ImportsEndedSinceTests(unittest.TestCase):
    """The imports the page tells of once they end, wherever they started."""

    def test_one_that_ended_after_the_page_opened_is_told(self) -> None:
        found = imports_ended_since([_import("j1")], OPENED, set())

        self.assertEqual([one["id"] for one in found], ["j1"])

    def test_one_that_ended_before_the_page_opened_is_not_news(self) -> None:
        self.assertEqual(
            imports_ended_since([_import("j1", finished_at=990.0)], OPENED, set()), [])

    def test_one_already_told_is_not_told_again(self) -> None:
        self.assertEqual(imports_ended_since([_import("j1")], OPENED, {"j1"}), [])

    def test_a_failed_import_has_the_failed_line(self) -> None:
        jobs = [_import("j1", "failed", report=False)]

        self.assertEqual(imports_ended_since(jobs, OPENED, set()), [])

    def test_one_whose_report_could_not_be_kept_is_still_told(self) -> None:
        found = imports_ended_since([_import("j1", report=False)], OPENED, set())

        self.assertEqual([one["id"] for one in found], ["j1"])

    def test_other_work_is_not_an_import(self) -> None:
        job = {**_import("j1"), "kind": KIND_MEDIA_FILL}

        self.assertEqual(imports_ended_since([job], OPENED, set()), [])


class ListIsIdleTests(unittest.TestCase):
    """When the end of a background import may redraw the list that is on screen."""

    def test_a_list_with_nothing_open_or_ticked_is_redrawn(self) -> None:
        for view in ("games", "tables"):
            with self.subTest(view=view):
                self.assertTrue(list_is_idle({"view": view, "game": "", "ticked": 0}))
                self.assertTrue(list_is_idle({"view": view}))

    def test_an_open_game_or_a_ticked_row_keeps_the_page_as_it_is(self) -> None:
        self.assertFalse(list_is_idle({"view": "games", "game": "g1", "ticked": 0}))
        self.assertFalse(list_is_idle({"view": "tables", "game": "", "ticked": 2}))

    def test_a_search_in_the_box_keeps_the_page_as_it_is(self) -> None:
        self.assertFalse(list_is_idle({"view": "games", "searching": True}))

    def test_a_page_that_is_not_a_list_is_left_alone(self) -> None:
        for view in ("overview", "settings", "jobs"):
            with self.subTest(view=view):
                self.assertFalse(list_is_idle({"view": view}))


class WaitingRunTests(unittest.TestCase):
    """What the line says of a recording run that is not going."""

    def _run(self, state: str, reason: dict | None) -> dict:
        return {"state": state, "reason": reason, "done": 6, "of": 24}

    def test_a_paused_run_says_where_and_why(self) -> None:
        full = {"key": "capture.run.space", "params": {"device": "Cab 1"}}

        self.assertEqual(record.waiting(self._run("paused", full)),
                         ("Recording paused at 7 of 24", "Cab 1 is nearly out of space"))
        self.assertEqual(record.waiting(self._run("paused", None)),
                         ("Recording paused at 7 of 24", ""))

    def test_a_run_vpinfe_closed_during_reads_stopped(self) -> None:
        said = record.waiting(self._run("paused", {"key": run.INTERRUPTED, "params": {}}))

        self.assertEqual(said[0], "Recording stopped at 7 of 24")

    def test_a_run_that_is_going_or_none_says_nothing(self) -> None:
        self.assertEqual(record.waiting(self._run("running", None)), ("", ""))
        self.assertEqual(record.waiting({}), ("", ""))


if __name__ == "__main__":
    unittest.main()

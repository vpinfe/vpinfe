"""The timer that keeps looking for tables added or removed on disk.

Off unless configured, because a check re-reads every game folder. What it does when
the library is already busy matters more than the interval: a queue would turn a slow
refresh into a backlog of identical ones.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common.games import library_refresh
from common.runtime import jobs


class PeriodicTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        library_refresh.stop_periodic()
        self.addCleanup(library_refresh.stop_periodic)
        self.addCleanup(jobs.reset_for_tests)

    def test_zero_never_starts_a_thread(self) -> None:
        library_refresh.start_periodic(0)

        self.assertIsNone(library_refresh._ticker)

    def test_a_negative_interval_is_the_same_as_off(self) -> None:
        library_refresh.start_periodic(-5)

        self.assertIsNone(library_refresh._ticker)

    def test_an_interval_starts_one_daemon_thread(self) -> None:
        library_refresh.start_periodic(60)

        ticker = library_refresh._ticker
        self.assertIsNotNone(ticker)
        assert ticker is not None
        self.assertTrue(ticker.daemon, "it must never hold up a shutdown")

    def test_starting_twice_does_not_stack_tickers(self) -> None:
        library_refresh.start_periodic(60)
        first = library_refresh._ticker
        library_refresh.start_periodic(60)

        self.assertIs(library_refresh._ticker, first)

    def test_a_tick_lands_on_the_scan_kind(self) -> None:
        """Shared with POST /library/scan so the two cannot write .info files at once."""
        with patch.object(library_refresh, "_stop") as stop:
            # One tick, then stop: wait() returns False to run, True to leave.
            stop.wait.side_effect = [False, True]
            with patch.object(jobs, "submit") as submit:
                library_refresh.start_periodic(60)
                assert library_refresh._ticker is not None
                library_refresh._ticker.join(timeout=5)

        submit.assert_called_once()
        self.assertEqual(submit.call_args[0][0], jobs.KIND_LIBRARY_SCAN)

    def test_a_tick_during_a_scan_is_dropped_not_queued(self) -> None:
        with patch.object(library_refresh, "_stop") as stop:
            stop.wait.side_effect = [False, False, True]
            with patch.object(jobs, "submit",
                              side_effect=jobs.JobBusyError("busy")) as submit:
                library_refresh.start_periodic(60)
                assert library_refresh._ticker is not None
                library_refresh._ticker.join(timeout=5)

        # Both ticks tried and neither raised out of the thread.
        self.assertEqual(submit.call_count, 2)


if __name__ == "__main__":
    unittest.main()

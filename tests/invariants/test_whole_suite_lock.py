"""A whole-suite run takes a lock, so two at once are refused, and each one is counted."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

from tests import wholesuite


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.lock = self.dir / "whole.lock"

    def test_the_first_run_takes_it_and_a_second_is_told_who_has_it(self) -> None:
        self.assertIsNone(wholesuite.take(self.lock))
        busy = wholesuite.take(self.lock)
        self.assertIsNotNone(busy)
        assert busy is not None
        self.assertIn(f"process {os.getpid()}", busy)
        self.assertIn("--overlap", busy)

    def test_releasing_it_lets_the_next_run_take_it(self) -> None:
        wholesuite.take(self.lock)
        wholesuite.release(self.lock)
        self.assertIsNone(wholesuite.take(self.lock))

    def test_a_lock_whose_process_is_gone_is_taken_over(self) -> None:
        if sys.platform == "win32":
            self.skipTest("a process id cannot be probed there; only the age check applies")
        self.lock.write_text(f'{{"pid": {2**22 + 7}, "started": {time.time()}}}',
                             encoding="utf-8")
        self.assertIsNone(wholesuite.take(self.lock))

    def test_a_lock_held_for_longer_than_any_run_is_taken_over(self) -> None:
        old = time.time() - wholesuite.STALE_AFTER_S - 1
        self.lock.write_text(f'{{"pid": {os.getpid()}, "started": {old}}}', encoding="utf-8")
        self.assertIsNone(wholesuite.take(self.lock))

    def test_a_lock_nobody_can_read_is_taken_over(self) -> None:
        for body in ("not json", "[1, 2]", "null"):
            self.lock.write_text(body, encoding="utf-8")
            self.assertIsNone(wholesuite.take(self.lock), body)
            wholesuite.release(self.lock)

    def test_release_leaves_another_process_s_lock_alone(self) -> None:
        self.lock.write_text(f'{{"pid": {os.getppid()}, "started": {time.time()}}}',
                             encoding="utf-8")
        wholesuite.release(self.lock)
        self.assertTrue(self.lock.exists())

    def test_of_many_runs_starting_together_exactly_one_takes_it(self) -> None:
        answers: list[str | None] = []
        gate = threading.Barrier(8)

        def start() -> None:
            gate.wait()
            answers.append(wholesuite.take(self.lock))

        threads = [threading.Thread(target=start) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(answers.count(None), 1)
        self.assertEqual([path.name for path in self.dir.iterdir()], ["whole.lock"])

    def test_a_folder_that_cannot_be_written_takes_no_lock_and_refuses_nothing(self) -> None:
        blocker = self.dir / "file"
        blocker.write_text("", encoding="utf-8")
        self.assertIsNone(wholesuite.take(blocker / "whole.lock"))
        wholesuite.record(1.0, 1, True, blocker / "runs.jsonl")


class CountTests(unittest.TestCase):
    def test_runs_are_counted_by_the_day_they_ended(self) -> None:
        log = Path(self.enterContext(tempfile.TemporaryDirectory())) / "runs.jsonl"
        self.assertEqual(wholesuite.today(log), (0, 0.0))
        wholesuite.record(120.0, 10, True, log)
        wholesuite.record(60.0, 10, False, log)
        wholesuite.record(300.0, 10, True, log, now=time.time() - 2 * 86400)
        self.assertEqual(wholesuite.today(log), (2, 180.0))
        self.assertEqual(wholesuite.today(log, date.today() - timedelta(days=2)), (1, 300.0))

    def test_a_run_logs_the_modules_that_failed_and_the_ones_that_ran_without_a_browser(
            self) -> None:
        log = Path(self.enterContext(tempfile.TemporaryDirectory())) / "runs.jsonl"
        wholesuite.record(60.0, 10, False, log, failed=["tests.a"], sandboxed=["tests.b"])
        wholesuite.record(60.0, 10, True, log)
        first, second = (json.loads(line) for line in log.read_text().splitlines())
        self.assertEqual((["tests.a"], ["tests.b"]), (first["failed"], first["sandboxed"]))
        self.assertEqual(([], []), (second["failed"], second["sandboxed"]))


if __name__ == "__main__":
    unittest.main()

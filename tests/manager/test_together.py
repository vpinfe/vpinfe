"""Reads made at once answer in the order they were given, in the caller's context."""

from __future__ import annotations

import contextvars
import threading
import time
import unittest

from manager.kit.together import together

WHO: contextvars.ContextVar[str] = contextvars.ContextVar("who", default="nobody")


class Together(unittest.TestCase):
    def test_answers_come_back_in_the_order_given(self) -> None:
        def slow() -> str:
            time.sleep(0.05)
            return "slow"

        self.assertEqual(together(slow, lambda: "fast"), ["slow", "fast"])

    def test_none_and_one_are_answered_without_a_pool(self) -> None:
        self.assertEqual(together(), [])
        self.assertEqual(together(threading.current_thread), [threading.current_thread()])

    def test_the_calls_run_at_once(self) -> None:
        both = threading.Barrier(2, timeout=2)

        self.assertEqual(together(both.wait, both.wait).__len__(), 2)

    def test_each_call_sees_the_callers_context(self) -> None:
        WHO.set("a page")

        self.assertEqual(together(WHO.get, WHO.get), ["a page", "a page"])

    def test_every_call_finishes_before_a_failure_is_raised(self) -> None:
        finished: list[str] = []

        def fails() -> None:
            raise OSError("down")

        def last() -> None:
            time.sleep(0.05)
            finished.append("last")

        with self.assertRaises(OSError):
            together(fails, last)

        self.assertEqual(finished, ["last"])


if __name__ == "__main__":
    unittest.main()

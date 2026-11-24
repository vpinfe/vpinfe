"""A bulk act's failures are said once, after the last item."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common.i18n import t
from manager.kit.failed_together import Failures

KEY = "manager.launchers.remove_failed"


class FailuresSaidOnce(unittest.TestCase):
    def _said(self, failed: Failures) -> list[tuple[str, str, str]]:
        with patch("manager.kit.failed_together.ui.notify") as notify:
            failed.say(KEY)
        return [(call.args[0], call.kwargs["caption"], call.kwargs["type"])
                for call in notify.call_args_list]

    def test_nothing_failed_says_nothing(self) -> None:
        self.assertEqual(self._said(Failures()), [])

    def test_one_failure_is_named_as_it_would_be_alone(self) -> None:
        failed = Failures()
        failed.add("Cabinet", "No permission")

        self.assertEqual(self._said(failed),
                         [(t(KEY, count=1, name="Cabinet"), "No permission", "negative")])

    def test_several_are_counted_with_each_reason_once(self) -> None:
        failed = Failures()
        for name in ("A", "B", "C"):
            failed.add(name, "No permission")
        failed.add("D", "Disk full")

        said = self._said(failed)

        self.assertEqual(said, [(t(KEY, count=4), "No permission; Disk full", "negative")])
        self.assertIn("4", said[0][0])

    def test_a_failure_with_no_reason_leaves_the_caption_empty(self) -> None:
        failed = Failures()
        failed.add("A", "")
        failed.add("B", "")

        self.assertEqual(self._said(failed)[0][1], "")


if __name__ == "__main__":
    unittest.main()

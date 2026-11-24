"""Protects: (d) a table is marked as running from the lines its game writes to its log.

The lines a game appends to a log file are read from when it starts, across the game
rewriting its log, and nothing written before that is heard.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.host.line_follower import Follower


class FollowerTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.log = Path(folder.name) / "vpinball.log"
        self.heard: list[str] = []

    def _follow(self) -> Follower:
        return Follower([self.log], self.heard.append)

    def _append(self, text: str) -> None:
        with self.log.open("ab") as handle:
            handle.write(text.encode("utf-8"))

    def test_it_starts_from_the_end_so_an_earlier_session_is_not_heard(self) -> None:
        self.log.write_text("Startup done\nold line\n", encoding="utf-8")
        follower = self._follow()

        self._append("new line\n")
        follower.poll()

        self.assertEqual(self.heard, ["new line"])

    def test_a_file_that_appears_later_is_read_from_the_top(self) -> None:
        follower = self._follow()

        self._append("first\r\nsecond\n")
        follower.poll()

        self.assertEqual(self.heard, ["first", "second"])

    def test_half_a_line_waits_for_its_end(self) -> None:
        self.log.write_text("", encoding="utf-8")
        follower = self._follow()

        self._append("Unpausing")
        follower.poll()
        self._append(" Game\n")
        follower.poll()

        self.assertEqual(self.heard, ["Unpausing Game"])

    def test_a_log_that_shrinks_is_read_from_the_top_again(self) -> None:
        self.log.write_text("a long line from before the game rewrote its log\n",
                            encoding="utf-8")
        follower = self._follow()

        self.log.write_text("Startup done\n", encoding="utf-8")
        follower.poll()

        self.assertEqual(self.heard, ["Startup done"])

    def test_a_log_rewritten_in_place_to_a_longer_one_is_read_from_the_top(self) -> None:
        self.log.write_text("old session line one\nold session line two\n", encoding="utf-8")
        follower = self._follow()

        self.log.write_text("Startup done\nand a good deal more written since the start\n",
                            encoding="utf-8")
        follower.poll()

        self.assertEqual(self.heard[0], "Startup done")

    def test_a_replaced_log_is_read_from_the_top_again(self) -> None:
        self.log.write_text("x\n", encoding="utf-8")
        follower = self._follow()
        replacement = self.log.with_name("replacement.log")
        replacement.write_text("y\nz\nStartup done\n", encoding="utf-8")

        replacement.replace(self.log)
        follower.poll()

        self.assertEqual(self.heard, ["y", "z", "Startup done"])

    def test_bytes_that_are_not_text_do_not_stop_it(self) -> None:
        self.log.write_text("", encoding="utf-8")
        follower = self._follow()

        with self.log.open("ab") as handle:
            handle.write(b"bad \xff byte\nStartup done\n")
        follower.poll()

        self.assertEqual(self.heard[-1], "Startup done")
        self.assertEqual(len(self.heard), 2)

    def test_stopping_reads_what_was_written_last(self) -> None:
        self.log.write_text("", encoding="utf-8")
        follower = self._follow()
        follower.start()

        self._append("Startup done\n")
        follower.stop()

        self.assertEqual(self.heard, ["Startup done"])

    def test_stopping_gives_a_last_line_that_has_no_line_ending(self) -> None:
        self.log.write_text("", encoding="utf-8")
        follower = self._follow()

        self._append("first\nStartup done")
        follower.stop()

        self.assertEqual(self.heard, ["first", "Startup done"])

    def test_a_log_that_cannot_be_opened_is_said_once_and_read_when_it_can(self) -> None:
        self.log.write_text("", encoding="utf-8")
        follower = self._follow()

        with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
            with self.assertLogs("vpinfe.common.host.line_follower", level="WARNING") as seen:
                follower.poll()
                follower.poll()
        self._append("Startup done\n")
        follower.poll()

        self.assertEqual(len(seen.records), 1)
        self.assertEqual(self.heard, ["Startup done"])

    def test_a_handler_that_raises_does_not_end_the_following(self) -> None:
        self.log.write_text("", encoding="utf-8")
        calls: list[str] = []

        def handler(line: str) -> None:
            calls.append(line)
            if line == "bad":
                raise ValueError(line)

        follower = Follower([self.log], handler)
        self._append("bad\ngood\n")
        with self.assertLogs("vpinfe.common.host.line_follower", level="ERROR"):
            follower.poll()

        self.assertEqual(calls, ["bad", "good"])


if __name__ == "__main__":
    unittest.main()

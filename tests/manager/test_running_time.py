"""A video's or a sound's running time, as a player shows it."""

from __future__ import annotations

import unittest

from manager.library.file_facts import running_time


class RunningTime(unittest.TestCase):
    def test_minutes_and_seconds(self) -> None:
        self.assertEqual(["0:36", "2:05", "1:00:01"],
                         [running_time(s) for s in (35.76, 125.0, 3601.0)])

    def test_a_sound_shorter_than_a_second_still_runs(self) -> None:
        self.assertEqual("0:01", running_time(0.3))


if __name__ == "__main__":
    unittest.main()

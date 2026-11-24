"""The Logs source select names the logs that can be read."""

from __future__ import annotations

import unittest
from typing import Any

from manager.system import logs


def _held(**more: Any) -> dict[str, Any]:
    return {"sources": ["vpinfe.log"], "visual_pinball": "", "source": "", **more}


class WhatTheSelectOffers(unittest.TestCase):
    def test_jobs_is_not_offered_there(self) -> None:
        self.assertNotIn("jobs", logs._source_options(_held()))

    def test_the_log_being_written_is_the_empty_choice(self) -> None:
        self.assertIn("", logs._source_options(_held()))

    def test_each_earlier_run_is_offered_under_the_name_of_its_file(self) -> None:
        options = logs._source_options(
            _held(sources=["vpinfe.log", "vpinfe.log.1", "vpinfe.log.2"]))

        self.assertIn("vpinfe.log.1", options)
        self.assertIn("vpinfe.log.2", options)
        self.assertNotEqual(options["vpinfe.log.1"], options["vpinfe.log.2"])

    def test_visual_pinball_is_offered_only_when_there_is_a_log_to_name(self) -> None:
        self.assertNotIn("vpinball.log", logs._source_options(_held()))
        self.assertIn("vpinball.log",
                      logs._source_options(_held(visual_pinball="vpinball.log")))


    def test_a_source_the_last_read_did_not_list_is_still_something_the_select_holds(
            self) -> None:
        self.assertIn("vpinball.log", logs._source_options(_held(source="vpinball.log")))


class ASourceThatHasGone(unittest.TestCase):
    def test_the_current_log_and_the_ones_listed_are_known(self) -> None:
        self.assertTrue(logs._known(_held()))
        self.assertTrue(logs._known(_held(sources=["a", "b"], source="b")))
        self.assertTrue(logs._known(_held(visual_pinball="v", source="v")))

    def test_a_rotation_that_is_no_longer_listed_is_not(self) -> None:
        self.assertFalse(logs._known(_held(source="vpinfe.log.3")))

    def test_visual_pinball_is_not_once_the_launcher_stops_naming_it(self) -> None:
        self.assertFalse(logs._known(_held(source="vpinball.log")))


class WhichFilesAreStillBeingWritten(unittest.TestCase):
    def test_the_current_log_is_live(self) -> None:
        self.assertTrue(logs._live(_held()))

    def test_visual_pinballs_log_is_live(self) -> None:
        self.assertTrue(logs._live(_held(source="vpinball.log",
                                         visual_pinball="vpinball.log")))

    def test_a_rotation_is_not(self) -> None:
        self.assertFalse(logs._live(_held(source="vpinfe.log.1")))


if __name__ == "__main__":
    unittest.main()

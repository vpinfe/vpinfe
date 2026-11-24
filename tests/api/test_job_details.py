"""A finished job's counts, as the one line the Jobs view shows."""

from __future__ import annotations

import unittest
from typing import Any

from common.i18n import t
from common.runtime import job_details, jobs


class Describers(unittest.TestCase):
    def test_a_library_read_counts_its_games(self) -> None:
        said = job_details.of(jobs.KIND_LIBRARY_SCAN, {"games": 1, "new_games": 0})

        self.assertEqual(t("said.job_library_read", count=1), said)

    def test_a_library_read_with_new_games_says_how_many_matched(self) -> None:
        said = job_details.of(jobs.KIND_LIBRARY_SCAN,
                              {"games": 40, "new_games": 3, "new_matched": 2})

        self.assertEqual(t("said.job_library_new", count=40, new=3, matched=2), said)

    def test_a_scan_counts_found_and_not_found(self) -> None:
        said = job_details.of(jobs.KIND_LIBRARY_SCAN, {"found": 9, "not_found": 2})

        self.assertEqual(t("said.job_scan_found", found=9, missing=2), said)

    def test_a_media_fill_counts_what_it_did(self) -> None:
        said = job_details.of(jobs.KIND_MEDIA_FILL,
                              {"games": 5, "filled": 3, "unmatched": 1, "failed": 1})

        self.assertEqual(t("said.job_media_filled", count=5, filled=3, unmatched=1, failed=1), said)

    def test_a_media_look_says_what_it_found_and_not_what_it_replaced(self) -> None:
        said = job_details.of(jobs.KIND_MEDIA_LOOK,
                              {"games": 5, "updated": 0, "failed": 0, "found": 2, "unread": 1})

        self.assertEqual(t("said.job_media_looked", count=5, found=2, unread=1), said)
        self.assertIn("2 newer", said)
        self.assertNotIn("replaced", said)

    def test_a_settings_write_counts_the_tables_changed_and_not(self) -> None:
        said = job_details.of(jobs.KIND_TABLE_SETTINGS,
                              {"tables": 5, "written": ["a", "b", "c"], "failed": [{}, {}]})

        self.assertEqual(t("said.job_table_settings", count=3, failed=2), said)

    def test_a_draw_order_pass_counts_the_tables_written_and_not(self) -> None:
        said = job_details.of(jobs.KIND_BACKGLASS_ORDER,
                              {"games": 9, "written": ["a", "b"], "failed": [{}]})

        self.assertEqual(t("said.job_table_order", count=2, failed=1), said)

    def test_a_selection_write_says_how_many_rows_it_changed(self) -> None:
        said = job_details.of(jobs.KIND_MARKS, {"rows": 4, "written": [{}, {}, {}],
                                                "failed": [{}]})

        self.assertEqual(t("said.job_marks", count=3, failed=1), said)

    def test_a_recording_says_how_many_it_made(self) -> None:
        said = job_details.of(jobs.KIND_MEDIA_CAPTURE, {"run": {"done": 2, "of": 5}})

        self.assertEqual(t("said.job_recorded", done=2, total=5), said)

    def test_a_tool_download_says_the_version(self) -> None:
        self.assertEqual(t("said.job_tool_version", version="7"),
                         job_details.of(jobs.KIND_TOOL_GET, {"version": "7"}))

    def test_a_match_again_counts_what_moved_and_what_is_still_unmatched(self) -> None:
        self.assertEqual(t("said.job_auto_matched", changed=4, unmatched=2),
                         job_details.auto_matched({"changed": 4, "unmatched": 2}))

    def test_an_info_upgrade_counts_each_outcome(self) -> None:
        said = job_details.info_upgraded({"upgraded": 3, "already_current": 5, "failed": 1})

        self.assertEqual(t("said.job_info_upgraded", upgraded=3, current=5, failed=1), said)

    def test_an_info_restore_counts_each_outcome(self) -> None:
        said = job_details.info_restored({"restored": 2, "failed": 0})

        self.assertEqual(t("said.job_info_restored", restored=2, failed=0), said)

    def test_a_dof_config_download_says_whether_a_file_changed(self) -> None:
        self.assertEqual(t("said.dof_config.updated"),
                         job_details.of(jobs.KIND_DOF_CONFIG, {"changed": True}))
        self.assertEqual(t("manager.settings.already_date"),
                         job_details.of(jobs.KIND_DOF_CONFIG, {"changed": False}))
        self.assertEqual("", job_details.of(jobs.KIND_DOF_CONFIG, {}))

    def test_a_result_of_a_shape_not_named_has_no_line(self) -> None:
        result: Any
        for result in (None, "text", 3, [], {}, {"unrelated": 1}):
            for kind in (jobs.KIND_LIBRARY_SCAN, jobs.KIND_MEDIA_FILL,
                         jobs.KIND_MEDIA_CAPTURE, jobs.KIND_TOOL_GET):
                self.assertEqual("", job_details.of(kind, result), (kind, result))

    def test_a_kind_nobody_describes_has_no_line(self) -> None:
        self.assertEqual("", job_details.of("acme.sync", {"games": 1, "new_games": 0}))


if __name__ == "__main__":
    unittest.main()

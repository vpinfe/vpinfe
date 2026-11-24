"""Asking VPinFE to look at the disk again.

The counterpart to `POST /library/scan`, which goes to VPSdb. This one only reads
folders, so it is what you call after copying a table in by hand.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import httpapi
from common.i18n import t
from common.runtime import jobs


class LibraryRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_it_accepts_and_hands_back_a_job_to_watch(self) -> None:
        with patch("common.games.library_refresh.refresh", return_value={}) as ran:
            response = self.client.post("/library/refresh")

        self.assertEqual(response.status_code, 202)
        self.assertIn("/api/v1/jobs/", response.headers["Location"])
        self.assertEqual(response.json()["kind"], jobs.KIND_LIBRARY_SCAN)
        ran.assert_called_once()

    def test_it_is_called_what_it_is_not_the_scan(self) -> None:
        with patch("common.games.library_refresh.refresh", return_value={}):
            response = self.client.post("/library/refresh")

        self.assertEqual(response.json()["title"], t("said.title_refresh"))

    def test_it_will_not_run_beside_a_scan(self) -> None:
        """Both write a .info for every game they touch, so the second caller is
        refused rather than left to interleave writes with the first."""
        with jobs.track(jobs.KIND_LIBRARY_SCAN):
            response = self.client.post("/library/refresh")

        self.assertEqual(response.status_code, 409)


class AutoMatchRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.game = MagicMock(full_path_game="/library/Fathom (Bally 1981)")
        catalog = patch("common.games.game_repository.catalog",
                        return_value={"g1": self.game})
        catalog.start()
        self.addCleanup(catalog.stop)

    def test_it_answers_the_counts_for_the_games_it_holds(self) -> None:
        counts = {"games": 1, "changed": 1, "unmatched": 0, "yours": 0}
        with patch("common.games.auto_match.match_again",
                   return_value=(counts, [self.game])) as ran, \
                patch("common.games.library_folders.blocked_games", return_value=[]), \
                patch("common.games.game_repository.refresh_game") as reread:
            response = self.client.post("/library/auto_match",
                                        json={"game_ids": ["g1", "gone"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {**counts, "skipped": 0})
        self.assertEqual(ran.call_args[0][0], [self.game])
        reread.assert_called_once()

    def test_it_is_a_job_of_its_own_name_that_says_what_it_matched(self) -> None:
        counts = {"games": 1, "changed": 1, "unmatched": 3, "yours": 0}
        with patch("common.games.auto_match.match_again", return_value=(counts, [])), \
                patch("common.games.library_folders.blocked_games", return_value=[]):
            self.client.post("/library/auto_match", json={"game_ids": ["g1"]})

        (job,) = jobs.recent(5)
        self.assertEqual(job.snapshot()["title"], t("said.title_auto_match"))
        self.assertEqual(job.detail, t("said.job_auto_matched", changed=1, unmatched=3))

    def test_a_game_whose_folder_cannot_be_written_to_is_counted_not_matched(self) -> None:
        with patch("common.games.auto_match.match_again",
                   return_value=({"games": 0}, [])) as ran, \
                patch("common.games.library_folders.blocked_games",
                      return_value=[(self.game, "read-only")]):
            response = self.client.post("/library/auto_match", json={"game_ids": ["g1"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["skipped"], 1)
        self.assertEqual(ran.call_args[0][0], [])

    def test_it_will_not_run_beside_a_scan(self) -> None:
        with jobs.track(jobs.KIND_LIBRARY_SCAN):
            response = self.client.post("/library/auto_match", json={"game_ids": ["g1"]})

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()

"""Asking for the art a selection of games is missing, and then getting it."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import httpapi
from common.i18n import t
from common.runtime import jobs

PLAN = {"games": 2, "unmatched": 1, "sources": ["VPinMediaDB"], "unreachable": [],
        "kinds": [{"kind": "wheel", "missing": 2, "available": 1, "local": 0}]}


class MediaFillRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.game = MagicMock(full_path_game="/library/Fathom (Bally 1981)")
        for patcher in (
                patch("common.games.game_repository.catalog", return_value={"g1": self.game}),
                patch("common.games.media_fill.kept_kinds",
                      return_value={"wheel", "backglass"}),
                patch("common.games.media_fill._reachable", return_value=("vpinmediadb",))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_answers_the_counts_and_fetches_nothing(self) -> None:
        with patch("common.games.media_fill.plan", return_value=PLAN) as plan, \
                patch("common.games.media_fill._fill_games") as filled:
            response = self.client.post("/library/media/missing",
                                        json={"game_ids": ["g1", "g2"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), PLAN)
        plan.assert_called_once_with(["g1", "g2"])
        filled.assert_not_called()

    def test_offered_names_the_games_a_source_can_fill_by_kind(self) -> None:
        with patch("common.games.media_fill.offered_games",
                   return_value={"playfield": ["g1"]}), \
                patch("common.games.media_fill._fill_games") as filled:
            response = self.client.get("/library/media/offered")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"kinds": {"playfield": ["g1"]}})
        filled.assert_not_called()

    def test_fill_accepts_and_hands_back_a_job_to_watch(self) -> None:
        counts = {"games": 1, "filled": 1, "unmatched": 0, "failed": 0}
        with patch("common.games.media_fill._fill_games", return_value=counts) as filled:
            response = self.client.post("/library/media/fill",
                                        json={"game_ids": ["g1", "gone"],
                                              "kinds": ["wheel", "flyer"]})
            job = jobs.get(response.json()["id"])
            while job is not None and job.state == jobs.RUNNING:
                time.sleep(0.01)

        self.assertEqual(response.status_code, 202)
        self.assertIn("/api/v1/jobs/", response.headers["Location"])
        self.assertEqual(response.json()["kind"], jobs.KIND_MEDIA_FILL)
        self.assertEqual(filled.call_args.args[0], [("g1", self.game, {"wheel"})])

    def test_a_slot_names_one_kind_for_one_game(self) -> None:
        with patch("common.games.media_fill._fill_games", return_value={}) as filled:
            response = self.client.post(
                "/library/media/fill",
                json={"kinds": ["wheel"], "slots": [{"game_id": "g1", "kind": "backglass"}]})
            job = jobs.get(response.json()["id"])
            while job is not None and job.state == jobs.RUNNING:
                time.sleep(0.01)

        self.assertEqual(filled.call_args.args[0], [("g1", self.game, {"backglass"})])

    def test_it_will_not_run_beside_another_fill(self) -> None:
        with jobs.track(jobs.KIND_MEDIA_FILL):
            response = self.client.post("/library/media/fill", json={})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["message"], t("error.media_fill.busy"))


if __name__ == "__main__":
    unittest.main()

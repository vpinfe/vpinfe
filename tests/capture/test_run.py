"""What a recording would fill and replace, and starting one: the service and HTTP."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import events, service_errors
from common.apps.contract import VIEW_FSS
from common.capture import adapters, placing, run, session
from common.capture.run import Request
from common.games import asset_origin
from common.host import launch_state
from common.i18n import t
from common.runtime import jobs
from tests.capture.test_preflight import MONITORS, FakeAdapter, found, report
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Record001"
FOLDER = "Cactus Canyon (Bally 1998)"
PLAIN, MOD = "tbl0000001", "tbl0000002"

INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {PLAIN: {"id": PLAIN, "filename": f"{FOLDER}.vpx"},
               MOD: {"id": MOD, "filename": "Mod.vpx"}},
    "assets": {
        "medias/table.mp4": {"source": {"host": "vpinmediadb", "hash": "abc"}},
        "medias/bg.png": {"source": {"host": "user"}},
        "medias/(Playfield) Mod.mp4": {"source": {"host": asset_origin.RECORDED}},
    },
}
PICTURES_AND_VIDEOS = ("playfield", "playfield_video", "backglass", "backglass_video",
                       "scoreview", "scoreview_video")


class _Library(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                            files={f"{FOLDER}.vpx": b"vpx", "Mod.vpx": b"vpx"},
                            medias={"table.mp4": b"v", "bg.png": b"\x89PNG",
                                    "dmd.mp4": b"v", "(Playfield) Mod.mp4": b"v"})
        game = fake_game(folder, FOLDER, meta=INFO)
        for target, value in (("common.games.game_repository.catalog", {GAME_ID: game}),
                              ("common.games.game_repository.game_by_id",
                               lambda game_id: game if game_id == GAME_ID else None)):
            patcher = patch(target, return_value=value) if not callable(value) \
                else patch(target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.report = report()

    def plan(self, **kwargs) -> dict:
        kwargs.setdefault("games", [GAME_ID])
        return run.plan(Request(**kwargs), self.report)

    def does(self, planned: dict) -> dict[str, tuple[str, str | None]]:
        return {row["kind"]: (row["does"], row["source"])
                for row in planned["targets"][0]["kinds"]}


class PlanTests(_Library):
    def test_fill_records_only_what_nothing_serves(self) -> None:
        planned = self.plan(kinds=["playfield", "playfield_video", "backglass",
                                   "scoreview_video"])

        self.assertEqual(self.does(planned), {
            "playfield": ("fill", None), "playfield_video": ("leave", "vpinmediadb"),
            "backglass": ("leave", "user"), "scoreview_video": ("leave", "unknown")})
        self.assertEqual((planned["recording"], planned["replacing"], planned["launches"]),
                         (["playfield"], 0, 1))

    def test_replace_downloaded_takes_only_the_catalogs_files(self) -> None:
        planned = self.plan(kinds=["playfield_video", "backglass", "scoreview_video"],
                            existing=run.REPLACE_DOWNLOADED)

        self.assertEqual(planned["replacing_by_source"], {"vpinmediadb": 1})
        self.assertEqual(planned["recording"], ["playfield_video"])

    def test_replace_all_counts_what_it_replaces_by_whose_it_is(self) -> None:
        planned = self.plan(kinds=["playfield_video", "backglass", "scoreview_video"],
                            existing=run.REPLACE_ALL)

        self.assertEqual(planned["replacing_by_source"],
                         {"vpinmediadb": 1, "user": 1, "unknown": 1})
        self.assertEqual(planned["replacing"], 3)

    def test_a_tables_slot_is_its_own_file_else_the_shared_one(self) -> None:
        own = self.plan(games=[], tables=[(GAME_ID, MOD)], kinds=["playfield_video"],
                        existing=run.REPLACE_ALL)
        shared = self.plan(games=[], tables=[(GAME_ID, PLAIN)], kinds=["playfield_video"])

        self.assertEqual(self.does(own), {"playfield_video": ("replace", "capture")})
        self.assertEqual(self.does(shared), {"playfield_video": ("leave", "vpinmediadb")})

    def test_a_recording_is_not_downloaded_art(self) -> None:
        planned = self.plan(games=[], tables=[(GAME_ID, MOD)], kinds=["playfield_video"],
                            existing=run.REPLACE_DOWNLOADED)

        self.assertEqual(self.does(planned), {"playfield_video": ("leave", "capture")})

    def test_no_kinds_named_is_every_kind_this_device_can_record(self) -> None:
        planned = self.plan(existing=run.REPLACE_ALL)
        with_sound = self.plan(existing=run.REPLACE_ALL, sound=True)

        self.assertEqual([row["kind"] for row in planned["kinds"]], list(PICTURES_AND_VIDEOS))
        self.assertEqual(with_sound["kinds"][-1]["kind"], "audio")

    def test_a_kind_this_device_cannot_record_is_left_with_why(self) -> None:
        planned = self.plan(kinds=["topper"])

        self.assertEqual(planned["targets"][0]["kinds"][0]["does"], "leave")
        self.assertEqual(planned["kinds"][0]["reason"]["key"], "capture.screen.none")
        self.assertEqual(planned["launches"], 0)

    def test_a_device_that_records_nothing_says_why_for_every_kind_named(self) -> None:
        self.report = report(adapters.Unsupported("wayland", adapters.NO_WAY))

        planned = self.plan(kinds=["playfield", "backglass_video", "audio"])

        self.assertEqual({row["does"] for row in planned["targets"][0]["kinds"]}, {"leave"})
        self.assertEqual({row["reason"]["key"] for row in planned["kinds"]},
                         {adapters.NO_WAY})
        self.assertEqual(planned["launches"], 0)

    def test_the_estimate_is_one_launch_with_every_screen_at_once(self) -> None:
        at_once = self.plan(existing=run.REPLACE_ALL)
        self.report = report(FakeAdapter(hardware=False))
        in_turn = self.plan(existing=run.REPLACE_ALL)

        self.assertGreater(in_turn["estimate_seconds"], at_once["estimate_seconds"])

    def test_what_cannot_be_asked_is_refused(self) -> None:
        refused: tuple[dict[str, Any], ...] = ({"games": []}, {"kinds": ["wheel"]},
                                               {"existing": "some"})
        for kwargs in refused:
            with self.subTest(kwargs), self.assertRaises(service_errors.RefusedError):
                self.plan(**kwargs)
        with self.assertRaises(service_errors.NotFoundError):
            self.plan(games=[], tables=[(GAME_ID, "tbl9999999")])


class FullSingleScreenPlanTests(_Library):
    def plan_fss(self, views: frozenset[str], **kwargs) -> dict:
        with patch("common.host.launch.this_devices_copy", side_effect=lambda game: game), \
                patch("common.capture.placing.views", return_value=views):
            return self.plan(**kwargs)

    def test_a_table_without_the_view_leaves_it_with_why(self) -> None:
        planned = self.plan_fss(frozenset(), kinds=["playfield_fss"])

        self.assertEqual(planned["targets"][0]["kinds"][0]["does"], "leave")
        self.assertEqual(planned["kinds"][0]["reason"]["key"], run.NO_FSS)
        self.assertEqual(planned["launches"], 0)

    def test_a_table_with_the_view_records_it_in_a_launch_of_its_own(self) -> None:
        planned = self.plan_fss(frozenset({VIEW_FSS}), kinds=["playfield", "playfield_fss"])

        self.assertEqual(planned["recording"], ["playfield", "playfield_fss"])
        self.assertEqual(planned["launches"], 2)

    def test_the_picture_alone_is_one_launch(self) -> None:
        planned = self.plan_fss(frozenset({VIEW_FSS}), kinds=["playfield_fss"])

        self.assertEqual(planned["launches"], 1)

    def test_the_view_costs_a_second_launch_in_the_estimate(self) -> None:
        own = self.plan_fss(frozenset({VIEW_FSS}), kinds=["playfield"])
        both = self.plan_fss(frozenset({VIEW_FSS}), kinds=["playfield", "playfield_fss"])

        self.assertGreater(both["estimate_seconds"], own["estimate_seconds"])

    def test_it_is_not_among_the_kinds_recorded_when_none_is_named(self) -> None:
        planned = self.plan_fss(frozenset({VIEW_FSS}))

        self.assertNotIn("playfield_fss", [row["kind"] for row in planned["kinds"]])


class JoinedTests(unittest.TestCase):
    def test_a_launch_that_recorded_makes_the_table_recorded_whatever_the_other_did(self) -> None:
        failed = {"kind": "playfield_fss", "reason": {"key": session.NO_FRAMES, "params": {}}}

        joined = run._joined(session.Result(session.RECORDED, placed=[{"kind": "playfield"}]),
                             session.Result(session.FAILED, failed=[failed],
                                            reason={"key": "x", "params": {}}))

        self.assertEqual((joined.state, joined.placed, joined.failed, joined.reason),
                         (session.RECORDED, [{"kind": "playfield"}], [failed], None))

    def test_two_launches_that_recorded_nothing_fail_with_the_first_reason(self) -> None:
        first = {"key": "first", "params": {}}

        joined = run._joined(session.Result(session.FAILED, reason=first),
                             session.Result(session.FAILED, reason={"key": "second",
                                                                    "params": {}}))

        self.assertEqual((joined.state, joined.reason), (session.FAILED, first))

    def test_a_table_closed_or_a_stop_in_the_second_launch_is_the_whole_outcome(self) -> None:
        closed = {"key": "closed", "params": {}}
        for state in (session.CLOSED, session.STOPPED):
            with self.subTest(state):
                joined = run._joined(
                    session.Result(session.RECORDED, placed=[{"kind": "playfield"}]),
                    session.Result(state, reason=closed))

                self.assertEqual((joined.state, joined.placed, joined.reason),
                                 (state, [{"kind": "playfield"}], closed))

    def test_two_launches_that_were_skipped_are_skipped(self) -> None:
        joined = run._joined(session.Result(session.SKIPPED), session.Result(session.SKIPPED))

        self.assertEqual(joined.state, session.SKIPPED)


class _Started(_Library):
    def setUp(self) -> None:
        super().setUp()
        self.blocked = report(found=found(missing=("ffmpeg",)))
        jobs.reset_for_tests()
        run.reset_for_tests()
        launch_state.clear()
        self.addCleanup(jobs.reset_for_tests)
        self.addCleanup(run.reset_for_tests)
        self.addCleanup(launch_state.clear)
        self.sessions: list[Any] = []
        held_run = tempfile.TemporaryDirectory()
        self.addCleanup(held_run.cleanup)
        self.run_file = Path(held_run.name) / "run.json"
        for target, path in (("common.capture.run.RUN_FILE", self.run_file),
                             ("common.capture.run.WORK", Path(held_run.name) / "work")):
            moved = patch(target, path)
            moved.start()
            self.addCleanup(moved.stop)

        def recorded(this):
            self.sessions.append(this)
            return session.Result(session.RECORDED, placed=[{"kind": "playfield"}])

        held = found()
        self.shown = placing.Shown("Visual Pinball X", {"playfield": "DP-2"})
        for target, value in (
                ("common.capture.preflight.report", self.report),
                ("common.capture.placing.shown", self.shown),
                ("common.capture.adapters.resolve", FakeAdapter()),
                ("common.host.display_service.get_display_monitors", MONITORS),
                ("common.host.launch.this_devices_copy", None)):
            patcher = patch(target, return_value=value) if value is not None \
                else patch(target, side_effect=lambda game: game)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, side_effect in (
                ("common.host.tools.resolve", lambda tool: held[tool.id]),
                ("common.capture.session.Session.run", recorded)):
            patcher = patch(target, side_effect=side_effect, autospec=target.endswith("run"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def finished(self, job: jobs.Job | None) -> jobs.Job:
        assert job is not None
        done = threading.Event()

        def heard(**_: object) -> None:
            done.set()

        for name in (events.JOB_DONE, events.JOB_FAILED):
            events.subscribe(name, heard)
            self.addCleanup(events.unsubscribe, name, heard)
        if job.finished_at is None:
            done.wait(10)
        return job


class StartTests(_Started):
    def test_a_run_records_the_table_and_answers_with_its_outcome(self) -> None:
        job = self.finished(run.start(Request(games=[GAME_ID], kinds=["playfield"])))

        self.assertEqual(job.kind, jobs.KIND_MEDIA_CAPTURE)
        assert isinstance(job.result, dict)
        self.assertEqual(job.result["tables"][0]["state"], session.RECORDED)
        target = self.sessions[0].target
        self.assertEqual((target.game_id, target.table_id, target.table, target.kinds),
                         (GAME_ID, "", None, ("playfield",)))

    def test_the_full_single_screen_picture_is_a_launch_after_the_tables_own(self) -> None:
        with patch("common.capture.placing.views", return_value=frozenset({VIEW_FSS})):
            job = self.finished(run.start(Request(games=[GAME_ID],
                                                  kinds=["playfield", "playfield_fss"])))

        self.assertEqual([(one.target.kinds, one.target.view) for one in self.sessions],
                         [(("playfield",), ""), (("playfield_fss",), VIEW_FSS)])
        assert isinstance(job.result, dict)
        self.assertEqual(job.result["tables"][0]["state"], session.RECORDED)

    def test_a_stop_asked_between_the_two_launches_starts_no_second(self) -> None:
        class AsksToStop(list):
            def append(self, one: Any) -> None:
                super().append(one)
                run._halt.set()

        self.sessions = AsksToStop()
        with patch("common.capture.placing.views", return_value=frozenset({VIEW_FSS})):
            self.finished(run.start(Request(games=[GAME_ID],
                                            kinds=["playfield", "playfield_fss"])))

        self.assertEqual([one.target.view for one in self.sessions], [""])

    def test_a_table_is_launched_by_its_own_file(self) -> None:
        self.finished(run.start(Request(tables=[(GAME_ID, MOD)], kinds=["playfield"])))

        self.assertEqual((self.sessions[0].target.table_id, self.sessions[0].target.table),
                         (MOD, "Mod.vpx"))

    def test_the_session_places_windows_as_the_tables_own_app_says(self) -> None:
        with patch("common.capture.placing.shown", return_value=self.shown) as asked:
            self.finished(run.start(Request(tables=[(GAME_ID, MOD)], kinds=["playfield"])))

        self.assertEqual(asked.call_args.args[1], "Mod.vpx")
        started = self.sessions[0]
        self.assertIs(started.placed.shown, self.shown)
        self.assertEqual(started.screens["playfield"].name, "DP-2")

    def test_a_replacing_run_needs_the_plans_count(self) -> None:
        asked = Request(games=[GAME_ID], kinds=["playfield_video", "backglass"],
                        existing=run.REPLACE_ALL)

        with self.assertRaises(service_errors.RefusedError) as refused:
            run.start(asked)
        self.assertEqual(refused.exception.details["replacing"], 2)

        confirmed = Request(**{**asked.__dict__, "confirmed": 2})
        self.assertEqual(self.finished(run.start(confirmed)).state, jobs.DONE)

    def test_it_is_refused_while_a_table_runs(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        with self.assertRaises(service_errors.BlockedError):
            run.start(Request(games=[GAME_ID]))

    def test_choose_after_recording_proposes_what_would_replace_a_file(self) -> None:
        self.finished(run.start(Request(games=[GAME_ID], existing=run.CHOOSE,
                                        kinds=["playfield", "playfield_video"])))

        target = self.sessions[0].target
        self.assertEqual((target.kinds, target.propose, target.replace),
                         (("playfield", "playfield_video"), frozenset({"playfield_video"}),
                          frozenset()))

    def test_a_replacing_run_tells_the_session_what_it_replaces(self) -> None:
        self.finished(run.start(Request(games=[GAME_ID], existing=run.REPLACE_ALL,
                                        kinds=["playfield_video"], confirmed=1)))

        self.assertEqual(self.sessions[0].target.replace, frozenset({"playfield_video"}))

    def test_a_device_that_records_nothing_says_why(self) -> None:
        with patch("common.capture.preflight.report", return_value=self.blocked), \
                self.assertRaises(service_errors.UnavailableError) as refused:
            run.start(Request(games=[GAME_ID]))
        self.assertIn(t("capture.tool.needed", tool="FFmpeg"), str(refused.exception))


class ExclusiveTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)

    def test_a_recording_and_a_media_fill_never_run_together(self) -> None:
        for running, asked, key in (
                (jobs.KIND_MEDIA_FILL, jobs.KIND_MEDIA_CAPTURE, "error.capture.fill_running"),
                (jobs.KIND_MEDIA_CAPTURE, jobs.KIND_MEDIA_FILL, "error.media_fill.recording")):
            with self.subTest(asked), jobs.track(running), \
                    self.assertRaises(jobs.JobBusyError) as refused:
                jobs.submit(asked, lambda job: None)
            self.assertEqual(str(refused.exception), t(key))


class HttpTests(_Started):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_plan_is_served(self) -> None:
        response = self.client.post("/capture/plan", json={
            "games": [GAME_ID], "kinds": ["playfield_video"], "existing": "replace_all"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["replacing_by_source"], {"vpinmediadb": 1})

    def test_a_run_is_accepted_with_where_to_watch_it(self) -> None:
        response = self.client.post("/capture/runs", json={
            "tables": [{"game": GAME_ID, "table": MOD}], "kinds": ["playfield"]})

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.headers["Location"], f"/api/v1/jobs/{response.json()['id']}")
        self.assertEqual(response.json()["kind"], jobs.KIND_MEDIA_CAPTURE)

    def test_each_refusal_has_its_status(self) -> None:
        cases = (({"games": [GAME_ID], "kinds": ["backglass"], "existing": "replace_all"},
                  400),
                 ({"games": [GAME_ID], "kinds": ["backglass"], "existing": "replace_all",
                   "confirmed": {"count": 1}}, 202),
                 ({"games": ["nope"]}, 404))
        for body, status in cases:
            with self.subTest(body=body):
                response = self.client.post("/capture/runs", json=body)
                self.assertEqual(response.status_code, status)
                if status == 202:
                    self.finished(jobs.get(response.json()["id"]))
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)
        self.assertEqual(self.client.post("/capture/runs",
                                          json={"games": [GAME_ID]}).status_code, 409)
        launch_state.clear()
        with patch("common.capture.preflight.report", return_value=self.blocked):
            response = self.client.post("/capture/runs", json={"games": [GAME_ID]})
        self.assertEqual((response.status_code, response.json()["error"]["code"]),
                         (501, "feature_unavailable"))


if __name__ == "__main__":
    unittest.main()

"""A run over several games or tables: written down as it goes, paused, resumed,
stopped, and what the API and the event stream say of it."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import events, service_errors
from common.capture import run, session, space
from common.capture.run import Request
from common.host import launch_state
from common.runtime import jobs
from tests.capture.test_run import GAME_ID, MOD, PLAIN, _Library, _Started

THREE = Request(games=[GAME_ID], tables=[(GAME_ID, PLAIN), (GAME_ID, MOD)],
                kinds=["playfield"])
PLENTY = 1 << 50


class _Run(_Started):
    """Each session is answered by `self.answer`, handed the session and how many ran
    before it."""

    def setUp(self) -> None:
        super().setUp()
        self.free = PLENTY
        patcher = patch.object(space, "_free", side_effect=lambda _path: self.free)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.answer: Callable[[Any, int], session.Result] = self.recorded
        self.heard: list[Any] = []

        def heard(**payload: Any) -> None:
            self.heard.append(payload["run"])

        events.subscribe(events.CAPTURE_RUN_CHANGED, heard)
        self.addCleanup(events.unsubscribe, events.CAPTURE_RUN_CHANGED, heard)

        def answered(this: Any) -> session.Result:
            self.sessions.append(this)
            return self.answer(this, len(self.sessions) - 1)

        patcher = patch("common.capture.session.Session.run", new=answered)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def recorded(_this: Any, _index: int) -> session.Result:
        return session.Result(session.RECORDED, placed=[{"kind": "playfield"}])

    def ran(self, job: jobs.Job) -> dict[str, Any]:
        self.finished(job)
        result = job.result or {}
        assert isinstance(result, dict)
        return result

    def targets(self) -> list[tuple[str, str]]:
        return [(one.target.game_id, one.target.table_id) for one in self.sessions]


class ManyTests(_Run):
    def test_each_target_is_recorded_in_turn_and_the_run_is_gone_once_done(self) -> None:
        result = self.ran(run.start(THREE))

        self.assertEqual(self.targets(), [(GAME_ID, ""), (GAME_ID, PLAIN), (GAME_ID, MOD)])
        self.assertEqual([one["state"] for one in result["tables"]], ["recorded"] * 3)
        self.assertEqual(result["run"], {"state": "done", "reason": None, "done": 3, "of": 3})
        self.assertIsNone(run.current())
        self.assertFalse(self.run_file.exists())
        self.assertIsNone(self.heard[-1])

    def test_the_run_is_written_down_as_it_goes(self) -> None:
        seen: list[dict[str, Any] | None] = []

        def looked(this: Any, index: int) -> session.Result:
            seen.append(run.current())
            return self.recorded(this, index)

        self.answer = looked
        self.ran(run.start(THREE))

        views = [one for one in seen if one is not None]
        self.assertEqual([(one["state"], one["done"], one["recorded"]) for one in views],
                         [("running", 0, 0), ("running", 1, 1), ("running", 2, 2)])
        self.assertEqual(views[1]["game"]["table_id"], PLAIN)
        self.assertEqual(views[1]["job_id"], views[0]["job_id"])

    def test_the_job_says_which_game_of_how_many(self) -> None:
        said: list[str] = []

        def looked(this: Any, index: int) -> session.Result:
            said.append(jobs.active(jobs.KIND_MEDIA_CAPTURE)[0].message)
            return self.recorded(this, index)

        self.answer = looked
        job = run.start(THREE)
        self.ran(job)

        name = run.plan(THREE, self.report)["targets"][1]["name"]
        self.assertEqual(said[1], f"Recording “{name}” - 2 of 3")
        self.assertTrue(job.stoppable)

    def test_a_game_with_nothing_to_record_is_skipped_without_launching(self) -> None:
        result = self.ran(run.start(Request(games=[GAME_ID], tables=[(GAME_ID, PLAIN)],
                                            kinds=["backglass"])))

        self.assertEqual(self.sessions, [])
        self.assertEqual([one["state"] for one in result["tables"]], ["skipped"] * 2)

    def test_a_game_named_twice_is_recorded_once(self) -> None:
        result = self.ran(run.start(Request(games=[GAME_ID, GAME_ID], kinds=["playfield"])))

        self.assertEqual(len(result["tables"]), 1)

    def test_the_plan_counts_each_kind_over_every_target(self) -> None:
        planned = run.plan(Request(games=[GAME_ID], tables=[(GAME_ID, PLAIN), (GAME_ID, MOD)],
                                   kinds=["playfield", "playfield_video"]), self.report)

        self.assertEqual(planned["games"], 3)
        self.assertEqual({row["kind"]: (row["missing"], row["have"])
                          for row in planned["kinds"]},
                         {"playfield": (3, 0), "playfield_video": (0, 3)})
        self.assertEqual((planned["fills"], planned["launches"]), (3, 3))
        self.assertEqual(planned["estimate_seconds"],
                         sum(one["estimate_seconds"] for one in planned["targets"]))


class PauseTests(_Run):
    def test_a_close_at_the_cabinet_pauses_a_run_of_many_there(self) -> None:
        def closed_second(this: Any, index: int) -> session.Result:
            if index == 1:
                return session.Result(session.CLOSED,
                                      reason=session.said(session.CLOSED_AT_CABINET))
            return self.recorded(this, index)

        self.answer = closed_second
        result = self.ran(run.start(THREE))

        self.assertEqual(result["run"]["state"], "paused")
        now = run.current()
        assert now is not None and now["reason"] is not None
        self.assertEqual((now["state"], now["done"], now["reason"]["key"]),
                         ("paused", 1, session.CLOSED_AT_CABINET))
        self.assertIsNone(now["job_id"])

        self.answer = self.recorded
        resumed = self.ran(run.resume())

        self.assertEqual(self.targets()[-2:], [(GAME_ID, PLAIN), (GAME_ID, MOD)])
        self.assertEqual(resumed["run"]["state"], "done")
        self.assertEqual(len(resumed["tables"]), 3)
        self.assertIsNone(run.current())

    def test_a_run_of_one_closed_at_the_cabinet_ends(self) -> None:
        self.answer = lambda _this, _index: session.Result(
            session.CLOSED, reason=session.said(session.CLOSED_AT_CABINET))

        result = self.ran(run.start(Request(games=[GAME_ID], kinds=["playfield"])))

        self.assertEqual(result["tables"][0]["state"], "closed")
        self.assertIsNone(run.current())

    def test_a_table_launched_from_elsewhere_pauses_the_run_before_its_next_game(
            self) -> None:
        def someone_played(this: Any, index: int) -> session.Result:
            if index == 0:
                events.emit(events.TABLE_LAUNCHING, source=launch_state.SOURCE_FRONTEND)
            return self.recorded(this, index)

        self.answer = someone_played
        self.ran(run.start(THREE))

        now = run.current()
        assert now is not None
        self.assertEqual((len(self.sessions), now["done"], now["reason"]["key"]),
                         (1, 1, run.PAUSED_LAUNCHED))

    def test_a_disk_nearly_full_pauses_between_games(self) -> None:
        def filled_up(this: Any, index: int) -> session.Result:
            self.free = space.RESERVE
            return self.recorded(this, index)

        self.answer = filled_up
        self.ran(run.start(THREE))

        now = run.current()
        assert now is not None
        self.assertEqual((now["done"], now["reason"]["key"]), (1, run.PAUSED_SPACE))

    def test_a_run_that_would_not_fit_is_refused(self) -> None:
        self.free = space.RESERVE

        with self.assertRaises(service_errors.BlockedError):
            run.start(THREE)
        self.assertIsNone(run.current())

    def test_a_run_left_running_by_a_closed_vpinfe_reads_paused_and_resumes(self) -> None:
        self.run_file.write_text(json.dumps({
            "id": "r1", "request": {"existing": "fill", "kinds": ["playfield"]},
            "targets": [{"game_id": GAME_ID, "table_id": "", "name": "Cactus Canyon",
                         "estimate": 59},
                        {"game_id": GAME_ID, "table_id": MOD, "name": "Cactus Canyon",
                         "estimate": 59}],
            "at": 1, "outcomes": [{"state": "recorded", "seconds": 30, "estimate": 60}],
            "state": "running", "reason": None, "job_id": "gone"}), encoding="utf-8")

        now = run.current()
        assert now is not None
        self.assertEqual((now["state"], now["reason"]["key"], now["done"], now["of"]),
                         ("paused", run.INTERRUPTED, 1, 2))
        self.assertEqual(now["estimate_seconds"], 30)

        result = self.ran(run.resume())

        self.assertEqual(self.targets(), [(GAME_ID, MOD)])
        self.assertEqual(result["run"]["state"], "done")

    def test_a_new_run_waits_for_one_in_hand(self) -> None:
        self.answer = lambda _this, _index: session.Result(
            session.CLOSED, reason=session.said(session.CLOSED_AT_CABINET))
        self.ran(run.start(THREE))

        with self.assertRaises(service_errors.BlockedError):
            run.start(THREE)


class _Held(_Run):
    """The first session holds until the run halts it."""

    def setUp(self) -> None:
        super().setUp()
        self.inside = threading.Event()

        def held(this: Any, index: int) -> session.Result:
            if index == 0:
                self.inside.set()
                if this.halt.wait(10):
                    return session.Result(session.STOPPED)
            return self.recorded(this, index)

        self.answer = held


class StopTests(_Held):
    def test_stop_throws_the_game_in_hand_away_and_ends_the_run(self) -> None:
        job = run.start(THREE)
        self.inside.wait(10)

        run.stop()
        result = self.ran(job)

        self.assertEqual((result["run"]["state"], result["tables"]), ("stopped", []))
        self.assertEqual(len(self.sessions), 1)
        self.assertIsNone(run.current())
        self.assertTrue(job.stopped)

    def test_pause_holds_the_run_at_the_game_in_hand(self) -> None:
        job = run.start(THREE)
        self.inside.wait(10)

        run.pause()
        self.ran(job)

        self.assertTrue(job.stopped, "a held run is not a finished one")
        now = run.current()
        assert now is not None
        self.assertEqual((now["state"], now["done"], now["reason"]), ("paused", 0, None))
        self.ran(run.resume())
        self.assertEqual(self.targets()[1:], [(GAME_ID, ""), (GAME_ID, PLAIN), (GAME_ID, MOD)])

    def test_discarding_a_paused_run_forgets_it(self) -> None:
        job = run.start(THREE)
        self.inside.wait(10)
        run.pause()
        self.ran(job)

        self.assertIsNone(run.discard())
        self.assertIsNone(run.current())
        with self.assertRaises(service_errors.NotFoundError):
            run.resume()


class HttpTests(_Held):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_run_in_hand_is_read_and_steered(self) -> None:
        self.assertEqual(self.client.get("/capture/runs/current").json(), {"run": None})
        started = self.client.post("/capture/runs", json={
            "games": [GAME_ID], "tables": [{"game": GAME_ID, "table": PLAIN}],
            "kinds": ["playfield"]})
        self.assertEqual(started.status_code, 202, started.text)
        job = started.json()
        self.assertEqual((job["stoppable"], job["links"]["stop"]),
                         (True, "/api/v1/capture/runs/current/stop"))
        self.inside.wait(10)

        read = self.client.get("/capture/runs/current").json()["run"]
        self.assertEqual((read["state"], read["of"], read["job_id"]),
                         ("running", 2, job["id"]))
        self.assertEqual(self.client.post("/capture/runs",
                                          json={"games": [GAME_ID]}).status_code, 409)

        paused = self.client.post("/capture/runs/current/pause")
        self.assertEqual(paused.status_code, 200, paused.text)
        self.finished(jobs.get(job["id"]))
        self.assertEqual(self.client.get("/capture/runs/current").json()["run"]["state"],
                         "paused")

        resumed = self.client.post("/capture/runs/current/resume")
        self.assertEqual(resumed.status_code, 202, resumed.text)
        self.finished(jobs.get(resumed.json()["id"]))
        self.assertEqual(self.client.get("/capture/runs/current").json(), {"run": None})
        for verb in ("stop", "discard", "resume", "pause"):
            with self.subTest(verb):
                self.assertEqual(
                    self.client.post(f"/capture/runs/current/{verb}").status_code, 404)

    def test_the_run_is_on_the_event_stream(self) -> None:
        from httpapi import events as stream

        self.assertIn(events.CAPTURE_RUN_CHANGED, stream.STREAMED_EVENTS)


class NoRunTests(_Library):
    def test_a_finished_job_offers_no_stop(self) -> None:
        from httpapi import jobs as jobs_api

        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        job = jobs.submit(jobs.KIND_MEDIA_CAPTURE, lambda _job: None, stoppable=True)
        done = threading.Event()

        def heard(**_: object) -> None:
            done.set()

        events.subscribe(events.JOB_DONE, heard)
        self.addCleanup(events.unsubscribe, events.JOB_DONE, heard)
        if job.finished_at is None:
            done.wait(5)

        self.assertNotIn("stop", jobs_api.resource(job)["links"])

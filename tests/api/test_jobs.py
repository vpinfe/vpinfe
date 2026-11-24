"""Slow work as a job: one event shape, one at a time, answerable after the fact.

The rules under test are the ones a consumer depends on - that a job publishes the
shape common/events.py documents whoever started it, that two of a kind cannot run
at once over the same files, and that finishing is observable both by event and by
asking later.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import requests
from fastapi.testclient import TestClient

import httpapi
from common import events
from common.config import paths
from common.i18n import t
from common.runtime import jobs
from common.runtime import jobs as job_registry


def _held(job_id: str) -> jobs.Job:
    found = jobs.get(job_id)
    if found is None:
        raise AssertionError(f"no job {job_id} is held")
    return found


class _Recorder:
    """Collects the bus traffic a remote subscriber would see."""

    def __init__(self, test):
        self.seen = []
        for name in (events.JOB_PROGRESS, events.JOB_DONE, events.JOB_FAILED):
            test.addCleanup(events.subscribe(name, self._make(name)))

    def _make(self, name):
        def handler(**payload):
            self.seen.append((name, payload))
        return handler

    def names(self):
        return [name for name, _ in self.seen]

    def payloads(self, name):
        return [payload for seen, payload in self.seen if seen == name]


class TrackTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.bus = _Recorder(self)

    def test_progress_reaches_the_bus_in_the_documented_shape(self) -> None:
        with jobs.track("test.kind") as job:
            job.progress(1, 4, "quarter")

        progress = self.bus.payloads(events.JOB_PROGRESS)
        self.assertEqual(progress, [{"job_id": job.id, "pct": 25, "message": "quarter"}])
        self.assertEqual(self.bus.payloads(events.JOB_DONE), [{"job_id": job.id}])

    def test_a_starters_own_callbacks_still_fire(self) -> None:
        """A starter keeps its own progress bar; the bus is additive."""
        seen: list[tuple[int, int, str]] = []
        logged: list[str] = []

        with jobs.track("test.kind", progress_cb=lambda c, t, m: seen.append((c, t, m)),
                        log_cb=logged.append) as job:
            job.progress(2, 4, "half")
            job.log("working")
            self.assertEqual(job.pct, 50, "reported mid-flight, before completion")

        self.assertEqual(seen, [(2, 4, "half")])
        self.assertEqual(logged, ["working"])
        self.assertEqual(job.pct, 100, "completion is always 100, whatever was reported")

    def test_a_broken_callback_does_not_break_the_job(self) -> None:
        def explode(*_args):
            raise RuntimeError("bad listener")

        with self.assertLogs("vpinfe.common.runtime.jobs", level="DEBUG") as caught:
            with jobs.track("test.kind", progress_cb=explode, log_cb=explode) as job:
                job.progress(1, 2, "still fine")
                job.log("still fine")

        self.assertTrue(any("callback failed" in line.lower() for line in caught.output),
                        "the failure is contained, not swallowed silently")
        self.assertEqual(job.state, jobs.DONE)
        self.assertEqual(self.bus.payloads(events.JOB_DONE), [{"job_id": job.id}])

    def test_zero_total_does_not_divide_by_zero(self) -> None:
        with jobs.track("test.kind") as job:
            job.progress(0, 0, "nothing to do")
            self.assertEqual(job.pct, 0)

    def test_a_raising_job_fails_loudly_and_records_why(self) -> None:
        with self.assertRaises(ValueError):
            with jobs.track("test.kind") as job:
                raise ValueError("scan blew up")

        self.assertEqual(job.state, jobs.FAILED)
        self.assertEqual(job.error, "scan blew up")
        self.assertEqual(self.bus.payloads(events.JOB_FAILED),
                         [{"job_id": job.id, "error": "scan blew up"}])
        self.assertNotIn(events.JOB_DONE, self.bus.names())

    def test_a_request_nothing_answered_names_the_host_it_asked(self) -> None:
        request = requests.Request("GET", "https://catalog.example/data.json").prepare()
        with self.assertRaises(requests.ConnectTimeout):
            with jobs.track("test.kind") as job:
                raise requests.ConnectTimeout(request=request)

        self.assertEqual(job.error, t("said.why.timed_out_at", host="catalog.example"))

    def test_one_kind_at_a_time(self) -> None:
        """Two library scans would interleave writes to the same .info files."""
        with jobs.track("test.kind"):
            with self.assertRaises(jobs.JobBusyError) as raised:
                with jobs.track("test.kind"):
                    pass

        self.assertEqual(str(raised.exception), t("error.jobs.busy"))

    def test_different_kinds_do_not_block_each_other(self) -> None:
        with jobs.track("test.one"), jobs.track("test.two"):
            self.assertEqual(len(jobs.active()), 2)

    def test_the_slot_frees_after_a_failure(self) -> None:
        """A crashed scan must not lock the kind out until restart."""
        with self.assertRaises(ValueError):
            with jobs.track("test.kind"):
                raise ValueError("boom")

        with jobs.track("test.kind") as second:
            self.assertEqual(second.state, jobs.RUNNING)


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)

    def test_submit_runs_off_thread_and_the_result_is_queryable_after(self) -> None:
        started, release = threading.Event(), threading.Event()

        def work(job):
            started.set()
            release.wait(5)
            job.progress(1, 1, "done working")

        job = jobs.submit("test.kind", work)
        self.assertTrue(started.wait(5))
        self.assertEqual(_held(job.id).state, jobs.RUNNING, "returns before finishing")

        release.set()
        for _ in range(500):
            if _held(job.id).state != jobs.RUNNING:
                break
            threading.Event().wait(0.01)

        finished = _held(job.id)
        self.assertEqual(finished.state, jobs.DONE)
        self.assertEqual(finished.pct, 100)
        self.assertIsNotNone(finished.finished_at)

    def test_a_failing_submitted_job_is_recorded_not_raised(self) -> None:
        def work(_job):
            raise RuntimeError("worker died")

        with self.assertLogs("vpinfe.common.runtime.jobs", level="ERROR"):
            job = jobs.submit("test.kind", work)
            for _ in range(500):
                if _held(job.id).state != jobs.RUNNING:
                    break
                threading.Event().wait(0.01)

        self.assertEqual(_held(job.id).state, jobs.FAILED)
        self.assertEqual(_held(job.id).error, "worker died")

    def test_a_job_outlived_by_a_reset_frees_only_its_own_slot(self) -> None:
        release = threading.Event()
        first = jobs.submit("test.kind", lambda job: release.wait(5))
        jobs.reset_for_tests()

        with jobs.track("test.kind") as second:
            release.set()
            for _ in range(500):
                if first.finished_at is not None:
                    break
                threading.Event().wait(0.01)

            self.assertIsNotNone(first.finished_at)
            self.assertEqual(jobs.active("test.kind"), [second])

    def test_an_unknown_id_is_none_rather_than_an_error(self) -> None:
        self.assertIsNone(jobs.get("nope"))

    def test_history_is_bounded_so_a_long_session_does_not_grow(self) -> None:
        for index in range(jobs.HISTORY_LIMIT + 5):
            with jobs.track(f"test.kind{index}"):
                pass

        self.assertEqual(len(jobs.recent(jobs.HISTORY_LIMIT + 5)), jobs.HISTORY_LIMIT)

    def test_a_listing_is_short_unless_the_whole_history_is_asked_for(self) -> None:
        for index in range(30):
            with jobs.track(f"test.kind{index}"):
                pass

        self.assertEqual(len(jobs.recent()), 20)
        self.assertEqual(len(jobs.recent(500)), 30)

    def test_a_finished_job_carries_what_its_result_says(self) -> None:
        with jobs.track(jobs.KIND_TOOL_GET) as job:
            job.result = {"version": "1.2"}

        self.assertEqual(job.detail, t("said.job_tool_version", version="1.2"))
        self.assertEqual(job.snapshot()["detail"], job.detail)

    def test_a_detail_the_work_set_is_kept(self) -> None:
        with jobs.track(jobs.KIND_TOOL_GET) as job:
            job.result = {"version": "1.2"}
            job.detail = "said by the work"

        self.assertEqual(job.detail, "said by the work")

    def test_a_describer_that_raises_does_not_unfinish_the_job(self) -> None:
        with jobs.track(jobs.KIND_LIBRARY_SCAN) as job:
            job.result = {"games": "many", "new_games": 1}

        self.assertEqual(job.state, jobs.DONE)
        self.assertEqual(job.detail, "")

    def test_a_title_is_kept_on_the_job_and_on_the_wire(self) -> None:
        with jobs.track(jobs.KIND_LIBRARY_SCAN, title="Reload from disk") as tracked:
            pass
        started = jobs.submit(jobs.KIND_MEDIA_FILL, lambda job: None, title="Fill art")

        self.assertEqual(tracked.snapshot()["title"], "Reload from disk")
        self.assertEqual(started.snapshot()["title"], "Fill art")

    def test_a_job_with_no_title_has_an_empty_one(self) -> None:
        with jobs.track(jobs.KIND_TOOL_GET) as job:
            pass

        self.assertEqual(job.snapshot()["title"], "")

    def test_a_job_sharing_the_scan_kind_still_takes_its_lock(self) -> None:
        with jobs.track(jobs.KIND_LIBRARY_SCAN, title="Auto-match"):
            with self.assertRaises(jobs.JobBusyError):
                jobs.track(jobs.KIND_LIBRARY_SCAN, title="Reload from disk").__enter__()

    def test_a_job_is_not_stopped_unless_its_work_says_so(self) -> None:
        with jobs.track("test.kind") as job:
            pass

        self.assertFalse(job.snapshot()["stopped"])

    def test_recent_lists_running_jobs_before_finished_ones(self) -> None:
        with jobs.track("test.finished"):
            pass
        with jobs.track("test.running"):
            kinds = [job.kind for job in jobs.recent()]

        self.assertEqual(kinds[0], "test.running")
        self.assertIn("test.finished", kinds)


class JobEndpointTests(unittest.TestCase):
    """The wire side: jobs are readable, and a scan is accepted rather than awaited."""

    def setUp(self) -> None:
        from starlette.testclient import TestClient

        import httpapi

        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_a_running_job_is_listed_and_addressable(self) -> None:
        with jobs.track("test.kind") as job:
            job.progress(1, 2, "halfway")
            listed = self.client.get("/jobs").json()["jobs"]
            one = self.client.get(f"/jobs/{job.id}").json()

        self.assertEqual([row["id"] for row in listed], [job.id])
        self.assertEqual(one["state"], "running")
        self.assertEqual(one["pct"], 50)
        self.assertEqual(one["message"], "halfway")
        self.assertEqual(one["links"]["self"], f"/api/v1/jobs/{job.id}")
        self.assertEqual(one["links"]["events"], "/api/v1/events")

    def test_the_outcome_survives_the_job_finishing(self) -> None:
        """A client that missed job.done can still learn how it went."""
        with jobs.track("test.kind") as job:
            pass

        body = self.client.get(f"/jobs/{job.id}").json()

        self.assertEqual(body["state"], "done")
        self.assertEqual(body["pct"], 100)
        self.assertIsNone(body["error"])
        self.assertIsNotNone(body["finished_at"])

    def test_the_listing_takes_a_limit(self) -> None:
        for index in range(30):
            with jobs.track(f"test.kind{index}"):
                pass

        self.assertEqual(len(self.client.get("/jobs").json()["jobs"]), 20)
        self.assertEqual(len(self.client.get("/jobs", params={"limit": 500}).json()["jobs"]), 30)
        self.assertEqual(self.client.get("/jobs", params={"limit": 0}).status_code, 422)
        self.assertEqual(self.client.get(
            "/jobs", params={"limit": jobs.HISTORY_LIMIT + 1}).status_code, 422)

    def test_a_job_on_the_wire_says_whether_it_stopped_and_what_it_counted(self) -> None:
        with jobs.track(jobs.KIND_TOOL_GET) as job:
            job.result = {"version": "3"}
            job.stopped = True

        body = self.client.get(f"/jobs/{job.id}").json()

        self.assertTrue(body["stopped"])
        self.assertEqual(body["detail"], t("said.job_tool_version", version="3"))

    def test_a_title_comes_over_the_wire(self) -> None:
        with jobs.track(jobs.KIND_LIBRARY_SCAN, title="Auto-match") as job:
            pass

        self.assertEqual(self.client.get(f"/jobs/{job.id}").json()["title"], "Auto-match")

    def test_filtering_by_kind(self) -> None:
        with jobs.track("test.wanted"), jobs.track("test.other"):
            rows = self.client.get("/jobs", params={"kind": "test.wanted"}).json()["jobs"]

        self.assertEqual([row["kind"] for row in rows], ["test.wanted"])

    def test_an_unknown_job_is_a_not_found_envelope(self) -> None:
        response = self.client.get("/jobs/nope")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_a_scan_is_accepted_and_points_at_its_job(self) -> None:
        from unittest.mock import patch

        started = threading.Event()

        def fake_build(job=None, **_kwargs):
            started.set()
            job.progress(1, 1, "scanned")
            return {"found": 1, "not_found": 0}

        with patch("common.games.game_service.build_metadata", fake_build):
            response = self.client.post("/library/scan", json={"download_media": False})
            self.assertTrue(started.wait(5))

        body = response.json()
        self.assertEqual(response.status_code, 202, "accepted, not awaited")
        self.assertEqual(response.headers["Location"], f"/api/v1/jobs/{body['id']}")
        self.assertEqual(body["kind"], jobs.KIND_LIBRARY_SCAN)

    def test_a_second_scan_is_refused_while_one_runs(self) -> None:
        with jobs.track(jobs.KIND_LIBRARY_SCAN):
            response = self.client.post("/library/scan")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "conflict")
        self.assertEqual(response.json()["error"]["message"], t("error.jobs.library_busy"))

    def test_discovery_advertises_the_jobs_collection(self) -> None:
        links = self.client.get("/").json()["links"]

        self.assertEqual(links["jobs"], "/api/v1/jobs")


class ResultTests(unittest.TestCase):
    """A job that produces an answer has somewhere to put it.

    Some jobs are their own outcome - a library scan leaves a scanned library. An
    import's outcome is which games were imported and which were not, and without this
    every such job has to invent a back channel of its own.
    """

    def setUp(self) -> None:
        job_registry.reset_for_tests()
        self.addCleanup(job_registry.reset_for_tests)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _finished(self, answer):
        done = threading.Event()
        job = job_registry.submit("probe", lambda _job: answer)
        for _ in range(200):
            if job.state != job_registry.RUNNING:
                break
            time.sleep(0.01)
        done.set()
        return job

    def test_what_the_work_answered_comes_back_on_the_job(self) -> None:
        job = self._finished({"created": 3, "failed": 1})

        found = self.client.get(f"/jobs/{job.id}").json()

        self.assertEqual(found["result"], {"created": 3, "failed": 1})

    def test_work_that_answers_nothing_carries_nothing(self) -> None:
        job = self._finished(None)

        self.assertIsNone(self.client.get(f"/jobs/{job.id}").json()["result"])

    def test_the_listing_leaves_it_out(self) -> None:
        """A row per game is a fine answer for one job and an expensive one for twenty,
        so watching the queue does not pay for it."""
        self._finished({"games": [{"name": "one"}, {"name": "two"}]})

        listed = self.client.get("/jobs").json()["jobs"]

        self.assertTrue(listed)
        self.assertIsNone(listed[0].get("result"))


class KeptResultTests(unittest.TestCase):
    """A job that asks to be kept can be opened after the page that started it has gone,
    and after the install restarted."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patched = mock.patch.object(paths, "JOBS_DIR", Path(tmp.name) / "jobs")
        patched.start()
        self.addCleanup(patched.stop)
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.dir = paths.JOBS_DIR

    def _run(self, answer, *, keep: bool = True, problems: int = 0) -> jobs.Job:
        def work(job):
            job.problems = problems
            return answer

        job = jobs.submit("probe", work, keep_result=keep)
        for _ in range(500):
            if job.state != jobs.RUNNING:
                break
            time.sleep(0.01)
        return job

    def _restart(self) -> None:
        jobs.reset_for_tests()
        jobs._loaded = False

    def test_a_kept_result_is_written_and_says_so_on_the_wire(self) -> None:
        job = self._run({"games": 3}, problems=1)

        self.assertTrue((self.dir / f"{job.id}.json").is_file())
        wire = job.snapshot()
        self.assertTrue(wire["report"])
        self.assertEqual(wire["problems"], 1)

    def test_a_job_that_did_not_ask_leaves_nothing_behind(self) -> None:
        job = self._run({"games": 3}, keep=False)

        self.assertFalse(self.dir.is_dir() and list(self.dir.glob("*.json")))
        self.assertFalse(job.snapshot()["report"])

    def test_a_failed_job_keeps_nothing(self) -> None:
        def work(_job):
            raise RuntimeError("no")

        job = jobs.submit("probe", work, keep_result=True)
        for _ in range(500):
            if job.state != jobs.RUNNING:
                break
            time.sleep(0.01)

        self.assertFalse(job.snapshot()["report"])

    def test_it_comes_back_after_a_restart_with_its_result(self) -> None:
        job = self._run({"games": 3}, problems=2)
        self._restart()

        found = jobs.get(job.id)

        assert found is not None
        self.assertEqual(found.state, jobs.DONE)
        self.assertEqual(found.result, {"games": 3})
        self.assertEqual(found.problems, 2)
        self.assertTrue(found.snapshot()["report"])
        self.assertIn(job.id, [one.id for one in jobs.recent()])

    def test_only_the_latest_are_kept(self) -> None:
        for number in range(jobs.KEPT_LIMIT + 3):
            self._run({"n": number})

        self.assertEqual(len(list(self.dir.glob("*.json"))), jobs.KEPT_LIMIT)

    def test_a_file_that_cannot_be_read_does_not_hide_the_others(self) -> None:
        job = self._run({"games": 3})
        (self.dir / "broken.json").write_text("{", encoding="utf-8")
        self._restart()

        self.assertIsNotNone(jobs.get(job.id))

    def test_a_result_that_cannot_be_written_does_not_fail_the_job(self) -> None:
        with mock.patch.object(jobs, "write_atomic", side_effect=OSError("disk full")):
            job = self._run({"games": 3})

        self.assertEqual(job.state, jobs.DONE)
        self.assertFalse(job.snapshot()["report"])


if __name__ == "__main__":
    unittest.main()

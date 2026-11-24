"""An extension's job is shown to the callers who reach what the request that started it did.

A Remote that can open an extension's Play page must not read the progress, the error text or
the result of work a Library or Protected request started. Core's own jobs stay readable by
anyone holding `jobs:read`.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from starlette.requests import Request

from common import events
from common.config import paths
from common.extensions import reach
from common.extensions.context import ExtensionJobs
from common.runtime import jobs as job_registry
from httpapi import events as event_stream
from httpapi import jobs as jobs_api
from httpapi.errors import NotFoundError


def _request(tier: str) -> Request:
    return cast(
        Request, SimpleNamespace(state=SimpleNamespace(identity=SimpleNamespace(tier=tier))))


def _started(kind: str, *, extension: str = "", tier: str = "") -> job_registry.Job:
    job = job_registry.submit(kind, lambda _job: {"secret": kind}, extension=extension,
                              tier=tier, keep_result=True)
    for _ in range(500):
        if job.state != job_registry.RUNNING:
            break
        time.sleep(0.01)
    return job


class JobReachTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        patched = mock.patch.object(paths, "JOBS_DIR", Path(holder.name) / "jobs")
        patched.start()
        self.addCleanup(patched.stop)
        job_registry.reset_for_tests()
        self.addCleanup(job_registry.reset_for_tests)

    def _listed(self, tier: str) -> set[str]:
        found = jobs_api.list_jobs(_request(tier), kind="", limit=20)
        return {job.kind for job in found.jobs}

    def test_a_play_caller_sees_core_jobs_and_play_started_ones_only(self) -> None:
        _started("core.scan")
        _started("tool.play", extension="tool", tier=reach.PLAY)
        _started("tool.library", extension="tool", tier=reach.LIBRARY)
        _started("tool.protected", extension="tool", tier=reach.PROTECTED)

        self.assertEqual(self._listed(reach.PLAY), {"core.scan", "tool.play"})

    def test_a_library_caller_sees_a_protected_started_job_because_reading_needs_library(
            self) -> None:
        _started("tool.protected", extension="tool", tier=reach.PROTECTED)
        _started("tool.library", extension="tool", tier=reach.LIBRARY)

        self.assertEqual(self._listed(reach.LIBRARY), {"tool.protected", "tool.library"})

    def test_a_hidden_job_answers_as_an_unknown_one(self) -> None:
        hidden = _started("tool.library", extension="tool", tier=reach.LIBRARY)

        with self.assertRaises(NotFoundError):
            jobs_api.get_job(hidden.id, _request(reach.PLAY))
        self.assertEqual(jobs_api.get_job(hidden.id, _request(reach.LIBRARY)).kind,
                         "tool.library")

    def test_an_extension_job_with_no_tier_recorded_is_library_s(self) -> None:
        job = _started("tool.old", extension="tool")

        self.assertNotIn("tool.old", self._listed(reach.PLAY))
        self.assertIn("tool.old", self._listed(reach.LIBRARY))
        self.assertEqual(jobs_api.tier_needed(job), reach.LIBRARY)

    def test_the_extension_and_tier_survive_a_restart(self) -> None:
        job = _started("tool.library", extension="tool", tier=reach.LIBRARY)
        job_registry.reset_for_tests()
        job_registry._loaded = False

        found = job_registry.get(job.id)

        assert found is not None
        self.assertEqual((found.extension, found.tier), ("tool", reach.LIBRARY))
        self.assertNotIn("tool.library", self._listed(reach.PLAY))

    def test_a_job_an_extension_starts_takes_the_tier_of_the_request(self) -> None:
        submit = ExtensionJobs("tool").submit
        for tier in (reach.PLAY, reach.LIBRARY):
            with reach.marking(tier):
                job = submit(f"run-{tier}", lambda _job: None)
            self.assertEqual((job.extension, job.tier), ("tool", tier))

    def test_a_job_started_with_no_request_behind_it_is_library_s(self) -> None:
        job = ExtensionJobs("tool").submit("timer", lambda _job: None)

        self.assertEqual((job.extension, job.tier), ("tool", reach.LIBRARY))


class JobEventReachTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        events.clear()
        event_stream.reset()
        event_stream.attach()
        job_registry.reset_for_tests()
        self.addCleanup(job_registry.reset_for_tests)
        self.addCleanup(events.clear)
        self.addCleanup(event_stream.reset)

    def _open(self, tier: str, last_event_id: str | None = None):
        stream = event_stream._stream(None, last_event_id, tier)

        async def close():
            await stream.aclose()

        self.addAsyncCleanup(close)
        return stream

    async def _next(self, stream) -> str:
        return await asyncio.wait_for(anext(stream), 2.0)

    async def _hello(self, stream) -> None:
        await self._next(stream)
        await self._next(stream)

    def _held(self, extension: str, tier: str) -> job_registry.Job:
        release = threading.Event()
        job = job_registry.submit(f"{extension or 'core'}.{tier}", lambda _job: release.wait(5),
                                  extension=extension, tier=tier)

        def settle() -> None:
            # A job that finishes after the next test's setUp has cleared the registry puts
            # itself back, and its done event is then a frame that stream was never meant to see.
            release.set()
            for worker in threading.enumerate():
                if worker.name == f"job-{job.kind}":
                    worker.join(2.0)

        self.addCleanup(settle)
        return job

    async def _names_seen(self, stream) -> list[str]:
        await asyncio.sleep(0.05)
        seen: list[str] = []
        while True:
            try:
                frame = await asyncio.wait_for(anext(stream), 0.1)
            except TimeoutError:
                return seen
            data = [line for line in frame.splitlines() if line.startswith("data: ")]
            if data:
                payload = json.loads(data[0][6:])
                seen.append(payload.get("message") or payload.get("error") or "")

    async def test_a_play_subscriber_is_not_sent_a_library_jobs_progress(self) -> None:
        play = self._open(reach.PLAY)
        library = self._open(reach.LIBRARY)
        await self._hello(play)
        await self._hello(library)
        hidden = self._held("tool", reach.LIBRARY)
        shown = self._held("tool", reach.PLAY)

        events.emit(events.JOB_PROGRESS, job_id=hidden.id, pct=1, message="library work")
        events.emit(events.JOB_PROGRESS, job_id=shown.id, pct=1, message="play work")

        self.assertEqual(await self._names_seen(play), ["play work"])
        self.assertEqual(await self._names_seen(library), ["library work", "play work"])

    async def test_core_job_progress_reaches_a_play_subscriber(self) -> None:
        play = self._open(reach.PLAY)
        await self._hello(play)
        core = self._held("", "")

        events.emit(events.JOB_PROGRESS, job_id=core.id, pct=1, message="core work")

        self.assertEqual(await self._names_seen(play), ["core work"])

    async def test_a_resuming_play_subscriber_is_not_replayed_what_it_could_not_see(self) -> None:
        hidden = self._held("tool", reach.LIBRARY)
        events.emit(events.JOB_PROGRESS, job_id=hidden.id, pct=1, message="library work")
        events.emit(events.JOB_FAILED, job_id=hidden.id, error="path /secret failed")
        play = self._open(reach.PLAY, last_event_id="0")

        await self._hello(play)

        self.assertEqual(await self._names_seen(play), [])

    async def test_a_play_subscriber_is_not_sent_a_library_jobs_error_text(self) -> None:
        play = self._open(reach.PLAY)
        library = self._open(reach.LIBRARY)
        await self._hello(play)
        await self._hello(library)
        hidden = self._held("tool", reach.LIBRARY)

        events.emit(events.JOB_FAILED, job_id=hidden.id, error="path /secret failed")

        self.assertEqual(await self._names_seen(play), [])
        self.assertEqual(await self._names_seen(library), ["path /secret failed"])

    async def test_the_route_opens_the_stream_at_the_callers_tier(self) -> None:
        hidden = self._held("tool", reach.LIBRARY)
        route = await event_stream.subscribe(_request(reach.PLAY), names="", last_event_id=None)
        body = route.body_iterator

        async def close():
            await body.aclose()

        self.addAsyncCleanup(close)
        await asyncio.wait_for(anext(body), 2.0)  # type: ignore[call-overload]
        await asyncio.wait_for(anext(body), 2.0)  # type: ignore[call-overload]

        events.emit(events.JOB_PROGRESS, job_id=hidden.id, pct=1, message="library work")

        self.assertEqual(await self._names_seen(body), [])

    async def test_a_job_event_for_an_unknown_job_is_library_s(self) -> None:
        play = self._open(reach.PLAY)
        await self._hello(play)

        events.emit(events.JOB_PROGRESS, job_id="gone", pct=1, message="who knows")

        self.assertEqual(await self._names_seen(play), [])


if __name__ == "__main__":
    unittest.main()

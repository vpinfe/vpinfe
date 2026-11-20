"""Get FFmpeg: which build each device gets, the download kept only where it is the file
pinned, and the registry finding it afterwards."""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common import service_errors
from common.capture import preflight
from common.host import get_ffmpeg, tools, vpinos
from common.i18n import size, t
from common.runtime import jobs
from common.security import http_client
from tests.support.skips import needs_executable_bit

PROGRAM = b"#!/bin/sh\nexit 0\n"


def _zip(member: str, data: bytes = PROGRAM) -> bytes:
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w") as archive:
        archive.writestr(member, data)
    return held.getvalue()


def _pinned(archive: bytes, suffix: str, member: str) -> get_ffmpeg.Build:
    return get_ffmpeg.Build("9.0.2", f"https://example.test/ffmpeg{suffix}",
                            hashlib.sha256(archive).hexdigest(), len(archive), member,
                            "example.test")


class PinTests(unittest.TestCase):
    def test_each_platform_gets_its_own_build_and_linux_none(self) -> None:
        cases = {(tools.WINDOWS, "AMD64"): get_ffmpeg.WINDOWS_X64,
                 (tools.DARWIN, "arm64"): get_ffmpeg.MACOS_ARM64,
                 (tools.DARWIN, "x86_64"): get_ffmpeg.MACOS_X64,
                 (tools.LINUX, "x86_64"): None}
        for (system, machine), expected in cases.items():
            with self.subTest(system=system, machine=machine):
                self.assertIs(get_ffmpeg.build(system, machine), expected)

    def test_every_pin_is_a_checksummed_file_over_https(self) -> None:
        for pin in (get_ffmpeg.WINDOWS_X64, get_ffmpeg.MACOS_ARM64, get_ffmpeg.MACOS_X64):
            with self.subTest(pin.url):
                self.assertTrue(pin.url.startswith("https://"))
                self.assertRegex(pin.sha256, re.compile(r"^[0-9a-f]{64}$"))
                self.assertGreater(pin.size, 0)
                self.assertIn(Path(pin.member).name, ("ffmpeg", "ffmpeg.exe"))
                self.assertIn(pin.version, pin.url)
                self.assertTrue(pin.url.endswith(".zip"))


class GetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / "ffmpeg"
        self.enterContext(patch.object(get_ffmpeg, "FOLDER", self.folder))
        self.probed = self.enterContext(patch.object(
            tools, "probed", return_value=tools.Probe(True, "9.0.2")))
        self.seen: list[tuple[int, int]] = []

    def _fetch(self, archive: bytes) -> Any:
        def fetch(url: str, dest: Path, progress: Any = None) -> None:
            dest.write_bytes(archive)
            if progress is not None:
                progress(len(archive))
        return fetch

    def _get(self, archive: bytes, pin: get_ffmpeg.Build) -> Path:
        return get_ffmpeg.get(lambda done, total: self.seen.append((done, total)),
                              fetch=self._fetch(archive), chosen=pin)

    def test_the_program_is_unpacked_runnable_and_the_archive_gone(self) -> None:
        archive = _zip("ffmpeg")

        got = self._get(archive, _pinned(archive, ".zip", "ffmpeg"))

        self.assertEqual(got, self.folder / "ffmpeg")
        self.assertEqual(got.read_bytes(), PROGRAM)
        self.assertEqual(sorted(os.listdir(self.folder)), ["ffmpeg"])
        self.assertEqual(self.seen, [(len(archive), len(archive))])
        self.probed.assert_called_once_with(tools.FFMPEG, got)

    @needs_executable_bit
    def test_the_unpacked_program_can_be_run(self) -> None:
        archive = _zip("ffmpeg")

        got = self._get(archive, _pinned(archive, ".zip", "ffmpeg"))

        self.assertTrue(got.stat().st_mode & stat.S_IXUSR)

    def test_windows_build_comes_out_of_its_zip_by_its_path_inside(self) -> None:
        member = get_ffmpeg.WINDOWS_X64.member
        archive = _zip(member)

        got = self._get(archive, _pinned(archive, ".zip", member))

        self.assertEqual((got.name, got.read_bytes()), ("ffmpeg.exe", PROGRAM))
        self.assertEqual(sorted(os.listdir(self.folder)), ["ffmpeg.exe"])

    def test_a_download_that_is_not_the_pinned_file_is_not_kept(self) -> None:
        archive = _zip("ffmpeg")
        for wrong in (_pinned(_zip("ffmpeg", b"other"), ".zip", "ffmpeg"),
                      get_ffmpeg.Build("9.0.2", "https://example.test/ffmpeg.zip",
                                       hashlib.sha256(archive).hexdigest(), 1, "ffmpeg",
                                       "example.test")):
            with self.subTest(size=wrong.size), \
                    self.assertRaises(get_ffmpeg.GetFFmpegError) as refused:
                self._get(archive, wrong)
            self.assertEqual(str(refused.exception), t(get_ffmpeg.DAMAGED))
            self.assertEqual(os.listdir(self.folder), [])
        self.probed.assert_not_called()

    def test_one_that_does_not_run_here_is_removed(self) -> None:
        archive = _zip("ffmpeg")
        self.probed.return_value = tools.Probe(False, reason=tools.FAILED)

        with self.assertRaises(get_ffmpeg.GetFFmpegError) as refused:
            self._get(archive, _pinned(archive, ".zip", "ffmpeg"))

        self.assertEqual(str(refused.exception), t(get_ffmpeg.DOES_NOT_RUN))
        self.assertEqual(os.listdir(self.folder), [])

    def test_a_download_that_fails_leaves_nothing(self) -> None:
        def fails(url: str, dest: Path, progress: Any = None) -> None:
            dest.write_bytes(b"half")
            raise OSError("gone")

        with self.assertRaises(OSError):
            get_ffmpeg.get(fetch=fails, chosen=_pinned(b"x", ".zip", "ffmpeg"))
        self.assertEqual(os.listdir(self.folder), [])

    def test_there_is_nothing_to_get_on_linux(self) -> None:
        with patch.object(tools, "here", return_value=tools.LINUX), \
                self.assertRaises(service_errors.UnavailableError):
            get_ffmpeg.start()


class DownloadProgressTests(unittest.TestCase):
    def test_the_bytes_written_so_far_are_handed_on_after_each_chunk(self) -> None:
        class Answered:
            def __enter__(self) -> Answered:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int) -> Any:
                yield from (b"ab", b"", b"cde")

        dest = Path(self.enterContext(tempfile.TemporaryDirectory())) / "file"
        seen: list[int] = []
        with patch.object(http_client, "_asked", return_value=Answered()):
            http_client.download_file("https://example.test/file", dest, progress=seen.append)

        self.assertEqual((seen, dest.read_bytes()), ([2, 5], b"abcde"))


class JobTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)

    def test_it_runs_as_a_job_saying_how_far_it_has_got(self) -> None:
        done = threading.Event()

        def got(progress: Any, **_kwargs: Any) -> Path:
            progress(12_000_000, 34_000_000)
            done.set()
            return Path("/x/ffmpeg")

        with patch.object(tools, "here", return_value=tools.DARWIN), \
                patch.object(get_ffmpeg, "get", side_effect=got):
            job = get_ffmpeg.start()
            self.assertTrue(done.wait(5))
            for _ in range(100):
                if job.state != jobs.RUNNING:
                    break
                threading.Event().wait(0.01)

        self.assertEqual(job.kind, jobs.KIND_TOOL_GET)
        self.assertEqual(job.state, jobs.DONE)
        self.assertEqual(job.result, {"path": str(Path("/x/ffmpeg")), "version": "9.0.2"})
        self.assertEqual(job.message, t("tools.get.progress", tool="FFmpeg",
                                        done=size(12_000_000), total=size(34_000_000)))


class RegistryTests(unittest.TestCase):
    """What discovery says of a missing FFmpeg where VPinFE can get one."""

    def setUp(self) -> None:
        self.enterContext(patch.object(vpinos, "detected", return_value=False))

    def test_a_missing_ffmpeg_on_windows_and_macos_offers_get_ffmpeg(self) -> None:
        for where in (tools.WINDOWS, tools.DARWIN):
            with self.subTest(where), patch.object(tools, "here", return_value=where):
                row = tools.row(tools.Found(tools.FFMPEG, tools.State.MISSING))

                self.assertEqual(row["fix"], tools.FIX_AUTO)
                self.assertEqual(row["remedy"]["key"], "tools.ffmpeg.hint.get")
                self.assertEqual(row["remedy"]["get"]["version"], "9.0.2")
                self.assertIn(t("manager.settings.get_ffmpeg"), tools.words(row["remedy"]))

    def test_on_linux_it_is_the_persons_to_install(self) -> None:
        with patch.object(tools, "here", return_value=tools.LINUX):
            row = tools.row(tools.Found(tools.FFMPEG, tools.State.MISSING))

        self.assertEqual((row["fix"], row["remedy"]["key"]), (tools.FIX_USER,
                                                              "tools.hint.linux"))
        self.assertNotIn("get", row["remedy"])

    def test_one_that_is_found_offers_nothing(self) -> None:
        with patch.object(tools, "here", return_value=tools.DARWIN):
            row = tools.row(tools.Found(tools.FFMPEG, tools.State.FOUND, Path("/f"),
                                        tools.Probe(True, "9.0")))

        self.assertEqual((row["fix"], row["remedy"]), (tools.FIX_NONE, None))

    def test_a_recording_that_needs_it_says_vpinfe_can_get_it(self) -> None:
        with patch.object(tools, "here", return_value=tools.WINDOWS):
            said = preflight._needs({"ffmpeg": tools.Found(tools.FFMPEG,
                                                           tools.State.MISSING)},
                                    tools.FFMPEG)

        assert said is not None
        self.assertEqual(said["fix"], tools.FIX_AUTO)
        self.assertTrue(said["remedy"]["get"])

    def test_an_ffmpeg_that_lacks_an_encoder_is_the_persons_to_replace(self) -> None:
        found = tools.Found(tools.FFMPEG, tools.State.FOUND, Path("/f"),
                            tools.Probe(True, "9.0", {tools.ENCODERS: frozenset({"png"})}))
        with patch.object(tools, "here", return_value=tools.DARWIN):
            said = preflight._needs({"ffmpeg": found}, tools.FFMPEG, "libx264", "H.264")

        assert said is not None
        self.assertEqual(said["fix"], tools.FIX_USER)
        self.assertNotIn("get", said["remedy"])

    @unittest.skipIf(sys.platform.startswith("win"), "the program here is a shell script")
    def test_the_copy_it_got_is_found_where_nothing_else_is(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        folder = root / "tools" / "ffmpeg"
        folder.mkdir(parents=True)
        program = folder / "ffmpeg"
        program.write_bytes(PROGRAM)
        program.chmod(0o755)
        self.enterContext(patch.object(tools, "here", return_value=tools.DARWIN))
        self.enterContext(patch.dict(os.environ, {"PATH": str(root / "empty")}))
        self.enterContext(patch.dict(tools._PLACES, {tools.DARWIN: ()}))
        self.enterContext(patch.dict(tools.FFMPEG.places, {tools.DARWIN: (str(folder),)}))
        self.enterContext(patch.object(tools, "probed", return_value=tools.Probe(True, "9")))

        found = tools.resolve(tools.FFMPEG, "")

        self.assertEqual((found.state, found.path), (tools.State.FOUND, program))

    def test_the_registry_looks_in_the_folder_get_ffmpeg_writes(self) -> None:
        for where in (tools.WINDOWS, tools.DARWIN):
            with self.subTest(where):
                self.assertIn(str(get_ffmpeg.FOLDER), tools.FFMPEG.places[where])


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_it_is_accepted_with_where_to_watch_it(self) -> None:
        job = jobs.Job(id="abc", kind=jobs.KIND_TOOL_GET)
        with patch.object(get_ffmpeg, "start", return_value=job):
            response = self.client.post("/config/tools/ffmpeg/get")

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.headers["location"], "/api/v1/jobs/abc")
        self.assertEqual(response.json()["kind"], jobs.KIND_TOOL_GET)

    def test_a_tool_vpinfe_cannot_get_is_not_found(self) -> None:
        self.assertEqual(self.client.post("/config/tools/grim/get").status_code, 404)

    def test_a_device_with_nothing_to_get_says_so(self) -> None:
        with patch.object(tools, "here", return_value=tools.LINUX):
            response = self.client.post("/config/tools/ffmpeg/get")

        self.assertEqual(response.status_code, 501)
        self.assertIn(t(get_ffmpeg.NOT_HERE), response.text)


if __name__ == "__main__":
    unittest.main()

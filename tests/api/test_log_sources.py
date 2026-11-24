"""Which files the Logs page can read or hand over, and that a name is all it takes.

The allowlist is the whole of the safety here: a source is a name that resolves against
the files this install knows about, never a path somebody supplied.
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import httpapi
from common.host import log_sources
from common.runtime import log_setup

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]

VPX_LINES = (
    "﻿2026-10-02 15:20:32.448 INFO  [3414553] [VPApp::InitInstance@326] Starting\n"
    "2026-10-02 15:20:33.001 WARN  [3414553] [Player::Render@88] Slow frame\n"
    "a continuation line\n"
    "\n"
    "2026-10-02 15:20:34.500 ERROR [3414553] [Table::Load@12] Could not load\n"
)


class _Logs(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._release)
        self.dir = Path(self.tmp.name)
        log_setup.configure_logging(self.dir / "config")
        self.vpx = self.dir / "vpx" / "vpinball.log"
        self.vpx.parent.mkdir()
        patch = mock.patch("common.host.log_sources.vpx_log.current_vpinball_log",
                           return_value=self.vpx)
        patch.start()
        self.addCleanup(patch.stop)

    def _release(self) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()


class ResolvingASource(_Logs):
    def test_the_empty_name_is_the_log_being_written(self) -> None:
        found = log_sources.resolve("")

        assert found is not None
        self.assertEqual(found.path, log_setup.log_file())

    def test_visual_pinballs_log_is_named_when_a_launcher_says_where_it_is(self) -> None:
        found = log_sources.resolve("vpinball.log")

        self.assertEqual(log_sources.visual_pinball(), "vpinball.log")
        assert found is not None
        self.assertEqual(found.path, self.vpx)

    def test_it_is_named_whether_or_not_it_has_been_written_yet(self) -> None:
        self.assertFalse(self.vpx.exists())

        self.assertIsNotNone(log_sources.resolve("vpinball.log"))

    def test_a_path_is_not_a_source(self) -> None:
        secret = self.dir / "secret.txt"
        secret.write_text("no", encoding="utf-8")

        self.assertIsNone(log_sources.resolve(str(secret)))
        self.assertIsNone(log_sources.resolve("../secret.txt"))

    def test_no_launcher_means_no_visual_pinball_source(self) -> None:
        with mock.patch("common.host.log_sources.vpx_log.current_vpinball_log",
                        return_value=None):
            self.assertEqual(log_sources.visual_pinball(), "")
            self.assertIsNone(log_sources.resolve("vpinball.log"))


class ReadingVisualPinballsLog(_Logs):
    def setUp(self) -> None:
        super().setUp()
        self.vpx.write_text(VPX_LINES, encoding="utf-8")

    def test_a_record_has_its_time_level_function_and_message(self) -> None:
        records = log_sources.read("vpinball.log", 10)["records"]

        self.assertEqual(records[0], {
            "when": "2026-10-02 15:20:32", "level": "INFO",
            "logger": "VPApp::InitInstance@326", "message": "Starting"})

    def test_warn_reads_as_the_warning_level_vpinfe_filters_on(self) -> None:
        records = log_sources.read("vpinball.log", 10, level="WARNING")["records"]

        self.assertEqual([r["logger"] for r in records], ["Player::Render@88"])

    def test_a_line_that_starts_no_record_belongs_to_the_one_before(self) -> None:
        records = log_sources.read("vpinball.log", 10)["records"]

        self.assertEqual(records[1]["message"], "Slow frame\na continuation line")

    def test_a_find_keeps_only_the_records_holding_it(self) -> None:
        records = log_sources.read("vpinball.log", 10, contains="load")["records"]

        self.assertEqual([r["logger"] for r in records], ["Table::Load@12"])

    def test_a_log_not_yet_written_has_no_records(self) -> None:
        self.vpx.unlink()

        found = log_sources.read("vpinball.log", 10)

        self.assertEqual(found["records"], [])
        self.assertEqual(found["visual_pinball"], "vpinball.log")

    def test_it_is_not_among_the_vpinfe_files(self) -> None:
        self.assertNotIn("vpinball.log", log_sources.read("", 10)["sources"])


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class TheRoutes(_Logs):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_visual_pinballs_records_come_back_with_where_the_file_is(self) -> None:
        self.vpx.write_text(VPX_LINES, encoding="utf-8")

        body = self.client.get("/logs", params={"source": "vpinball.log"}).json()

        self.assertEqual(body["count"], 3)
        self.assertEqual(body["path"], str(self.vpx))
        self.assertEqual(body["visual_pinball"], "vpinball.log")

    def test_a_name_that_is_not_a_source_reads_nothing(self) -> None:
        secret = self.dir / "secret.txt"
        secret.write_text("2026-10-02 15:20:32 INFO [x] leaked\n", encoding="utf-8")

        for name in (str(secret), "../secret.txt"):
            with self.subTest(name=name):
                body = self.client.get("/logs", params={"source": name}).json()
                self.assertEqual(body["records"], [])
                self.assertEqual(body["path"], "")

    def test_a_file_is_downloaded_whole(self) -> None:
        self.vpx.write_bytes(b"first\nsecond\n")

        answer = self.client.get("/logs/file", params={"source": "vpinball.log"})

        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.text, "first\nsecond\n")
        self.assertIn("vpinball.log", answer.headers["content-disposition"])

    def test_a_name_that_is_not_a_log_is_not_found(self) -> None:
        secret = self.dir / "secret.txt"
        secret.write_text("no", encoding="utf-8")

        for name in (str(secret), "../secret.txt", "secret.txt"):
            with self.subTest(name=name):
                self.assertEqual(
                    self.client.get("/logs/file", params={"source": name}).status_code, 404)

    def test_a_log_not_yet_written_is_not_found(self) -> None:
        self.assertEqual(
            self.client.get("/logs/file", params={"source": "vpinball.log"}).status_code,
            404)

    def test_a_file_past_the_cap_is_refused(self) -> None:
        self.vpx.write_text("x", encoding="utf-8")

        with mock.patch.object(log_sources, "MAX_DOWNLOAD_BYTES", 0):
            answer = self.client.get("/logs/file", params={"source": "vpinball.log"})

        self.assertEqual(answer.status_code, 413)


if __name__ == "__main__":
    unittest.main()

"""An install's own log, read back over HTTP.

Records rather than lines is the whole of it. A traceback is one thing that happened,
and the parsing is what decides whether a filter drops the half carrying the reason.
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpapi
from common.runtime import log_setup
from tests.support.library import TempTree

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]


class ReadingTheLogTests(unittest.TestCase):
    """The parsing, against a log this test wrote itself."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._release)
        log_setup.configure_logging(Path(self.tmp.name))
        # The terminal handler goes: these tests write on purpose, and every line of it
        # would land in the middle of somebody else's test output.
        root = logging.getLogger()
        for handler in list(root.handlers):
            if not getattr(handler, "baseFilename", ""):
                root.removeHandler(handler)
        self.log = logging.getLogger("vpinfe.test.logs")

    def _release(self) -> None:
        """Close the file before the directory holding it goes."""
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

    def test_the_file_being_written_is_the_one_read(self) -> None:
        """Asked of the handler rather than rebuilt from the config dir, which would
        name a file nothing is writing to."""
        written = log_setup.log_file()
        assert written is not None
        self.assertEqual(written.parent, Path(self.tmp.name))

    def test_a_record_arrives_with_its_level_and_its_source(self) -> None:
        self.log.info("something happened")

        found = log_setup.read_log()[-1]

        self.assertEqual(found["level"], "INFO")
        self.assertEqual(found["logger"], "vpinfe.test.logs")
        self.assertEqual(found["message"], "something happened")

    def test_a_traceback_stays_with_the_message_that_caused_it(self) -> None:
        """Split into rows it buries the message and the continuation lines carry no
        level of their own."""
        try:
            raise ValueError("boom")
        except ValueError:
            self.log.exception("it went wrong")

        found = log_setup.read_log()[-1]

        self.assertEqual(found["level"], "ERROR")
        self.assertTrue(found["message"].startswith("it went wrong"))
        self.assertIn("ValueError: boom", found["message"])

    def test_a_level_filter_keeps_the_whole_record(self) -> None:
        self.log.info("fine")
        try:
            raise ValueError("boom")
        except ValueError:
            self.log.exception("it went wrong")

        found = log_setup.read_log(level="error")

        self.assertEqual([r["level"] for r in found], ["ERROR"])
        self.assertIn("ValueError: boom", found[0]["message"])

    def test_text_matches_the_message_or_the_source(self) -> None:
        self.log.info("a needle in here")

        self.assertTrue(log_setup.read_log(contains="needle"))
        self.assertTrue(log_setup.read_log(contains="vpinfe.test"))
        self.assertFalse(log_setup.read_log(contains="nothing like that"))

    def test_the_newest_are_the_ones_kept(self) -> None:
        for number in range(10):
            self.log.info("line %d", number)

        found = log_setup.read_log(limit=3)

        self.assertEqual([r["message"] for r in found],
                         ["line 7", "line 8", "line 9"])


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class LogRouteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_it_answers_with_records_and_where_they_came_from(self) -> None:
        body = self.client.get("/logs").json()

        self.assertIn("records", body)
        self.assertEqual(body["count"], len(body["records"]))

    def test_a_limit_past_what_it_will_serve_is_refused(self) -> None:
        """A cap, so nobody pulls a 2MB file through a settings panel."""
        answer = self.client.get(f"/logs?limit={log_setup.TAIL_BYTES}")

        self.assertEqual(answer.status_code, 422)


if __name__ == "__main__":
    unittest.main()

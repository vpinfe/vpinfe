"""The log the package reads is the log the app writes.

Two things hold the package to the app's own call sites: the codes a person types never reach
the file, and the record shape the Health section groups by is the shape the logger writes.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.runtime import log_setup
from common.security import sign_in
from common.support import redact
from common.support.collect import health

GOLDEN = (
    "2026-01-02 03:04:05 INFO [vpinfe.a] started\n"
    "2026-01-02 03:04:06 WARNING [vpinfe.b] slow disk\n"
    "2026-01-02 03:04:07 WARNING [vpinfe.b] slow disk\n"
    "2026-01-02 03:04:08 ERROR [vpinfe.c] it failed\n"
    "Traceback (most recent call last):\n"
    "ValueError: boom\n"
    "2026-01-02 03:04:09 ERROR [vpinfe.c] it failed\n"
    "2026-01-02 03:04:10 DEBUG [vpinfe.d] noise\n"
)


class _LogFolder(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._release)
        log_setup.configure_logging(Path(self.tmp.name))
        root = logging.getLogger()
        for handler in list(root.handlers):
            if not getattr(handler, "baseFilename", ""):
                root.removeHandler(handler)

    def _release(self) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

    def written(self) -> str:
        path = log_setup.log_file()
        assert path is not None
        return path.read_text(encoding="utf-8")


class TheCodesAPersonTypes(_LogFolder):
    """Each call site that draws a code logs it for the terminal only."""

    def setUp(self) -> None:
        super().setUp()
        self.signing = sign_in.SignIn(path=Path(self.tmp.name) / "sessions.json")
        self.signing.screen = lambda: True

    def test_the_sign_in_code_is_not_in_the_file(self) -> None:
        asked = self.signing.ask("192.0.2.10", browser="b1")
        shown = self.signing.reveal(asked.id)
        assert shown is not None
        self.assertTrue(shown.code)
        self.assertNotIn(shown.code, self.written())
        self.assertIn("wants to sign in", self.written())

    def test_the_confirm_code_is_not_in_the_file(self) -> None:
        _token, owner = self.signing.open(kind=sign_in.OWNER, address="192.0.2.10")
        made = self.signing.confirm_ask(owner.id, "192.0.2.10")
        self.assertTrue(made.code)
        self.assertNotIn(made.code, self.written())
        self.assertIn("asked for a Confirm Code", self.written())

    def test_the_pairing_code_is_not_in_the_file(self) -> None:
        made = self.signing.pair_ask("192.0.2.10", name="Zed Display")
        self.assertTrue(made.code)
        self.assertNotIn(made.code, self.written())
        self.assertIn("wants to pair", self.written())

    def test_a_code_that_did_reach_a_log_is_hidden_by_the_cleanup(self) -> None:
        for line in ("Sign-in code for 192.0.2.10: 482913",
                     "Confirm code for 192.0.2.10: 482913",
                     "Pairing code for 192.0.2.10: 482913"):
            with self.subTest(line=line):
                cleaned = redact.Redactor(redact.Identity()).clean(line)
                self.assertNotIn("482913", cleaned)


class TheLogShapeTheSummaryReads(_LogFolder):
    def test_a_golden_log_groups_the_way_the_digest_does(self) -> None:
        path = Path(self.tmp.name) / "golden.log"
        path.write_text(GOLDEN, encoding="utf-8")
        with patch.object(health.log_setup, "log_file", lambda: path):
            lines = health._log()
        self.assertEqual(lines, [
            "### This Run's Log", "",
            "- **Errors:** 2", "- **Warnings:** 2",
            "- **2x** vpinfe.b: slow disk", "- **2x** vpinfe.c: it failed"])

    def test_what_the_logger_writes_is_what_the_section_counts(self) -> None:
        logger = logging.getLogger("vpinfe.test.support_shape")
        logger.warning("slow disk")
        logger.warning("slow disk")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("it failed")
        lines = health._log()
        self.assertIn("- **Errors:** 1", lines)
        self.assertIn("- **Warnings:** 2", lines)
        self.assertIn("- **2x** vpinfe.test.support_shape: slow disk", lines)


if __name__ == "__main__":
    unittest.main()

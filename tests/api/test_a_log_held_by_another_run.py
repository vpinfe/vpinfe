"""Protects: (d) a second VPinFE started while the first holds the log still starts.

Windows refuses to rename a file another process has open, so the start-of-run rollover
raises there. It must not take the launch down with it.
"""

from __future__ import annotations

import logging
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.runtime import log_setup


class ALogAnotherRunHolds(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._release)
        self.saved = log_setup._FILE_LOG_INITIALIZED
        self.addCleanup(setattr, log_setup, "_FILE_LOG_INITIALIZED", self.saved)

    def _release(self) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

    def test_a_rollover_the_system_refuses_does_not_stop_logging(self) -> None:
        folder = Path(self.tmp.name)
        (folder / log_setup.DEFAULT_LOG_FILE_NAME).write_text("a line\n", encoding="utf-8")
        log_setup._FILE_LOG_INITIALIZED = False
        refused = PermissionError(32, "being used by another process")

        with mock.patch.object(RotatingFileHandler, "rotate", side_effect=refused):
            path = log_setup.configure_logging(folder)

        logging.getLogger("held").warning("after the refusal")
        for handler in logging.getLogger().handlers:
            handler.flush()
        self.assertIn("after the refusal", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

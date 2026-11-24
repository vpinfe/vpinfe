"""A stamps file of a test's own, so one test's "asked a minute ago" is not the next one's."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class OwnStamps(unittest.TestCase):
    """Mix in first: `class Mine(OwnStamps):` gives each test an empty stamps file."""

    stamps_path: Path

    def setUp(self) -> None:
        super().setUp()
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.stamps_path = Path(held.name) / "stamps.json"
        patcher = mock.patch("common.online.cache_stamps.STAMPS_PATH", self.stamps_path)
        patcher.start()
        self.addCleanup(patcher.stop)

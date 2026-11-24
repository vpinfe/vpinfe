"""Matching a copy to the settings file an app keeps."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.games import config_backups as backups


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "backups"
        self.home.mkdir()
        self.ini = self.root / "VPinballX.ini"
        self.ini.write_text("[A]\nB = 1\n")
        self.files = {"application": str(self.ini)}
        self.copy = self.home / "VPinballX.ini.vpinfe-20260101T000000Z"
        self.copy.write_text("[A]\nB = 1\n")


class TargetTests(_Case):
    def test_the_file_is_the_one_the_copy_was_taken_under(self) -> None:
        self.assertEqual(backups.target_for(self.copy, self.files), self.ini)

    def test_a_file_the_app_does_not_keep_has_no_target(self) -> None:
        self.assertIsNone(backups.target_for(self.copy, {"other": "/x/Other.ini"}))


if __name__ == "__main__":
    unittest.main()

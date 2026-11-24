"""`vpinfe --support-package`: the zip, every file unzipped beside it, and what is printed."""

from __future__ import annotations

import contextlib
import io
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import cli
from common.support import package
from tests.supportpackage.test_package import SENTINELS, _Folder


class _Run(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write_logs()
        self.target = self.root / "out"
        self.target.mkdir()

    def run_flag(self, folder: Path) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = cli.support_package(str(folder))
        return status, out.getvalue()


class TheOutput(_Run):
    def test_it_writes_the_zip_and_every_file_unzipped_beside_it(self) -> None:
        status, printed = self.run_flag(self.target)
        self.assertEqual(status, 0)
        zips = list(self.target.glob("vpinfe-support-*.zip"))
        self.assertEqual(len(zips), 1)
        unzipped = self.target / zips[0].stem
        with zipfile.ZipFile(zips[0]) as archive:
            for name in archive.namelist():
                self.assertEqual((unzipped / name).read_bytes(), archive.read(name))
        self.assertIn(str(zips[0]), printed)
        self.assertIn(str(unzipped), printed)

    def test_it_prints_the_summary_and_the_readme(self) -> None:
        _status, printed = self.run_flag(self.target)
        self.assertIn("# VPinFE Support Summary", printed)
        self.assertIn("Automatic cleanup is thorough but not perfect.", printed)

    def test_nothing_private_is_printed_or_written(self) -> None:
        _status, printed = self.run_flag(self.target)
        written = "".join(one.read_text(encoding="utf-8") for one in self.target.rglob("*")
                          if one.is_file() and one.suffix != ".zip")
        for kind, value in SENTINELS.items():
            if kind == "table":
                continue  # kept unless hiding is asked for, and the flag does not ask
            with self.subTest(kind=kind):
                self.assertNotIn(value, printed)
                self.assertNotIn(value, written)


class WhenItCannot(_Run):
    def test_a_folder_that_cannot_be_written_says_why_and_exits_1(self) -> None:
        blocker = self.root / "a-file"
        blocker.write_text("x", encoding="utf-8")
        status, printed = self.run_flag(blocker / "inside")
        self.assertEqual(status, 1)
        self.assertIn("Could not write the support package to", printed)

    def test_a_package_over_the_cap_exits_1(self) -> None:
        with patch.object(package, "MAX_BUILT_BYTES", 100):
            status, printed = self.run_flag(self.target)
        self.assertEqual(status, 1)
        self.assertIn("too large", printed)
        self.assertEqual(list(self.target.iterdir()), [])


class TheFlag(_Run):
    def parse(self, *argv: str) -> list[str]:
        asked: list[str] = []

        def record(folder: str) -> int:
            asked.append(folder)
            return 0

        with patch("sys.argv", ["vpinfe", *argv]), \
                patch.object(cli, "support_package", record), \
                self.assertRaises(SystemExit) as raised:
            cli.parse_args()
        self.assertEqual(raised.exception.code, 0)
        return asked

    def test_a_folder_after_the_flag_is_where_it_writes(self) -> None:
        self.assertEqual(self.parse("--support-package", str(self.target)), [str(self.target)])

    def test_the_flag_alone_writes_here(self) -> None:
        self.assertEqual(self.parse("--support-package"), ["."])


if __name__ == "__main__":
    unittest.main()

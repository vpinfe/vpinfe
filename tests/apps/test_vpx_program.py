"""Protects: (a) a launcher's settings are read and written in the folder of its own VPX.

The preferences folder is named for the version of the program that reads it, so the
version has to come from the program a launcher points at.
"""

from __future__ import annotations

import os
import plistlib
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from apps.vpx import program


class FolderVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for kept in (program._ANSWERS, program._NO_ANSWER):
            kept.clear()
            self.addCleanup(kept.clear)

    def _bundle(self, info: Any, name: str = "VPinballX") -> Path:
        contents = self.root / f"{name}.app" / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        inside = contents / "MacOS" / "VPinballX"
        inside.write_bytes(b"x")
        return inside

    def test_macos_names_the_minor_version_from_the_bundle(self) -> None:
        inside = self._bundle({"CFBundleShortVersionString": "10.9.0"})

        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(program.folder_version(str(inside)), "10.9")
            self.assertEqual(program.folder_version(str(inside.parents[2])), "10.9")

    def test_a_patch_or_build_suffix_is_not_part_of_the_folder_name(self) -> None:
        inside = self._bundle({"CFBundleShortVersionString": "10.8.1 (build 2030)"})

        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(program.folder_version(str(inside)), "10.8")

    def test_a_bundle_with_no_readable_version_names_nothing(self) -> None:
        for number, info in enumerate(({}, {"CFBundleShortVersionString": "nightly"},
                                       ["not", "a", "dict"])):
            with self.subTest(info=info):
                inside = self._bundle(info, f"Bundle{number}")
                with mock.patch("sys.platform", "darwin"):
                    self.assertEqual(program.folder_version(str(inside)), "")

    @unittest.skipUnless(sys.platform == "win32", "reads a version resource Windows holds")
    def test_windows_reads_the_files_version_resource(self) -> None:
        said = program.folder_version(sys.executable)

        self.assertEqual(said, f"{sys.version_info.major}.{sys.version_info.minor}")

    @unittest.skipUnless(sys.platform == "win32", "reads a version resource Windows holds")
    def test_a_windows_file_with_no_version_resource_names_nothing(self) -> None:
        plain = self.root / "VPinballX64.exe"
        plain.write_bytes(b"MZ not a program")

        self.assertEqual(program.folder_version(str(plain)), "")

    def test_a_program_reached_through_a_link_is_read_where_it_lives(self) -> None:
        inside = self._bundle({"CFBundleShortVersionString": "10.9.0"})
        link = self.root / "vpx"
        try:
            link.symlink_to(inside)
        except OSError:
            self.skipTest("this system will not make a link")

        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(program.folder_version(str(link)), "10.9")

    def _linux_program(self, body: str, name: str = "VPinballX_BGFX") -> Path:
        """A program that is a shell script, so the real process handling is what runs."""
        exe = self.root / name
        exe.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        exe.chmod(0o755)
        return exe

    def _asking(self):
        if os.name != "posix":
            self.skipTest("the Linux reader runs a shell script here")
        return mock.patch("sys.platform", "linux")

    def test_linux_asks_the_program_for_its_version(self) -> None:
        exe = self._linux_program(
            'echo "2026-11-19 15:21:27.919 INFO  Starting VPX - v10.9.0 Beta (Rev. 6400)"\n'
            'echo "Visual Pinball v10.9.0 Beta (Rev. 6400 (abc), linux BGFX 64bits)"\n')

        with self._asking():
            self.assertEqual(program.folder_version(str(exe)), "10.9")

    def test_linux_asks_once_and_asks_again_when_the_program_changes(self) -> None:
        exe = self._linux_program('echo "Visual Pinball v10.8.1 Beta"\n'
                                  f'echo x >> "{self.root / "asked"}"\n')

        with self._asking():
            self.assertEqual(program.folder_version(str(exe)), "10.8")
            self.assertEqual(program.folder_version(str(exe)), "10.8")
            self.assertEqual((self.root / "asked").read_text(encoding="utf-8").count("x"), 1)
            exe.write_text(exe.read_text(encoding="utf-8").replace("10.8.1", "10.9.0"),
                           encoding="utf-8", newline="\n")
            os.utime(exe, ns=(exe.stat().st_atime_ns, exe.stat().st_mtime_ns + 1_000_000_000))
            self.assertEqual(program.folder_version(str(exe)), "10.9")

    def test_linux_gives_the_program_a_throwaway_preferences_folder(self) -> None:
        # Without it the program writes its own settings file and log, and a new minor
        # version copies the older folder into its own; asking must change none of that.
        exe = self._linux_program(
            'shift\n'
            f'echo "$1" > "{self.root / "scratch"}"\n'
            'echo "ini" > "$1/VPinballX.ini"\n'
            'echo "Visual Pinball v10.8.1 Beta"\n')

        with self._asking():
            self.assertEqual(program.folder_version(str(exe)), "10.8")

        given = Path((self.root / "scratch").read_text(encoding="utf-8").strip())
        self.assertFalse(given.exists())

    def test_linux_keeps_the_programs_own_data_folder_out_of_the_persons_home(self) -> None:
        # A build makes its own `major.minor` folder under the data home and copies the newest
        # earlier one into it before it reads the preferences override.
        exe = self._linux_program(
            f'echo "$XDG_DATA_HOME" > "{self.root / "data_home"}"\n'
            'echo "Visual Pinball v10.9.0 Beta"\n')

        real = {"XDG_DATA_HOME": str(self.root / "real")}
        with self._asking(), mock.patch.dict(os.environ, real):
            self.assertEqual(program.folder_version(str(exe)), "10.9")

        home = Path((self.root / "data_home").read_text(encoding="utf-8").strip())
        self.assertNotEqual(home, self.root / "real")
        self.assertFalse(home.exists())

    def test_linux_names_nothing_for_a_program_that_says_nothing_usable(self) -> None:
        for number, body in enumerate(
                ("exit 0\n", 'echo "Command Line Error"\nexit 1\n', "exit 3\n")):
            with self.subTest(body=body), self._asking():
                exe = self._linux_program(body, f"VPinballX_{number}")
                self.assertEqual(program.folder_version(str(exe)), "")

    def test_linux_stops_waiting_for_a_program_that_hangs_and_stops_the_program_it_started(
            self) -> None:
        mark = self.root / "grandchild"
        exe = self._linux_program(
            f'(sleep 1; echo alive > "{mark}") &\nsleep 30\n')

        with self._asking(), mock.patch.object(program, "_ASK_SECONDS", 0.2):
            self.assertEqual(program.folder_version(str(exe)), "")
            time.sleep(1.5)

        self.assertFalse(mark.exists())

    def test_linux_does_not_ask_again_at_once_after_no_answer(self) -> None:
        exe = self._linux_program(f'echo x >> "{self.root / "asked"}"\nexit 1\n')

        with self._asking():
            for _ in range(3):
                self.assertEqual(program.folder_version(str(exe)), "")
            self.assertEqual((self.root / "asked").read_text(encoding="utf-8").count("x"), 1)
            program._NO_ANSWER.clear()
            self.assertEqual(program.folder_version(str(exe)), "")
            self.assertEqual((self.root / "asked").read_text(encoding="utf-8").count("x"), 2)

    def test_linux_names_nothing_for_a_program_it_cannot_start(self) -> None:
        exe = self.root / "VPinballX_BGFX"
        exe.write_bytes(b"not a program")
        exe.chmod(0o755)

        with self._asking():
            self.assertEqual(program.folder_version(str(exe)), "")

    def test_linux_does_not_run_a_file_it_may_not_execute(self) -> None:
        exe = self._linux_program(f'echo x >> "{self.root / "asked"}"\n')
        exe.chmod(0o644)

        with self._asking():
            self.assertEqual(program.folder_version(str(exe)), "")

        self.assertFalse((self.root / "asked").exists())

    def test_no_program_or_one_that_is_not_there_names_nothing(self) -> None:
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(program.folder_version(""), "")
            self.assertEqual(program.folder_version(str(self.root / "missing")), "")


if __name__ == "__main__":
    unittest.main()

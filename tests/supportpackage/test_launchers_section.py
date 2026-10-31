"""Launchers And Programs: what each launcher runs, without running it."""

from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from common.games.launchers import Launcher
from common.support import collect
from common.support.collect import launchers as section
from common.support.sections import OK

INI = """[Version]
VPinball = 10.8.1-2030

[Plugin.PinMAME]
Enable = 1

[Plugin.DOF]
Enable = 0
"""


class Collecting(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.program = self.folder / "VPinballX_BGFX"
        self.program.write_bytes(b"x" * 2048)
        self.program.chmod(0o755)
        self.ini = self.folder / "VPinballX.ini"
        self.ini.write_text(INI, encoding="utf-8")

    def launcher(self, **settings: str) -> Launcher:
        return Launcher("one", app="vpx", display_name="Main",
                        settings={"bin_path": str(self.program), "ini_path": str(self.ini),
                                  **settings})

    def lines(self, held: list[Launcher] | None = None, mappings: dict[str, str] | None = None,
              system: str = "Linux", log: Path | None = None,
              records: list[dict[str, Any]] | None = None,
              rows: list[dict[str, Any]] | None = None, dof: Path | None = None) -> list[str]:
        held = [self.launcher()] if held is None else held
        store = SimpleNamespace(launchers=lambda: held, mappings=lambda: mappings or {})
        default = held[0] if held else None
        with patch.object(section.launchers, "get_launcher_store", return_value=store), \
                patch.object(section.launchers, "default_launcher", return_value=default), \
                patch.object(section, "_system", return_value=system), \
                patch.object(section.tools, "report", return_value=rows or []), \
                patch.object(section.dof_service, "find_dof_file", return_value=dof), \
                patch.object(section.libdmdutil_service, "find_libdmdutil_file",
                             return_value=None), \
                patch.object(section.pinmame_catalog, "find_library", return_value=None), \
                patch.object(section.log_setup, "log_file", return_value=log), \
                patch.object(section.log_setup, "read_records", return_value=records or []):
            found = section.collect()
        self.assertEqual(found.status, OK)
        return list(found.lines)

    def test_it_sits_between_the_browser_and_settings(self) -> None:
        order = [one.id for one in collect.SECTIONS]
        self.assertLess(order.index("browser"), order.index("launchers"))
        self.assertLess(order.index("launchers"), order.index("settings"))

    def test_a_launcher_reads_as_its_program_and_its_settings_file(self) -> None:
        text = "\n".join(self.lines(mappings={"a": "one", "b": "one", "c": "other"}))
        self.assertIn("### Main", text)
        self.assertIn("- **Default:** Yes", text)
        self.assertIn("- **Enabled:** Yes", text)
        self.assertIn("- **Tables set to this launcher:** 2", text)
        self.assertIn(f"- **Program:** {self.program} (Executable, 0.0 MB, modified ", text)
        self.assertIn(f"- **Settings file:** {self.ini} (Readable)", text)
        self.assertIn("- **Settings file records:** 10.8.1-2030", text)
        self.assertIn("- **Plugins in the settings file:** DOF, PinMAME", text)

    def test_a_second_launcher_is_not_the_default(self) -> None:
        other = Launcher("two", app="vpx", display_name="Beta", enabled=False,
                         settings={"bin_path": str(self.program)})
        text = "\n".join(self.lines([self.launcher(), other]))
        beta = text.split("### Beta")[1]
        self.assertIn("- **Default:** No", beta)
        self.assertIn("- **Enabled:** No", beta)

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_a_program_that_is_not_there_or_not_runnable_says_so(self) -> None:
        self.program.chmod(0o644)
        self.assertIn("Not executable", "\n".join(self.lines()))
        self.program.unlink()
        self.assertIn("(Not found)", "\n".join(self.lines()))

    def test_a_settings_file_that_is_not_there_says_so(self) -> None:
        self.ini.unlink()
        self.assertIn(f"- **Settings file:** {self.ini} (Not found)", "\n".join(self.lines()))

    def test_a_settings_file_with_no_version_says_so(self) -> None:
        self.ini.write_text("[Player]\nPlayfieldWidth = 1\n", encoding="utf-8")
        text = "\n".join(self.lines())
        self.assertIn("- **Settings file records:** No version", text)
        self.assertIn("- **Plugins in the settings file:** None", text)

    def test_macos_reads_the_bundle_s_plist(self) -> None:
        app = self.folder / "VPinballX.app" / "Contents"
        (app / "MacOS").mkdir(parents=True)
        (app / "Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleShortVersionString": "10.8.1", "CFBundleVersion": "2030"}))
        inside = app / "MacOS" / "VPinballX"
        inside.write_bytes(b"x")
        found = self.lines([self.launcher(bin_path=str(inside))], system="Darwin")
        self.assertIn("- **Program version:** 10.8.1 (build 2030)", found)

    def test_macos_with_no_plist_gives_no_version(self) -> None:
        text = "\n".join(self.lines(system="Darwin"))
        self.assertNotIn("Program version", text)

    def test_windows_reads_the_file_version_resource(self) -> None:
        with patch.object(section.about, "_windows_file_version",
                          return_value="10.8.1.2030") as read:
            found = self.lines(system="Windows")
        self.assertIn("- **Program version:** 10.8.1.2030 (file version)", found)
        read.assert_called_once_with(str(self.program))

    def test_windows_runs_nothing_for_a_program_that_is_not_there(self) -> None:
        self.program.unlink()
        with patch.object(section.about, "_windows_file_version") as read:
            self.lines(system="Windows")
        read.assert_not_called()

    def test_on_windows_the_extension_says_whether_it_is_executable(self) -> None:
        with patch.object(section.about, "_windows_file_version", return_value=""):
            self.assertIn("(Not executable,", "\n".join(self.lines(system="Windows")))
            exe = self.folder / "VPinballX64.exe"
            exe.write_bytes(b"x")
            self.assertIn("(Executable,", "\n".join(
                self.lines([self.launcher(bin_path=str(exe))], system="Windows")))

    def test_a_plist_that_is_not_a_dictionary_gives_no_version(self) -> None:
        app = self.folder / "VPinballX.app" / "Contents"
        (app / "MacOS").mkdir(parents=True)
        (app / "Info.plist").write_bytes(plistlib.dumps(["not", "a", "dictionary"]))
        inside = app / "MacOS" / "VPinballX"
        inside.write_bytes(b"x")
        text = "\n".join(self.lines([self.launcher(bin_path=str(inside))], system="Darwin"))
        self.assertNotIn("Program version", text)

    def test_linux_reads_no_version_and_nothing_is_run(self) -> None:
        with patch.object(section.about, "_windows_file_version") as read, \
                patch("subprocess.run", MagicMock(side_effect=AssertionError("ran"))), \
                patch("subprocess.Popen", MagicMock(side_effect=AssertionError("ran"))):
            text = "\n".join(self.lines(system="Linux"))
        read.assert_not_called()
        self.assertNotIn("Program version", text)

    def test_no_launchers_says_so_and_still_lists_the_rest(self) -> None:
        found = self.lines([])
        self.assertEqual(found[0], "No launchers.")
        self.assertIn("### Bundled Libraries", found)

    def test_the_programs_vpinfe_runs_say_which_are_there(self) -> None:
        rows = [{"name": "ffmpeg", "state": "found", "path": "/usr/bin/ffmpeg",
                 "version": "7.1"},
                {"name": "rar", "state": "missing", "path": "", "version": ""},
                {"name": "nvtop", "state": "not_here", "path": "", "version": ""}]
        text = "\n".join(self.lines(rows=rows))
        self.assertIn("- **ffmpeg:** Found (/usr/bin/ffmpeg, 7.1)", text)
        self.assertIn("- **rar:** Not installed", text)
        self.assertNotIn("nvtop", text)

    def test_the_libraries_are_present_or_not_present(self) -> None:
        library = self.folder / "dof_runner.py"
        library.write_text("", encoding="utf-8")
        text = "\n".join(self.lines(dof=library))
        self.assertIn("- **DOF:** Present, modified ", text)
        self.assertIn("- **libdmdutil:** Not present", text)
        self.assertIn("- **PinMAME:** Not present", text)

    def test_the_last_launch_is_the_one_the_log_holds(self) -> None:
        records = [{"when": "2026-10-25 08:00:00", "message": "Launching: ['/a', '-play']"},
                   {"when": "2026-10-25 09:00:00", "message": "Launching: ['/b', '-play']"},
                   {"when": "2026-10-25 09:05:00", "message": "Launching bootstrap via cmd"}]
        text = "\n".join(self.lines(log=self.folder / "vpinfe.log", records=records))
        self.assertIn("- **When:** 2026-10-25 09:00:00", text)
        self.assertIn("- **Command:** ['/b', '-play']", text)
        self.assertNotIn("['/a'", text)

    def test_a_log_with_no_launch_says_so(self) -> None:
        text = "\n".join(self.lines(log=self.folder / "vpinfe.log"))
        self.assertIn("No game launched in this run's log.", text)

    def test_no_log_file_says_so(self) -> None:
        self.assertIn("VPinFE is not writing a log file on this run.",
                      "\n".join(self.lines(log=None)))


if __name__ == "__main__":
    unittest.main()

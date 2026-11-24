"""Where Setup looks for Visual Pinball, on fixtures: no machine here has the Windows or
Linux places, so each is built in a temporary folder and handed to the search."""

from __future__ import annotations

import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from manager.settings import vpx_find


def _touch(path: Path, *, when: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if when is not None:
        os.utime(path, (when, when))
    return path


class ProgramInTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def _offers(self, *names: str, named: str = "") -> str | None:
        for name in names:
            _touch(self.folder / name)
        one = vpx_find.program_in(self.folder, named)
        return one.name if one else None

    def test_bgfx_is_offered_before_everything_else(self) -> None:
        self.assertEqual(self._offers("VPinballX.exe", "VPinballX64.exe",
                                      "VPinballX_GL64.exe", "VPinballX_BGFX.exe"),
                         "VPinballX_BGFX.exe")

    def test_without_one_the_64_bit_program_beats_the_plain_one(self) -> None:
        self.assertEqual(self._offers("VPinballX.exe", "VPinballX64.exe", "VPinballX_GL64.exe"),
                         "VPinballX64.exe")

    def test_without_that_the_plain_one_beats_a_gl_build(self) -> None:
        self.assertEqual(self._offers("VPinballX_GL64.exe", "VPinballX.exe"), "VPinballX.exe")

    def test_a_gl_build_is_offered_when_it_is_all_there_is(self) -> None:
        self.assertEqual(self._offers("VPinballX_GL64.exe"), "VPinballX_GL64.exe")

    def test_a_numbered_legacy_build_is_never_offered(self) -> None:
        self.assertIsNone(self._offers("VPinballX107_64bit.exe", "VPinball995.exe",
                                       "VPinballX1010.exe"))

    def test_what_a_frontends_settings_name_is_offered_first(self) -> None:
        self.assertEqual(self._offers("VPinballX_BGFX.exe", "VPinballX.exe", named="VPinballX.exe"),
                         "VPinballX.exe")

    def test_a_name_that_is_not_in_the_folder_is_not_offered(self) -> None:
        self.assertEqual(self._offers("VPinballX_BGFX.exe", named="VPinballX64.exe"),
                         "VPinballX_BGFX.exe")

    def test_a_folder_that_is_not_there_offers_nothing(self) -> None:
        self.assertIsNone(vpx_find.program_in(self.folder / "missing"))

    def test_linux_and_mac_names_without_an_extension_match(self) -> None:
        self.assertEqual(self._offers("VPinballX_GL", "VPinballX_BGFX"), "VPinballX_BGFX")


class FindsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _paths(self, found: list[vpx_find.Find]) -> list[tuple[str, str]]:
        return [(Path(one.path).relative_to(self.root).as_posix(), one.source) for one in found]

    def test_the_usual_places_are_each_drives_vpinball_folder(self) -> None:
        _touch(self.root / "vPinball" / "VisualPinball" / "VPinballX64.exe")

        found = vpx_find.finds("win32", drives=[str(self.root)], path="")

        self.assertEqual(self._paths(found),
                         [("vPinball/VisualPinball/VPinballX64.exe", vpx_find.USUAL)])

    def test_the_all_in_one_installers_folder_is_looked_in_on_the_first_drive(self) -> None:
        _touch(self.root / "Visual Pinball" / "VPinballX64.exe")

        found = vpx_find.finds("win32", drives=[str(self.root)], path="")

        self.assertEqual(self._paths(found),
                         [("Visual Pinball/VPinballX64.exe", vpx_find.USUAL)])

    def test_nothing_anywhere_finds_nothing(self) -> None:
        self.assertEqual(vpx_find.finds("win32", drives=[str(self.root)], path=""), [])

    def test_the_path_is_searched_a_folder_at_a_time(self) -> None:
        _touch(self.root / "bin" / "VPinballX_GL")
        _touch(self.root / "other" / "VPinballX_BGFX")

        found = vpx_find.finds("linux", path=os.pathsep.join(
            [str(self.root / "bin"), str(self.root / "other"), str(self.root / "bin")]))

        self.assertEqual(sorted(self._paths(found)),
                         [("bin/VPinballX_GL", vpx_find.PATH),
                          ("other/VPinballX_BGFX", vpx_find.PATH)])

    def test_the_newest_is_first(self) -> None:
        _touch(self.root / "old" / "VPinballX_GL", when=1_600_000_000)
        _touch(self.root / "new" / "VPinballX_BGFX", when=1_700_000_000)

        found = vpx_find.finds("linux", path=os.pathsep.join(
            [str(self.root / "old"), str(self.root / "new")]))

        self.assertEqual([Path(one.path).parent.name for one in found], ["new", "old"])

    def test_a_program_found_twice_is_one_find_under_the_best_source(self) -> None:
        exe = _touch(self.root / "PinballX" / "VPinballX64.exe")
        ini = self.root / "PinballX" / "Config" / "PinballX.ini"
        ini.parent.mkdir(parents=True)
        ini.write_text(f"[System_1]\nName=Visual Pinball X\nExecutable=VPinballX64.exe\n"
                       f"WorkingPath={exe.parent}\n", encoding="utf-16")

        found = vpx_find.finds("win32", drives=[str(self.root)], path=str(exe.parent))

        self.assertEqual(self._paths(found), [("PinballX/VPinballX64.exe", vpx_find.PINBALLX)])

    def test_pinballx_names_a_program_by_its_working_path_and_executable(self) -> None:
        _touch(self.root / "tools" / "VPinballX_GL64.exe")
        ini = self.root / "PinballX" / "Config" / "PinballX.ini"
        ini.parent.mkdir(parents=True)
        ini.write_text(
            "[System_1]\nName=Future Pinball\nExecutable=FuturePinball.exe\nWorkingPath=x\n"
            f"[System_2]\nName=Visual Pinball X\nExecutable=VPinballX_GL64.exe\n"
            f"WorkingPath={self.root / 'tools'}\n", encoding="utf-16")

        found = vpx_find.finds("win32", drives=[str(self.root)], path="")

        self.assertEqual(self._paths(found), [("tools/VPinballX_GL64.exe", vpx_find.PINBALLX)])

    def test_a_utf8_settings_file_without_a_byte_order_mark_is_read(self) -> None:
        _touch(self.root / "tools" / "VPinballX_GL64.exe")
        ini = self.root / "PinballX" / "Config" / "PinballX.ini"
        ini.parent.mkdir(parents=True)
        ini.write_text(
            "[System_1]\nName=Visual Pinball X\nExecutable=VPinballX_GL64.exe\n"
            f"WorkingPath={self.root / 'tools'}\n", encoding="utf-8")

        found = vpx_find.finds("win32", drives=[str(self.root)], path="")

        self.assertEqual(self._paths(found), [("tools/VPinballX_GL64.exe", vpx_find.PINBALLX)])

    def test_a_settings_file_that_cannot_be_read_names_nothing(self) -> None:
        ini = self.root / "PinballX" / "Config" / "PinballX.ini"
        ini.parent.mkdir(parents=True)
        ini.write_bytes(b"\xff\xfe\x00\x01 not an ini")

        self.assertEqual(vpx_find.finds("win32", drives=[str(self.root)], path=""), [])

    def test_popper_names_the_folder_its_emulator_launches_from(self) -> None:
        _touch(self.root / "emu" / "VPinballX64.exe")
        _touch(self.root / "emu" / "VPinballX_BGFX.exe")
        database = self.root / "vPinball" / "PinUPSystem" / "PUPDatabase.db"
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as db:
            db.execute("create table Emulators (EMUID integer, EmuName text, "
                       "EmuLaunchDir text, GamesExt text)")
            db.execute("insert into Emulators values (1, 'Future Pinball', 'nowhere', 'fpt')")
            db.execute("insert into Emulators values (2, 'Visual Pinball X', ?, 'vpx')",
                       (str(self.root / "emu"),))
            db.commit()

        found = vpx_find.finds("win32", drives=[str(self.root)], path="")

        self.assertEqual(self._paths(found), [("emu/VPinballX_BGFX.exe", vpx_find.POPPER)])

    def test_popper_without_the_column_falls_back_to_the_folder_above_its_tables(self) -> None:
        _touch(self.root / "vpx" / "VPinballX64.exe")
        (self.root / "vpx" / "Tables").mkdir()
        database = self.root / "vPinball" / "PinUPSystem" / "PUPDatabase.db"
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as db:
            db.execute("create table Emulators (EMUID integer, EmuName text, "
                       "DirGames text, GamesExt text)")
            db.execute("insert into Emulators values (1, 'Visual Pinball X', ?, 'vpx')",
                       (str(self.root / "vpx" / "Tables"),))
            db.commit()

        found = vpx_find.finds("win32", drives=[str(self.root)], path="")

        self.assertEqual(self._paths(found), [("vpx/VPinballX64.exe", vpx_find.POPPER)])

    def test_a_database_that_is_not_one_names_nothing(self) -> None:
        database = self.root / "vPinball" / "PinUPSystem" / "PUPDatabase.db"
        database.parent.mkdir(parents=True)
        database.write_text("not sqlite")

        self.assertEqual(vpx_find.finds("win32", drives=[str(self.root)], path=""), [])

    def test_macos_bundles_are_each_a_find(self) -> None:
        for name in ("VPinballX_BGFX.app", "VPinballX_GL.app", "Safari.app"):
            (self.root / name).mkdir()

        self.assertEqual([one.name for one in vpx_find._bundles(self.root)],
                         ["VPinballX_BGFX.app", "VPinballX_GL.app"])

    def test_each_os_says_where_it_looked(self) -> None:
        self.assertEqual(len({vpx_find.looked(one) for one in ("win32", "darwin", "linux")}), 3)


if __name__ == "__main__":
    unittest.main()

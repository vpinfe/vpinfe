"""Where Visual Pinball is on this machine, for Setup's first Visual Pinball page.

A frontend's own settings come first, since they name the program actually in use; then the
usual places each OS has, then the PATH. One program is offered per folder, chosen the way
`program_in` says, and a program found twice is one find that keeps the earliest source.
Nothing here writes, and a place that cannot be read is a place that holds nothing.
"""

from __future__ import annotations

import configparser
import os
import re
import sqlite3
import string
import sys
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

PINBALLX = "pinballx"
POPPER = "popper"
USUAL = "usual"
APPLICATIONS = "applications"
PATH = "path"

# Which find a program found twice is shown as.
_PRECEDENCE = (PINBALLX, POPPER, USUAL, APPLICATIONS, PATH)

# What a folder holding several programs offers, in order. Anything else in it, the
# numbered legacy builds included, is never offered; a frontend can still name one.
_BGFX = re.compile(r"VPinballX_BGFX(64)?(\.exe|\.app)?", re.IGNORECASE)
_X64 = re.compile(r"VPinballX64\.exe", re.IGNORECASE)
_X = re.compile(r"VPinballX\.exe", re.IGNORECASE)
_GL = re.compile(r"VPinballX_GL(64)?(\.exe|\.app)?", re.IGNORECASE)
_ORDER = (_BGFX, _X64, _X, _GL)
_NAMED = re.compile(r"VPinballX", re.IGNORECASE)

_PINBALLX_FOLDERS = ("PinballX", "PinballY")
_PINBALLX_INI = ("Config", "PinballX.ini")
_POPPER_FOLDER = ("vPinball", "PinUPSystem")
_POPPER_DATABASE = "PUPDatabase.db"
_USUAL_WINDOWS = ("vPinball", "VisualPinball")
_USUAL_LINUX = "/opt/vpinball"
_VPX_SYSTEM = ("vpx", "visual pinball x")


@dataclass(frozen=True)
class Find:
    """One program. `path` is what a launcher's program setting takes - the `.app` bundle
    on macOS. `source` is one of this module's names; `updated` is the program's
    modification time, 0 where it cannot be read."""
    path: str
    source: str
    updated: float = 0.0


def program_in(folder: Path, named: str = "") -> Path | None:
    """The program a folder offers, or None where it holds none.

    A name a frontend's settings give wins; otherwise BGFX, then `VPinballX64.exe`, then
    `VPinballX.exe`, then a GL build, so the newest renderer is the one assumed.
    """
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    if named and named in names:
        return folder / named
    for shape in _ORDER:
        for name in names:
            if shape.fullmatch(name):
                return folder / name
    return None


def _modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _find(path: Path, source: str) -> Find:
    return Find(str(path), source, _modified(path))


def _fixed_drives() -> list[str]:
    """The roots of the fixed drives on Windows, or just `C:\\` where they cannot be asked."""
    if sys.platform != "win32":
        return ["C:\\"]
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        found = [f"{letter}:\\" for index, letter in enumerate(string.ascii_uppercase)
                 if mask >> index & 1
                 and ctypes.windll.kernel32.GetDriveTypeW(  # type: ignore[attr-defined]
                     f"{letter}:\\") == 3]
    except (AttributeError, OSError):
        return ["C:\\"]
    return found or ["C:\\"]


def _pinballx_named(root: Path) -> Iterable[Path]:
    """The Visual Pinball programs PinballX's or PinballY's settings name."""
    path = root.joinpath(*_PINBALLX_INI)
    try:
        raw = path.read_bytes()
    except OSError:
        return
    # utf-16 decodes any even run of bytes, so only a byte-order mark may choose it.
    text = ""
    for encoding in (("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ()) + (
            "utf-8-sig", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    if not text:
        return
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return
    for section in parser.sections():
        if not section.lower().startswith("system_"):
            continue
        values = {key.lower(): value.strip() for key, value in parser[section].items()}
        executable = values.get("executable", "")
        if not _NAMED.match(Path(executable.replace("\\", "/")).name):
            continue
        working = values.get("workingpath", "")
        program = Path(executable)
        yield program if program.is_absolute() or not working else Path(working) / executable


def _popper_folders(root: Path) -> Iterable[Path]:
    """The folders PinUP Popper's Visual Pinball X emulators launch from: the emulator's
    own launch folder where the database has the column, else the folder above its Tables
    folder."""
    database = root / _POPPER_DATABASE
    if not database.is_file():
        return
    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = list(db.execute("select * from Emulators"))
    except sqlite3.Error:
        return
    for row in rows:
        values = {key.lower(): str(row[key] or "").strip() for key in row.keys()}  # noqa: SIM118
        said = " ".join((values.get("emuname", ""), values.get("gamesext", ""))).lower()
        if not any(word in said for word in _VPX_SYSTEM) and ".vpx" not in said:
            continue
        if launch := values.get("emulaunchdir", ""):
            yield Path(launch)
        if games := values.get("dirgames", ""):
            yield Path(games).parent


def _on_the_path(path: str) -> list[Path]:
    seen: set[str] = set()
    folders = []
    for one in path.split(os.pathsep):
        if one and (key := os.path.normcase(one)) not in seen:
            seen.add(key)
            folders.append(Path(one))
    return folders


def _bundles(folder: Path) -> list[Path]:
    """Each Visual Pinball bundle in a folder that holds applications of every kind."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [folder / name for name in names
            if name.lower().endswith(".app") and (_BGFX.fullmatch(name) or _GL.fullmatch(name))]


def finds(system: str = sys.platform, *, home: Path | None = None, path: str | None = None,
          drives: Sequence[str] | None = None) -> list[Find]:
    """Every Visual Pinball this machine has, newest first.

    `system` is `sys.platform`'s word for the OS, and `home`, `path` and `drives` stand in
    for what this machine says where a caller needs another.
    """
    home = Path.home() if home is None else home
    path = os.environ.get("PATH", "") if path is None else path
    windows = system == "win32"
    mac = system == "darwin"
    roots = [Path(drive) for drive in (drives if drives is not None else
                                       (_fixed_drives() if windows else ()))]
    found: list[Find] = []

    for drive in roots:
        for name in _PINBALLX_FOLDERS:
            found += [_find(one, PINBALLX) for one in _pinballx_named(drive / name)
                      if one.is_file()]
        for folder in _popper_folders(drive.joinpath(*_POPPER_FOLDER)):
            if (one := program_in(folder)) is not None:
                found.append(_find(one, POPPER))
    if windows:
        for place in [roots[0] / "Visual Pinball" if roots else Path("C:\\Visual Pinball"),
                      *(drive.joinpath(*_USUAL_WINDOWS) for drive in roots)]:
            if (one := program_in(place)) is not None:
                found.append(_find(one, USUAL))
    elif mac:
        for folder in (Path("/Applications"), home / "Applications"):
            found += [_find(one, APPLICATIONS) for one in _bundles(folder)]
    else:
        if (one := program_in(Path(_USUAL_LINUX))) is not None:
            found.append(_find(one, USUAL))
    for folder in _on_the_path(path):
        if (one := program_in(folder)) is not None:
            found.append(_find(one, PATH))
    return _once_each(found)


def _once_each(found: Iterable[Find]) -> list[Find]:
    """A program found by several routes is one find, shown by the route that names it
    best; newest first, the order within a tie kept."""
    by_program: dict[str, Find] = {}
    for one in found:
        key = os.path.normcase(os.path.realpath(one.path))
        held = by_program.get(key)
        if held is None or _PRECEDENCE.index(one.source) < _PRECEDENCE.index(held.source):
            by_program[key] = one
    return sorted(by_program.values(), key=lambda one: -one.updated)


# The catalog word naming where each OS looked, for the line under "Not found".
LOOKED = {"win32": "manager.setup.vpx.looked.windows",
          "darwin": "manager.setup.vpx.looked.mac"}
LOOKED_ELSEWHERE = "manager.setup.vpx.looked.linux"


def looked(system: str = sys.platform) -> str:
    return LOOKED.get(system, LOOKED_ELSEWHERE)

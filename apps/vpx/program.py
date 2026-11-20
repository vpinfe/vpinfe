"""Which Visual Pinball a launcher runs, as far as its preferences folder is concerned."""

from __future__ import annotations

import contextlib
import os
import plistlib
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_VERSION = re.compile(r"(\d+)\.(\d+)")
# What `-v` prints, as its own last line and again in the first log line it writes.
_ANNOUNCED = re.compile(r"(?:Visual Pinball|Starting VPX -) v(\d+\.\d+(?:\.\d+)?)")
_ANSWERS: dict[tuple[str, int], str] = {}
_NO_ANSWER: dict[tuple[str, int], float] = {}
_NO_ANSWER_SECONDS = 60.0
_ASK_SECONDS = 5.0


def folder_version(bin_path: str) -> str:
    """The `major.minor` of the Visual Pinball at `bin_path`, which is the name of the
    preferences folder that build reads. Empty where it cannot be read."""
    named = str(bin_path or "").strip()
    if not named:
        return ""
    try:
        path = Path(named).expanduser().resolve()
        stamp = path.stat().st_mtime_ns
    except (OSError, RuntimeError):
        return ""
    if sys.platform == "darwin":
        said = _bundle_version(path)
    elif sys.platform == "win32":
        said = _file_version(str(path))
    else:
        said = _announced_version(str(path), stamp)
    found = _VERSION.match(said)
    return f"{found[1]}.{found[2]}" if found else ""


def _bundle_version(path: Path) -> str:
    bundle = next((one for one in (path, *path.parents) if one.suffix.lower() == ".app"), None)
    if bundle is None:
        return ""
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ""
    if not isinstance(info, dict):
        return ""
    return str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "")


def _file_version(path: str) -> str:
    """The file's version resource, read through Windows' own version library. No process is
    started, so a slow start cannot hold up the several calls one listing or launch makes."""
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version")  # type: ignore[attr-defined]
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.LPVOID]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL

    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    block = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, block):
        return ""
    fixed = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(block, "\\", ctypes.byref(fixed), ctypes.byref(length)):
        return ""
    # VS_FIXEDFILEINFO opens with signature, structure version, file version (two words),
    # then the product version (two words).
    _, _, _, _, high, low = (wintypes.DWORD * 6).from_address(fixed.value or 0)
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def _announced_version(path: str, stamp: int) -> str:
    """What the program says for `-v`, run against an empty preferences folder that is removed
    afterwards, so the person's own settings and log are never written. An answer is kept
    against the file's timestamp; a failed ask is not repeated for a minute."""
    key = (path, stamp)
    if key in _ANSWERS:
        return _ANSWERS[key]
    if _NO_ANSWER.get(key, 0.0) > time.monotonic() or not os.access(path, os.X_OK):
        return ""
    found = _ANNOUNCED.search(_run_for_output(path))
    if not found:
        _NO_ANSWER[key] = time.monotonic() + _NO_ANSWER_SECONDS
        return ""
    _ANSWERS[key] = found[1]
    return found[1]


def _run_for_output(path: str) -> str:
    """The program's output for `-v`, or nothing. It runs in a group of its own, so a wrapper
    script that is killed at the limit takes the program it started along with it.

    `XDG_DATA_HOME` points at the scratch folder too: the program makes its own `major.minor`
    folder, and copies the newest earlier one into it, before it reads `-PrefPath`."""
    with tempfile.TemporaryDirectory(prefix="vpinfe-version-") as scratch:
        environment = {**os.environ, "XDG_DATA_HOME": str(Path(scratch) / "data")}
        try:
            child = subprocess.Popen([path, "-PrefPath", scratch, "-v"], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace",
                                     env=environment, start_new_session=True)
        except OSError:
            return ""
        try:
            return child.communicate(timeout=_ASK_SECONDS)[0] or ""
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate()
            return ""

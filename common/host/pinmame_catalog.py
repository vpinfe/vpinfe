"""PinMAME's own answer about a ROM set, from the library VPX ships.

Borrowed, never bundled: the library is located from the launcher the user
already configured, which ties it to the right install on the machine that
launches - the play host. A machine without VPX simply reports the audit
unavailable, through the same availability predicate as everything else.

The lookups run in one long-lived subprocess (see pinmame_worker), which loads the
library once for all of them, and each answer is cached against the roms folder's mtime
because a folder that has not changed gives the same audit.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

from common.host import tools
from common.i18n import t

logger = logging.getLogger("vpinfe.common.host.pinmame_catalog")

ENV_OVERRIDE = "VPINFE_LIBPINMAME"
WORKER_TIMEOUT_SECONDS = 20.0
# Consecutive helper failures, with no answer between them, before a library is given up on.
FAILURES_BEFORE_SKIP = 3

_cache: dict[tuple, dict] = {}
# Libraries given up on for this run, by path and modification time: a replaced file is tried again.
_skipped: dict[tuple[str, int], str] = {}
_strikes: dict[tuple[str, int], int] = {}
_answered: set[tuple[str, int]] = set()
_helper: tuple[str, subprocess.Popen] | None = None
_helper_lock = threading.Lock()


def _candidate_dirs(vpx_bin_path: str) -> list[Path]:
    """Where the library plausibly lives, derived from the configured launcher."""
    binary = Path(vpx_bin_path)
    vpx_dir = binary.parent
    dirs = [vpx_dir, vpx_dir / "libs", vpx_dir / "plugins" / "pinmame"]
    # macOS app bundle: .../Contents/MacOS/<binary> with Frameworks beside MacOS.
    if vpx_dir.name == "MacOS" and vpx_dir.parent.name == "Contents":
        dirs.insert(0, vpx_dir.parent / "Frameworks")
        dirs.append(vpx_dir.parent / "PlugIns" / "pinmame")
    return dirs


# By launcher: the directories' modification times when searched, the libraries this
# process can load, and the ones set aside because it cannot.
_found: dict[str, tuple[tuple[float, ...], list[Path], list[str]]] = {}

# Machine field of a PE header, as the bitness it means.
_PE_MACHINES = {0x14C: 32, 0x8664: 64, 0xAA64: 64}


def _dir_stamp(directory: Path) -> float:
    try:
        return directory.stat().st_mtime
    except OSError:
        return 0.0


def _dll_bits(path: Path) -> int | None:
    """32 or 64 from a Windows library's PE header, None when it cannot be read."""
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
            if len(head) < 64 or head[:2] != b"MZ":
                return None
            handle.seek(int.from_bytes(head[0x3C:0x40], "little"))
            signature = handle.read(6)
    except OSError:
        return None
    if len(signature) < 6 or signature[:4] != b"PE\0\0":
        return None
    return _PE_MACHINES.get(int.from_bytes(signature[4:6], "little"))


def _search(vpx_bin_path: str) -> tuple[list[Path], list[str]]:
    """Every library the install holds, in the order to try them, and those set aside.

    A Windows library built for the other bitness cannot load into this process, so it is
    never offered however its name sorts.
    """
    usable: list[Path] = []
    set_aside: list[str] = []
    app_bits = struct.calcsize("P") * 8
    patterns = ("libpinmame*.dylib", "libpinmame*.so*", "libpinmame*.dll", "pinmame*.dll")
    for directory in _candidate_dirs(vpx_bin_path):
        for pattern in patterns:
            try:
                hits = sorted(directory.glob(pattern))
            except OSError:
                continue
            for hit in hits:
                bits = _dll_bits(hit) if hit.suffix.lower() == ".dll" else None
                if bits is not None and bits != app_bits:
                    set_aside.append(f"{hit.name} ({bits}-bit)")
                elif hit not in usable:
                    usable.append(hit)
    return usable, set_aside


def _identity(path: Path) -> tuple[str, int]:
    try:
        return str(path), path.stat().st_mtime_ns
    except OSError:
        return str(path), 0


def _searched(vpx_bin_path: str) -> tuple[list[Path], list[str]]:
    """The search, remembered while the directories searched are unchanged: a file added
    or removed there changes its directory's time, and a list of every table asks once
    per table."""
    stamp = tuple(_dir_stamp(directory) for directory in _candidate_dirs(vpx_bin_path))
    held = _found.get(vpx_bin_path)
    if held is None or held[0] != stamp:
        usable, set_aside = _search(vpx_bin_path)
        held = (stamp, usable, set_aside)
        _found[vpx_bin_path] = held
    return held[1], held[2]


def find_library(vpx_bin_path: str) -> Path | None:
    """The library to use, or None. Never raises.

    The first one this process can load that has not already failed to.
    """
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() and _identity(path) not in _skipped else None
    if not (vpx_bin_path or "").strip():
        return None
    usable, _ = _searched(vpx_bin_path)
    for path in usable:
        if _identity(path) not in _skipped:
            return path
    return None


def unusable(vpx_bin_path: str) -> list[str]:
    """Libraries the install holds that were set aside or would not load, each with why."""
    if not (vpx_bin_path or "").strip():
        return []
    usable, set_aside = _searched(vpx_bin_path)
    failed = [f"{path.name} (would not load)" for path in usable if _identity(path) in _skipped]
    return [*set_aside, *failed]


def inventory(vpx_bin_path: str) -> tuple[Path | None, list[str]]:
    """The library to use and those the install holds that cannot be used, for a report."""
    return find_library(vpx_bin_path), unusable(vpx_bin_path)


def availability(vpx_bin_path: str) -> tuple[bool, str | None]:
    """For discovery: can this instance audit ROM sets at all?"""
    if not (vpx_bin_path or "").strip():
        return False, t("error.pinmame.no_launcher")
    if find_library(vpx_bin_path) is None:
        if unusable(vpx_bin_path):
            return False, t("error.pinmame.library_unusable")
        return False, t("error.pinmame.library_not_found")
    return True, None


def _note_failure(library: Path, reason: str, *, fatal: bool) -> bool:
    """Counts a helper failure against the library; True once it is given up on.

    A library that would not load is given up on at once. One that never answered is given
    up on after FAILURES_BEFORE_SKIP in a row; one that has answered is not, since a failure
    after that belongs to the set it was asked about.
    """
    identity = _identity(library)
    if not fatal and identity in _answered:
        return False
    _strikes[identity] = _strikes.get(identity, 0) + 1
    if fatal or _strikes[identity] >= FAILURES_BEFORE_SKIP:
        _skipped[identity] = reason
        logger.warning("Library %s is skipped for this run: %s", library, reason)
        return True
    return False


def _roms_dir_stamp(roms_dir: str) -> float:
    try:
        return os.stat(roms_dir).st_mtime
    except OSError:
        return 0.0


def lookup(vpx_bin_path: str, roms_dir: str, set_name: str) -> dict | None:
    """The catalog entry and audit for one set, or None when the audit cannot run.

    None means "no answer", never "no rom" - the caller falls back to what it
    knew without the library. A library that will not load is set aside and the next one
    the install holds is tried in the same call.
    """
    set_name = (set_name or "").strip()
    if not set_name:
        return None
    while True:
        library = find_library(vpx_bin_path)
        if library is None:
            return None

        key = (str(library), roms_dir, set_name, _roms_dir_stamp(roms_dir))
        if key in _cache:
            return _cache[key]

        with _helper_lock:
            # A caller queued behind the lock may find its answer already made, or the
            # library it chose given up on.
            if key in _cache:
                return _cache[key]
            if _identity(library) in _skipped:
                continue
            try:
                payload = _ask(str(library), roms_dir, set_name)
            except (OSError, ValueError, RuntimeError) as exc:
                _stop_helper()
                logger.warning("PinMAME catalog worker failed: %s", str(exc) or type(exc).__name__)
                if _note_failure(library, type(exc).__name__, fatal=False):
                    continue
                return None

            if "error" in payload:
                _stop_helper()
                logger.warning("PinMAME catalog worker: %s", payload["error"])
                if _note_failure(library, str(payload["error"]),
                                 fatal=bool(payload.get("load_failed"))):
                    continue
                return None
            _strikes.pop(_identity(library), None)
            _answered.add(_identity(library))
            entry = payload.get(set_name)
            if entry is not None:
                _cache[key] = entry
            return entry


def _helper_command(library: str) -> list[str]:
    """A frozen build has no `-m`: its executable starts VPinFE whatever it is handed."""
    frozen = getattr(sys, "frozen", False)
    helper = ["--pinmame-helper"] if frozen else ["-m", "common.host.pinmame_worker"]
    return [sys.executable, *helper, "--serve", library]


def _ask(library: str, roms_dir: str, set_name: str) -> dict:
    """One request to the helper, started on first use. Held under `_helper_lock`.

    A helper that does not answer in time is killed, which ends the read with an empty line.
    """
    global _helper
    if _helper is None or _helper[0] != library or _helper[1].poll() is not None:
        _stop_helper()
        process = subprocess.Popen(
            _helper_command(library),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=str(Path(__file__).resolve().parents[2]),
            creationflags=tools.NO_WINDOW,
        )
        _helper = (library, process)
    process = _helper[1]
    watchdog = threading.Timer(WORKER_TIMEOUT_SECONDS, process.kill)
    watchdog.start()
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps({"roms_dir": roms_dir, "names": [set_name]}) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
    finally:
        watchdog.cancel()
    if not line:
        raise BrokenPipeError
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise ValueError(line.strip())
    return payload


def _stop_helper() -> None:
    global _helper
    if _helper is None:
        return
    process = _helper[1]
    _helper = None
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    if process.stdout is not None:
        process.stdout.close()


atexit.register(_stop_helper)


def clear_cache() -> None:
    """For tests."""
    _cache.clear()
    _found.clear()
    _skipped.clear()
    _strikes.clear()
    _answered.clear()
    with _helper_lock:
        _stop_helper()

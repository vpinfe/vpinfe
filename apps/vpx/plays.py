"""What Visual Pinball counted for a table: the `VPX` section it writes into a `.info`."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from common.apps.contract import Entry, PlayRecord

logger = logging.getLogger("vpinfe.apps.vpx.plays")

INFO_SUFFIX = ".info"
TABLE_SUFFIX = ".vpx"
SECTION = "VPX"

# Read once per change of the file, since a list of hundreds is sorted by these numbers and
# the program rewrites the file at the start and end of every game.
_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _lookup(folder: str, name: str) -> str:
    """The file in `folder` called `name`, whatever its case, as VPX matches a companion."""
    try:
        listed = os.listdir(folder)
    except OSError:
        return ""
    wanted = name.lower()
    return next((one for one in listed if one.lower() == wanted), "")


def _tables_in(folder: str) -> int:
    try:
        return sum(1 for one in os.listdir(folder) if one.lower().endswith(TABLE_SUFFIX))
    except OSError:
        return 0


def _section(path: str) -> dict[str, Any]:
    try:
        stat = os.stat(path)
    except OSError:
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    held = _cache.get(path)
    if held is not None and held[:2] == stamp:
        return held[2]
    found: dict[str, Any] = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get(SECTION), dict):
            found = data[SECTION]
    except (OSError, ValueError) as error:
        logger.warning("Could not read %s (%s), so it has no count", path, error)
    _cache[path] = (*stamp, found)
    return found


def _whole(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _iso(value: Any) -> str:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat() \
                if value.strip() else ""
        except ValueError:
            return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value, UTC).isoformat()
    return ""


class VPXPlays:
    def record(self, entry: Entry, settings: Mapping[str, Any]) -> PlayRecord | None:
        """Where VPX looks for the file, in its order: `<table>.info`, then `<folder>.info`.
        The folder's file holds one count for every table VPX ran from the folder, so it
        answers only for a folder with the one table. None where there is no file, no
        `VPX` section, or the count is the folder's and a second table could be in it."""
        if not entry.table:
            return None
        # Beside the table, which is not the game's folder for a table kept elsewhere.
        folder = os.path.dirname(entry.table)
        stem = os.path.splitext(os.path.basename(entry.table))[0]
        shared = _lookup(folder, os.path.basename(os.path.normpath(folder)) + INFO_SUFFIX)
        own = _lookup(folder, stem + INFO_SUFFIX)
        # A table named like its folder reads and writes the folder's file as its own.
        if own and own != shared:
            found = _section(os.path.join(folder, own))
        else:
            chosen = own or shared
            if not chosen or _tables_in(folder) != 1:
                return None
            found = _section(os.path.join(folder, chosen))
        if not found:
            return None
        return PlayRecord(
            count=_whole(found.get("RunCount", found.get("StartCount"))),
            seconds=_whole(found.get("RunTime")),
            last=_iso(found.get("LastRun")),
        )

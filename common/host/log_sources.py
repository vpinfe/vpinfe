"""The files the Logs page may read, and the one list that decides it.

Reading and downloading both resolve a name against `available()`, so neither can be
handed a path: a name that is not on the list is not a file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from common.host import vpx_log
from common.runtime import log_setup

# A log nobody rotates can grow without bound, and a download holds the file in memory
# on the way out.
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Source:
    name: str
    path: Path
    # How a record starts in a file that is not VPinFE's own; None for VPinFE's.
    record: re.Pattern[str] | None = None


def available() -> list[Source]:
    """VPinFE's current log and its rotations, newest first, then Visual Pinball's when a
    launcher names its ini file - whether or not it has written one yet."""
    found = [Source(one.name, one) for one in log_setup.log_files()]
    vpx = vpx_log.current_vpinball_log()
    if vpx is not None:
        found.append(Source(vpx.name, vpx, vpx_log.RECORD))
    return found


def resolve(name: str) -> Source | None:
    """The source called `name`, or the live VPinFE log for an empty one."""
    if not name:
        current = log_setup.log_file()
        return Source(current.name, current) if current is not None else None
    return next((one for one in available() if one.name == name), None)


def visual_pinball() -> str:
    """The name Visual Pinball's log goes by, or "" where there is none to read."""
    return next((one.name for one in available() if one.record is not None), "")


def read(source: str, limit: int, level: str = "", contains: str = "") -> dict:
    """What the Logs page shows for `source`: its records, where the file is, and what
    else could be read."""
    listed = available()
    chosen = (next((one for one in listed if one.name == source), None) if source
              else resolve(""))
    records = (log_setup.read_records(chosen.path, limit, level, contains, chosen.record)
               if chosen else [])
    return {"count": len(records), "records": records,
            "path": str(chosen.path) if chosen else "",
            "sources": [one.name for one in listed if one.record is None],
            "visual_pinball": next((one.name for one in listed if one.record), "")}

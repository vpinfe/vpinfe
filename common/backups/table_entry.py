"""A table's record, kept in Backups beside its file whenever the file is.

The record is its plays, rating, tags, Hidden and the rest of its entry in the game's
`.info`, with the name a person calls the table by. It is a copy of its own in the tables
area, named `<table file>.entry.json`, taken in the same swap or delete as the file's, so
the file's copy can be found with it by the stamps. It is never listed as a copy: the file's
row carries its name, and removing the file's copy removes it.
"""

from __future__ import annotations

import glob
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import kept_copies
from common.games import info_migration

from . import folder as backup_folder
from . import media
from .copies import REASON_REPLACE

SUFFIX = ".entry.json"
# The record is copied a moment after the table file, so the two stamps can differ by seconds.
_APART = 10
_LABEL = "table"
_ENTRY = "entry"


def is_entry(relative: str) -> bool:
    return relative.endswith(SUFFIX)


def keep(game_id: str, filename: str, entry: dict[str, Any], label: str,
         reason: str = REASON_REPLACE) -> Path:
    """Copy `entry`, the record of the table `filename` called `label`, into Backups."""
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / f"{filename}{SUFFIX}"
        staged.write_text(json.dumps({_LABEL: label, _ENTRY: entry}, indent=2,
                                     ensure_ascii=False), encoding="utf-8")
        return media.keep(backup_folder.for_writing(), game_id, staged, staged.name,
                          reason, area=media.TABLES)


def _held(copy: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(copy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get(_ENTRY), dict) else None


def read(copy: Path) -> dict[str, Any] | None:
    """The record a copy holds, or None when it cannot be read."""
    held = _held(copy)
    return None if held is None else held[_ENTRY]


def beside_path(copy: Path) -> Path | None:
    """The copy of the record taken with a table file's copy: the one for the same file
    taken in the same swap or delete, or None when the file went to Backups some other way."""
    name = info_migration.copied_from(copy).name
    taken = _stamp(copy)
    if taken is None:
        return None
    best: tuple[float, Path] | None = None
    for each in copy.parent.glob(f"{glob.escape(name)}{SUFFIX}{kept_copies.BACKUP_MARKER}*"):
        at = _stamp(each)
        if at is None or not kept_copies.is_copy(each):
            continue
        apart = abs((at - taken).total_seconds())
        if apart <= _APART and (best is None or apart < best[0]):
            best = (apart, each)
    return None if best is None else best[1]


def beside(copy: Path) -> dict[str, Any] | None:
    """The record kept with a table file's copy, or None."""
    found = beside_path(copy)
    return None if found is None else read(found)


def label_beside(copy: Path) -> str:
    """The name the table had when its file's copy was taken, or "" when none was kept."""
    found = beside_path(copy)
    held = None if found is None else _held(found)
    return "" if held is None else str(held.get(_LABEL) or "")


def _stamp(copy: Path) -> datetime | None:
    try:
        return datetime.strptime(kept_copies.backup_stamp(copy), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC)
    except ValueError:
        return None

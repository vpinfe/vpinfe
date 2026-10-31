"""How a copy VPinFE keeps of a file is named, found and capped.

Every name that marks a kept copy is built here: `<file>.vpinfe-<UTC stamp>`, with a word
after the stamp for what the copy was taken for. The file may be a `.info` or the settings
file, so nothing here reads what is inside it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from common.atomic_write import naming_folder

logger = logging.getLogger("vpinfe.common.kept_copies")

BACKUP_MARKER = ".vpinfe-"
# What a copy VPinFE took was for, the word after the stamp. A copy of a 2.x file has none,
# so it keeps the bare `<file>.vpinfe-<UTC>` that 2.x looks for.
REASON_2X = ""
RESTORE = "restore"
IMPORT = "import"
RESET = "reset"
TURN = "turn"
CONVERT = "convert"
EDIT = "edit"
MOVE = "move"
REPLACE = "replace"
DELETE = "delete"
_COPY_TAIL = re.compile(
    re.escape(BACKUP_MARKER) + r"(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<reason>[a-z]+))?$")


def backup_path(info_path: str | Path, when: datetime | None = None,
                reason: str = REASON_2X) -> str:
    """Where a copy VPinFE takes of this file goes.

    Timestamped rather than .bak, so a restore point is not overwritten by the next one.
    UTC and no colons, because Windows will not have them in a filename. Every name that
    marks a kept copy is built here.
    """
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{info_path}{BACKUP_MARKER}{stamp}" + (f"-{reason}" if reason else "")


def backup_names(names: Iterable[str], info_name: str) -> list[str]:
    """The copies in a folder listing, newest first.

    Takes names so it can ride a scan already in progress; the stamp sorts lexically.
    """
    prefix = info_name + BACKUP_MARKER
    return sorted((n for n in names if n.startswith(prefix)), reverse=True)


def is_copy(name: str | Path) -> bool:
    """Whether a name is one of VPinFE's kept copies, in either spelling."""
    return _COPY_TAIL.search(Path(name).name) is not None


def backup_stamp(backup: str | Path) -> str:
    """When the copy was taken, as written in its name."""
    name = Path(backup).name
    found = _COPY_TAIL.search(name)
    return found.group("stamp") if found else name.rsplit(BACKUP_MARKER, 1)[-1]


def backup_reason(backup: str | Path) -> str:
    """What the copy was taken for, as written in its name. Empty for a copy of a 2.x file."""
    found = _COPY_TAIL.search(Path(backup).name)
    return (found.group("reason") or "") if found else ""


def _free_backup_path(info_path: str | Path, when: datetime | None = None,
                      reason: str = REASON_2X, into: str | Path | None = None) -> str:
    def named(at: datetime | None) -> str:
        path = backup_path(info_path, at, reason)
        return path if into is None else os.path.join(into, os.path.basename(path))

    path = named(when)
    while os.path.exists(path):        # never overwrite a restore point
        # A whole second, not the second field: replace(second=(s + 1) % 60) wraps 59 to
        # 0, and the name that is supposed to be newer then sorts 59 seconds older. These
        # names are the only ordering restore has.
        when = (when or datetime.now(UTC)) + timedelta(seconds=1)
        path = named(when)
    return path


def write_backup(info_path: str | Path, original_text: str,
                 when: datetime | None = None) -> str:
    """Copy the file aside before it is rewritten, and prove the copy is readable.

    A truncated backup is worse than none: it is the one file whose failure is only
    discovered at the moment somebody needs it. The copy of a 2.x file, so no reason in
    its name.
    """
    json.loads(original_text)
    path = _free_backup_path(info_path, when)
    with naming_folder(info_path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(original_text)
        with open(path, encoding="utf-8") as handle:
            json.load(handle)
    keep_newest(info_path)
    return path


def copy_aside(info_path: str | Path, reason: str, when: datetime | None = None,
               into: str | Path | None = None, restoring: str | Path | None = None) -> str:
    """Keep the current file before something replaces it, whatever state it is in.

    `reason` is what the copy is for: `REASON_2X` for a file a 2.x build reads, and otherwise the
    word that goes after the stamp. `into` is a folder other than the file's own.
    `restoring` is the copy about to be put back over the file, which the cap must not
    remove out from under the restore.

    No JSON check: a file too broken to parse is one somebody is restoring *because* it
    is broken.
    """
    path = _free_backup_path(info_path, when, reason, into)
    with naming_folder(info_path):
        shutil.copy2(info_path, path)
    keep_newest(info_path, into, spare=restoring)
    return path


def keep_newest(info_path: str | Path, folder: str | Path | None = None,
                spare: str | Path | None = None) -> list[str]:
    """The cap: remove the copies of this file that VPinFE took itself, keeping the newest
    copy of a 2.x file and the newest of any kind. Returns what it removed.

    Runs when a copy is written, never on a clock. A name that does not end in a stamp and
    at most one word is somebody else's and is left where it is.
    """
    path = Path(info_path)
    where = Path(folder) if folder is not None else path.parent
    try:
        names = os.listdir(where)
    except OSError:
        return []
    own = [n for n in backup_names(names, path.name) if _COPY_TAIL.search(n)]
    keep_2x = next((n for n in own if backup_reason(n) == REASON_2X), None)
    keep = set(own[:1]) | ({keep_2x} if keep_2x else set())
    if spare is not None:
        keep.add(Path(spare).name)
    removed = []
    for name in own:
        if name in keep:
            continue
        try:
            (where / name).unlink()
        except OSError:
            logger.warning("Could not remove an older copy: %s", where / name, exc_info=True)
            continue
        removed.append(name)
    return removed

"""Carrying the backups already taken into a new backups folder.

A backup is a zip beside the others in the folder, or a copy of a game's file under `media/`,
`assets/` or `tables/`. Both move as files, by the same path they have in the folder, and
nothing else in the old folder is touched. A file whose name is taken in the new folder is
left where it is: a backup is never replaced.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import atomic_write, kept_copies, service_errors
from common.config import path_state
from common.failures import why
from common.i18n import t
from common.runtime import jobs, shutdown

from . import archive
from . import folder as backup_folder
from .media import AREAS

logger = logging.getLogger("vpinfe.common.backups.relocate")

# Held from the check that no move is running to the job starting, so two requests cannot
# both save their folder and leave the backups behind the loser.
_BEGINNING = threading.Lock()


@dataclass(frozen=True)
class Plan:
    """What changing the folder to `new` would do with the backups in `old`."""

    old: Path
    new: Path
    moving: int
    already: int
    problem: str

    def as_dict(self) -> dict[str, Any]:
        return {"old": str(self.old), "new": str(self.new), "moving": self.moving,
                "already": self.already, "problem": self.problem}


def _files(folder: Path) -> Iterator[Path]:
    """The backups in a folder, as paths relative to it."""
    try:
        for entry in sorted(folder.iterdir()):
            if archive.is_archive_name(entry.name) and entry.is_file():
                yield entry.relative_to(folder)
    except OSError:
        return
    for area in AREAS:
        if (kept := folder / area).is_dir():
            for found in sorted(kept.rglob("*")):
                if kept_copies.is_copy(found) and found.is_file():
                    yield found.relative_to(folder)


def _answers(folder: Path) -> bool:
    return path_state.look(str(folder), path_state.DIR).state in (path_state.OK,
                                                                   path_state.READ_ONLY)


def plan(setting: str) -> Plan:
    """What would move if the setting became `setting`. Nothing to move when the old folder
    is empty or does not answer, and nothing to say about the new one then either."""
    old, new = backup_folder.current(), backup_folder.named(setting)
    if old == new or not _answers(old) or (new.exists() and old.samefile(new)):
        return Plan(old, new, 0, 0, "")
    moving = sum(1 for _ in _files(old))
    if not moving:
        return Plan(old, new, 0, 0, "")
    already = sum(1 for _ in _files(new)) if _answers(new) else 0
    return Plan(old, new, moving, already, backup_folder.problem(new))


def begin(setting: str, save: Callable[[], object]) -> jobs.Job:
    """Make `setting` the folder and move what is in the old one, as a job. `save` writes
    the setting, and runs only once nothing can stop the move starting: refused, with
    nothing changed, when the new folder cannot be written to or a move is already going."""
    with _BEGINNING:
        found = plan(setting)
        problem = found.problem or backup_folder.problem(found.new)
        if problem:
            raise service_errors.BlockedError(problem)
        if jobs.active(jobs.KIND_BACKUPS_MOVE):
            raise jobs.JobBusyError(t("error.backups.move_busy"))
        save()
        return jobs.submit(jobs.KIND_BACKUPS_MOVE,
                           lambda job: _move_all(found, job.reporter()))


def _move_all(found: Plan, reporter: jobs.JobReporter) -> dict[str, Any]:
    files = list(_files(found.old))
    moved = 0
    left: list[str] = []
    failed: list[dict[str, str]] = []
    for index, relative in enumerate(files):
        if shutdown.requested():
            break
        reporter.progress(index, len(files), t("said.backups_move", file=relative.name))
        try:
            if not _move_one(found.old / relative, found.new / relative):
                left.append(str(relative))
                continue
        except Exception as exc:  # noqa: BLE001 - one file stays, the rest go on
            logger.warning("Moving %s failed", relative, exc_info=True)
            failed.append({"name": str(relative), "reason": why(exc)})
            continue
        moved += 1
    for area in AREAS:
        _prune(found.old / area)
    logger.info("Moved %s backup(s) from %s to %s, %s left, %s failed",
                moved, found.old, found.new, len(left), len(failed))
    return {"moved": moved, "left": left, "failed": failed,
            "from": str(found.old), "to": str(found.new)}


def _move_one(source: Path, target: Path) -> bool:
    """Copy, check the copy is the size of the original, then remove the original. False,
    with the original kept, when `target` is taken: a backup is never replaced."""
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f"{atomic_write.STAGED_PREFIX}{os.urandom(6).hex()}"
                              f"{atomic_write.STAGED_SUFFIX}")
    try:
        with atomic_write.naming_folder(target):
            shutil.copy2(source, staged)
            # Windows refuses to fsync a handle that cannot write.
            with open(staged, "r+b" if os.name == "nt" else "rb") as copy:
                os.fsync(copy.fileno())
            if staged.stat().st_size != source.stat().st_size:
                raise OSError(t("error.backups.move_size", name=source.name))
            if not _claim(staged, target):
                return False
    finally:
        staged.unlink(missing_ok=True)
    source.unlink()
    return True


def _claim(staged: Path, target: Path) -> bool:
    """Give `staged` the name `target`, unless something took it first."""
    try:
        os.link(staged, target)
    except FileExistsError:
        return False
    except OSError:  # a share that cannot hard-link
        if target.exists():
            return False
        os.replace(staged, target)
    return True


def _prune(area: Path) -> None:
    """The folders under an area the move emptied, deepest first, and the area itself."""
    if not area.is_dir():
        return
    for folder in sorted((one for one in area.rglob("*") if one.is_dir()),
                         key=lambda one: len(one.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            continue
    try:
        area.rmdir()
    except OSError:
        return

"""Restores that wait for VPinFE to close.

The files in the config folder are held in memory by the running app and written back from
it, so a copy put in place now would be overwritten by the next save. The restore is
written down here and carried out as the app exits, after everything has stopped writing.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from common.atomic_write import write_atomic
from common.config.paths import CONFIG_DIR, STATE_DIR
from common.devices import device_registry
from common.games import info_maintenance
from common.games.info_migration import copied_from
from common.kept_copies import is_copy

from . import archive

logger = logging.getLogger("vpinfe.common.backups.pending")

PENDING_NAME = ".pending-restores.json"
STAGED_DIR = ".pending-files"


def _file(config_dir: Path | None = None) -> Path:
    return (config_dir or CONFIG_DIR) / PENDING_NAME


def _staged(config_dir: Path | None = None) -> Path:
    return (config_dir or CONFIG_DIR) / STAGED_DIR


def held(config_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """Each file waiting to be replaced: the file that replaces it, and the id of the copy
    that was asked for."""
    try:
        data = json.loads(_file(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(target): {"copy": str(one.get("copy") or ""), "id": str(one.get("id") or "")}
            for target, one in data.items() if isinstance(one, dict)}


def _write(found: dict[str, dict[str, str]], config_dir: Path | None) -> None:
    path = _file(config_dir)
    if not found:
        path.unlink(missing_ok=True)
        return
    write_atomic(path, lambda handle: json.dump(found, handle, indent=2))


def add(target: Path, copy: Path, config_dir: Path | None = None, *, copy_id: str) -> None:
    """A second copy for the same file replaces the first: only one can end up in place."""
    found = held(config_dir)
    found[str(target)] = {"copy": str(copy), "id": copy_id}
    _write(found, config_dir)


def stage(target: Path, backup: Path, member: archive.Member, copy_id: str,
          config_dir: Path | None = None) -> None:
    """Take a file out of a backup now, so deleting the backup before the app closes does
    not lose it."""
    folder = _staged(config_dir)
    folder.mkdir(parents=True, exist_ok=True)
    add(target, archive.extract(backup, member, folder), config_dir, copy_id=copy_id)


def waiting() -> list[str]:
    """The ids of the copies waiting for the app to close."""
    return [one["id"] for one in held().values() if one["id"]]


def drop(match: str | Path, config_dir: Path | None = None) -> bool:
    """Forget a restore waiting on this copy, by its file or its id. True when there was
    one."""
    found = held(config_dir)
    kept = {target: one for target, one in found.items()
            if str(match) not in (one["copy"], one["id"])}
    if kept == found:
        return False
    for target, one in found.items():
        if target not in kept and Path(one["copy"]).parent == _staged(config_dir):
            Path(one["copy"]).unlink(missing_ok=True)
    _write(kept, config_dir)
    return True


def _ready(target: Path, copy: Path, folder: Path) -> bool:
    if target.parent not in (folder, STATE_DIR) or not copy.is_file():
        return False
    if copy.parent == _staged(folder):
        return (target.name in archive.RECORD_KINDS
                and target.parent == archive.record_folder(target.name))
    return copy.parent == target.parent and is_copy(copy) and copied_from(copy) == target


def apply(config_dir: Path | None = None) -> int:
    """Put each waiting copy in place, keeping what it replaces. Returns how many went in.

    A copy that has gone is skipped, and one that cannot be put in place does not stop the
    others. The list is cleared either way, so a failure is not tried again on every exit.
    """
    folder = config_dir or CONFIG_DIR
    done = 0
    for target, one in held(folder).items():
        try:
            if not _ready(Path(target), Path(one["copy"]), folder):
                continue
            source = Path(one["copy"])
            if Path(target).name in archive.STATE_RECORDS:
                _staged(folder).mkdir(parents=True, exist_ok=True)
                source = _staged(folder) / f"own-row-{Path(target).name}"
                device_registry.keep_own_row(Path(one["copy"]), Path(target), source)
            info_maintenance.restore_file(Path(target), source)
            done += 1
        except (OSError, ValueError):
            logger.exception("Could not restore %s from %s", target, one["copy"])
    _file(folder).unlink(missing_ok=True)
    shutil.rmtree(_staged(folder), ignore_errors=True)
    return done

"""The folder VPinFE keeps its own backups in.

Only that folder is read. Changing the setting moves nothing by itself: `relocate` carries
the backups already taken across, when the person asks for it.
"""

from __future__ import annotations

import os
from pathlib import Path

from common.config import path_state, paths
from common.config.config_access import cfg_get
from common.config.paths import get_ini_config
from common.i18n import t

SECTION = "backups"
FOLDER = "folder"


def chosen() -> str:
    """The folder the setting names, as typed. Blank when none is chosen."""
    return cfg_get(get_ini_config(), SECTION, FOLDER).strip()


def current() -> Path:
    """Where a backup taken now is written."""
    return named(chosen())


def named(setting: str) -> Path:
    """The folder a value of the setting names: the default when it is blank."""
    setting = setting.strip()
    return spelled(setting) if setting else paths.DEFAULT_BACKUPS_DIR


def spelled(typed: str) -> Path:
    """Absolute and normalized, the one form a backup's id accepts. Symlinks are not
    resolved: that touches every component, and on a share that has dropped it never returns."""
    return Path(os.path.abspath(os.path.expanduser(typed)))


def known() -> list[Path]:
    """Every folder a backup may be in. Nothing on disk is touched, so a folder on a share
    that is not answering costs nothing."""
    return [current()]


def listable() -> list[Path]:
    """The known folders that answer now. A share that is not connected is left out, not
    waited on - a listing of a dead mount does not return."""
    folders = known()
    found = path_state.look_all([path_state.Ask(str(one), path_state.DIR) for one in folders])
    return [folder for folder, state in zip(folders, found, strict=True)
            if state.state in (path_state.OK, path_state.READ_ONLY)]


def holds(folder: Path) -> bool:
    """Whether a folder is one of the known ones, so a path believed from outside is only
    taken to be a backup when it sits in a place backups are kept."""
    return folder in known()


def refused(folder: Path) -> tuple[str, str]:
    """What is wrong with writing a backup to `folder`, as the reason and the state the disk
    reported. The default counts as fine before it exists: the first backup makes it."""
    if folder == paths.DEFAULT_BACKUPS_DIR and not folder.exists():
        return "", path_state.OK
    found = path_state.look(str(folder), path_state.DIR, writable=True)
    return path_state.refusal(t("backups.folder_name"), found), found.state


def problem(folder: Path) -> str:
    """What is wrong with writing a backup to `folder`, if anything."""
    return refused(folder)[0]


def status() -> dict[str, str]:
    """The folder a backup would go to now, and what is wrong with it, if anything."""
    folder = current()
    return {"path": str(folder), "problem": problem(folder)}


def for_writing() -> Path:
    """The folder a backup is about to be written to, made if it is the default. Raises
    `BlockedError` for a chosen folder that cannot be written to."""
    folder = current()
    if folder == paths.DEFAULT_BACKUPS_DIR:
        folder.mkdir(parents=True, exist_ok=True)
    path_state.require(str(folder), writable=True, name=t("backups.folder_name"))
    return folder

"""Where a game's `.info` is, and how to load and save it.

Every caller that needs the path of a game's file, or its record, goes through here. A
game's file is named for its folder: `<folder>/<folder>.info`.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

from common.games.info_file import MetaConfig, read_info

INFO_SUFFIX = ".info"


def info_name(folder_name: str) -> str:
    """The file name a game's record has in a folder called `folder_name`."""
    return f"{folder_name}{INFO_SUFFIX}"


def game_info_path(folder: str | os.PathLike[str]) -> Path:
    """The path of the record a game folder holds, whether or not it exists yet."""
    here = Path(folder)
    return here / info_name(here.name)


def info_path_of(game: Any) -> Path:
    """The path of a loaded game's record, from the folder and name the scan gave it."""
    return Path(str(game.full_path_game)) / info_name(game.game_dir_name)


def is_game_info(path: str | os.PathLike[str]) -> bool:
    """True for the file that is the record of the game folder it sits in."""
    file = Path(path)
    return file.name.lower() == info_name(file.parent.name).lower()


def load(folder: str | os.PathLike[str]) -> dict[str, Any]:
    """A game's record as the rest of VPinFE reads it; {} where the folder has none."""
    path = game_info_path(folder)
    return read_info(path) if path.is_file() else {}


def open_record(folder: str | os.PathLike[str]) -> MetaConfig:
    """The record held for editing; `write_config` on it saves schema 3 and the restore point."""
    return MetaConfig(str(game_info_path(folder)))


def open_for_writing(folder: str | os.PathLike[str]) -> MetaConfig:
    """`open_record`, refusing a file the person has made read-only.

    A save replaces the file by renaming a new one over it, which a read-only file in a
    writable folder does not stop; this is what keeps the refusal.
    """
    path = game_info_path(folder)
    if path.exists() and not os.access(path, os.W_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
    return open_record(folder)

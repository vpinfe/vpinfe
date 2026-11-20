"""Where DOF's config lives: Visual Pinball's `directoutputconfig`.

Visual Pinball's DOF plugin reads it from its preferences folder, and the frontend's own
DOF is given the same folder as its base path, so the two read the same files.
"""

from __future__ import annotations

from pathlib import Path

from apps.vpx import config as vpx_config
from common.games import launchers

FOLDER = "directoutputconfig"


def base_path() -> Path | None:
    """The folder DOF is given as its base path, or None where the platform names none:
    the preferences folder of the Visual Pinball the default launcher runs."""
    return vpx_config.preferences_folder(launchers.default_value("bin_path"))


def config_folder() -> Path | None:
    """The folder holding DOF's config files, or None where the platform names none."""
    base = base_path()
    return base / FOLDER if base is not None else None


LITTER = frozenset({"thumbs.db", "desktop.ini"})


def _is_litter(path: Path) -> bool:
    """A file a file manager leaves behind, which is not config."""
    return path.name.startswith(".") or path.name.lower() in LITTER


def present() -> bool:
    """Whether DOF has config to read. True where the platform names no folder, because
    there is nothing to look in."""
    folder = config_folder()
    if folder is None:
        return True
    try:
        return folder.is_dir() and any(
            path.is_file() and not _is_litter(path) for path in folder.iterdir())
    except OSError:
        return False

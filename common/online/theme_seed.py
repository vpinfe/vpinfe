"""Copying a 2.x install's themes into the folder 3.0 reads.

Runs while the 2.x ini is converted and consumes nothing: it runs once because a converted
file never converts again, and a theme already in the new folder is not copied over.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from common.config import config_schema
from common.config.paths import ASIDE_SUFFIX, THEMES_FOLDER

logger = logging.getLogger("vpinfe.common.online.theme_seed")

OLD_FOLDER = "themes"


def seed(store: Any) -> None:
    """Copy every folder of 2.x's `themes/` into `frontend_themes/`. A folder already
    there is left as it is, and 2.x's is only read."""
    config_dir = Path(store.ini_path).parent
    old, new = config_dir / OLD_FOLDER, config_dir / THEMES_FOLDER
    if not old.is_dir():
        return
    new.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in sorted(old.iterdir()):
        target = new / source.name
        if (source.name.startswith(".") or source.name.endswith(ASIDE_SUFFIX)
                or not source.is_dir() or os.path.lexists(target)):
            continue
        try:
            if source.is_symlink():
                os.symlink(os.readlink(source), target)
            else:
                shutil.copytree(source, target, symlinks=True)
            copied += 1
        except OSError:
            logger.exception("Could not copy the theme %s into %s", source.name, new)
            if target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target, ignore_errors=True)
    logger.info("Copied %d theme(s) from %s into %s", copied, old, new)


config_schema.register_seed("frontend_themes", seed)

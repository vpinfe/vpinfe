"""Going back to 2.x: every game's newest 2.x copy goes in as VPinFE exits.

The request is a file in the config folder, written before VPinFE quits and carried out by
`main.py` as it exits, when nothing is left to upgrade a 2.x file again.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from common import service_errors
from common.config.paths import CONFIG_DIR
from common.games import info_maintenance
from common.games.collection_store import restorable_collections_backup
from common.games.info_migration import restorable_backup
from common.host import system_actions
from common.i18n import t

logger = logging.getLogger("vpinfe.common.backups.going_back")

REQUEST_NAME = ".go-back-to-2x"
# Schema 0 is the .info and collections files 2.x wrote.
SCHEMA_2X = 0


def plan(config_dir: Path | None = None) -> dict[str, Any]:
    """What going back would put back: the games that hold a 2.x copy, and whether the
    collections do."""
    folder = config_dir or CONFIG_DIR
    games = sum(1 for one in info_maintenance.game_dirs()
                if restorable_backup(one, SCHEMA_2X))
    return {"games": games,
            "collections": bool(restorable_collections_backup(folder, SCHEMA_2X))}


def request(config_dir: Path | None = None) -> dict[str, Any]:
    """Ask for it on the next exit. Refused where there is nothing 2.x copies could put
    back."""
    folder = config_dir or CONFIG_DIR
    found = plan(folder)
    if not found["games"] and not found["collections"]:
        raise service_errors.RefusedError(t("error.backups.no_2x"))
    (folder / REQUEST_NAME).touch()
    return found


def clear(config_dir: Path | None = None) -> None:
    (config_dir or CONFIG_DIR).joinpath(REQUEST_NAME).unlink(missing_ok=True)


def carry_out(config_dir: Path | None = None) -> bool:
    """Put the copies back if they were asked for. True when it ran. A restart asked for as
    well is dropped."""
    folder = config_dir or CONFIG_DIR
    if not (folder / REQUEST_NAME).exists():
        return False
    clear(folder)
    system_actions.cancel_app_restart()
    result = info_maintenance.restore_library(
        max_schema=SCHEMA_2X, config_dir=folder, log_cb=logger.info)
    logger.info("Went back to 2.x: %d games and %s collections put back, %d could not be.",
                result["restored"], "the" if result["collections_restored"] else "no",
                result["failed"])
    return True

"""Remembering which game the player was on, by identity rather than by position."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from common.config.config_access import SettingsConfig, cfg_get
from common.config.config_store import ConfigStore
from common.devices import device_registry
from common.games.collection_resolver import Entry
from common.games.collection_store import LAST_COLLECTION, public_name
from common.games.game import GameRecord
from common.games.game_identity import game_id

logger = logging.getLogger("vpinfe.frontend.last_game")

# Fields on this install's row in the device registry, not settings.
STATE_KEY = "last_table"
COLLECTION_KEY = "last_collection"


def entry_identity(entry: Entry) -> str:
    """Stable id for one row of the wheel: the table's, falling back to its game's.

    A game offers several tables and a collection may name a particular one, so saving
    the game comes back to whichever table the list happens to hold. Ids rather than a
    path, because an install reading a library elsewhere never sees that filesystem.
    """
    table_id = str(getattr(entry, "table_id", "") or "").strip()
    if table_id:
        return table_id
    return str(game_id(getattr(entry, "game", entry)) or "").strip()


def save_last_launched(ini_config: ConfigStore, game: GameRecord,
                       table_id: str = "") -> None:
    """Persist what just launched. Takes the two ids rather than an entry: no entry
    exists on the path a Remote or API launch takes."""
    if not SettingsConfig.from_config(ini_config).restore_last_table:
        return
    identity = (str(table_id or "").strip()
                or str(game_id(game) or "").strip())
    if not identity:
        return
    row = device_registry.get_device_registry()
    if row.self_get(STATE_KEY, "") == identity:
        return  # unchanged; skip the disk write
    try:
        row.set_self(**{STATE_KEY: identity})
    except Exception:
        logger.exception("Could not persist last game selection")


def resolve_last_table_index(ini_config: ConfigStore, entries: Sequence[Entry]) -> int:
    """Return the index of the saved last row within `entries`, else 0.

    Returns 0 when the feature is off, nothing is saved, or the saved row
    isn't in the current view (e.g. filtered out by a startup collection).
    """
    if not SettingsConfig.from_config(ini_config).restore_last_table:
        return 0
    saved = str(device_registry.get_device_registry().self_get(STATE_KEY, "") or "").strip()
    if not saved:
        return 0
    for index, entry in enumerate(entries):
        if entry_identity(entry) == saved:
            return index
    return 0


def _asks_for_last(ini_config: ConfigStore) -> bool:
    """The name is a reserved word, so a file edited by hand may spell it any way."""
    wanted = cfg_get(ini_config, "behavior", "startup_collection", "").strip()
    return wanted.casefold() == LAST_COLLECTION.casefold()


def remember_collection(ini_config: ConfigStore, name: str) -> None:
    """Persist the collection on screen, where `startup_collection` asks for Last Collection.

    Nothing is written under any other setting, so a cabinet that opens on a fixed
    collection never touches its file for this. The whole library is saved as empty.
    """
    if not _asks_for_last(ini_config):
        return
    shown = public_name(name)
    row = device_registry.get_device_registry()
    if (row.self_get(COLLECTION_KEY, "") or "") == shown:
        return
    try:
        row.set_self(**{COLLECTION_KEY: shown or None})
    except Exception:
        logger.exception("Could not persist last collection")


def opening_collection(ini_config: ConfigStore) -> str:
    """The collection the frontend opens on, empty for the whole library.

    Last Collection is the one saved when the frontend last showed one, and the whole
    library until it has shown any.
    """
    if not _asks_for_last(ini_config):
        return cfg_get(ini_config, "behavior", "startup_collection", "").strip()
    return str(device_registry.get_device_registry().self_get(COLLECTION_KEY, "") or "").strip()

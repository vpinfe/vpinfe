"""Stable local identity for tables: an opaque id on each entry in the `tables` map.

Unique across the library, not just within a game, so an id alone identifies a table.
The game-level counterpart is `game_identity`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from common.games.game import GameRecord
from common.games.game_identity import minted_id
from common.games.game_metadata import keep_game_meta, load_game_meta
from common.games.info_file import VPINFE_SECTION
from common.games.tables import (
    DEFAULT_TABLE_KEY,
    TABLE_ID_KEY,
    TABLES_KEY,
    entry_for_filename,
    entry_native_key,
    rekey_by_id,
    stamp_added,
    table_id,
)
from common.ids import new_id

logger = logging.getLogger("vpinfe.common.games.table_identity")


def table_ids(game: GameRecord) -> dict[str, str]:
    """{native key: id} for a game's tables, skipping entries with no id yet.

    The native key is the filename for something in the folder and `app:key` for
    something with no file. One map either way: every caller is asking which entry this
    is, and none of them wants to answer that question twice.
    """
    entries = getattr(game, "meta_config", None) or {}
    entries = entries.get(TABLES_KEY) if isinstance(entries, dict) else None
    return {entry_native_key(e): i for i, e in rekey_by_id(entries).items()
            if entry_native_key(e)}


def ensure_unique_table_ids(games: Iterable[Any]) -> dict[str, tuple[Any, str]]:
    """Bring a library's table identity up to date, in one pass over every game.

    Mints missing ids, re-mints collisions, converts the filename-keyed map, stamps
    `added` on a table that has none, and rewrites a recorded default that still names a
    file. Returns {id: (game, filename)}.

    Writes a .info only when that game changed: this runs at startup over the whole
    library, and on a share a needless write is a round trip each.

    A collision is not chance at this length - it means a game folder was copied.
    """
    from common.games.library_folders import configured

    by_id: dict[str, tuple[Any, str]] = {}
    minted = remixed = rekeyed = defaults = stamped = 0
    locations = configured()
    games = list(games)
    # Held by tables not yet reached too, so a derived id never lands on one of them.
    stored_ids = {
        table_id(entry)
        for game in games
        for entry in ((getattr(game, "meta_config", None) or {}).get(TABLES_KEY) or {}).values()
        if table_id(entry)}

    for game in games:
        stored = (getattr(game, "meta_config", None) or {}).get(TABLES_KEY)
        if not isinstance(stored, dict):
            continue

        entries = rekey_by_id(stored)
        # Same object back means there was nothing to convert.
        changed = entries is not stored
        rekeyed += changed

        game_dir = str(getattr(game, "full_path_game", "") or "")
        resolved: dict[str, dict] = {}
        for entry in entries.values():
            filename = entry_native_key(entry)
            # The entry's own id, never the map key: an id-less entry is keyed by its
            # filename, which is truthy and is not an id.
            current = table_id(entry)
            if current and current not in by_id:
                resolved[current] = entry
                by_id[current] = (game, filename)
                continue

            if current:
                other_game, other_file = by_id[current]
                logger.warning(
                    "Table id %s is used by both %s/%s and %s/%s; assigning a new id "
                    "to the latter",
                    current, other_game.game_dir_name, other_file,
                    game.game_dir_name, filename)
                remixed += 1
            else:
                minted += 1

            fresh = minted_id(game_dir, filename,
                              location_id=str(getattr(game, "location_id", "") or ""),
                              locations=locations)
            while fresh in by_id or fresh in stored_ids:
                fresh = new_id()
            resolved[fresh] = {**entry, TABLE_ID_KEY: fresh}
            by_id[fresh] = (game, filename)
            changed = True

        for entry in resolved.values():
            if game_dir and stamp_added(game_dir, entry):
                stamped += 1
                changed = True

        # The recorded default is a table id. The 2.x migration seeds it with the
        # filename 2.x described, so convert it here rather than teaching every reader
        # to accept both. A name matching no table is left alone: `default_table`
        # already falls through to one that exists.
        vpinfe = (getattr(game, "meta_config", None) or {}).get(VPINFE_SECTION)
        new_default = ""
        if isinstance(vpinfe, dict):
            recorded = str(vpinfe.get(DEFAULT_TABLE_KEY, "") or "").strip()
            if recorded and recorded not in resolved:
                new_default = entry_for_filename(resolved, recorded)[0]
                if new_default:
                    vpinfe[DEFAULT_TABLE_KEY] = new_default
                    defaults += 1
                    changed = True

        if not changed:
            continue

        game.meta_config[TABLES_KEY] = resolved
        # Re-read so unrelated sections come from disk, but replace tables outright:
        # re-deriving here would mint different ids than the ones just handed out.
        try:
            config = load_game_meta(game)
        except Exception:
            logger.exception("Could not read %s to write its table ids", game.game_dir_name)
            continue
        config[TABLES_KEY] = resolved
        if new_default:
            config.setdefault(VPINFE_SECTION, {})[DEFAULT_TABLE_KEY] = new_default
        keep_game_meta(game, config)

    if minted or remixed or rekeyed or defaults or stamped:
        logger.info("Assigned ids to %s tables, re-minted %s collisions, re-keyed %s "
                    "games, converted %s recorded defaults, dated %s tables",
                    minted, remixed, rekeyed, defaults, stamped)
    return by_id


def find_table_by_id(games: Iterable[Any], wanted: str) -> tuple[Any, str] | None:
    """(game, native key) for this table id, or None. The native key is a filename for
    a table in the folder, and `app:key` for one the app finds itself."""
    wanted = (wanted or "").strip()
    if not wanted:
        return None
    for game in games:
        for filename, found in table_ids(game).items():
            if found == wanted:
                return game, filename
    return None

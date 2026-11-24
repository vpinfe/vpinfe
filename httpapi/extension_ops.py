"""What core lets an extension do to the library.

One table, kept beside the routes rather than inside the extension host, because these
are core's functions and adding a route is the moment somebody should be deciding whether
extensions get it too.

**Every operation here is the service the matching route calls.** The route is a thin
adapter over the same function - it adds the path, the scope gate in its
`dependencies=[...]`, and the response model, and nothing else. So an extension reaches
exactly the code an HTTP client reaches, and the two cannot drift; the host cannot end up
with a smaller copy of the library, which it silently had once before. The scope gate is
not lost by going straight to the service: `offer` below names the scope for each one, and
the host is what checks it.

The names are what an extension calls them, not what the service is called. A service is
named for the noun it acts on; an extension author is reading a list of things they can do.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from common.config.paths import get_ini_config
from common.extensions.games import offer
from common.extensions.hardware import offer_hardware
from common.extensions.library_collections import offer_collection
from common.games import (
    auto_match,
    collection_ops,
    game_lens,
    game_ops,
    game_service,
    launcher_ops,
    library_folder_ops,
    library_vps_state,
    media_ops,
    table_ops,
)
from common.host import hardware, play_service
from common.i18n import t
from common.online import vps_lens, vpsdb_sync

from . import scopes


def _vps_state(game_id: str) -> dict:
    return library_vps_state.state_of(game_lens.game_or_refuse(game_id), game_id)


def _launch(game_id: str, table: str = "") -> dict:
    """Start a game. `table` picks one of its tables; left out, the default one."""
    return play_service.start(game_id, table or None)


def _set_details(game_id: str, **fields: Any) -> dict:
    return game_ops.set_details(game_id, dict(fields))


def _vps_catalog(downloading: Callable[[], object] | None = None) -> dict:
    """Whether there is a catalog to match against, fetching one first where there is none.

    The fetch is the one a library refresh makes, and `downloading` is called before it
    starts and only then, so a caller can say what it is waiting for. `enabled` is the
    *Match new games* switch: where it is off nothing is fetched, and nothing a caller
    matches by this should be written.
    """
    if not auto_match.enabled():
        return {"enabled": False, "ready": False}
    return {"enabled": True,
            "ready": vpsdb_sync.ensure_catalog(get_ini_config(), downloading)}


def _vps_settle(rows: list[dict]) -> list[dict]:
    """`auto_match.settle` over the catalog on disk, one answer per row in the same order."""
    return auto_match.settle(game_service.load_vpsdb(), rows)


def _create_collection(name: str, games: Iterable[str] = (), description: str = "") -> dict:
    """A hand-picked collection. Rules, ordering and copies are for the Manager."""
    return collection_ops.create(name, games, description)


def _rename_collection(name: str, new_name: str) -> dict:
    return collection_ops.patch(name, new_name=new_name)


def _describe_collection(name: str, description: str) -> dict:
    return collection_ops.patch(name, description=description)


# Reading is one scope and writing another. Taking an entry's details from a catalog is
# a write, because it changes the record.
#
# Launching is neither, and has its own. It takes over the cabinet rather than editing
# something, and the scope vocabulary already said so before extensions existed: reading
# what is happening is not the same as causing it to happen, and stopping a table
# somebody may be mid-game on is a third thing again. An extension that wants to start a
# game asks for that by name, and whoever installs it reads it by name.
READS: dict[str, Callable[..., Any]] = {
    "list_games": game_lens.listing,
    "get_game": game_lens.detail,
    "game_tables": table_ops.rows_of,
    "game_media": media_ops.game_media,
    "table_media": media_ops.table_media,
    "media_detail": media_ops.detail,
    "vps_state": _vps_state,
    "vps_details": game_ops.vps_details,
    "vps_catalog": _vps_catalog,
    "vps_settle": _vps_settle,
    "vps_entry": vps_lens.entry,
    "vps_releases": vps_lens.releases,
}

# Where games live is a setting, and these keep the scopes the routes under /locations
# keep: an extension that can add a library folder can already point an install at its
# library, and should ask for that by name.
CONFIG_READS: dict[str, Callable[..., Any]] = {
    "new_game_destination": library_folder_ops.destination,
    # What the program that plays a table is, so a setting is written in the form it reads.
    "table_program": launcher_ops.table_program,
}

CONFIG_WRITES: dict[str, Callable[..., Any]] = {
    "use_library_folder": library_folder_ops.use_library_folder,
    # One table's own settings, never the launcher's: what an import carries over from a
    # table's old cabinet must not change the new one.
    "write_table_settings": launcher_ops.write_table_settings,
    "use_logos_for_wheels": media_ops.use_logos_for_wheels,
}

LAUNCHES: dict[str, Callable[..., Any]] = {
    "launch_game": _launch,
}

WRITES: dict[str, Callable[..., Any]] = {
    "set_details": _set_details,
    "rate_game": game_ops.set_rating,
    "rate_table": table_ops.set_rating,
    "set_tags": game_ops.set_tags,
    "set_favorite": game_ops.set_favorite,
    # Never `set_private`: an extension turning it off would send a game's data without
    # the person, as setting Share would.
    "set_default_table": table_ops.set_default,
    "set_play_record": game_ops.set_play_record,
    "reset_play_record": game_ops.reset_play_record,
    "take_vps_details": game_ops.adopt_details,
    "match_game": game_ops.match_game,
    "remove_media": media_ops.remove,
    "get_missing_media": media_ops.get_missing_media,
    "forget_table": table_ops.forget,
    "add_keyed_table": table_ops.add_keyed,
}


# Collections have scopes of their own, the ones the routes under /collections use. Rules,
# exclusions and pictures are not offered.
COLLECTION_READS: dict[str, Callable[..., Any]] = {
    "list_collections": collection_ops.listing,
    "get_collection": collection_ops.resource,
    "collection_games": collection_ops.games_in,
    "collection_members": collection_ops.members_of,
}

COLLECTION_WRITES: dict[str, Callable[..., Any]] = {
    "create_collection": _create_collection,
    "delete_collection": collection_ops.delete,
    "add_to_collection": collection_ops.add_member,
    "remove_from_collection": collection_ops.remove_member,
    "set_collection_order": collection_ops.set_arrangement,
    "rename_collection": _rename_collection,
    "describe_collection": _describe_collection,
}


def _access_help() -> str:
    return t(hardware.access_help_key())


HARDWARE_READS: dict[str, Callable[..., Any]] = {
    "devices": hardware.list_devices,
    "access_help": _access_help,
}


def offer_all() -> None:
    """Hand the whole table to the extension host. Once, before anything loads."""
    for name, run in READS.items():
        offer(name, scopes.GAMES_READ, run)
    for name, run in WRITES.items():
        offer(name, scopes.GAMES_WRITE, run)
    for name, run in CONFIG_READS.items():
        offer(name, scopes.CONFIG_READ, run)
    for name, run in CONFIG_WRITES.items():
        offer(name, scopes.CONFIG_WRITE, run)
    for name, run in LAUNCHES.items():
        offer(name, scopes.LAUNCH_INVOKE, run)
    for name, run in COLLECTION_READS.items():
        offer_collection(name, scopes.COLLECTIONS_READ, run)
    for name, run in COLLECTION_WRITES.items():
        offer_collection(name, scopes.COLLECTIONS_WRITE, run)
    for name, run in HARDWARE_READS.items():
        offer_hardware(name, scopes.HARDWARE_READ, run)

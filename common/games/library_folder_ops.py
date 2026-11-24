"""Where this install looks for entries.

A location is an object somebody adds and removes, and the order is the priority: a game
folder carries its id, so one library reached through two locations holds every id twice,
and the folder that answers for an id is the one in the location nearer the top.

Reachable and writable are asked of the disk on every read. They are facts about this
machine at this moment, not something stored: a stored answer is wrong the moment a mount
drops.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

from common import service_errors
from common.config import path_state
from common.config.config_access import cfg_bool
from common.config.paths import get_ini_config
from common.games import game_identity, game_repository
from common.games import library_folders as locations
from common.i18n import t

logger = logging.getLogger("vpinfe.common.games.library_folder_ops")

# On this device, or on a share the OS mounted.
WHERE_LOCAL = "local"
WHERE_SYSTEM = "system"


def _described(location: locations.Location, write_to: str,
               state: path_state.PathState, shadowed: int = 0,
               games: int = 0) -> dict[str, Any]:
    origin = state.origin or location.origin
    return {
        "location_id": location.location_id,
        "path": location.path,
        "name": location.name,
        "kind": location.kind,
        "where": WHERE_SYSTEM if origin else WHERE_LOCAL,
        "origin": {**origin.as_dict(), "source": origin.source} if origin else None,
        "state": state.state,
        "reachable": state.reachable,
        "writable": state.writable,
        # Empty when there is nothing to say. A reason on every row would say nothing.
        "reason": state.reason,
        "write_to": location.location_id == write_to,
        # How many of this location's game folders hold an id a higher one is already
        # using. Nearly always zero; the case it exists for is one library reached
        # through two locations, where it is the whole count and says so.
        "shadowed": shadowed,
        # Game folders in this location, the ones a higher location is answering for
        # included: they are all here. Zero as well when the location cannot be read.
        "games": games,
    }


def _shadowed() -> Any:
    """What each location holds that something above it is answering for.

    Asked of the library on every read, like reachable and writable: whether a folder is
    shadowed depends on the other locations, so a stored answer would be wrong the
    moment one is added, removed or reordered.
    """
    return game_identity.resolve_ids(game_repository.all_games())


def _write_to(store: locations.LocationStore) -> str:
    """The location a new game would be created in, as `locations.destination()` uses it.

    The stored choice whatever its state: an away or read-only folder stays named, and the
    refusal that follows says why. Only where nothing is chosen, or the choice is gone, is
    it the first writable folder of games.
    """
    chosen = store.chosen_write_to()
    if chosen and store.get(chosen) is not None:
        return chosen
    target = store.write_to()
    return target.location_id if target is not None else ""


def _counted(games: Any) -> Counter[str]:
    return Counter(str(getattr(one, "location_id", "") or "") for one in games)


def _games_in(location_id: str) -> int:
    return _counted(game_repository.all_games())[location_id]


def _location_or_refuse(location_id: str) -> None:
    if locations.get_location_store().get(location_id) is None:
        raise service_errors.NotFoundError(
            t("error.library_folders.unknown_folder", location_id=(location_id)))


def listing() -> dict[str, Any]:
    """The locations in order, each with what the disk says about it right now.

    `write_to` is named rather than left to be worked out: a client re-deriving it is a
    second place for it to be wrong. It is what `destination()` uses, so a chosen folder
    that has gone away is still the one named.
    """
    store = locations.get_location_store()
    held = store.locations()
    write_to = _write_to(store)
    games = game_repository.all_games()
    found = game_identity.resolve_ids(games)
    held_by = _counted(games)
    states = locations.states_of(held)
    return {
        "locations": [_described(one, write_to, states[one.location_id],
                                 len(found.under(one.location_id)),
                                 held_by[one.location_id])
                      for one in held],
        "write_to": write_to,
        "kinds": list(locations.KINDS),
        # The order is the priority: an id two folders hold is answered by the one in
        # the location nearer the top. Said here rather than left to be inferred from
        # the list being a list.
        "order_is_priority": True,
        "ask_where_new_games_go": _asks(),
    }


def destination() -> dict[str, Any]:
    """Where it would go, or why it could not, and what else it could go to instead.

    Asked before an import rather than discovered by one: a destination that has gone
    read-only refuses rather than quietly becoming a different folder, and a refusal is
    only useful if it says where else this could land.

    Declared above `/{location_id}`, which would otherwise read "destination" as an id.
    """
    found = locations.destination()
    return {
        "location_id": found.location.location_id if found.location else "",
        "path": found.path,
        "name": found.location.name if found.location else "",
        "reason": found.reason,
        # Every other writable root, so a surface can offer them for this one import
        # without asking somebody to go and change a setting first.
        "alternatives": [{"location_id": one.location_id, "name": one.name,
                          "path": one.path} for one in found.alternatives],
        # Whether to ask at all. Never where there is only one place it could go: a
        # question with one answer is a click charged for nothing.
        "ask": (_asks() and bool(found.alternatives)),
    }


def _asks() -> bool:
    return cfg_bool(get_ini_config(), "updates", "ask_where_new_games_go", True)


def put(location_id: str, path: str, kind: str = "") -> dict[str, Any]:
    """Whole, under the id the caller names, the way a launcher arrives.

    An id already held is changed in place, and refuses a path some other row holds. An
    unheld id on a path already held replaces that row rather than making a second.
    """
    wanted = str(location_id or "").strip()
    if not wanted:
        raise service_errors.RefusedError(t("error.library_folders.needs_id"))
    path = str(path or "").strip()
    if not path:
        raise service_errors.RefusedError(t("error.library_folders.needs_path"))
    if not os.path.isabs(os.path.expanduser(path)):
        raise service_errors.RefusedError(t("error.library_folders.not_absolute"))
    kind = str(kind or locations.KIND_ROOT).strip()
    if kind not in locations.KINDS:
        raise service_errors.RefusedError(
            t("error.library_folders.unknown_kind", kind=(kind),
                    kinds=', '.join(locations.KINDS)))

    store = locations.get_location_store()
    try:
        written = store.put(locations.Location(location_id=wanted, path=path, kind=kind))
    except locations.FolderHeldError as held:
        raise service_errors.RefusedError(
            t("error.library_folders.duplicate", name=held.held.name)) from held
    found = game_identity.resolve_ids(game_repository.all_games())
    return _described(written, _write_to(store), locations.state_of(written),
                      len(found.under(written.location_id)), _games_in(written.location_id))


def use_library_folder(path: str) -> dict[str, Any]:
    """A folder of game folders, as a location: the one already there, or a new one.

    For a caller that has a folder and no id to name it by. A folder already held keeps
    its id and its place in the order, so asking again does not change where new games
    go; a new one goes last. Always a root: what is being asked for is a folder whose
    children are games, and a folder held as a single game is made a root by it, which
    is what the caller just said it is.
    """
    return put(locations.mint_location_id(), path, locations.KIND_ROOT)


def shadowed_in(location_id: str) -> dict[str, Any]:
    """What this location holds that is out of the library, and what took its place.

    Both sides, because the question a person brings here is which of the two copies
    they meant to keep - and that cannot be answered by naming only one of them.
    """
    _location_or_refuse(location_id)
    return {"shadowed": [
        {"game_id": one.game_id, "path": one.path,
         "used_path": one.used_path, "used_location_id": one.used_location_id}
        for one in _shadowed().under(location_id)]}


def adopt(location_id: str, path: str) -> dict[str, Any]:
    """Settle one clash by making this folder a game in its own right.

    **The one write in any of this, and it only happens because somebody asked.** The
    folder keeps everything else it has - its media, its curation, its play record - and
    gains a new id, so it stops being a copy of the other and starts being its own game.
    Anything that named the id keeps pointing at the folder that kept it.

    The other way to settle it is to remove the location, which is right when the whole
    location is a second view of one library. That is `DELETE /config/locations/{id}` and
    it writes nothing at all.
    """
    _location_or_refuse(location_id)
    wanted = path_state.canonical(str(path or ""))
    if not wanted:
        raise service_errors.RefusedError(t("error.library_folders.needs_folder"))

    found = _shadowed()
    one = next((entry for entry in found.under(location_id)
                if path_state.canonical(entry.path) == wanted), None)
    if one is None:
        raise service_errors.NotFoundError(
            t("error.library_folders.not_shadowed"),
            details={"path": str(path or "")})

    game = next((held for held in game_repository.all_games()
                 if path_state.canonical(str(held.full_path_game or ""))
                 == wanted), None)
    if game is None:
        raise service_errors.NotFoundError(t("error.library_folders.folder_removed"))
    return {"path": one.path, "game_id": game_identity.ensure_id(game, force_new=True)}

"""Stable local identity for games: an opaque id in the .info at `vpinfe.game_id`.

Addresses a game in the HTTP API, in events and in jobs. VPSId cannot do that job
and keeps its own. Reading never writes; minting is explicit. See docs/http-api.md.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from common.games.game import Game, GameRecord
from common.games.game_metadata import (
    keep_game_meta,
    load_game_meta,
    normalize_meta,
    persist_game_meta,
    section,
)
from common.games.info_file import GAME_ID_KEY, VPINFE_SECTION
from common.ids import ALPHABET as ID_ALPHABET
from common.ids import LENGTH as ID_LENGTH
from common.ids import derive_id, new_id

logger = logging.getLogger("vpinfe.common.games.game_identity")

# Also written by MetaConfig.write_config_meta, which mints during a metadata rebuild.
ID_SECTION = VPINFE_SECTION
ID_KEY = GAME_ID_KEY

__all__ = ["ID_ALPHABET", "ID_LENGTH", "ID_KEY", "ID_SECTION", "new_id",
           "game_id", "ensure_id", "minted_id", "ensure_unique_ids", "resolve_ids", "Resolution",
           "Shadowed", "find_by_id"]


def game_id(game: GameRecord) -> str:
    """The table's id, or "" if it hasn't been assigned one. Never writes."""
    meta = normalize_meta(getattr(game, "meta_config", {}))
    return str(section(meta, ID_SECTION).get(ID_KEY, "") or "").strip()


def _vpinfe_section(config: dict[str, Any]) -> dict[str, Any]:
    existing = config.get(ID_SECTION)
    if not isinstance(existing, dict):
        existing = {}
        config[ID_SECTION] = existing
    return existing


def minted_id(folder: str, *rest: str, location_id: str = "",
              locations: Sequence[Any] | None = None) -> str:
    """The id a game folder with none is given: the same on every call for the same
    location and path inside it, then `rest`. Random for a folder no location holds.

    `location_id` picks among nested locations that all hold the folder; none given, or none
    matching, the first one listed wins. `locations` spares a batch the lookup per folder.
    """
    from common.games.library_folders import configured, inside

    if not folder:
        return new_id()
    where = os.path.normpath(os.path.abspath(folder))
    holding = []
    for location in configured() if locations is None else locations:
        root = os.path.normpath(os.path.abspath(location.path))
        if inside(root, where):
            holding.append((location, root))
    if not holding:
        return new_id()
    location, root = next((one for one in holding if one[0].location_id == location_id),
                          holding[0])
    within = "" if where == root else os.path.relpath(where, root).replace(os.sep, "/")
    return derive_id(location.location_id, within, *rest)


def _unclaimed(wanted: str, taken: set[str] | None) -> str:
    """`wanted`, or a random id when something in the library already holds it: a renamed
    folder keeps its old id, which a new folder at the old path would derive."""
    while taken is not None and wanted in taken:
        wanted = new_id()
    return wanted


def ensure_id(game: Game, *, force_new: bool = False) -> str:
    """The table's id, minting and persisting one if it has none.

    Re-reads from disk first so a stale in-memory copy isn't written back. Raises if
    the write fails: an id that isn't on disk isn't an identity.
    """
    return _ensure(game, force_new, persist_game_meta)


def _ensure(game: Game, force_new: bool,
            write: Callable[[Game, dict[str, Any]], None], *,
            locations: Sequence[Any] | None = None, taken: set[str] | None = None) -> str:
    if not force_new:
        existing = game_id(game)
        if existing:
            return existing

    config = load_game_meta(game)
    vpinfe = _vpinfe_section(config)
    existing = str(vpinfe.get(ID_KEY, "") or "").strip()
    if existing and not force_new:
        # Present on disk but not in the loaded copy; adopt it rather than mint.
        game.meta_config = config
        return existing

    # A re-issued id is random: the folder's own path would give the same id back.
    minted = new_id() if force_new else _unclaimed(minted_id(
        str(game.full_path_game or ""), location_id=str(getattr(game, "location_id", "") or ""),
        locations=locations), taken)
    vpinfe[ID_KEY] = minted
    write(game, config)
    logger.debug("Assigned game id %s to %s", minted, game.game_dir_name)
    return minted


@dataclass(frozen=True)
class Shadowed:
    """A game folder whose id another folder is already using.

    Named for what happened to it rather than for the fault: nothing is wrong with this
    folder, and nothing has been done to it. Another one answers for its id, so this one
    is out of the library until somebody says which should.
    """

    game_id: str
    path: str
    location_id: str
    # The folder that is answering for the id instead, so the report can say what won.
    used_path: str
    used_location_id: str


@dataclass(frozen=True)
class Resolution:
    """Which folder answers for each id, and which were shadowed doing it."""

    by_id: dict[str, Any]
    shadowed: tuple[Shadowed, ...] = ()

    def under(self, location_id: str) -> tuple[Shadowed, ...]:
        """What this location holds that something else is answering for."""
        return tuple(one for one in self.shadowed
                     if one.location_id == str(location_id or ""))


def in_priority(games: Iterable[Any], order: Sequence[str] | None = None) -> list[Any]:
    """Games in the order their claim on an id is honoured.

    Locations are a list and that list is the priority: the earlier location wins, which
    is the same rule `LocationStore.write_to` already applies when it falls back to the
    first writable one. Within one location there is no priority to appeal to, so the
    tie goes to the first by path - arbitrary, but the same answer on every run, which
    directory order is not.
    """
    if order is None:
        from common.games.library_folders import configured

        order = [one.location_id for one in configured()]
    rank = {location_id: index for index, location_id in enumerate(order)}
    # A location the list does not name goes last rather than first: it is not part of
    # the answer somebody arranged, so it cannot outrank what is.
    unranked = len(rank)
    return sorted(
        games,
        key=lambda game: (rank.get(str(getattr(game, "location_id", "") or ""),
                                  unranked),
                          str(game.full_path_game or "")))


def resolve_ids(games: Iterable[Any],
                order: Sequence[str] | None = None) -> Resolution:
    """Which folder answers for each id. **Nothing is rewritten to settle a clash.**

    A folder carries its id, so two folders holding one means the same game is in the
    library twice - a copy, or the same tree reached through two locations. Re-minting
    the loser would settle it silently, and that is a write to somebody's file to
    resolve something only they can: it makes the copy a different game for good, and
    anything that named it goes with it.

    So the higher-priority location answers, the rest are reported, and the decision
    waits for a person. Ids are still minted for folders that have none - that is not a
    clash, and a game with no id cannot be addressed at all.
    """
    from common.games.library_folders import configured

    by_id: dict[str, Any] = {}
    holder: dict[str, Any] = {}
    shadowed: list[Shadowed] = []
    minted = 0
    locations = configured()
    games = list(games)
    taken = {one for one in (game_id(game) for game in games) if one}

    if order is None:
        order = [one.location_id for one in locations]

    for game in in_priority(games, order):
        current = game_id(game)
        if not current:
            current = _ensure(game, False, keep_game_meta, locations=locations, taken=taken)
            taken.add(current)
            minted += 1
        if current in by_id:
            first = holder[current]
            shadowed.append(Shadowed(
                game_id=current,
                path=str(game.full_path_game or ""),
                location_id=str(getattr(game, "location_id", "") or ""),
                used_path=str(first.full_path_game or ""),
                used_location_id=str(getattr(first, "location_id", "") or "")))
            continue
        by_id[current] = game
        holder[current] = game

    if minted:
        logger.info("Assigned ids to %s of %s games", minted, len(by_id))
    if shadowed:
        logger.warning(
            "%s game folder(s) hold an id another folder is already using; they are "
            "out of the library until somebody says which should have it", len(shadowed))
    return Resolution(by_id=by_id, shadowed=tuple(shadowed))


def ensure_unique_ids(games: Iterable[Any],
                      order: Sequence[str] | None = None) -> dict[str, Any]:
    """Every game that answers for an id, keyed by it. The shadowed ones are not here -
    `resolve_ids` is what reports those."""
    return resolve_ids(games, order).by_id


def find_by_id(games: Iterable[Any], wanted: str) -> Any | None:
    """The game with this id, or None. A game with no id can't match."""
    wanted = (wanted or "").strip()
    if not wanted:
        return None
    for game in games:
        if game_id(game) == wanted:
            return game
    return None

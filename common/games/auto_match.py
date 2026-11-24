"""Matching a game to its catalog entry from its folder name, where no person has.

From the catalog on disk only: every caller runs where the network is not allowed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from common.config.config_access import cfg_bool
from common.config.paths import get_ini_config
from common.games import game_service
from common.games.game_metadata import (
    declared_no_match,
    dissolve_agreed_overrides,
    effective_vps_id,
    keep_game_meta,
    load_game_meta,
    merge_guides,
    normalize_meta,
    persist_game_meta,
    record_vps_match,
    vps_matched_by,
)
from common.games.info_file import GUIDES_KEY, guides_from_vps, info_from_vps, ipdb_of
from common.online.vpsdb import guess, lookup, parse_folder_name
from common.uploads.asset_import_service import vps_folder_name

logger = logging.getLogger("vpinfe.common.games.auto_match")


def enabled() -> bool:
    return cfg_bool(get_ini_config(), "updates", "match_new_games", True)


def unmatched(meta: dict[str, Any]) -> bool:
    """No entry in force, and nobody has said there is none."""
    return not effective_vps_id(meta) and not declared_no_match(meta)


def adopt_guess(game: Any, entry: dict[str, Any],
                write: Callable[[Any, dict[str, Any]], None] = persist_game_meta,
                matched_by: str = "") -> None:
    """Write `entry` as the game's match from its folder name.

    The match alone: tables and a person's overrides are left as they are. `matched_by`
    is how the entry was chosen, `""` for a guess.
    """
    config = load_game_meta(game)
    config["Info"] = info_from_vps(entry)
    config[GUIDES_KEY] = merge_guides(config.get(GUIDES_KEY) or [], guides_from_vps(entry))
    record_vps_match(config, matched_by)
    dissolve_agreed_overrides(config)
    write(game, config)


def match_new(games: Iterable[Any], catalog: list[dict] | None = None) -> dict[str, int]:
    """Guess each of `games` that nothing has matched, and count the result.

    `games` is every game handed in, `matched` those with an entry in force afterwards and
    `unmatched` those still waiting for one. A declared no-match is neither.
    """
    games = list(games)
    if games and enabled():
        held = game_service.load_vpsdb() if catalog is None else catalog
        for game in games:
            if not unmatched(normalize_meta(game.meta_config or {})):
                continue
            entry = guess(held, str(game.game_dir_name or ""))
            if entry is None:
                continue
            try:
                adopt_guess(game, entry, keep_game_meta)
            except Exception:
                logger.exception("Could not match %s", game.game_dir_name)
    metas = [normalize_meta(game.meta_config or {}) for game in games]
    return {"games": len(games),
            "matched": sum(1 for meta in metas if effective_vps_id(meta)),
            "unmatched": sum(1 for meta in metas if unmatched(meta))}


def match_again(games: Iterable[Any],
                catalog: list[dict] | None = None) -> tuple[dict[str, int], list[Any]]:
    """Guess each of `games` again, except where a person made the match or declared none.

    A guess that finds nothing leaves the match as it was. Answers the counts - `changed`
    the games whose match moved, `unmatched` those still without one, `yours` those left
    alone for a person's match - and the games that moved.
    """
    games = list(games)
    held = game_service.load_vpsdb() if catalog is None else catalog
    moved: list[Any] = []
    yours = 0
    for game in games:
        meta = normalize_meta(game.meta_config or {})
        if vps_matched_by(meta):
            yours += 1
            continue
        entry = guess(held, str(game.game_dir_name or ""))
        if entry is None or str(entry.get("id") or "") == effective_vps_id(meta):
            continue
        try:
            adopt_guess(game, entry)
        except Exception:
            logger.exception("Could not write the match for %s", game.game_dir_name)
            continue
        moved.append(game)
    waiting = sum(1 for game in games if unmatched(normalize_meta(game.meta_config or {})))
    return ({"games": len(games), "changed": len(moved), "unmatched": waiting,
             "yours": yours}, moved)


# What a look can say about a game it could not settle, for the person who settles it.
NOT_FOUND = "not_found"
TWO_ENTRIES = "two_entries"
IPDB_DIFFERS = "ipdb_differs"

CANDIDATES_SHOWN = 5


def _said(entry: dict[str, Any]) -> dict[str, str]:
    return {"id": str(entry.get("id") or ""), "name": str(entry.get("name") or ""),
            "manufacturer": str(entry.get("manufacturer") or ""),
            "year": str(entry.get("year") or ""), "type": str(entry.get("type") or ""),
            "ipdb_id": ipdb_of(entry), "folder_name": vps_folder_name(entry)}


def settle(catalog: list[dict], rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entry each of `rows` is, where it can be told, and why not where it cannot.

    A row carries what a source knows - `vps_id`, `ipdb_id`, `title`, `manufacturer`,
    `year` and `folder`, the name it came in, read where the title is missing - and is
    answered in the same order with `vps_id` set and `how` naming the test that settled it
    (`vps_id`, `ipdb` or `name`), or `vps_id` empty and `reason` one of `not_found`,
    `two_entries` and `ipdb_differs` with up to five `candidates`.

    A number is tried before the name. One that points at one entry while the name points
    at another settles nothing, and says `ipdb_differs`.

    Reads the catalog it is handed and writes nothing.
    """
    by_id = {str(one.get("id") or ""): one for one in catalog}
    by_ipdb: dict[str, list[dict]] = {}
    for one in catalog:
        number = ipdb_of(one)
        if number:
            by_ipdb.setdefault(number, []).append(one)
    return [_settled(catalog, by_id, by_ipdb, row) for row in rows]


def _settled(catalog: list[dict], by_id: dict[str, dict], by_ipdb: dict[str, list[dict]],
             row: dict[str, Any]) -> dict[str, Any]:
    def found(entry: dict, how: str) -> dict[str, Any]:
        return {"vps_id": str(entry.get("id") or ""), "how": how, "reason": "",
                "candidates": [], "entry": _said(entry)}

    def left(reason: str, entries: list[dict]) -> dict[str, Any]:
        return {"vps_id": "", "how": "", "reason": reason, "entry": {},
                "candidates": [_said(one) for one in entries[:CANDIDATES_SHOWN]]}

    said = str(row.get("vps_id") or "").strip()
    if said and said in by_id:
        return found(by_id[said], "vps_id")

    name = {"name": str(row.get("title") or ""),
            "manufacturer": str(row.get("manufacturer") or ""),
            "year": row.get("year") or ""}
    if not (name["name"] and name["manufacturer"] and name["year"]):
        parsed = parse_folder_name(str(row.get("folder") or ""))
        if parsed:
            name = parsed

    shared = by_ipdb.get(str(row.get("ipdb_id") or "").strip(), [])
    if len(shared) == 1:
        entry = shared[0]
        if lookup(shared, **name) is entry:
            return found(entry, "ipdb")
        # Only now is the whole catalog read for the name: an entry whose own name agrees
        # with the source's needs no second opinion.
        other = lookup(catalog, **name)
        if other is not None and other is not entry:
            return left(IPDB_DIFFERS, [entry, other])
        return found(entry, "ipdb")
    if shared:
        chosen = lookup(shared, **name)
        return found(chosen, "ipdb") if chosen else left(TWO_ENTRIES, shared)

    chosen = lookup(catalog, **name)
    return found(chosen, "name") if chosen else left(NOT_FOUND, [])

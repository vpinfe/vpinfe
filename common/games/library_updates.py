"""What the library holds that has a later version: the games Updates for Your Games lists.

An update is a release a file is tied to whose version VPS now lists higher than it listed
when the tie was made (`ties.update_available`), never the file's own version. The ties are
read where they are kept - the `source` block of each table, and of each file in the assets
ledger - and so are the other sources' answers to "what is later for this game": a replaced
picture. A table's move is not counted where another file of the game is already that
release at the listed version or later: the newer file has taken its place.

Read from the library as it stands and the catalog as loaded, with no request and nothing
stored, so an update clears the moment a person ties the release again.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from common.games import asset_origin, game_repository, game_service, ties
from common.games.game_metadata import effective_vps_id, game_title, normalize_meta, table_source
from common.games.tables import ABSENT_SINCE_KEY, entry_filename, table_entries
from common.online import sources, vps_lens, vps_releases
from common.values import newer_version, parse_version

logger = logging.getLogger("vpinfe.common.games.library_updates")

TABLES = "tableFiles"
# The source an update of a tied release is reported under: the spreadsheet.
VPS = "vps"

_located: tuple[Any, dict[str, tuple[str, str, str]]] | None = None


def _releases() -> dict[str, tuple[str, str, str]]:
    """Every release the catalog lists, by id: the entry it is listed under, the kind it
    is listed as, and the version VPS lists now. Built once per catalog load."""
    global _located
    built = vps_releases.index()
    if _located is None or _located[0] is not built:
        found: dict[str, tuple[str, str, str]] = {}
        for vps_id, listed in built["entries"].items():
            for listing, releases in listed.items():
                for release in releases:
                    found.setdefault(release["id"], (vps_id, listing, release["version"]))
        _located = (built, found)
    return _located[1]


def _moved(source: dict[str, Any], file: str, listing_of: dict[str, tuple[str, str, str]],
           ) -> dict[str, Any] | None:
    """The move a tie records, or None where the release is not later than it was."""
    release_id = str(source.get(ties.FILE_KEY) or "")
    if release_id not in listing_of:
        return None
    vps_id, listing, now = listing_of[release_id]
    if not ties.update_available({**source, "version": now}):
        return None
    return {"source": VPS, "kind": listing, "release_id": release_id, "vps_id": vps_id,
            "file": file, "then": str(source.get(ties.VERSION_KEY) or ""), "now": now}


def _reaches(other: dict[str, Any], release_id: str, now: str) -> bool:
    """Whether another held table is this release at the version VPS lists now or later.

    A table tied to it counts by its tie, an untied one by its own version, so a newer file
    imported under another name stands in before it is tied. A table tied to another
    release, declined, or made by a patch is not this release.
    """
    source = table_source(other)
    held = str(source.get(ties.FILE_KEY) or "")
    if held:
        have = parse_version(source.get(ties.VERSION_KEY)) if held == release_id else ()
    elif ties.declined(source) or source.get("base") or source.get("patch"):
        return False
    else:
        have = parse_version(other.get("version"))
    return bool(have) and not newer_version(parse_version(now), have)


def _table_moves(game: Any, listing_of: dict[str, tuple[str, str, str]]) -> list[dict[str, Any]]:
    held = [one for one in table_entries(getattr(game, "meta_config", None)).values()
            if isinstance(one, dict) and not one.get(ABSENT_SINCE_KEY)]
    out = []
    for entry in held:
        source = table_source(entry)
        release = game_service.find_vps_release(str(source.get(ties.FILE_KEY) or ""))
        # A build this table cannot be is no release of it, as the table's row reads it.
        if not release or not game_service.release_fits(release, entry):
            continue
        move = _moved(source, entry_filename(entry), listing_of)
        if move is None or move["kind"] != TABLES:
            continue
        if any(other is not entry and game_service.release_fits(release, other)
               and _reaches(other, move["release_id"], move["now"]) for other in held):
            continue
        out.append(move)
    return out


def _file_moves(game_dir: Path, listing_of: dict[str, tuple[str, str, str]],
                ) -> list[dict[str, Any]]:
    """The moves of the files the ledger ties to a release. A file no longer on disk is
    none: the ledger keeps its entry after the file is deleted."""
    out = []
    for key, source in asset_origin.sources(game_dir).items():
        if not (game_dir / key).is_file():
            continue
        move = _moved(source, Path(key).name, listing_of)
        if move is not None:
            out.append(move)
    return out


def _once(moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two files tied to the same release at the same version are one move, the first named."""
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for move in moves:
        seen.setdefault((move["source"], move["release_id"], move["then"], move["now"]), move)
    return list(seen.values())


def _later(who: dict[str, str], kinds: Iterable[str],
           among: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    """What the other sources say is later for the game `who` names, one entry for each
    source and kind, with `file` the game's folder. A source names the version it has as
    `later` when it has one, and a picture it has replaced by its `md5`."""
    out = []
    for kind in kinds:
        for source_id, answer in sources.upgrades(who, kind, among).items():
            if source_id == VPS:
                continue
            found = answer if isinstance(answer, dict) else {}
            out.append({"source": source_id, "kind": kind, "release_id": "",
                        "vps_id": who.get("vps_id", ""), "file": who.get("folder", ""),
                        "then": "", "now": str(found.get("later") or ""),
                        "md5": str(found.get("md5") or "")})
    return out


def of_game(game: Any, game_id: str, listing_of: dict[str, tuple[str, str, str]],
            kinds: Iterable[str], among: Iterable[Any] | None = None) -> dict[str, Any] | None:
    """One game's moves, or None where nothing it holds has a later version. `among` is the
    sources to ask what is later, where the caller has them already."""
    meta = normalize_meta(getattr(game, "meta_config", None) or {})
    vps_id = effective_vps_id(meta)
    folder = str(getattr(game, "game_dir_name", "") or "")
    game_dir = Path(str(getattr(game, "full_path_game", "") or ""))
    moves = _once([*_table_moves(game, listing_of), *_file_moves(game_dir, listing_of)])
    moves += _later({"game_id": game_id, "vps_id": vps_id, "folder": folder}, kinds, among)
    if not moves:
        return None
    entry = game_service.matched_vps_entry(game)
    return {"game_id": game_id,
            "vps_id": vps_id or str(next((one["vps_id"] for one in moves if one["vps_id"]), "")),
            "game": game_title(game),
            "img_url": vps_lens.picture(entry) if entry else "",
            "moves": moves}


def found_for(game_id: str) -> list[dict[str, Any]]:
    """`found` for one game: the others are not looked at. Empty for a game the library
    does not hold."""
    game = game_repository.catalog().get(game_id)
    if game is None:
        return []
    among = sources.upgrade_sources()
    one = of_game(game, game_id, _releases(), sorted(sources.upgrade_kinds()), among)
    return [one] if one is not None else []


def found() -> list[dict[str, Any]]:
    """Every game of the library with something later, each with the moves behind it, in
    the order of the library. Marks are not applied: `vps_updates.shelf` does that."""
    listing_of = _releases()
    kinds = sorted(sources.upgrade_kinds())
    among = sources.upgrade_sources()
    out = []
    for game_id, game in game_repository.catalog().items():
        try:
            one = of_game(game, game_id, listing_of, kinds, among)
        except Exception:  # noqa: BLE001 - one game that cannot be read costs its own tile
            logger.warning("Could not look for updates to %s", game_id, exc_info=True)
            continue
        if one is not None:
            out.append(one)
    return out

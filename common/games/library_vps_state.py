"""What the catalog lists against what the library holds, per game and across the library.

`state_of` answers one game. The rollup asks the same question of every game, which is
the only way to see that a kind is held by nothing at all - and it counts `state_of`'s
answer rather than forming a second opinion of its own.

On a job, because resolving media for every game is slow enough to be felt.
Resolving only the kinds this needs is not the fix it looks like: virtual kinds borrow
from `logo` and a `fallback_kind` borrows another kind's winner, so a filtered resolve
answers differently rather than faster.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from common import timestamps
from common.atomic_write import write_atomic
from common.games import (
    asset_origin,
    asset_registry,
    asset_resolver,
    game_lens,
    game_repository,
    game_service,
    library_policy,
    media_service,
)
from common.games.game import Game
from common.games.game_metadata import effective_vps_id, game_vps_id, normalize_meta
from common.games.game_service import load_vpsdb
from common.games.media_service import CACHE_DIR
from common.games.tables import ABSENT_SINCE_KEY, entry_filename, table_entries
from common.i18n import t
from common.online import obtainability, vps_kinds, vps_lens, vps_marks, vps_releases
from common.runtime.jobs import JobReporter

logger = logging.getLogger("vpinfe.common.games.library_vps_state")

ROLLUP_PATH = CACHE_DIR / "vps-rollup.json"
SCHEMA = 3
TABLES = "tableFiles"


@lru_cache(maxsize=1)
def _crowded_links_for(size: int) -> frozenset[str]:
    """The links standing behind enough records to be somewhere to browse.

    Keyed on the catalog's length, which is a cheap stand-in for "the snapshot has been
    replaced" - walking every URL in the catalog on each request answers a question
    about the corpus that changes only when the corpus does.
    """
    return obtainability.crowded(
        link.get("url")
        for entry in load_vpsdb()
        for kind in vps_kinds.BY_LISTING
        for record in (entry.get(kind) or [])
        for link in (record.get("urls") or []))


def _crowded_links() -> frozenset[str]:
    return _crowded_links_for(len(load_vpsdb()))


def _we_hold(kind: vps_kinds.VpsKind, inventory: dict, media: dict) -> bool:
    """Whether this game has any of what the entry is offering.

    Any, not all: a kind maps to more than one of ours where VPS draws the line in a
    different place, and holding either Serum or VNI is holding a colorization.
    """
    for name in kind.ours:
        if kind.held_in == vps_kinds.MEDIA:
            if (media.get(name) or {}).get("present"):
                return True
        elif (inventory.get(asset_registry.lens_kind(name)) or {}).get("present"):
            return True
    return False


def _roms(game_dir: Path) -> dict[str, Any]:
    """The game's ROM zips, as an inventory slot. `inventory_assets` has none: a ROM lives
    in a folder of its own, not beside the table, and its origin is recorded under the
    folder-relative path, which is what `origin` carries."""
    zips = sorted(name for name in asset_resolver.list_rom_files(str(game_dir))
                  if name.lower().endswith(".zip"))
    folder = asset_resolver.ROMS_FOLDER
    return {"present": bool(zips),
            "files": [{"file": name, "origin": f"{folder}/{name}"} for name in zips]}


# What a kind held as a folder is called on disk; the inventory says only that it is there.
_FOLDER_NAMES = {"pup_pack": "pupvideos", "altcolor_serum": "serum", "altcolor_vni": "vni"}


def _held_file(name: str, vps_file_id: str) -> dict[str, str]:
    return {"name": name, "vps_file_id": vps_file_id}


def _bound(recorded: dict[str, dict], key: str) -> str:
    return str((recorded.get(key) or {}).get("vps_file_id") or "")


def _held_files(kind: vps_kinds.VpsKind, game_dir: Path, inventory: dict, media: dict,
                recorded: dict[str, dict]) -> list[dict[str, str]]:
    """What the game holds of this kind, each by the name on disk and the VPS record it is
    bound to ("" for none). A kind held as a folder is its usual folder name, not the
    folder's casing on disk, which the inventory does not carry."""
    found: list[dict[str, str]] = []
    for name in kind.ours:
        if kind.held_in == vps_kinds.MEDIA:
            slot = media.get(name) or {}
            if slot.get("present") and slot.get("file"):
                found.append(_held_file(str(slot["file"]), str(slot.get("matched_to") or "")))
            continue
        slot = inventory.get(asset_registry.lens_kind(name)) or {}
        if slot.get("files"):
            found.extend(_held_file(str(one["file"]),
                                    _bound(recorded, str(one.get("origin") or one["file"])))
                         for one in slot["files"])
        elif name == "altsound" and slot.get("present"):
            folder = asset_registry.altsound_folder(
                game_dir, asset_resolver.folder_listing(game_dir)[1]) or "altsound"
            found.append(_held_file(folder, _bound(recorded, folder)))
        elif slot.get("present") and name in _FOLDER_NAMES:
            folder = _FOLDER_NAMES[name]
            formats = slot.get("formats")
            if formats is None or folder in formats:
                found.append(_held_file(folder, _bound(recorded, folder)))
    return found


def held_tables(game: Game) -> list[dict[str, str]]:
    """The game's tables that are on disk, by file name and the release each is bound to."""
    return [_held_file(entry_filename(one), str((one.get("source") or {}).get("vps_file_id")
                                                 or ""))
            for one in table_entries(getattr(game, "meta_config", None)).values()
            if isinstance(one, dict) and not one.get(ABSENT_SINCE_KEY) and entry_filename(one)]


def state_of(game: Game, game_id: str = "") -> dict:
    """One game's state. The same answer the endpoint serves and the rollup counts,
    so the two cannot form separate opinions.

    `game_id` addresses the media links.
    """
    entry = game_service.matched_vps_entry(game)
    game_dir = Path(str(game.full_path_game))
    inventory = {**game_lens.inventory_assets(game_dir), "rom": _roms(game_dir)}
    prefix = f"/api/v1/games/{game_id}/media"
    media = media_service.media_entries(
        media_service.resolved_media(game_dir, None), game_dir, prefix)
    shared = _crowded_links()
    # A record id is only in one kind's list, so the ids alone place every binding.
    recorded = asset_origin.sources(game_dir)
    bound = {str(source.get("vps_file_id") or "") for source in recorded.values()}
    bound.discard("")

    kinds = []
    for kind in vps_kinds.KINDS:
        records = list(entry.get(kind.listed_as) or []) if entry else []
        answers = [obtainability.best_of(
            [link.get("url") for link in (record.get("urls") or [])], shared)
            for record in records]
        kinds.append({
            "kind": kind.listed_as,
            "ours": list(kind.ours),
            "held_in": kind.held_in,
            "held": _we_hold(kind, inventory, media),
            "files": _held_files(kind, game_dir, inventory, media, recorded),
            "identified": any(str(record.get("id") or "") in bound
                              for record in records),
            "listed": len(records),
            "obtainable": sum(1 for word in answers
                              if word == obtainability.AVAILABLE),
            "why_not": sorted({word for word in answers
                               if word != obtainability.AVAILABLE}),
        })
    return {"matched": bool(entry), "vps_id": str(entry.get("id") or ""), "kinds": kinds,
            "tables": held_tables(game)}


def stored() -> dict[str, Any]:
    """The last rollup, or empty for one that has never run. Empty is not zero: a
    consumer must not read "never counted" as "you own none of these"."""
    try:
        data = json.loads(ROLLUP_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("Could not read %s; treating it as never computed", ROLLUP_PATH)
        return {}
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else {}


def store(rollup: dict[str, Any]) -> None:
    """Written whole and atomically - a half-written rollup reads as a plausible one,
    and the numbers would be believed."""
    ROLLUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_atomic(ROLLUP_PATH,
                     lambda out: json.dump({"schema": SCHEMA, **rollup}, out, indent=2))
    except Exception:
        logger.exception("Could not write %s", ROLLUP_PATH)


_TALLIES = ("holding", "identified", "listed", "obtainable")


def _entry_of(entries: dict[str, dict[str, Any]], state: dict) -> dict[str, Any] | None:
    """What the library holds of the catalog entry this game's state names, or None for a
    game matched to nothing. Several games can name one entry, and the entry holds what
    any of them does."""
    vps_id = str(state.get("vps_id") or "")
    if not (state.get("matched") and vps_id):
        return None
    return entries.setdefault(vps_id, {"tables": 0, "held": set(), "bound": set(),
                                       "games": set()})


def _hold(held: dict[str, Any], game_id: str, game: Game, state: dict) -> None:
    """Add what one game holds to the entry it names. The one place an entry is built from a
    game, for the count of the whole library and for the follow-up of a single game."""
    known = {kind.listed_as for kind in vps_kinds.KINDS}
    held["games"].add(game_id)
    held["tables"] += sum(1 for one in table_entries(getattr(game, "meta_config", None)).values()
                          if not one.get(ABSENT_SINCE_KEY))
    held["bound"].update(_bound_ids(state))
    held["held"].update(str(item.get("kind") or "") for item in state.get("kinds") or []
                        if item.get("held") and str(item.get("kind") or "") in known)


def _stored_entry(found: dict[str, Any]) -> dict[str, Any]:
    return {"tables": found["tables"], "held": sorted(found["held"]),
            "bound": sorted(found["bound"]), "games": sorted(found["games"])}


def _bound_ids(state: dict) -> set[str]:
    held = [*(state.get("tables") or []),
            *(one for item in state.get("kinds") or [] for one in item.get("files") or [])]
    return {str(one.get("vps_file_id") or "") for one in held} - {""}


def compute(games: dict, per_game: Callable[[Game, str], dict],
            reporter: JobReporter | None = None) -> dict[str, Any]:
    """Count every kind across the library, from `{game_id: game}`.

    Keyed by id because `per_game` addresses a game's media by it. `per_game` answers one
    game's state, so the two cannot drift. A game that cannot be read still counts in
    `games` - dropping it would shrink the denominator silently.
    """
    counts = {kind.listed_as: dict.fromkeys(_TALLIES, 0)
              for kind in vps_kinds.KINDS}
    entries: dict[str, dict[str, Any]] = {}
    matched = 0
    total = len(games)
    for index, (game_id, game) in enumerate(games.items()):
        if reporter is not None:
            reporter.progress(index, total, t("said.vps_compare"))
        try:
            state = per_game(game, game_id)
        except Exception:
            logger.warning("Could not read VPS state for %s", game_id, exc_info=True)
            continue
        matched += 1 if state.get("matched") else 0
        held = _entry_of(entries, state)
        if held is not None:
            _hold(held, game_id, game, state)
        for item in state.get("kinds") or []:
            tally = counts.get(str(item.get("kind") or ""))
            if tally is None:
                continue
            tally["holding"] += 1 if item.get("held") else 0
            tally["identified"] += 1 if item.get("identified") else 0
            tally["listed"] += 1 if int(item.get("listed") or 0) else 0
            tally["obtainable"] += 1 if int(item.get("obtainable") or 0) else 0
    return {
        "computed": timestamps.utc_now_iso(),
        "games": total,
        "matched": matched,
        "entries": {vps_id: _stored_entry(found) for vps_id, found in entries.items()},
        "kinds": [{"kind": kind.listed_as, "ours": list(kind.ours),
                   "held_in": kind.held_in, **counts[kind.listed_as]}
                  for kind in vps_kinds.KINDS],
    }


def discovery() -> dict[str, Any]:
    """Every catalog entry against what the last count found the library holding.

    `counted` empty means the library has never been counted: `in_library` is then None on
    every row, because an entry nobody has checked is not an entry you do not own. `held`
    names the kinds a game of the library holds for the entry, in the catalog's own keys,
    with `tableFiles` standing for a playable table. Every row has `listed`, so a consumer
    can say "missing" only for what VPS lists. `releases` is the entry's releases by listing
    (see `vps_releases`), each saying whether a file or table of the library is bound to
    it, and `people` is the one list their `by` positions point into.
    """
    counted = stored()
    built = vps_releases.index()
    held_by = counted.get("entries") or {}
    known = bool(counted)
    rows = []
    for entry in sorted(load_vpsdb(), key=lambda one: (str(one.get("name") or "").lower(),
                                                      str(one.get("year") or ""))):
        vps_id = str(entry.get("id") or "")
        found = held_by.get(vps_id)
        held = list(found["held"]) if found else []
        if found and found["tables"]:
            held.insert(0, TABLES)
        rows.append({
            "vps_id": vps_id,
            "name": entry.get("name"),
            "manufacturer": entry.get("manufacturer"),
            "year": entry.get("year"),
            "type": entry.get("type"),
            "themes": [str(word) for word in entry.get("theme") or []
                       if isinstance(entry.get("theme"), list)],
            "url": vps_lens.entry_address(vps_id),
            "img_url": vps_lens.picture(entry),
            "listed": [kind for kind in (TABLES, *vps_kinds.BY_LISTING) if entry.get(kind)],
            "in_library": (found is not None) if known else None,
            "held": held,
            "releases": vps_releases.of_entry(built, vps_id, (found or {}).get("bound") or ()),
        })
    return {"counted": str(counted.get("computed") or ""),
            "people": built["people"], "rows": rows}


def kept_listings() -> list[str]:
    """The VPS kinds this library collects, the table first. The table has no switch."""
    kept = library_policy.kept_kinds()
    switchable = {kind.listed_as: kind for kind in vps_kinds.KINDS}
    return [listed for listed in (TABLES, *switchable)
            if listed not in switchable
            or set(switchable[listed].ours) & kept[switchable[listed].held_in]]


def note_in_library(vps_id: str) -> None:
    """Record that the library now holds `vps_id`, so a game added from the catalog reads as
    in the library before the next count. A library never counted stays never counted."""
    counted = stored()
    if not counted or not vps_id:
        return
    entries = dict(counted.get("entries") or {})
    entries.setdefault(vps_id, {"tables": 0, "held": [], "bound": [], "games": []})
    counted.pop("schema", None)
    store({**counted, "entries": entries})


def ties_of(game: Game) -> tuple:
    """What ties a game to the catalog: the entry in force and, for each table, its file, the
    release it is tied to and whether it is gone. Two reads that differ here are a match,
    an unmatch or a replaced table."""
    meta = normalize_meta(getattr(game, "meta_config", None) or {})
    return (effective_vps_id(meta),
            tuple(sorted((str(table_id), str(entry_filename(one) or ""),
                          str((one.get("source") or {}).get("vps_file_id") or ""),
                          bool(one.get(ABSENT_SINCE_KEY)))
                         for table_id, one in table_entries(meta).items()
                         if isinstance(one, dict))))


def counted_as(game: Game, game_id: str) -> bool:
    """Whether the stored count already has this game under the entry it names now, with
    the releases its tables are tied to. Read from the stored count and the game's own
    record alone, so it is cheap enough to ask of a game nothing is known about. True for a
    library never counted."""
    counted = stored()
    if not counted:
        return True
    entries = counted.get("entries") or {}
    meta = normalize_meta(getattr(game, "meta_config", None) or {})
    named = effective_vps_id(meta)
    under = {vps_id for vps_id, one in entries.items() if game_id in (one.get("games") or ())}
    if under != ({named} if named in entries else set()):
        return False
    tied = {str((one.get("source") or {}).get("vps_file_id") or "")
            for one in table_entries(meta).values()
            if isinstance(one, dict) and not one.get(ABSENT_SINCE_KEY)} - {""}
    return tied <= set((entries.get(named) or {}).get("bound") or ())


_follow_lock = threading.Lock()


def follow(game: Game, game_id: str, per_game: Callable[[Game, str], dict] = state_of) -> bool:
    """Bring the stored count's entries up to date for one game that was just re-read: the
    entry it names now and any it was counted under, each rebuilt from every game that
    names it. True when the stored count changed.

    Leaves a library that has never been counted as it is, and the tallies of kinds to the
    next count. Reads the games it rebuilds from, so a caller must not hold the library's
    lock.
    """
    with _follow_lock:
        counted = stored()
        if not counted or not game_id:
            return False
        entries = dict(counted.get("entries") or {})
        games = game_repository.catalog()
        state = per_game(game, game_id)
        now = str(state.get("vps_id") or "") if state.get("matched") else ""
        affected = {vps_id for vps_id, one in entries.items()
                    if game_id in (one.get("games") or ())}
        affected.add(now)
        affected.discard("")
        rebuilt: dict[str, dict[str, Any]] = {}
        for vps_id in sorted(affected):
            held: dict[str, Any] = {"tables": 0, "held": set(), "bound": set(), "games": set()}
            for other_id, other in games.items():
                if other_id == game_id:
                    answer = state
                elif game_vps_id(other) == vps_id:
                    try:
                        answer = per_game(other, other_id)
                    except Exception:
                        logger.warning("Could not read VPS state for %s", other_id, exc_info=True)
                        continue
                else:
                    continue
                if answer.get("matched") and str(answer.get("vps_id") or "") == vps_id:
                    _hold(held, other_id, other, answer)
            if held["games"]:
                rebuilt[vps_id] = _stored_entry(held)
        updated = {vps_id: one for vps_id, one in entries.items() if vps_id not in affected}
        updated.update(rebuilt)
        if updated == entries:
            return False
        counted.pop("schema", None)
        store({**counted, "entries": updated})
        clear_met_wants({"entries": rebuilt})
        return True


def recompute(games: dict, per_game: Callable[[Game, str], dict],
              reporter: JobReporter | None = None) -> dict[str, Any]:
    """Count it and keep it."""
    rollup = compute(games, per_game, reporter)
    store(rollup)
    clear_met_wants(rollup)
    return rollup


def clear_met_wants(rollup: dict[str, Any]) -> None:
    """Take Wanted off the releases the library now holds, since the want is met. A game
    marked Wanted is a plain mark and stays until it is taken off. A failure leaves the marks
    as they were: the count it follows is not worth failing for."""
    held = {one for found in (rollup.get("entries") or {}).values()
            for one in found.get("bound") or []}
    if not held:
        return
    try:
        vps_marks.met(held)
    except Exception:  # noqa: BLE001 - the count has been kept; the next one tries again
        logger.warning("Could not take the releases the library holds off Wanted", exc_info=True)


def recount(reporter: JobReporter | None = None) -> dict[str, Any]:
    """Count the whole library and keep the answer. What the job runs."""
    return recompute(game_repository.catalog(), state_of, reporter)

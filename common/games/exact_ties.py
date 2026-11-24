"""Ties VPinFE makes without being asked, for the files where there is only one answer.

A table whose authors and version are found together on exactly one release of its game's
VPS entry is that release. Anything short of that - no authors, a version that does not
read as one, two releases that fit - is left for a person to pick, and shows up as a table
with no release picked.

A ROM zip named exactly as one ROM of its game's VPS entry is that ROM. Two ROMs of one
name, or a zip named for none, are left for a person to pick.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from common.games import asset_origin, asset_resolver, identity_claims, library_folders, ties
from common.games.game_metadata import (
    SOURCE_KEY,
    effective_vps_id,
    keep_game_meta,
    load_game_meta,
    normalize_meta,
)
from common.games.game_repository import all_games
from common.games.game_service import load_vpsdb, release_fits
from common.games.tables import TABLES_KEY, table_entries
from common.i18n import t
from common.online import vps_kinds, vps_releases
from common.runtime import jobs as job_registry
from common.values import parse_version

logger = logging.getLogger("vpinfe.common.games.exact_ties")

ROMS = vps_kinds.BY_OURS["rom"].listed_as


def _spelled(version: object) -> str:
    """A version as written, less case, spacing and a leading v, and "" for one that does
    not read as a version. Numeric equality is not enough here: 1.2.1a and 1.2.1b are one
    number, and 1.0 and 1.0.0 are two releases."""
    text = "".join(str(version or "").split()).casefold().removeprefix("v")
    return text if parse_version(text) else ""


def exact_release(table: Mapping[str, Any], vps_entry: Mapping[str, Any]) -> dict:
    """The one release of `vps_entry` that credits every author the table names and lists
    its version, or {}. A table file names the lead and VPS credits the whole team, so the
    release may credit more people than the file does."""
    authors = vps_releases.credit_keys(table.get("authors"))
    version = _spelled(table.get("version"))
    if not authors or not version:
        return {}
    found = [release for release in vps_entry.get(vps_releases.TABLES) or []
             if release.get("id")
             and authors <= vps_releases.credit_keys(release.get("authors"))
             and _spelled(release.get("version")) == version
             and release_fits(release, dict(table))]
    return found[0] if len(found) == 1 else {}


def _untied(table: Mapping[str, Any]) -> bool:
    """Whether nothing has said which release this is, a person has not said it is none,
    and it is not a file we made: a patched table is bytes VPS never listed, so no
    release is its release."""
    held = table.get(SOURCE_KEY)
    source = held if isinstance(held, dict) else {}
    return not (source.get(ties.FILE_KEY) or source.get("base") or source.get("patch")
                or ties.declined(source))


def _sure(config: Mapping[str, Any], catalog: Mapping[str, Mapping[str, Any]]) -> dict:
    """The untied tables of a game's `.info` that have an exact release, by table id."""
    vps_entry = catalog.get(effective_vps_id(normalize_meta(config)))
    if not vps_entry:
        return {}
    found = {}
    for table_id, table in table_entries(dict(config)).items():
        if isinstance(table, dict) and _untied(table) and (
                release := exact_release(table, vps_entry)):
            found[table_id] = release
    return found


def _rom_stem(name: object) -> str:
    """A ROM's name as VPS lists it and as its zip is named: less case, spacing and `.zip`."""
    return str(name or "").strip().casefold().removesuffix(".zip")


def exact_rom(stem: str, vps_entry: Mapping[str, Any]) -> dict:
    """The one ROM of `vps_entry` named `stem`, or {}. VPS holds a ROM's name in `version`."""
    found = [rom for rom in vps_entry.get(ROMS) or []
             if rom.get("id") and _rom_stem(rom.get("version")) == stem]
    return found[0] if len(found) == 1 else {}


def _sure_roms(game: Any, config: Mapping[str, Any],
               catalog: Mapping[str, Mapping[str, Any]]) -> dict:
    """The ROM zips in a game's folder that nobody has matched or cleared and that have an
    exact ROM, by their key in the assets ledger."""
    vps_entry = catalog.get(effective_vps_id(normalize_meta(config)))
    if not vps_entry or not vps_entry.get(ROMS):
        return {}
    held = config.get(asset_origin.ASSETS_KEY)
    held = held if isinstance(held, dict) else {}
    ledger = {str(key).lower(): str(key) for key in held}
    folder = str(getattr(game, "full_path_game", "") or "")
    by_stem: dict[str, list[str]] = {}
    for name in asset_resolver.list_rom_files(folder):
        if name.lower().endswith(".zip") and os.path.isfile(os.path.join(
                folder, *asset_resolver.ROMS_FOLDER.split("/"), name)):
            by_stem.setdefault(_rom_stem(name), []).append(name)
    found = {}
    for stem, names in by_stem.items():
        rom = exact_rom(stem, vps_entry)
        if not rom or len(names) != 1:
            continue
        place = f"{asset_resolver.ROMS_FOLDER}/{names[0]}"
        key = ledger.get(place.lower(), place)
        entry = held.get(key)
        source = entry.get(SOURCE_KEY) if isinstance(entry, dict) else None
        if isinstance(source, dict) and (source.get(ties.FILE_KEY) or ties.declined(source)):
            continue
        found[key] = rom
    return found


def _has_work(game: Any, catalog: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether the game's cached `.info` shows a table or ROM zip to tie."""
    cached = getattr(game, "meta_config", None) or {}
    return bool(_sure(cached, catalog) or _sure_roms(game, cached, catalog))


def tie_game(game: Any, catalog: Mapping[str, Mapping[str, Any]],
             checked: bool = False) -> tuple[int, int]:
    """Tie each of a game's untied tables and ROM zips that have an exact match. How many of
    each were tied.

    The cached `.info` says whether there is anything to tie before the disk is read, unless
    the caller has already asked (`checked`), and the file is read again before it is
    written. The folder must be known writable.
    """
    if getattr(game, "info_pending_upgrade", False):
        return 0, 0
    if not checked and not _has_work(game, catalog):
        return 0, 0
    config = load_game_meta(game)
    sure, roms = _sure(config, catalog), _sure_roms(game, config, catalog)
    if not (sure or roms):
        return 0, 0
    if sure:
        entries = table_entries(config)
        for table_id, release in sure.items():
            held = entries[table_id].get(SOURCE_KEY)
            entries[table_id][SOURCE_KEY] = ties.tie(
                dict(held) if isinstance(held, dict) else {}, str(release["id"]),
                identity_claims.EXACT, release)
        config[TABLES_KEY] = entries
    if roms:
        ledger = config.get(asset_origin.ASSETS_KEY)
        if not isinstance(ledger, dict):
            ledger = config[asset_origin.ASSETS_KEY] = {}
        for key, rom in roms.items():
            entry = ledger.get(key)
            if not isinstance(entry, dict):
                entry = ledger[key] = {}
            held = entry.get(SOURCE_KEY)
            entry[SOURCE_KEY] = ties.tie(
                dict(held) if isinstance(held, dict) else {}, str(rom["id"]),
                identity_claims.EXACT, rom)
    keep_game_meta(game, config)
    return len(sure), len(roms)


def tie_library() -> int | None:
    """Tie what can be tied across the library, and say how many tables and ROM files were.
    None where another pass over the `.info` files is running, so nothing was looked at.

    A game whose folder cannot be written to is left alone.
    """
    try:
        with job_registry.track(job_registry.KIND_LIBRARY_SCAN,
                                title=t("said.title_tie")) as job:
            catalog = {str(entry.get("id") or ""): entry for entry in load_vpsdb()}
            games = [game for game in (all_games() if catalog else ())
                     if _has_work(game, catalog)]
            blocked = {id(game) for game, _ in library_folders.blocked_games(games)}
            tables = roms = 0
            for game in games:
                if id(game) in blocked:
                    continue
                try:
                    more_tables, more_roms = tie_game(game, catalog, checked=True)
                    tables += more_tables
                    roms += more_roms
                except Exception:
                    logger.warning("Could not tie the files of %s",
                                   getattr(game, "game_dir_name", "?"), exc_info=True)
            job.detail = _told(tables, roms)
            return tables + roms
    except job_registry.JobBusyError:
        return None


def _told(tables: int, roms: int) -> str:
    """What the job says it did."""
    if not roms:
        return t("said.job_tied", count=tables)
    said = t("said.job_tied_roms", count=roms)
    return t("said.job_tied_both", tables=t("said.job_tied", count=tables), roms=said) \
        if tables else said

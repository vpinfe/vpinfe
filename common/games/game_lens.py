"""One game answered as a resource: identity, overrides, and what its folder holds.

The catalog row is what the last scan recorded. This is that row turned into the shape
every surface reads, plus an inventory that is recomputed per call - an audit reporting
yesterday's folder is worse than none.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import service_errors
from common.games import asset_resolver, game_repository, library_folders
from common.games.asset_registry import altsound_folder, is_readme
from common.games.game_repository import catalog, collections_by_game_id, game_to_row
from common.games.tables import table_names
from common.i18n import t

__all__ = ["asset_summary", "catalog", "detail", "game_for_writing", "game_or_refuse",
           "game_resource", "inventory_assets", "listing", "resource_of"]


def asset_summary(row: dict) -> dict:
    """Presence per kind, as objects so a kind can grow attributes without a
    breaking change. alt_color keeps its formats - the flat boolean lost them."""
    formats = [name for name, flag in (("serum", "serum_exists"), ("vni", "vni_exists"))
               if row.get(flag)]
    return {
        "backglass": {"present": bool(row.get("b2s_exists"))},
        # `ini`, not `settings`: manager has a Settings section, and one word for a
        # nav destination and a file kind is two things sharing a name.
        "ini": {"present": bool(row.get("ini_exists"))},
        "pup_pack": {"present": bool(row.get("pup_pack_exists"))},
        "alt_color": {"present": bool(formats), "formats": formats},
        "alt_sound": {"present": bool(row.get("alt_sound_exists"))},
        "music": {"present": bool(row.get("music_exists"))},
        "readme": {"present": bool(row.get("readme_exists"))},
    }


def inventory_assets(game_dir: Path) -> dict:
    """Every asset file in the folder attributed, plus the folder-wide kinds.

    Computed fresh, not from the scan - an audit that reports yesterday's folder is
    worse than none.
    """
    files, subdirs = asset_resolver.folder_listing(game_dir)
    inv = asset_resolver.inventory(game_dir.name, files, table_names(files))
    for entry in inv.values():
        entry["present"] = bool(entry["files"])
    subdir_set = {name.lower() for name in subdirs}
    formats = [fmt for fmt, folder in (("serum", "serum"), ("vni", "vni"))
               if folder in subdir_set]
    inv["pup_pack"] = {"present": "pupvideos" in subdir_set}
    inv["alt_color"] = {"present": bool(formats), "formats": formats}
    inv["alt_sound"] = {"present": bool(altsound_folder(game_dir, subdirs))}
    inv["music"] = {"present": "music" in subdir_set}
    inv["readme"] = {"present": any(is_readme(name) for name in files)}
    return inv


def game_resource(row: dict, game_id: str) -> dict[str, Any]:
    """One game as a client reads it, from the row a scan produced."""
    prefix = f"/api/v1/games/{game_id}"
    return {
        "id": game_id,
        # Correlation with VPSdb, VPinPlay and the like - not this table's identity.
        # The effective id, not the discovered one: `alt_vpsid` is somebody saying the
        # match was wrong, and every other field here is already the value in force -
        # `name` is the alt title the moment one is set. `discovered` below is what an
        # undo reverts to, and is the only place the superseded id belongs.
        "vps_id": ("" if row.get("alt_vpsid", "") is None
                   else row.get("alt_vpsid", "") or row.get("vpsid", "")),
        "vps_matched_by": row.get("vps_matched_by", ""),
        "name": row.get("name", ""),
        "manufacturer": row.get("manufacturer", ""),
        "year": str(row.get("year") or ""),
        "type": row.get("type", ""),
        "themes": row.get("themes") or [],
        "authors": row.get("authors") or [],
        "rom": row.get("rom", ""),
        "version": row.get("version", ""),
        # How many tables this game offers, so a client can tell a row that collapses
        # six from one that collapses one. `rom` and `version` above are read off the
        # default table; this is what says whether there was a choice to make.
        "table_count": int(row.get("table_count") or 0),
        "hidden": bool(row.get("hidden")),
        "private": bool(row.get("private")),
        "rating": row.get("rating", 0),
        "collections": row.get("collections") or [],
        "folder": str(row.get("game_dir", "") or ""),
        "overrides": {
            "alt_title": row.get("alt_title", ""),
            "alt_vps_id": row.get("alt_vpsid", ""),
            "frontend_dof_event": row.get("frontend_dof_event", ""),
            "alt_manufacturer": row.get("alt_manufacturer", ""),
            "alt_year": row.get("alt_year", ""),
            "alt_type": row.get("alt_type", ""),
            "alt_themes": row.get("alt_themes") or [],
            "alt_ipdb_id": row.get("alt_ipdb_id", ""),
        },
        "discovered": {
            "name": row.get("found_name", ""),
            "vps_id": row.get("vpsid", ""),
            "vps_matched_by": row.get("found_vps_matched_by", ""),
            "manufacturer": row.get("found_manufacturer", ""),
            "year": str(row.get("found_year") or ""),
            "type": row.get("found_type", ""),
            "themes": row.get("found_themes") or [],
            "ipdb_id": row.get("found_ipdb_id", ""),
        },
        "ipdb_id": row.get("ipdb_id", ""),
        "tutorial": row.get("tutorial_url", ""),
        "guides": row.get("guides") or [],
        # Assets, not media: these are what the game needs to play as intended.
        # Media is the artwork VPinFE shows while browsing - see docs/conventions.md.
        # Summary from the scan; the detail endpoint recomputes and attributes files.
        "assets": asset_summary(row),
        "user": row.get("user") or {},
        "derived_tags": row.get("derived_tags") or [],
        "links": {
            "self": prefix,
            "tables": f"{prefix}/tables",
            "media": f"{prefix}/media",
            "archive": f"{prefix}/archive",
            "launch": f"{prefix}/launch",
            "rating": f"{prefix}/rating",
        },
    }


def game_or_refuse(game_id: str) -> Any:
    """The game an id names, or a refusal naming the id. One lookup, so nothing forms a
    second answer to which game an id names."""
    game = game_repository.game_by_id(game_id)
    if game is None:
        raise service_errors.NotFoundError(
            t("error.games.unknown_game", game_id=(game_id)))
    return game


def game_for_writing(game_id: str) -> Any:
    """`game_or_refuse`, once the game's folder has said it can be written to.

    What every caller that changes a game's folder or its .info starts from, so a dropped
    share or a read-only one is refused in the same words wherever the write came from.
    """
    game = game_or_refuse(game_id)
    library_folders.writable_game_folder(str(game.full_path_game or ""),
                                         called=str(game.game_dir_name or game_id))
    return game


def resource_of(game_id: str) -> dict[str, Any]:
    """One game, with the assets the last scan counted."""
    game = game_or_refuse(game_id)
    return game_resource(game_to_row(game, collections_by_game_id()), game_id)


def detail(game_id: str) -> dict[str, Any]:
    """One game, with its folder read again. What `GET /games/{id}` answers: the scan's
    summary is a count, and a curator looking at one game wants the files."""
    resource = resource_of(game_id)
    resource["assets"] = inventory_assets(Path(resource["folder"]))
    return resource


def listing(q: str = "", limit: int = 0, offset: int = 0) -> dict[str, Any]:
    """Every game, by name, optionally narrowed and paged.

    Sorted before the search so the window is stable: a client paging through results has
    to see the same order the last page came from.
    """
    collections = collections_by_game_id()
    found = sorted(
        ((game_to_row(game, collections), game_id)
         for game_id, game in catalog().items()),
        key=lambda pair: pair[0].get("name", "").lower())
    resources = [game_resource(row, game_id) for row, game_id in found]

    if q:
        needle = q.strip().lower()
        resources = [one for one in resources
                     if needle in one["name"].lower()
                     or needle in (one["manufacturer"] or "").lower()
                     or needle in (one["rom"] or "").lower()]

    total = len(resources)
    if offset:
        resources = resources[offset:]
    if limit:
        resources = resources[:limit]
    return {"total": total, "offset": offset, "count": len(resources),
            "games": resources}

"""The library, held once and shared.

Every page and endpoint reads games from here rather than re-scanning. Re-reading a
game replaces the object, so anything
holding the old one is stale - `game.changed` is announced for that reason.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from time import perf_counter
from typing import Any

from common import events
from common.config import path_state
from common.config.paths import COLLECTIONS_PATH, get_ini_config
from common.games import derived_tags, library_folders, unwritten
from common.games.collection_store import CollectionStore
from common.games.game import Game
from common.games.game_identity import ensure_unique_ids, in_priority
from common.games.game_identity import game_id as vpinfe_id
from common.games.game_metadata import (
    GAME_OVERRIDES,
    as_string_list,
    contract_1_tutorial,
    declared_no_match,
    default_table,
    first_meta_value,
    game_discovered,
    game_override,
    game_private,
    game_title,
    guides_on_wire,
    info_matched_by,
    load_game_meta,
    normalize_rating,
    play_record,
    reorder_leading_article,
    section,
    vpinfe_section,
    vps_matched_by,
    write_held_meta,
)
from common.games.game_parser import GameParser
from common.games.info_migration import INFO_SCHEMA, schema_of
from common.games.tables import all_hidden, table_entries
from common.games.unreadable import UnreadableGame

_LOCK = threading.Lock()
# One parser per location, keyed by its path and kind. A parser reads one path and knows
# nothing about locations, which is what keeps the plural half here and out of the scan.
_PARSERS: dict[tuple[str, str], GameParser] = {}
# Locations that could not be reached at the last read, left alone until the next reload.
_AWAY: set[tuple[str, str]] = set()
logger = logging.getLogger("vpinfe.common.games.game_repository")

_generation = 0


def generation() -> int:
    """Changes whenever something a game's rows were built from changed that a re-read of
    the game does not show: its own settings file, the launchers, the collections."""
    return _generation


def touch(**_: Any) -> None:
    global _generation
    _generation += 1


# Sections no row is built from. A key outside these, a list or one in a section not named
# here moves the generation: an unlisted section is slow, a wrongly listed one is stale.
SECTIONS_BESIDE_ROWS = frozenset({
    "backups", "behavior", "capture", "chromium", "commands", "device", "dof", "input",
    "logger", "manager", "network", "real_dmd", "themes", "tools", "updates", "vpinplay",
    "vpxmobile", "windows",
})


def _settings_changed(keys: Any = (), **_: Any) -> None:
    names = [str(key) for key in keys or ()]
    if names and all(name.split(".", 1)[0] in SECTIONS_BESIDE_ROWS for name in names):
        return
    touch()


events.subscribe(events.SETTINGS_CHANGED, _settings_changed)
events.subscribe(events.COLLECTIONS_CHANGED, touch)


def _held(reload: bool) -> tuple[list[Any], bool]:
    """Every game across every location, and whether anything was actually read.

    Parsers are kept and reused; one is built for a location that has not been read yet
    and dropped for a location that has gone. Rebuilding on a changed list is what stops
    a second library being answered with the first one's games.
    """
    wanted = library_folders.configured()
    keys = {(one.path, one.kind) for one in wanted}
    for gone in [key for key in _PARSERS if key not in keys]:
        _PARSERS.pop(gone)
    _AWAY.intersection_update(keys)

    unread = [one for one in wanted if reload
              or (one.path, one.kind) not in _PARSERS and (one.path, one.kind) not in _AWAY]
    states = library_folders.states_of(unread, wait=path_state.MOUNT_SECONDS) if unread else {}

    games: list[Any] = []
    read = False
    for location in wanted:
        key = (location.path, location.kind)
        state = states.get(location.location_id)
        if state is not None:
            read = True
            if not state.reachable:
                _PARSERS.pop(key, None)
                _AWAY.add(key)
                logger.warning("Skipped %s: %s", location.path, state.reason)
                continue
            _AWAY.discard(key)
            if key in _PARSERS:
                _PARSERS[key].load_games(reload=True)
            else:
                _PARSERS[key] = GameParser(location.path, get_ini_config(),
                                           one_game=location.kind == library_folders.KIND_GAME)
        parser = _PARSERS.get(key)
        if parser is None:
            continue
        for game in parser.get_all_games():
            game.location_id = location.location_id
            games.append(game)
    return games, read


def all_games(reload: bool = False) -> list[Any]:
    """Every game across every location, read once and held."""
    started_at = perf_counter()
    with _LOCK:
        if reload:
            write_held_meta()
        games, read = _held(reload)

    # Only when it read the library. Logging every call logged the caller rather than the
    # work - a dozen call sites answered from cache on every page render, with the one
    # line that matters buried among them. An unexpected line here means the locations
    # changed or something is discarding the cache, and both are worth seeing.
    if read:
        logger.debug("read %s location(s): %s games in %.3fs",
                     len(_PARSERS), len(games), perf_counter() - started_at)
    return games


def reread_media_settings() -> bool:
    """Read the library again if the media settings now in force differ from what its
    games were resolved under: the playfield variant or the active wheel set. True when
    it did. Left alone otherwise, because a rescan is the slow part on a network share."""
    store = get_ini_config()
    with _LOCK:
        changed = [parser.read_media_settings(store) for parser in _PARSERS.values()]
    if not any(changed):
        return False
    all_games(reload=True)
    return True


def waiting_for() -> list[str]:
    """What an empty library is waiting on: each of its folders, by server where it is on
    one, while none of them can be reached. Empty once one answers, the library read
    again."""
    wanted = library_folders.configured()
    if not wanted or all_games():
        return []
    states = library_folders.states_of(wanted)
    if any(state.reachable for state in states.values()):
        all_games(reload=True)
        return []
    named = ((states[one.location_id].origin or one.origin, one.name) for one in wanted)
    return list(dict.fromkeys(origin.server if origin else name for origin, name in named))


def catalog() -> dict[str, Any]:
    """Every game keyed by id. Writes a .info for any game that lacks one, so it is a
    no-op once the library has been through it."""
    return ensure_unique_ids(all_games())


def game_by_id(game_id: str) -> Any | None:
    """The game an id names, or None. One lookup, so nothing forms a second answer."""
    return catalog().get(game_id)


def called(game_id: str) -> str:
    """What a person calls the game an id names, or the id where no game has it."""
    game = game_by_id(game_id)
    return game_title(game) if game is not None else game_id


def game_folder(game_id: str) -> Path | None:
    """The folder one game lives in, for a caller that has an id and needs the files."""
    game = game_by_id(game_id)
    return Path(str(game.full_path_game)) if game is not None else None


def by_folder_name(name: str) -> Any | None:
    """The game whose folder has this name, from the higher location when two do."""
    return next(iter(in_priority(game for game in all_games()
                                 if game.game_dir_name == name)), None)


def on_this_device(game: Any) -> Any | None:
    """The game this device can play for one it was handed: the same game when it has a
    folder here, otherwise this device's own copy of that id, or None without one."""
    if str(getattr(game, "full_path_game", "") or ""):
        return game
    return game_by_id(vpinfe_id(game))


def refresh_games() -> list[Any]:
    return all_games(reload=True)


def _upgradable(games: list[Any]) -> list[Any]:
    """The games on an older format, but for those whose .info could not be written."""
    held = unwritten.reasons(str(game.full_path_game or "") for game in games)
    return [game for game in games if getattr(game, "info_pending_upgrade", False)
            and str(game.full_path_game or "") not in held]


def info_maintenance_counts(reload: bool = False) -> dict[str, int]:
    """How many games could be upgraded, and how many have something to restore.

    Off the loaded library, which already read every .info and listed every folder.
    """
    games = all_games(reload=reload)
    return {
        "pending_upgrade": len(_upgradable(games)),
        "restorable": sum(1 for t in games if getattr(t, "info_restorable", False)),
        # Written by a build newer than this one. Without this the page cannot tell "I
        # upgraded these" from "something newer did, and I cannot fully read them" - and
        # says the first, which is a lie the moment a schema 3 exists.
        "newer_than_us": sum(1 for t in games
                             if (schema_of(t.meta_config) or 0) > INFO_SCHEMA),
    }


def unreadable_games() -> list[UnreadableGame]:
    """Folders whose .info could not be read, so the game was left out of the library."""
    all_games()
    with _LOCK:
        return [row for parser in _PARSERS.values()
                for row in parser.get_unreadable_games()]


def unwritten_games() -> list[dict[str, str]]:
    """Games holding what their .info could not take, each with why, by folder."""
    games = all_games()
    held = unwritten.reasons(str(game.full_path_game or "") for game in games)
    return sorted(({"folder": game.game_dir_name, "error": held[str(game.full_path_game)]}
                   for game in games if str(game.full_path_game or "") in held),
                  key=lambda row: row["folder"].lower())


def pending_upgrade_game_names() -> list[str]:
    """Folders whose .info the upgrade did not reach, for the list its dialog shows."""
    return sorted((game.game_dir_name for game in _upgradable(all_games())), key=str.lower)


def newest_backup_stamp() -> str:
    """The most recent backup timestamp in the library, or ""."""
    stamps = [s for s in (getattr(t, "info_backup_stamp", "")
                          for t in all_games()) if s]
    return max(stamps) if stamps else ""


def restorable_game_names() -> list[str]:
    """Folders holding a saved copy of their .info, for the list a restore dialog shows."""
    return sorted(
        (t.game_dir_name for t in all_games() if getattr(t, "info_restorable", False)),
        key=str.lower,
    )


def refresh_game(game_dir: Path) -> list[Any]:
    """Re-read one game folder, not the library.

    One folder, because every caller changes one. A whole-library reload makes setting a
    star rating on a big network share take minutes.
    """
    normalized = str(Path(game_dir).expanduser().resolve())
    started_at = perf_counter()
    with _LOCK:
        # The one whose root holds this folder. Asking the wrong parser to re-read it
        # would add the game to a location it is not in.
        parser = _parser_holding(normalized)
        if parser is None:
            reloaded = None
        else:
            reloaded = parser.reload_game(normalized)
            games, _ = _held(reload=False)
    if reloaded is None:
        # Nothing loaded yet, so there is no single game to refresh - read the library.
        games = all_games(reload=True)

    logger.debug("refresh_game %s elapsed=%.3fs", normalized, perf_counter() - started_at)
    found = [game for game in games if str(Path(game.full_path_game).resolve()) == normalized]

    # Announced here rather than at each caller: every path that changes one game already
    # comes through this to be re-read, so a new one cannot forget to say so.
    events.emit(events.GAME_CHANGED, game=found[0] if found else None, path=normalized)
    return found


def reread_game(game: Any) -> Any:
    """One game as it now stands on disk, after something wrote its record.

    When the library holds no folder for it, the object passed in comes back with its
    record reloaded.
    """
    found = refresh_game(Path(str(game.full_path_game or "")))
    if found:
        return found[0]
    game.meta_config = load_game_meta(game)
    return game


def get_missing_games(reload: bool = False) -> list[dict[str, str]]:
    all_games(reload=reload)
    with _LOCK:
        return [dict(row) for parser in _PARSERS.values()
                for row in parser.get_missing_games()]


def collections_by_game_id() -> dict[str, list[str]]:
    """Collection names keyed by the game id membership is recorded under.

    Every collection that names this game, whether or not it also carries criteria:
    A rule and named members are combinable, so a game hand-added to a collection
    that also filters is a member of it and skipping those would under-report.

    What criteria match is still decided per game at display time and is not here -
    this is the stored membership, not the resolved one.
    """
    mapping: dict[str, list[str]] = {}
    try:
        collections = CollectionStore(str(COLLECTIONS_PATH))
        for collection_name in collections.get_collections_name():
            try:
                for member_id in collections.get_members(collection_name):
                    mapping.setdefault(member_id, []).append(collection_name)
            except Exception:
                pass
    except Exception:
        pass
    return mapping


def game_to_row(game: Game,
                collections_map: dict[str, list[str]] | None = None) -> dict[str, Any]:
    meta = game.meta_config or {}
    user = section(meta, "User")
    vpinfe = vpinfe_section(meta)
    game_name = Path(str(game.full_path_game)).name
    vpsid = first_meta_value(meta, ("Info", "VPSId"), default="")
    # The row describes one table - the game's default. A folder can hold several,
    # and the API lists them all separately; this is what the game-level views show.
    gf_name, gf = default_table(meta)

    def gf_value(key: str, default: Any = "") -> Any:
        value = gf.get(key, None)
        return default if value in ("", None) else value

    # What each field would say with no override, kept beside the effective value
    # because a surface offering to undo one has to say what it undoes to, and the
    # effective value has already resolved that away.
    answered = {name: game_override(meta, name) for name in GAME_OVERRIDES}
    found = {name: game_discovered(meta, name) for name in GAME_OVERRIDES}
    found["title"] = reorder_leading_article(found["title"] or game_name)
    for name in ("manufacturer", "year", "type"):
        found[name] = found[name] or gf_value(name)
    found["themes"] = as_string_list(found["themes"] or [])

    row = {
        "name": answered["title"] or found["title"],
        "found_name": found["title"],
        "filename": gf_name or Path(str(game.full_path_vpx_file)).name,
        # vpsid and alt_vpsid correlate with VPSdb, VPinPlay and anything else keyed
        # by them. vpinfe_id is this install's own id (common/games/game_identity.py)
        # and is what identifies the game here - in the API, in events, in collection
        # membership. Empty until the game has been assigned one; reading never mints.
        "vpsid": vpsid,
        "vpinfe_id": vpinfe_id(game),
        "ipdb_id": answered["ipdb_id"] or found["ipdb_id"],
        "found_ipdb_id": found["ipdb_id"],
        "tutorial_url": contract_1_tutorial(meta),
        "guides": guides_on_wire(meta, hidden=True),
        # Info carries what VPS knows; the table's own claim is the fallback and can
        # legitimately differ from it.
        "manufacturer": answered["manufacturer"] or found["manufacturer"],
        "found_manufacturer": found["manufacturer"],
        "year": answered["year"] or found["year"],
        "found_year": found["year"],
        "type": answered["type"] or found["type"],
        "found_type": found["type"],
        "themes": as_string_list(answered["themes"] or found["themes"]),
        "found_themes": found["themes"],
        # Authors are per table, never rolled up: multi-table folders often
        # name different authors in different ones.
        "authors": as_string_list(gf_value("authors", [])),
        "rom": gf_value("rom"),
        "version": gf_value("version"),
        # How many tables this game offers. A game row collapses them, and this is
        # the only thing that says so - which is what makes the by-table lens
        # discoverable, and what qualifies the values above read off the default.
        "table_count": len(table_entries(meta)),
        "hidden": all_hidden(table_entries(meta)),
        "private": game_private(meta),
        "filehash": gf_value("file_hash"),
        "vbshash": gf_value("vbs_hash"),
        "detectnfozzy": gf_value("detect_nfozzy"),
        "detectfleep": gf_value("detect_fleep"),
        "detectssf": gf_value("detect_ssf"),
        "detectlut": gf_value("detect_lut"),
        "detectscorebit": gf_value("detect_scorbit"),
        "detectfastflips": gf_value("detect_fastflips"),
        "detectflex": gf_value("detect_flex"),
        "detectpinmame": gf_value("detect_pinmame"),
        "game_dir": game.full_path_game,
        "b2s_exists": bool(game.b2s_exists),
        "pup_pack_exists": bool(game.pup_pack_exists),
        "serum_exists": bool(game.alt_color_exists),
        "vni_exists": bool(game.vni_exists),
        "alt_sound_exists": bool(game.alt_sound_exists),
        "ini_exists": bool(game.ini_exists),
        "music_exists": bool(game.music_exists),
        "readme_exists": bool(game.readme_exists),
        "delete_nvram_on_close": vpinfe.get("delete_nvram_on_close", False),
        "alt_launcher": str(vpinfe.get("alt_launcher", "") or "").strip(),
        "plugin_profile": str(vpinfe.get("plugin_profile", "") or "").strip(),
        "alt_title": str(vpinfe.get("alt_title", "") or "").strip(),
        # None survives: null is somebody saying there is no match, and `or ""` here
        # would turn that into "no opinion" and let the scan's answer stand again.
        "alt_vpsid": (None if declared_no_match(meta)
                      else str(vpinfe.get("alt_vpsid", "") or "").strip()),
        "vps_matched_by": vps_matched_by(meta),
        "found_vps_matched_by": info_matched_by(meta),
        **{GAME_OVERRIDES[name][0]: answered[name]
           for name in ("manufacturer", "year", "type", "themes", "ipdb_id")},
        "frontend_dof_event": str(vpinfe.get("frontend_dof_event", "") or "").strip(),
        "rating": normalize_rating(user.get("Rating", 0)),
        # The whole play record beside the flat rating. The rating shipped alone and
        # readers hold it; this is where the rest of what a person did with a game
        # lives, and it reached only the play lens until now.
        "user": play_record(meta),
        "derived_tags": derived_tags.game_tags(game),
        "collections": [],
    }
    if collections_map is not None:
        row["collections"] = _collections_for(row, collections_map)
    return row


def _collections_for(row: dict[str, Any], collections_map: dict[str, list[str]]) -> list[str]:
    """Which collections a row belongs to, tolerating entries not yet migrated.

    Matches CollectionStore.is_member. The migration leaves an entry alone when no
    game matched it - the game may simply not be installed yet - and it only runs
    once, so an entry can stay VPS-keyed indefinitely. Without the fallbacks the
    frontend would show that membership and the Manager would not.
    """
    for key in (row.get("vpinfe_id"), row.get("alt_vpsid"), row.get("vpsid")):
        if key and key in collections_map:
            return collections_map[key]
    return []


def get_game_rows(reload: bool = False) -> list[dict[str, Any]]:
    # A row is addressed by its game id, so every row has to have one - a game
    # imported since startup would otherwise carry an empty id and collide with
    # every other game that has none. Already-assigned libraries pay nothing:
    # this only touches disk for a game that has no id yet.
    games = ensure_unique_ids(all_games(reload=reload)).values()
    collections_map = collections_by_game_id()
    rows = [game_to_row(game, collections_map) for game in games]
    rows.sort(key=lambda row: (row.get("name") or "").lower())
    return rows


def get_game_name_map(reload: bool = False) -> dict[str, str]:
    """Display names keyed by game id, for showing what is in a collection."""
    return {
        row["vpinfe_id"]: row.get("name") or row["vpinfe_id"]
        for row in get_game_rows(reload=reload)
        if row.get("vpinfe_id")
    }


def _parser_holding(game_dir: str) -> GameParser | None:
    """The parser for the location this folder is in, or None when nothing is loaded."""
    for (path, _kind), parser in _PARSERS.items():
        if not parser.get_game_count():
            continue
        try:
            if Path(game_dir).is_relative_to(Path(path).expanduser().resolve()):
                return parser
        except (OSError, ValueError):
            continue
    return None

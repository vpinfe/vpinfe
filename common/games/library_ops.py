"""The library as a whole, rather than one game of it.

A scan rewrites metadata across every game folder; a tag merge renames a word half the
library carries. Neither is an operation on a game, and doing either one folder at a time
leaves the library in a state nobody asked for.

The long ones run as jobs. `start` is how one is put on the queue, because every caller
wants the same thing back - something to watch - and the same refusal when the queue is
busy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from common import service_errors
from common.games import (
    derived_tags,
    dismissals,
    entry_lens,
    game_repository,
    game_service,
    launchers,
    library_folders,
    library_updates,
    library_vps_state,
    media_fill,
    opened_pages,
    tag_registry,
)
from common.games.collection_filters import AXES, GameListFilters
from common.games.collection_resolver import resolve
from common.games.collection_store import BUILTIN_ALL
from common.games.collections_service import get_collections_manager
from common.games.game_metadata import (
    carrying,
    effective_vps_id,
    game_tags,
    game_title,
    normalize_meta,
    normalize_tag,
    retag_library,
    table_tags,
)
from common.games.tables import table_entries
from common.i18n import t
from common.online import vps_home, vps_marks, vps_updates
from common.runtime import job_details
from common.runtime import jobs as job_registry

logger = logging.getLogger("vpinfe.common.games.library_ops")


def start(kind: str, work: Callable[[job_registry.Job], object],
          title: str = "") -> job_registry.Job:
    """Put a library-wide pass on the queue, or refuse because one is already running."""
    return job_registry.submit(kind, work, title=title)


def filter_axes() -> dict[str, Any]:
    """Every filter axis, with the values this library holds.

    Projected from the registry the resolver matches on, so the two cannot disagree. An
    axis declaring choices carries those, whatever is installed.
    """
    library = GameListFilters(game_repository.all_games())
    available, counts = library.available_options(), library.value_counts()
    return {"axes": [{"name": axis.name, "scope": axis.scope, "kind": axis.kind,
                      # The name a reader sees, which the registry owns. Without it a
                      # caller derives one from the key and gets "Game type" where the
                      # rest of the app says "Type".
                      "label": axis.label,
                      "label_key": f"filter.{axis.name}.label",
                      "summary": axis.summary,
                      # Whether the axis takes several values, which is an OR across
                      # them. Declared so a caller renders the right control without
                      # knowing which axes exist.
                      "many": axis.many,
                      "values": list(axis.choices) or available.get(axis.values_key),
                      "counts": counts.get(axis.values_key),
                      "field": axis.field}
                     for axis in AXES]}


def _carried() -> tuple[dict[str, int], dict[str, int]]:
    """How many games each tag reaches, on the game or one of its tables, and how many
    tables carry it."""
    games: dict[str, int] = {}
    tables: dict[str, int] = {}
    for game in game_repository.all_games():
        reached = set(game_tags(game)) | set(derived_tags.game_tags(game))
        for entry in table_entries(getattr(game, "meta_config", {})).values():
            held = (set(table_tags(entry)) | set(derived_tags.table_tags(entry))
                    if isinstance(entry, dict) else set())
            reached |= held
            for tag in held:
                tables[tag] = tables.get(tag, 0) + 1
        for tag in reached:
            games[tag] = games.get(tag, 0) + 1
    return games, tables


def tags() -> dict[str, Any]:
    entries = tag_registry.load()
    games, tables = _carried()
    sources = derived_tags.sources()
    return {"tags": [{"name": name, "games": games.get(name, 0),
                      "tables": tables.get(name, 0), **tag_registry.describe(name, entries),
                      "sources": sources.get(name, [])}
                     for name in sorted(set(games) | set(tables) | set(entries)
                                        | set(sources), key=str.casefold)]}


def put_tag(tag: str, description: str | None, color: str | None) -> dict[str, Any]:
    said = tag_registry.put(tag, description=description, color=color)
    name = normalize_tag(tag)
    games, tables = _carried()
    return {"name": name, "games": games.get(name, 0), "tables": tables.get(name, 0),
            **said}


def merge_tags(sources: Iterable[str], into: str) -> dict[str, Any]:
    """Across the library, because a tag is not owned by a game - half of them renamed
    is a worse state than either end of the merge."""
    sources = list(sources)
    derived_tags.refuse([*sources, into])
    games = game_repository.all_games()
    survivor = normalize_tag(into)
    _refuse_if_blocked(carrying(games, [s for s in sources if normalize_tag(s) != survivor]))
    changed = retag_library(games, sources, into)
    tag_registry.moved(sources, into)
    return {"changed": changed}


def _refuse_if_blocked(games: list[Any]) -> None:
    """Refuse the whole sweep, with nothing written, when a game it would change cannot
    be written to."""
    blocked = library_folders.blocked_games(games)
    if not blocked:
        return
    names = [str(game.game_dir_name or "") for game, _ in blocked]
    shown = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
    raise service_errors.BlockedError(
        t("error.tags.sweep_blocked", count=len(blocked), names=shown),
        details=[{"game": name, "reason": said}
                 for name, (_, said) in zip(names, blocked, strict=True)])


def drop_tag(tag: str) -> dict[str, Any]:
    derived_tags.refuse([tag])
    games = game_repository.all_games()
    _refuse_if_blocked(carrying(games, [tag]))
    changed = retag_library(games, [tag], "")
    tag_registry.dropped(tag)
    return {"changed": changed}


def entries() -> dict[str, Any]:
    """The play lens over everything, which is what a frontend shows before a collection
    is chosen.

    The whole library is a collection - `builtin:all`, synthesized rather than stored, so
    it answers without putting a row in anyone's file.
    """
    resolved = resolve(BUILTIN_ALL, get_collections_manager(), game_repository.all_games())
    return {"collection": "", "count": len(resolved),
            "entries": [entry_lens.entry_resource(entry) for entry in resolved]}


def preview(criteria: dict, limit: int = 0) -> dict[str, Any]:
    """Resolve criteria against the library without writing them anywhere.

    An unsaved rule is `builtin:all` plus its criteria, so this is the ordinary resolve
    with nothing stored.
    """
    manager = get_collections_manager()
    # Set for this call and cleared after it, because the store object outlives it and a
    # leftover constraint would narrow the next reader's whole library.
    try:
        manager.set_view_filters(criteria)
        resolved = resolve(BUILTIN_ALL, manager, game_repository.all_games())
    finally:
        manager.set_view_filters(None)
    if limit and limit > 0:
        resolved = resolved[:limit]
    return {"collection": "", "count": len(resolved),
            "entries": [entry_lens.entry_resource(entry) for entry in resolved]}


def scan(options: dict[str, Any]) -> job_registry.Job:
    """Rebuild game metadata from VPSdb. Writes a .info for every game it can match."""
    return start(job_registry.KIND_LIBRARY_SCAN,
                 lambda job: game_service.build_metadata(
                     job=job,
                     download_media=options.get("download_media", False),
                     update_all=options.get("update_all", False)))


def refresh() -> job_registry.Job:
    """The local counterpart to a scan: re-read the folders, give every .vpx an id, note
    the ones that are gone, and read whatever nothing has read yet. Never the network.

    Shares the scan's job kind deliberately - both write a .info for every game they
    touch, so running them at once would interleave writes to the same files.
    """
    from common.games.library_refresh import refresh as run_refresh

    return start(job_registry.KIND_LIBRARY_SCAN,
                 lambda job: run_refresh(job.reporter()), t("said.title_refresh"))


def auto_match(game_ids: Iterable[str]) -> dict[str, int]:
    """Match the games named again from their folder names, leaving a person's matches.

    On the caller's thread, under the scan's job kind: it writes a .info for every game
    whose match moves. Ids the library does not hold are left out of the counts, and a
    game whose folder cannot be written to is left alone and counted as `skipped`.
    """
    from pathlib import Path

    from common.games import auto_match as matching
    from common.games import game_repository

    held = game_repository.catalog()
    games = [held[one] for one in dict.fromkeys(game_ids) if one in held]
    blocked = {id(game) for game, _ in library_folders.blocked_games(games)}
    with job_registry.track(job_registry.KIND_LIBRARY_SCAN,
                            title=t("said.title_auto_match")) as job:
        counts, moved = matching.match_again([g for g in games if id(g) not in blocked])
        job.detail = job_details.auto_matched(counts)
    for game in moved:
        game_repository.refresh_game(Path(str(game.full_path_game)))
    return {**counts, "skipped": len(blocked)}


def recount_vps_state() -> job_registry.Job:
    """Its own job kind: read-only, so it may run beside a scan."""
    return start(job_registry.KIND_VPS_ROLLUP,
                 lambda job: library_vps_state.recount(job.reporter()))


def one_info_pass(work: Callable[..., object], title: str = "") -> job_registry.Job:
    """The scan's job kind, because these rewrite exactly the files a scan does and two
    at once would interleave writes to the same file."""
    return start(job_registry.KIND_LIBRARY_SCAN, lambda job: work(job=job), title)


def dismissed() -> dict[str, Any]:
    return {"rows": [{"row": row, "ids": ids} for row, ids in dismissals.dismissed().items()]}


def dismiss(row: str, ids: Iterable[str]) -> dict[str, Any]:
    """Replace what one Overview row was dismissed over; none at all brings it back."""
    dismissals.dismiss(row, ids)
    return dismissed()


def vps_rollup() -> dict[str, Any]:
    """The last rollup counted, and when. Never live: counting it resolves media for
    every game, which measured 650ms over 149 folders."""
    return library_vps_state.stored() or {"computed": "", "games": 0, "matched": 0,
                                          "kinds": []}


def vps_discovery() -> dict[str, Any]:
    """The catalog against the library, as the last count found it. Never live."""
    return library_vps_state.discovery()


def discovery_home(days: int) -> dict[str, Any]:
    """Discovery's front page: the VPS shelves from the last comparison, the media a source can
    fill from the library now. Asks no site."""
    return vps_home.home(library_vps_state.discovery(), library_vps_state.kept_listings(), days,
                         media=media_fill.offered_listing(), holders=media_fill.holders(),
                         marks=vps_marks.read(), updates=_updates_found(),
                         formats=launchers.playable_formats(
                             launchers.get_launcher_store().launchers()),
                         library=library_games(),
                         opened=opened_pages.all_recent(),
                         unmatched_games=_unmatched_games())


def discovery_missing() -> dict[str, Any]:
    """What Home's `missing` counts, without the rows it draws: for each tab `count`, `on_vps`
    and `game_ids`, and `counted`. Built as `discovery_home` builds them. `offered` is the
    games a source can fill, by kind, from the same read of what sources hold."""
    found = library_vps_state.discovery()
    offered = media_fill.offered_listing()
    tabs = vps_home.missing_tabs(found, library_vps_state.kept_listings(), vps_marks.read(),
                                 media=offered, holders=media_fill.holders())
    return {"counted": found.get("counted") or "",
            "missing": {tab: {key: one[key] for key in ("count", "on_vps", "game_ids")}
                        for tab, one in tabs.items()},
            "offered": {kind: sorted(one["game_id"] for one in records)
                        for kind, records in offered.items()}}


def library_games() -> list[dict[str, Any]]:
    """Each game of the library as `{game_id, name, vps_id, tables}`, in the catalog's order:
    the entry in force and the count of tables the Games grid reads. Empty where they cannot
    be read, so Home goes without the shelf that needs them."""
    try:
        return [{"game_id": game_id, "name": game_title(game),
                 "vps_id": effective_vps_id(normalize_meta(game.meta_config or {})),
                 "tables": len(table_entries(game.meta_config))}
                for game_id, game in game_repository.catalog().items()]
    except Exception:  # noqa: BLE001 - one shelf of Home
        logger.warning("Could not read the library's games for Home", exc_info=True)
        return []


def _unmatched_games() -> list[dict[str, Any]]:
    """The library's games with no VPS match, as `{game_id, name, manufacturer, year}`. One
    declared to be in no catalog is not among them: that is an answer, not a gap. Empty where
    they cannot be read, so Home goes without the shelf that needs them."""
    from common.games import auto_match as matching

    try:
        out = []
        for game_id, game in sorted(game_repository.catalog().items()):
            if matching.unmatched(normalize_meta(game.meta_config or {})):
                row = game_repository.game_to_row(game)
                out.append({"game_id": game_id, "name": row["name"],
                            "manufacturer": row["manufacturer"], "year": row["year"]})
        return out
    except Exception:  # noqa: BLE001 - one shelf of Home
        logger.warning("Could not read the library's unmatched games for Home", exc_info=True)
        return []


def _updates_found() -> list[dict[str, Any]]:
    """The games with an update; none where they cannot be found, so Home goes without
    that shelf rather than without itself."""
    try:
        return library_updates.found()
    except Exception:  # noqa: BLE001 - one shelf of Home
        logger.warning("Could not look for updates", exc_info=True)
        return []


def discovery_updates(game_id: str = "") -> dict[str, Any]:
    """Every game of the library with a later version of something it holds, minus what is
    skipped or ignored; the one game named by `game_id` where it is set. Asks no site."""
    found = library_updates.found_for(game_id) if game_id else library_updates.found()
    return vps_updates.shelf(found, vps_marks.read())


def picture_changes(game_id: str = "") -> dict[str, Any]:
    """The pictures a source has replaced since they were placed, of one game or every game,
    as the last daily check found them. Asks no site."""
    from common.online import picture_changes as changes

    return {"checked_at": changes.checked_at(), "pictures": changes.pending(game_id)}


def discovery_marks() -> dict[str, Any]:
    """Every Wanted and Ignored mark on this install, the newest first."""
    return {"marks": vps_marks.listed()}


def mark_discovery(state: str, scope: str, vps_id: str, detail: str) -> dict[str, Any]:
    """Put a state on a thing; `replaced` is the state it held, "" if it held none."""
    return {"replaced": vps_marks.mark(state, scope, vps_id, detail), **discovery_marks()}


def unmark_discovery(scope: str, vps_id: str, detail: str) -> dict[str, Any]:
    """Take the mark off a thing; `replaced` is the state it held, "" if it held none."""
    return {"replaced": vps_marks.clear(scope, vps_id, detail), **discovery_marks()}


def info_maintenance() -> dict[str, Any]:
    """The three states a `.info` file can be in that are worth acting on, the folders
    with no readable one at all, and the games whose id theirs could not take. Counted
    off the library already in memory."""
    counts = game_repository.info_maintenance_counts()
    return {"pending_upgrade": counts.get("pending_upgrade", 0),
            "restorable": counts.get("restorable", 0),
            "newer_than_us": counts.get("newer_than_us", 0),
            "newest_backup": game_service.newest_backup_stamp() or "",
            "pending_games": game_service.pending_upgrade_game_names(),
            "restorable_games": game_service.restorable_game_names(),
            "unreadable": game_repository.unreadable_games(),
            "unwritten": game_repository.unwritten_games()}

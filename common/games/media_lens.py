"""Every media file in the library, as its own lens on it.

A row here is a file, or the absence of one. That is the difference from the media a game
or a table reports: those answer "what does this game look like", one cell per kind, and a
cell can hold one fact. A file has several - which tables it serves, who put it there,
what it is - and a missing one has to be countable and selectable before anything can be
done about it in bulk.

Named for the file rather than for the table, because one shared file serves every table
in its folder. Reporting it once per table would count one gap as four, repeat its origin
four times, and put the same file in a selection four times over.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from time import perf_counter
from typing import Any

from common.games import (
    asset_origin,
    asset_resolver,
    collation,
    game_repository,
    media_probe,
    media_service,
    table_lens,
)
from common.games.game_metadata import effective_vps_id
from common.games.game_repository import game_to_row
from common.i18n import t
from common.media import sized_media
from common.media.media_specs import (
    MEDIA_SPECS,
    VIDEO_FAMILY,
    media_candidates,
    media_label_map,
    resolve_media_entries,
    resolve_media_pages,
    spec_named,
    unsupported_named,
)

logger = logging.getLogger("vpinfe.common.games.media_lens")


# The tiers that are this slot's own file, by the scope they belong to. A set or a
# cross-kind fallback is neither: it is another slot's file being borrowed, so it
# leaves this row empty and is reported as what is standing in.
# Two ways a file on disk answers for nobody, and they are not the same question. An
# ORPHAN names a table this folder does not have, so nothing will ever look for it.
# UNUSED is correctly named and covered by something more specific - it is the fallback,
# and it resolves again the moment what covers it goes.
ORPHAN = "orphan"
UNUSED = "unused"
# A file VPinFE named for a kind in an extension that kind cannot read: here, and as good
# as no file until it is converted.
UNSUPPORTED = "unsupported"
LOOSE = (ORPHAN, UNUSED, UNSUPPORTED)

# Played by VPinFE's own decoder, so no browser's codecs matter to them.
_NOT_PLAYED_BY_A_BROWSER = frozenset({"real_dmd_video", "real_dmd_color_video"})

_SHARED_TIERS = frozenset({"game", "default"})
_TABLE_TIERS = frozenset({"table"})
_KINDS = [spec.kind for spec in MEDIA_SPECS]

# The library listing's rows by game id, with the catalog entry and the stamp they were
# read against. Read and written only under the lock.
_HELD: dict[str, tuple[Any, tuple, list[dict]]] = {}
_LOCK = threading.Lock()


def _covered(game_dir: Path, files: set[str], medias: set[str], kind: str,
             variant: str, sets: dict[str, str] | None, stem: str | None,
             tiers: frozenset[str], winner: Path | None) -> list[Path]:
    """Files at this scope that the winner hides, each of which is a row of its own.

    A shared write only clears the spec-named family at its stem, so a `wheel.png` a
    catalog left sits under a `(Wheel) <folder>.png` and comes back the moment that one
    is removed. It is not dead, it is the fallback - which is why it is a row somebody
    can look at and decide about rather than a name in a column.
    """
    if winner is None:
        return []
    return sorted((item.path
                   for item in media_candidates(game_dir, files, medias, kind, variant,
                                                stem, sets)
                   if item.tier in tiers and item.path != winner),
                  key=lambda path: path.name)


def _orphans(files: set[str], medias: set[str], known: set[str]) -> list[tuple[str, str, str]]:
    """Spec-named files whose stem is no table here and not the folder.

    `(kind, stem, relative path)` each. Only the two places resolution looks - the
    folder root and `medias/` itself - because a file deeper than that is inside a set,
    where its name answers to the set rather than to a table.

    A file we did not name is not considered at all. `spec_named` answers None for one,
    and an unrecognized file in a folder the user owns is not a defect.
    """
    found = []
    for name in sorted(files | {item for item in medias if "/" not in item}):
        parsed = spec_named(name)
        if parsed is None or parsed.stem.lower() in known:
            continue
        found.append((parsed.kind, parsed.stem, name if name in files else f"medias/{name}"))
    return found


def _unsupported(files: set[str], medias: set[str],
                 variant: str) -> list[tuple[str, str, int, str]]:
    """`(kind, stem, page, relative path)` for each file named for a kind in an extension
    its family does not hold but can be converted to. Judged from the name alone, in the two
    places `_orphans` looks."""
    found = []
    for name in sorted(files | {item for item in medias if "/" not in item}):
        parsed = unsupported_named(name, variant)
        if parsed is not None:
            found.append((parsed.kind, parsed.stem, parsed.page,
                          name if name in files else f"medias/{name}"))
    return found


def _codec(kind: str, path: Path | None) -> str | None:
    """The codec a browser would be asked to play, for a video that one plays."""
    if (path is None or path.suffix.lower() not in VIDEO_FAMILY
            or kind in _NOT_PLAYED_BY_A_BROWSER):
        return None
    return media_probe.probe(path)["video_codec"]


def _unused(game_id: str, row: dict, kind: str, path: Path, game_dir: Path,
            recorded: dict, hosts: dict, table: str = "",
            table_file: str = "") -> dict:
    """A file that is here, correctly named, and that nothing resolves to.

    Not the same as an orphan and not as safe to delete: the name is one resolution
    would look for, so removing whatever covers it brings this back. It is the fallback,
    and `serves` is 0 because right now nothing falls to it.
    """
    return {**_row(game_id, row, kind, table, table_file),
            "id": f"{game_id}:{kind}:{path.name}",
            "serves": 0,
            "present": True,
            "file": path.name,
            "path": asset_origin.path_of(game_dir, path) or None,
            "via": UNUSED,
            "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
            "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
            "standing_in": ""}


def _row(game_id: str, row: dict, kind: str, table: str, table_file: str,
         page: int = 1) -> dict:
    """The fields every row carries whatever its scope. `label` is the kind's and the
    same on every page of it; `named` is what a person calls this row."""
    label = media_label_map().get(kind, kind)
    return {"id": f"{game_id}:{kind}:{table}" + (f":{page}" if page > 1 else ""),
            "game_id": game_id,
            "game": str(row.get("name") or ""),
            "manufacturer": str(row.get("manufacturer") or ""),
            "year": str(row.get("year") or ""),
            "kind": kind, "label": label,
            "page": page,
            "named": label if page == 1 else t("media.page_named", label=label, page=page),
            "label_key": f"media.kind.{kind}.label",
            "table": table, "table_file": table_file,
            # What a catalog is asked for. On the row because every act on a slot needs
            # it and the alternative is a lookup per row against the games list.
            "vps_id": str(row.get("vps_id") or "")}


def _page_rows(game_id: str, row: dict, kind: str, table: str, table_file: str,
               first: dict, pages: list[Path], game_dir: Path, recorded: dict,
               hosts: dict) -> list[dict]:
    """The rows for pages 2 and up of a slot, each a file of its own and sharing the
    first page's scope."""
    return [{**first,
             **_row(game_id, row, kind, table, table_file, number),
             "file": path.name,
             "path": asset_origin.path_of(game_dir, path) or None,
             "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
             "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
             "version": sized_media.version(path)}
            for number, path in enumerate(pages[1:], 2)]


def _game_rows(game_id: str, entry: Any, variant: str,
               sets: dict[str, str] | None) -> list[dict] | None:
    """Every row of one game, of every kind, or None where its folder cannot be read."""

    def resolve(game_dir: Path, files: set[str], medias: set[str],
                stem: str | None) -> dict:
        return resolve_media_entries(game_dir, files, medias, variant, stem, sets)

    found: list[dict] = []
    row = {**game_to_row(entry),
           "vps_id": effective_vps_id(entry.meta_config or {})}
    game_dir = Path(entry.full_path_game or "")
    try:
        folder = asset_resolver.folder_listing(game_dir)
        files, medias = media_service.media_contents(game_dir, folder)
    except OSError:
        logger.warning("media: cannot read %s", game_dir)
        return None

    tables = [table for table in table_lens.table_files(game_dir, folder[0])
              if table["id"]]
    shared = resolve(game_dir, files, medias, None)
    # Resolved per table only to find the files named for one. The folder is walked
    # once above; each of these is set lookups against what it found.
    owned: dict[str, list[dict]] = {}
    for table in tables:
        stem = Path(table.get("filename") or "").stem
        for slot, hit in resolve(game_dir, files, medias, stem).items():
            if hit.tier in _TABLE_TIERS:
                owned.setdefault(slot, []).append({**table, "stem": stem,
                                                   "hit": hit})

    recorded = asset_origin.sources(game_dir)
    hosts = {key: str(source.get("host", "") or "").strip()
             for key, source in recorded.items()
             if str(source.get("host", "") or "").strip()}

    known = {game_dir.name.lower()} | {Path(table.get("filename") or "").stem.lower()
                                       for table in tables}
    for orphan_kind, stem, relative in _orphans(files, medias, known):
        path = game_dir / relative
        found.append({
            **_row(game_id, row, orphan_kind, "", stem),
            # Named for the file. The shared row of the same game and kind derives
            # its id from the table, which for both of them is "" - so without this
            # the two collide, the grid keys one over the other, and a click hands
            # the panel the wrong row.
            "id": f"{game_id}:{orphan_kind}:{relative}",
            "serves": 0,
            "present": True,
            "file": Path(relative).name,
            "path": relative,
            "via": ORPHAN,
            "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
            "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
            "standing_in": "",
        })

    for kind, stem, page, relative in _unsupported(files, medias, variant):
        path = game_dir / relative
        found.append({
            **_row(game_id, row, kind, "", stem, page),
            "id": f"{game_id}:{kind}:{relative}",
            "serves": 0,
            "present": True,
            "file": Path(relative).name,
            "path": relative,
            "via": UNSUPPORTED,
            "format": path.suffix[1:].upper(),
            "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
            "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
            "standing_in": "",
        })

    for slot in _KINDS:
        hit = shared.get(slot)
        shared_path = (hit.path if hit is not None and hit.tier in _SHARED_TIERS
                       else None)
        mine = owned.get(slot) or []
        serves = len(tables) - len(mine)
        # A shared row that nothing falls through to is not a gap: every table has a
        # file of its own, so there is nothing for a shared file to serve. Kept where
        # a file is actually there, because a file on disk is a row whatever uses it.
        if shared_path is not None or serves > 0 or not tables:
            first = {
                **_row(game_id, row, slot, "", ""),
                # Only ever about a file. A row with none says nothing here rather
                # than how many tables would have used one, which is a different
                # question wearing the same column.
                "serves": serves if shared_path is not None else None,
                "present": shared_path is not None,
                "file": shared_path.name if shared_path is not None else None,
                "path": asset_origin.path_of(game_dir, shared_path) or None,
                "via": (hit.tier if hit is not None and shared_path is not None
                        else None),
                "origin": asset_origin.origin_of(hosts, game_dir,
                                                 shared_path) or None,
                "matched_to": asset_origin.match_of(recorded, game_dir,
                                                    shared_path) or None,
                "version": sized_media.version(shared_path),
                "video_codec": _codec(slot, shared_path),
                "standing_in": (hit.tier if hit is not None and hit.path is not None
                                and hit.tier not in _SHARED_TIERS else ""),
            }
            found.append(first)
            if shared_path is not None and hit is not None:
                pages = resolve_media_pages(game_dir, files, medias, {slot: hit},
                                            variant, None).get(slot, [])
                found.extend(_page_rows(game_id, row, slot, "", "", first, pages,
                                        game_dir, recorded, hosts))
            for covered in _covered(game_dir, files, medias, slot, variant, sets,
                                    None, _SHARED_TIERS, shared_path):
                found.append(_unused(game_id, row, slot, covered, game_dir,
                                     recorded, hosts))
        for table in mine:
            own = table["hit"].path
            table_file = str(table.get("filename") or "")
            first = {
                **_row(game_id, row, slot, table["id"], table_file),
                # One: the table it is named for. Not null - the question applies
                # and has an answer, and a blank here would read as "no file".
                "serves": 1,
                "present": True,
                "file": own.name,
                "path": asset_origin.path_of(game_dir, own) or None,
                "via": table["hit"].tier,
                "origin": asset_origin.origin_of(hosts, game_dir, own) or None,
                "matched_to": asset_origin.match_of(recorded, game_dir, own) or None,
                "version": sized_media.version(own),
                "video_codec": _codec(slot, own),
                "standing_in": "",
            }
            found.append(first)
            pages = resolve_media_pages(game_dir, files, medias, {slot: table["hit"]},
                                        variant, table["stem"]).get(slot, [])
            found.extend(_page_rows(game_id, row, slot, table["id"], table_file, first,
                                    pages, game_dir, recorded, hosts))
            for covered in _covered(game_dir, files, medias, slot, variant, sets,
                                    table["stem"], _TABLE_TIERS, own):
                found.append(_unused(game_id, row, slot, covered, game_dir,
                                     recorded, hosts, table["id"],
                                     str(table.get("filename") or "")))
    return found


def _library_rows(variant: str, sets: dict[str, str] | None) -> list[dict]:
    """Every game's rows, read again only for a game that has changed since."""
    settings = (variant, tuple(sorted((sets or {}).items())))
    catalog = game_repository.catalog()
    for gone in _HELD.keys() - catalog.keys():
        del _HELD[gone]
    found: list[dict] = []
    for game_id, entry in catalog.items():
        # Taken before the read, so a write made during it is seen next time.
        stamp = (settings, table_lens.game_stamp(Path(entry.full_path_game or "")))
        held = _HELD.get(game_id)
        if held is None or held[0] is not entry or held[1] != stamp:
            rows = _game_rows(game_id, entry, variant, sets)
            if rows is None:
                _HELD.pop(game_id, None)
                continue
            held = (entry, stamp, rows)
            _HELD[game_id] = held
        found.extend(held[2])
    return found


def library_rows() -> list[dict]:
    """`listing` for the whole library, in the order the games were read: for a caller that
    filters the rows and has no use for the sort that makes a page."""
    variant, sets = media_service.media_settings()
    with _LOCK:
        return _library_rows(variant, sets)


def read_ahead() -> None:
    """Read the library's media on a thread of its own, so the first page to ask is not
    the one that waits."""
    threading.Thread(target=_read_ahead, daemon=True, name="media-read-ahead").start()


def _read_ahead() -> None:
    started_at = perf_counter()
    try:
        total = listing()["total"]
    except Exception:
        logger.exception("media: could not read ahead; the first page to ask reads it")
        return
    logger.info("Media: %s rows read ahead in %.2fs", total, perf_counter() - started_at)


SHARED_FIELDS = ("game_id", "kind", "page", "present", "file", "path", "via", "origin",
                 "matched_to", "version")


def shared_rows() -> list[dict[str, Any]]:
    """What a count of the library's media covers reads: one row per game and kind (a later
    page of a paged kind too) that is the shared file or the lack of one, cut to the fields
    that count reads. A file named for one table, an orphaned one, an unused one and an
    unsupported one are not here. In the order the games were read, unsorted."""
    return [{field: row.get(field) for field in SHARED_FIELDS}
            for row in library_rows()
            if not row.get("table") and row.get("via") not in LOOSE]


def listing(limit: int = 0, offset: int = 0, game: str = "",
            kind: str = "") -> dict[str, Any]:
    """One row per media file the library holds, plus one per file it does not.

    A game and kind has a **shared** row - the file each of its tables falls through to -
    and a table earns a row of its own only where a file is named for it. Two rows are
    deliberately not reported, because each would be the software inventing a gap: an
    empty per-table row, which would say a table-specific file ought to exist, and a
    missing shared row in a folder where every table already has its own.

    One game is always read from disk; its callers write next on what it says.
    """
    variant, sets = media_service.media_settings()
    if game:
        entry = game_repository.catalog().get(game)
        found = (_game_rows(game, entry, variant, sets) or []) if entry is not None else []
    else:
        with _LOCK:
            found = _library_rows(variant, sets)
    if kind:
        found = [item for item in found if item["kind"] == kind]
    found = sorted(found, key=lambda item: (collation.sort_key(item["game"]),
                                            collation.sort_key(item["label"]),
                                            collation.sort_key(item["table_file"])))
    total = len(found)
    window = found[offset:offset + limit] if limit else found[offset:]
    return {"total": total, "offset": offset, "count": len(window), "media": window}

"""What the library holds, reconciled against what the .info files say.

Discovery is not enrichment. Listing a folder's .vpx and giving each one an entry costs
a directory read the scan has already done; parsing them for a rom and an author costs an
OLE read each. Fusing the two is why only the table that launches ever had an id, and why
a folder's other builds could not be addressed at all.

An entry added here carries a filename and nothing else. It claims no parsed facts, so it
is not the half-filled record `entry_from_parsed` warns about - the minting pass gives it
an id, and enrichment fills the rest in later.

**No table is ever removed.** A table's entry carries the user's `hidden`, its play stats
and its match records, and a library on a network share reads as entirely absent for as
long as the mount is late. A file that is not there is recorded with the time it was first
missed and left for a person to act on.

The manifest's other files follow the same rule where a line holds something. A line with
a `source` is marked and kept; one with nothing in it is dropped, since the next scan lists
the file again. A game whose folder could not be read in full is not reconciled.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Collection, Iterable
from typing import Any

from common.games import file_listing, info_files, table_files, unwritten
from common.games.game import Game
from common.games.game_metadata import keep_game_meta, load_game_meta
from common.games.info_codec import ASSETS_KEY
from common.games.tables import (
    ABSENT_SINCE_KEY,
    TABLE_FILENAME_KEY,
    TABLES_KEY,
    entry_filename,
    table_entries,
)
from common.timestamps import utc_now_iso

logger = logging.getLogger("vpinfe.common.games.library_discovery")


def _reconcile(game: Game, on_disk: list[str],
               listing: Collection[str] | None = None) -> tuple[dict, dict[str, int]]:
    """The record this game should hold, and what changed to get there.

    Tables follow `on_disk`, the files in the manifest follow `listing`; either is left
    alone where it is None or, for tables, empty.
    """
    config = load_game_meta(game)
    news = {"found": 0, "absent": 0, "returned": 0, "split": 0,
            "files_listed": 0, "files_dropped": 0, "files_absent": 0, "files_returned": 0}
    if on_disk:
        _reconcile_tables(config, on_disk, news)
        info_path = info_files.info_path_of(game)
        news["split"] = int(unwritten.held(info_path) is None and table_files.due(
            config, game.game_dir_name or "", on_disk, getattr(game, "info_names", None),
            os.path.isfile(info_path)))
    if listing is not None:
        folder = str(game.full_path_game)
        held, changed = file_listing.sync(
            config.get(ASSETS_KEY), listing, utc_now_iso(),
            lambda path: os.path.lexists(os.path.join(folder, path)))
        for key, count in changed.items():
            news[f"files_{key}"] = count
        if any(changed.values()):
            config[ASSETS_KEY] = held
    return config, news


def _reconcile_tables(config: dict, on_disk: list[str], news: dict[str, int]) -> None:
    entries = dict(table_entries(config))
    described = {entry_filename(entry).lower() for entry in entries.values()}
    present = {name.lower() for name in on_disk}

    for name in on_disk:
        if name.lower() in described:
            continue
        # Keyed by filename with no id, which is the shape the minting pass expects of
        # an entry it has not reached yet.
        entries[name] = {TABLE_FILENAME_KEY: name}
        news["found"] += 1

    for key, entry in list(entries.items()):
        filename = entry_filename(entry).lower()
        if not filename:
            continue
        recorded = str(entry.get(ABSENT_SINCE_KEY, "") or "")
        if filename not in present and not recorded:
            entries[key] = {**entry, ABSENT_SINCE_KEY: utc_now_iso()}
            news["absent"] += 1
        elif filename in present and recorded:
            entries[key] = {k: v for k, v in entry.items() if k != ABSENT_SINCE_KEY}
            news["returned"] += 1

    config[TABLES_KEY] = entries


def discover(games: Iterable[Game]) -> dict[str, int]:
    """Reconcile every game's tables and files against what the scan found on disk.

    Writes only where something changed, so a settled library costs a pass over what is
    already in memory and no I/O at all.
    """
    totals = {"found": 0, "absent": 0, "returned": 0, "split": 0, "games": 0,
              "files_listed": 0, "files_dropped": 0, "files_absent": 0, "files_returned": 0}
    for game in games:
        on_disk = getattr(game, "table_files", None)
        listing = getattr(game, "file_listing", None)
        # None means the scan never recorded a listing for this game, and empty means it
        # read one and saw nothing. Concluding "every table is gone" from a listing we do
        # not have is the one outcome worth refusing outright.
        if not on_disk and listing is None:
            continue
        try:
            config, news = _reconcile(game, list(on_disk or ()), listing)
        except Exception:
            logger.exception("Could not reconcile tables for %s",
                             game.game_dir_name)
            continue
        if not any(news.values()):
            continue
        keep_game_meta(game, config)
        for kind, count in news.items():
            totals[kind] += count
        totals["games"] += 1

    if totals["games"]:
        logger.info("Discovery: %s tables found, %s newly absent, %s back; files %s listed, "
                    "%s dropped, %s newly absent, %s back; across %s games",
                    totals["found"], totals["absent"], totals["returned"],
                    totals["files_listed"], totals["files_dropped"], totals["files_absent"],
                    totals["files_returned"], totals["games"])
    return totals


def absent_since(entry: dict[str, Any] | None) -> str:
    """When the file behind this entry was first not found, or ""."""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(ABSENT_SINCE_KEY, "") or "")

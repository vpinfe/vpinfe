"""Reading the tables discovery found, so an entry says more than its own name.

Discovery is cheap because it only compares listings. This is the other half and it is
not cheap: `extract_file` hashes the whole `.vpx` to fill `file_hash`, so enriching one
table is a full file read. That is why this never runs inline - it belongs on a job,
where it can report progress and be watched.

Only entries nothing has read are touched. `is_parsed` is the test, so a table keeps
whatever was recorded about it and the pass costs nothing on a library it has finished.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from common.failures import why
from common.games import unreadable
from common.games.game import Game
from common.games.game_metadata import keep_game_meta, load_game_meta
from common.games.tables import (
    TABLES_KEY,
    entry_filename,
    entry_from_parsed,
    is_parsed,
    table_entries,
)
from common.runtime.jobs import JobReporter

logger = logging.getLogger("vpinfe.common.games.library_enrichment")


class TableReader(Protocol):
    """What enrichment needs of a table reader: one file, read.

    The capability rather than the program, for the reason `_parser` gives - this module
    names the thing that reads a table file exactly once.
    """

    def single_file_extract(self, vpx_file: str) -> dict[str, str] | None: ...


def pending(games: Iterable[Game]) -> list[tuple[Game, str, str]]:
    """Every (game, key, filename) an entry exists for and nothing has read yet, but for
    a file that could not be read and has not changed since.

    Counted before the work starts so a job can say how many of how many, which on a
    network share is the difference between a progress bar and a hang.
    """
    todo = []
    for game in games:
        on_disk = {name.lower() for name in (getattr(game, "table_files", None) or ())}
        if not on_disk:
            continue
        folder = Path(str(game.full_path_game or ""))
        for key, entry in table_entries(getattr(game, "meta_config", {})).items():
            filename = entry_filename(entry)
            if (filename.lower() in on_disk and not is_parsed(entry)
                    and unreadable.failed(folder / filename) is None):
                todo.append((game, key, filename))
    return todo


def _read(parser: TableReader, game: Game, filename: str) -> dict | None:
    path = Path(str(game.full_path_game or "")) / filename
    try:
        parsed = parser.single_file_extract(str(path))
    except Exception as exc:
        logger.exception("Could not read %s", path)
        unreadable.note(path, why(exc))
        return None
    if not parsed:
        unreadable.note(path)
    return parsed


def _parser() -> TableReader:
    """The thing that reads a table file. One place names it, so this module's coupling
    to a particular program is a single line rather than one per caller."""
    from common.games.formats.vpx_parser import VPXParser

    return VPXParser()


def read_one(table: Path) -> dict | None:
    """What one table file says about itself, or None if it could not be read.

    The same parse `enrich` does across a library, for the moment a single table
    arrives. Here rather than at the call site because this module is where reading a
    table's own format belongs - a copy operation should not have to know which program
    wrote the file it just copied.
    """
    try:
        return _parser().single_file_extract(str(table))
    except Exception:
        logger.exception("Could not read %s", table)
        return None


def enrich(games: Iterable[Game], reporter: JobReporter | None = None) -> dict[str, int]:
    """Fill in what a parse knows for every table nothing has read.

    Written per game rather than per table: a folder with three unread builds is one
    `.info` write, not three.
    """
    todo = pending(games)
    totals = {"read": 0, "failed": 0, "games": 0}
    if not todo:
        return totals

    parser = _parser()
    by_game: dict[int, list[tuple[str, str]]] = {}
    order: list[Game] = []
    for game, key, filename in todo:
        if id(game) not in by_game:
            by_game[id(game)] = []
            order.append(game)
        by_game[id(game)].append((key, filename))

    done = 0
    for game in order:
        config = load_game_meta(game)
        entries = dict(table_entries(config))
        changed = False
        for key, filename in by_game[id(game)]:
            done += 1
            if reporter:
                reporter.progress(done, len(todo), filename)
            parsed = _read(parser, game, filename)
            if not parsed:
                totals["failed"] += 1
                continue
            # Merged over the entry, never replacing it: hidden, play stats and match
            # records live here too and a parse knows nothing about them.
            entries[key] = {**entries.get(key, {}), **entry_from_parsed(parsed)}
            totals["read"] += 1
            changed = True
        if not changed:
            continue
        config[TABLES_KEY] = entries
        keep_game_meta(game, config)
        totals["games"] += 1

    message = (f"Enrichment: read {totals['read']} tables across {totals['games']} games"
               + (f", {totals['failed']} could not be read" if totals["failed"] else ""))
    (reporter.log(message) if reporter else logger.info(message))
    return totals

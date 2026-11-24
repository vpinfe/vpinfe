"""The order a table's backglass and score display draw in, kept to what its backglass file
places.

Every table gets a live DMD from the launcher's order. A table whose file places a Score
View DMD is given an order of its own in its settings so that file's art shows with the DMD
on it. The app says what to hold (`launcher_ops.backglass_order`) and this writes it, from
the state the file and the settings are in, so running it again changes nothing. What was
written is recorded (`order_record`), and only a pair still equal to its record is changed
or taken out.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from common import failures, service_errors
from common.apps.contract import TableOrder
from common.games import (
    backglass_ops,
    game_identity,
    game_repository,
    launcher_ops,
    order_record,
)
from common.i18n import t
from common.runtime import jobs, shutdown

logger = logging.getLogger("vpinfe.common.games.backglass_order")

# hash(game_id) % 64: two unrelated games can share a lock.
_LOCKS = tuple(threading.RLock() for _ in range(64))
_STARTING = threading.Lock()


def _lock_of(game_id: str) -> threading.RLock:
    return _LOCKS[hash(game_id) % len(_LOCKS)]


def settle(game_id: str, launcher_id: str = "") -> list[dict[str, str]]:
    """Bring the order of each table of the game to what its file places. Answers the
    tables that could not be written, each with why, and never raises for one: the file or
    the settings the caller changed stand either way."""
    game = game_repository.game_by_id(game_id)
    return _settle([game], launcher_id)["failed"] if game is not None else []


def settle_table(table_id: str) -> list[dict[str, str]]:
    """`settle` for the game that holds this table."""
    return settle_tables([table_id])


def settle_tables(table_ids: Iterable[str]) -> list[dict[str, str]]:
    """`settle` for each game that holds one of these tables, once however many of its tables
    are named."""
    from common.games.table_identity import find_table_by_id

    games = game_repository.all_games()
    held: dict[str, Any] = {}
    for table_id in table_ids:
        found = find_table_by_id(games, table_id)
        if found is not None:
            held.setdefault(game_identity.game_id(found[0]), found[0])
    return _settle(held.values())["failed"] if held else []


def refuse_while_running() -> None:
    """Refuse, before anything is written, while a pass over the library is still going."""
    if jobs.active(jobs.KIND_BACKGLASS_ORDER):
        raise jobs.JobBusyError(t("error.jobs.order_busy"))


@contextlib.contextmanager
def launcher_write() -> Iterator[None]:
    """For a write at the launcher's scope that will start a pass: refuses while one runs,
    and holds off another such write until `settle_launcher` has started this one."""
    with _STARTING:
        refuse_while_running()
        yield


def settle_launcher(launcher_id: str) -> jobs.Job:
    """`settle` for every game that has a table this launcher plays, as a job: the library
    is read once and each backglass file only when it has changed. Called within
    `launcher_write`."""
    def work(job: jobs.Job) -> dict[str, Any]:
        return _settle(list(game_repository.catalog().values()), launcher_id, job)

    return jobs.submit(jobs.KIND_BACKGLASS_ORDER, work)


def _settle(games: Iterable[Any], launcher_id: str = "",
            job: jobs.Job | None = None) -> dict[str, Any]:
    games = list(games)
    written: list[str] = []
    failed: list[dict[str, str]] = []
    for index, game in enumerate(games):
        if shutdown.requested():
            break
        if job is not None:
            job.reporter().progress(index, len(games), t("said.table_order"))
        with _lock_of(game_identity.game_id(game)):
            _game(game, launcher_id, written, failed)
    if job is not None:
        job.problems = len(failed)
    return {"games": len(games), "written": written, "failed": failed}


def _game(game: Any, launcher_id: str, written: list[str], failed: list[dict[str, str]]) -> None:
    """A table is looked at only where its file places a Score View DMD or a record says
    this wrote its order; every other table has nothing to bring or take back."""
    try:
        tables = backglass_ops.score_dmds(game, launcher_id)
    except (service_errors.ServiceError, OSError):
        logger.exception("Could not read the backglass files of %s",
                         game_identity.game_id(game))
        return
    if not tables:
        return
    try:
        record = order_record.held()
    except order_record.Unreadable as exc:
        logger.warning("The record of draw orders cannot be read: %s", failures.why(exc))
        failed.extend(_failure(one, exc) for one in tables if one.in_file)
        return
    for one in tables:
        kept = record.get(one.table, {})
        if not (one.in_file or kept):
            continue
        try:
            order = launcher_ops.backglass_order(one.launcher, one.file, one.in_file, kept)
            if _apply(one.table, order, kept):
                written.append(one.table)
        except (service_errors.ServiceError, OSError) as exc:
            logger.warning("Could not set the draw order of table %s: %s", one.table,
                           failures.why(exc))
            failed.append(_failure(one, exc))


def _failure(one: backglass_ops.Scored, exc: BaseException) -> dict[str, str]:
    return {"table": one.table, "name": Path(one.file).name, "reason": failures.why(exc)}


def _apply(table: str, order: TableOrder, kept: dict[str, str]) -> bool:
    """Write what the order calls for and keep the record of it. A pair is recorded before
    it is written and a cleared one after; a pair being replaced is cleared first, so the
    settings never hold a pair the record does not name. Whether they were written."""
    ours = kept if order.ours is None else order.ours
    if order.write and ours:
        if kept:
            launcher_ops.write_table_settings(table, dict.fromkeys({**kept, **order.write}, ""),
                                              settle=False)
        order_record.keep({table: ours})
        try:
            launcher_ops.write_table_settings(table, order.write, settle=False)
        except Exception:
            _put_back(table, kept)
            raise
        return True
    if order.write:
        launcher_ops.write_table_settings(table, order.write, settle=False)
    if ours != kept:
        order_record.keep({table: ours})
    return bool(order.write)


def _put_back(table: str, kept: dict[str, str]) -> None:
    try:
        order_record.keep({table: kept})
    except (service_errors.ServiceError, OSError):
        logger.exception("Could not put back the record of table %s", table)

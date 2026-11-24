"""The sources watch: every due source refreshes, and what moved is compared.

One daemon thread for as long as the process runs. Each wake asks every source that can
refresh whether it changed; a source that changed has what it lists stamped into the
arrivals, and a change to the spreadsheet owes the library a comparison against it. A process
owes one from the start, so a page never opens on a library nobody has counted, and the
comparison is a job the pages only show and never start.

A match, an unmatch, a replaced table or a scan changes what the library holds of the
catalog, so each owes a comparison too. It starts once those stop for a few seconds, and
the entry of the game that changed is brought up to date at once, ahead of it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from common import events
from common.config.config_store import ConfigStore
from common.online import arrivals, sources, vps_source, vpsdb_sync
from common.online.sources import Source
from common.runtime import jobs as job_registry

logger = logging.getLogger("vpinfe.common.online.sources_watch")

# How often the runner wakes to ask whether a source is due. Not a schedule: a source keeps
# its own, and an install left running for a week has to notice a daily check coming round.
_WAKE_SECONDS = 60 * 60

# How long changes to what ties games to the catalog must stop before the comparison they
# owe starts, so a burst of them costs one.
_SETTLE_SECONDS = 3.0

_lock = threading.Lock()
# True from the start: a process owes the library one comparison before anything changes.
_comparison_owed = True
_settling: threading.Timer | None = None
# What last tied each game to the catalog, by game id, to tell a change from a re-read.
_tied: dict[str, tuple] = {}
# Set on the thread that is tying tables for a comparison, whose own writes owe nothing.
_own = threading.local()


def refreshed(source: Source, changed: bool) -> None:
    """Take note that a source was asked. Its listing is stamped into the arrivals when it
    changed, or when it has never been stamped; a change to the spreadsheet owes the
    comparison."""
    global _comparison_owed
    if source.listing is not None and (changed or not arrivals.seeded(source.id)):
        try:
            arrivals.stamp(source.id, source.listing())
        except Exception:
            logger.warning("Could not take what %s lists", source.name, exc_info=True)
    if changed and source.id == vps_source.ID:
        with _lock:
            _comparison_owed = True


def compare() -> bool:
    """Start the comparison of the library with the catalog, after tying the tables the
    catalog now names exactly. False when there is no catalog to compare with, one is already
    running, or another pass over the `.info` files is, which owes the comparison again."""
    from common.games import exact_ties, library_ops
    from common.games.game_service import VPSDB_JSON_PATH

    if not VPSDB_JSON_PATH.exists():
        return False
    _own.tying = True
    try:
        tied = exact_ties.tie_library()
    except Exception:
        logger.warning("Tying tables to their releases failed", exc_info=True)
        tied = 0
    finally:
        _own.tying = False
        with _lock:
            _tied.clear()
    if tied is None:
        return False
    if tied:
        logger.info("Tied %s files to the one VPS record that fits them", tied)
    try:
        library_ops.recount_vps_state()
    except Exception:
        logger.debug("The comparison with VPS did not start", exc_info=True)
        return False
    return True


def owe_comparison_soon() -> None:
    """Owe the comparison, and start it once nothing has asked for one for a few seconds."""
    global _comparison_owed, _settling
    with _lock:
        _comparison_owed = True
        if _settling is not None:
            _settling.cancel()
        _settling = threading.Timer(_SETTLE_SECONDS, compare_if_owed)
        _settling.daemon = True
        _settling.start()


def compare_if_owed() -> None:
    """Start the comparison if one is owed. The debt is cleared before it starts, so a
    change that arrives while it starts is a debt again; one that could not start is
    owed still."""
    global _comparison_owed
    with _lock:
        if not _comparison_owed:
            return
        _comparison_owed = False
    if not compare():
        with _lock:
            _comparison_owed = True


def _comparison_ended(job_id: str, *, failed: bool) -> None:
    """A job ended. If it was a comparison that failed, it is owed again for the next wake;
    if one was owed while it ran, it starts now rather than at the next wake."""
    global _comparison_owed
    job = job_registry.get(job_id)
    if job is None:
        return
    if job.kind == job_registry.KIND_LIBRARY_SCAN:
        if not getattr(_own, "tying", False):
            owe_comparison_soon()
        return
    if job.kind != job_registry.KIND_VPS_ROLLUP:
        return
    if failed:
        with _lock:
            _comparison_owed = True
        return
    compare_if_owed()


def _on_done(job_id: str, **_: object) -> None:
    _comparison_ended(job_id, failed=False)


def _on_failed(job_id: str, **_: object) -> None:
    _comparison_ended(job_id, failed=True)


def _on_game_changed(game: Any = None, **_: object) -> None:
    """A game was re-read. If what ties it to the catalog moved, its entry in the stored
    comparison is brought up to date now and a comparison is owed behind it. A game not seen
    before is only looked at in the stored count, and acted on if that does not hold it."""
    if getattr(_own, "tying", False):
        return
    from common.games import game_identity, library_vps_state

    if game is None:
        owe_comparison_soon()
        return
    game_id = game_identity.game_id(game)
    if not game_id:
        return
    tied = library_vps_state.ties_of(game)
    with _lock:
        before = _tied.get(game_id)
        _tied[game_id] = tied
    if before == tied:
        return
    try:
        if before is None and library_vps_state.counted_as(game, game_id):
            return
        library_vps_state.follow(game, game_id)
    except Exception:
        logger.warning("Could not bring the comparison up to date for %s", game_id,
                       exc_info=True)
    owe_comparison_soon()


def listen() -> None:
    """Hear comparisons end and games change. Once is enough; asking again changes nothing."""
    events.unsubscribe(events.GAME_CHANGED, _on_game_changed)
    events.subscribe(events.GAME_CHANGED, _on_game_changed)
    for name, handler in ((events.JOB_DONE, _on_done), (events.JOB_FAILED, _on_failed)):
        events.unsubscribe(name, handler)
        events.subscribe(name, handler)


def watch_once(config: ConfigStore) -> None:
    """One wake: compare what is already on disk if a comparison is owed, refresh what is
    due, then compare again if a refresh made one owed. One source failing or being down
    costs only itself."""
    compare_if_owed()
    for source in sources.watched():
        try:
            changed = bool(source.refresh(config)) if source.refresh else False
        except Exception:
            logger.warning("%s could not be refreshed; will try again", source.name,
                           exc_info=True)
            continue
        refreshed(source, changed)
    try:
        if vpsdb_sync.adopt_due(config):
            took = vpsdb_sync.adopt_across_library()
            logger.info("Took catalog details for %s of %s matched games",
                        took["changed"], took["looked"])
    except Exception:
        logger.warning("Taking catalog details failed; will try again", exc_info=True)
    try:
        if vpsdb_sync.art_due():
            vpsdb_sync.update_art()
    except Exception:
        logger.warning("Updating downloaded art failed; will try again", exc_info=True)
    compare_if_owed()


def start_watch(config: ConfigStore, shutdown: threading.Event | None = None) -> threading.Thread:
    """Keep every source current in the background, for as long as this process runs.

    A daemon thread rather than a job: nothing is waiting on it, it has no progress worth
    reporting, and it must never hold up a shutdown. A failure is logged and the next wake
    tries again, because a source being unreachable is a normal Tuesday.
    """
    stop = shutdown or threading.Event()
    listen()

    def run() -> None:
        while not stop.is_set():
            try:
                watch_once(config)
            except Exception:
                logger.warning("The sources watch failed; will try again", exc_info=True)
            stop.wait(_WAKE_SECONDS)

    thread = threading.Thread(target=run, name="sources-watch", daemon=True)
    thread.start()
    return thread

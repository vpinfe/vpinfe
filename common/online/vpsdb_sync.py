"""Keeping the local VPSdb copy current, and knowing when it is due.

Everything that reads the catalog reads a file: matching, the release list, what a kind
is offered from, whether a link is a download. `load_vpsdb` opens `vpsdb.json` and
returns it, so all of those are exactly as fresh as the last download.

The check is cheap and the download is not: `lastUpdate.json` is one line, and the
catalog is about 7 MB. So the schedule governs how often the *question* is asked; the
answer only costs bandwidth when it has changed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta

from common import timestamps
from common.config.config_access import cfg_bool, cfg_get
from common.config.config_store import ConfigStore
from common.devices import device_registry
from common.i18n import t
from common.online import cache_stamps
from common.runtime import jobs

logger = logging.getLogger("vpinfe.common.online.vpsdb_sync")

# What this module writes: the stamps in the cache, and two fields on this install's row.
# The schedules it reads are a person's, and sit in SCHEDULES.
SECTION = "vpsdb"
GAMES_KEY = "vps_games_updated_to"
ART_KEY = "vps_art_checked"
SCHEDULES = "updates"
NEVER = "never"

# How long an answer stays good. `never` is absent rather than zero: it is not a very
# long interval, it is a decision not to ask.
EVERY = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}


def schedule(config: ConfigStore) -> str:
    return (cfg_get(config, SCHEDULES, "download_spreadsheet", "daily") or "daily").strip().lower()


def checked_at() -> str:
    """When the catalog was last asked, ISO 8601 UTC, or "" if it never has been."""
    return cache_stamps.get(SECTION, "checked")


def due(config: ConfigStore, now: float | None = None) -> bool:
    """Whether a check is owed.

    Never checked counts as due whatever the schedule, short of `never`: a fresh install
    holding no catalog at all should not wait a day to fetch one.
    """
    wanted = schedule(config)
    if wanted == NEVER or wanted not in EVERY:
        return False
    was = timestamps.iso_to_epoch(checked_at())
    if was is None:
        return True
    moment = timestamps.iso_to_epoch(timestamps.utc_now_iso()) if now is None else now
    return (moment or 0) - was >= EVERY[wanted].total_seconds()


def adopt_due(config: ConfigStore) -> bool:
    """Whether the library is owed a sweep against the catalog it now holds."""
    if not cfg_bool(config, SCHEDULES, "update_game_details", False):
        return False
    held = cache_stamps.get(SECTION, "version")
    taken = str(device_registry.get_device_registry().self_get(GAMES_KEY, "") or "").strip()
    return bool(held) and held != taken


def art_due(now: float | None = None) -> bool:
    """Whether downloaded art is owed its daily comparison with the catalog. Owed whatever
    `update_downloaded_media` says: with it off the comparison still runs and only looks."""
    stamped = device_registry.get_device_registry().self_get(ART_KEY, "")
    was = timestamps.iso_to_epoch(str(stamped or "").strip())
    if was is None:
        return True
    moment = timestamps.iso_to_epoch(timestamps.utc_now_iso()) if now is None else now
    return (moment or 0) - was >= EVERY["daily"].total_seconds()


def update_art() -> bool:
    """Start the art sweep and stamp it. False, and no stamp, when a fill is running."""
    from common.games import media_fill

    if not media_fill.start_update():
        return False
    device_registry.get_device_registry().set_self(**{ART_KEY: timestamps.utc_now_iso()})
    return True


def adopt_across_library() -> dict:
    """Take what the catalog now says, for every game already matched to it.

    Answers `{looked, changed}`. Skips a game with no match, and writes only fields
    that differ.
    """
    from common.games import game_service
    from common.games.game_metadata import adopt_vps_details, vps_details_differ
    from common.games.game_repository import all_games

    changed = looked = 0
    for game in all_games():
        entry = game_service.matched_vps_entry(game)
        if not entry:
            continue
        looked += 1
        try:
            if vps_details_differ(game.meta_config, entry):
                adopt_vps_details(game, entry)
                changed += 1
        except Exception:
            logger.warning("Could not take catalog details for %s",
                           getattr(game, "game_dir_name", "?"), exc_info=True)
    device_registry.get_device_registry().set_self(
        **{GAMES_KEY: cache_stamps.get(SECTION, "version") or None})
    return {"looked": looked, "changed": changed}


def stamp() -> None:
    """Record that the question was asked. Written whether or not anything came back:
    a catalog that cannot be reached must not be re-asked on every draw."""
    cache_stamps.put(SECTION, checked=timestamps.utc_now_iso())


# Held for a download, so the watch and a library read never fetch the catalog together.
_downloading = threading.RLock()


def sync(config: ConfigStore, force: bool = False) -> dict:
    """Ask the catalog whether it has changed, and take it if it has.

    Returns what happened rather than a bool, because "checked and it was already
    current" and "not due yet" are different answers and a surface says them
    differently.
    """
    from common.games.game_service import ensure_vpsdb_downloaded

    if not force and not due(config):
        return {"checked": False, "reason": "not_due", "at": checked_at()}
    with _downloading:
        before = cache_stamps.get(SECTION, "version")
        ok = ensure_vpsdb_downloaded()
        stamp()
    after = cache_stamps.get(SECTION, "version")
    if not ok:
        logger.warning("VPSdb sync could not read the catalog")
    return {"checked": True, "ok": bool(ok), "changed": bool(after and after != before),
            "version": after, "at": checked_at()}


def ensure_catalog(config: ConfigStore,
                   downloading: Callable[[], object] | None = None) -> bool:
    """Download the catalog if there is none on disk, whenever it was last asked, unless
    the schedule is `never`. The download is a job, so the rail shows it, and a failure is
    the job's. Calls `downloading` first, and only when it downloads. True when there is one
    on disk afterwards."""
    from common.games.game_service import VPSDB_JSON_PATH

    if VPSDB_JSON_PATH.exists():
        return True
    if schedule(config) == NEVER:
        return False
    with _downloading:
        if not VPSDB_JSON_PATH.exists():
            if downloading:
                downloading()
            try:
                with jobs.track(jobs.KIND_SPREADSHEET_DOWNLOAD) as job:
                    job.progress(0, 1, t("said.spreadsheet_download"))
                    sync(config, force=True)
                    if not VPSDB_JSON_PATH.exists():
                        raise RuntimeError(t("error.jobs.spreadsheet_not_downloaded"))
            except Exception as error:
                logger.warning("The catalog could not be downloaded: %s", error)
    return VPSDB_JSON_PATH.exists()

"""The last good read of each Community list, kept on disk a file per list, and the read
every 30 minutes of the lists that tag or rank."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from common import events
from common.atomic_write import write_atomic
from common.config import paths
from common.failures import why
from common.games import derived_tags, rankings
from common.i18n import t
from common.runtime import shutdown
from common.timestamps import utc_now_iso

logger = logging.getLogger("vpinfe.common.games.community_lists")

EVERY_SECONDS = 30 * 60
RETRY_SECONDS = 60
FIRST_SECONDS = 15

Fetch = Callable[[str], dict]

_ticker: threading.Thread | None = None
_stop = threading.Event()


def _kept_at(extension: str, key: str) -> Path:
    return paths.COMMUNITY_KEPT_DIR / extension / f"{quote(key, safe='')}.json"


def kept(extension: str, key: str) -> dict[str, Any]:
    """The last good read of a list, with `rows` None when there is none."""
    nothing = {"rows": None, "read_at": "", "stale": False, "error": ""}
    try:
        said = json.loads(_kept_at(extension, key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return nothing
    except (OSError, ValueError):
        logger.warning("Could not read the kept copy of %s/%s", extension, key,
                       exc_info=True)
        return nothing
    found = said.get("rows") if isinstance(said, dict) else None
    if not isinstance(found, list):
        return nothing
    return {**nothing, "rows": found, "read_at": str(said.get("read_at") or "")}


def _listing(extension: str, key: str) -> dict[str, Any] | None:
    from common import extensions

    record = extensions.registry().get(extension)
    if record is None or not record.running:
        return None
    return next((one for one in record.lists() if one["key"] == key), None)


def _keep(extension: str, key: str,
          rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    listing = _listing(extension, key) or {}
    ranked = rankings.views_of(listing)
    before = (kept(extension, key)["rows"] or []) if ranked else []
    said = {"rows": rows, "read_at": utc_now_iso()}
    path = _kept_at(extension, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, lambda handle: json.dump(said, handle, ensure_ascii=False))
    except OSError:
        logger.warning("Could not keep %s/%s", extension, key, exc_info=True)
        return {**said, "stale": False, "error": ""}, False
    if ranked:
        rankings.forget()
    moved = any(rankings.ranks(before, listing, view) != rankings.ranks(rows, listing, view)
                for view in ranked)
    return {**said, "stale": False, "error": ""}, moved


def _tell() -> None:
    events.emit(events.COLLECTIONS_CHANGED, path=str(paths.COMMUNITY_KEPT_DIR))


def keep(extension: str, key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Answers as `kept` would. Emits `COLLECTIONS_CHANGED` if a ranked view's order
    moved."""
    said, moved = _keep(extension, key, rows)
    if moved:
        _tell()
    return said


def reading() -> list[tuple[str, dict[str, Any]]]:
    """(extension, list) for every list of a running extension that tags or ranks."""
    from common import extensions

    return [(record.name, listing) for record in extensions.records() if record.running
            for listing in record.lists()
            if (listing.get("tag") and listing.get("relation"))
            or rankings.views_of(listing)]


def refresh(fetch: Fetch) -> bool:
    """Read every list that tags or ranks, once. `fetch` takes a path under the API root
    and answers what that route did. True when a derived tag or a ranking moved."""
    changed = False
    for extension, listing in reading():
        key = derived_tags.list_key(extension, listing["key"])
        tagged = bool(listing.get("tag"))
        try:
            rows = (fetch(f"/ext/{extension}{listing.get('base') or ''}") or {}).get("rows")
            if not isinstance(rows, list):
                raise ValueError(t("said.why.unreadable"))
        except Exception as exc:
            logger.warning("Could not read the Community list %s: %s", key, exc)
            if tagged:
                derived_tags.failed(key, why(exc))
            continue
        found = [one for one in rows if isinstance(one, dict)]
        changed = _keep(extension, listing["key"], found)[1] or changed
        if tagged:
            field = str(listing["relation"].get("field") or "")
            ids = {str(row.get(field) or "").strip() for row in found} - {""}
            changed = derived_tags.record(key, sorted(ids)) or changed
    if changed:
        _tell()
    return changed


def start_periodic(fetch: Fetch) -> None:
    global _ticker
    if _ticker is not None:
        return
    _stop.clear()

    def _tick() -> None:
        wait = FIRST_SECONDS
        while not _stop.wait(wait):
            if shutdown.requested():
                return
            try:
                refresh(fetch)
            except Exception:
                logger.exception("The read of the Community lists failed")
            wait = (EVERY_SECONDS
                    if all(_kept_at(extension, listing["key"]).exists()
                           for extension, listing in reading())
                    else RETRY_SECONDS)

    _ticker = threading.Thread(target=_tick, daemon=True, name="community-lists")
    _ticker.start()


def stop_periodic() -> None:
    global _ticker
    _stop.set()
    _ticker = None

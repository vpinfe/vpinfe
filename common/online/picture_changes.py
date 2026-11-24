"""Pictures VPinMediaDB has replaced since VPinFE placed them, as the last check found them.

The daily check keeps its answer here whenever the automatic replacement is off, and pages
read the answer and never the network. A change is a picture VPinFE placed, has not seen
altered, and the source no longer publishes. What the answer holds is re-read against the
file and the marks at every use, so a picture that was taken, edited or set aside stops
being offered without waiting for the next check.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from common import timestamps
from common.atomic_write import write_atomic
from common.config.config_access import cfg_bool
from common.config.paths import CACHE_DIR, get_ini_config
from common.online import sources, vps_marks
from common.online.vpsdb_media import file_md5

logger = logging.getLogger("vpinfe.common.online.picture_changes")

ANSWER_PATH = CACHE_DIR / "picture-changes.json"
SCHEMA = 1
# The setting by which a changed picture is replaced without being asked.
AUTOMATIC = ("updates", "update_downloaded_media")
# What a change record holds, besides the file it describes.
FIELDS = ("game_id", "vps_id", "name", "kind", "source", "size", "md5")

_LOCK = threading.RLock()


_kept: tuple[tuple[Any, ...], dict[str, Any]] | None = None


def _read() -> dict[str, Any]:
    """The answer file, parsed again only when the file has changed: `upgrades` asks about
    every game and kind, and the file is not read for each."""
    global _kept
    try:
        seen = ANSWER_PATH.stat()
        stamp = (ANSWER_PATH, seen.st_ino, seen.st_mtime_ns, seen.st_size)
        if _kept is not None and _kept[0] == stamp:
            return _kept[1]
        found = json.loads(ANSWER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = found if isinstance(found, dict) and found.get("schema") == SCHEMA else {}
    _kept = (stamp, found)
    return found


def held() -> list[dict[str, Any]]:
    """Every change the last check kept, unchecked against the file or the marks."""
    changes = _read().get("changes")
    return [one for one in changes if isinstance(one, dict)
            and all(name in one for name in (*FIELDS, "folder", "path", "have"))] \
        if isinstance(changes, list) else []


def signature(path: Path) -> list[int]:
    """The file's size and modification time, which a later read compares before it trusts
    the file to be unaltered. Empty when it cannot be read."""
    try:
        found = path.stat()
    except OSError:
        return []
    return [found.st_size, found.st_mtime_ns]


def record(changes: Iterable[Mapping[str, Any]]) -> None:
    """Keep these as the answer, replacing the last. A check that found none keeps an empty
    one, which is how the pictures it once found stop being offered.

    Each carries `FIELDS`, and `folder`, `path` and `have`: where the file is and the hash of
    what VPinFE placed, which is how a later read knows it is still unchanged. `stamp`, the
    file's `signature` when it was checked, lets a read of every game's changes skip hashing
    a file that has not been touched."""
    body = {"schema": SCHEMA, "checked_at": timestamps.utc_now_iso(),
            "changes": [dict(one) for one in changes]}
    with _LOCK:
        try:
            ANSWER_PATH.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(ANSWER_PATH, lambda out: json.dump(body, out))
        except OSError:
            logger.warning("Could not write %s", ANSWER_PATH, exc_info=True)


def checked_at() -> str:
    """When the check last wrote its answer, ISO 8601 UTC, or "" if it never has."""
    return str(_read().get("checked_at") or "")


def key(vps_id: str, kind: str) -> str:
    """The id a picture's marks are kept under: the entry and the slot. The scope `updates`
    takes no detail, so the slot has to be in the id."""
    return f"{vps_id}:{kind}"


def set_aside(one: Mapping[str, Any], marks: vps_marks.Marks) -> bool:
    """Whether the person has left this change alone: Ignore Updates on the picture, Keep
    Mine on this version of it, or the game ignored."""
    at = key(str(one["vps_id"]), str(one["kind"]))
    return (marks.ignored(vps_marks.UPDATES, at)
            or marks.ignored(vps_marks.VERSION, at, str(one["md5"]))
            or marks.game_ignored(str(one["vps_id"])))


def _unchanged(one: Mapping[str, Any], exact: bool) -> bool:
    """Whether the file is still the one VPinFE placed. With `exact` the file is hashed; else
    one whose size and time are as they were checked is taken as it was, and only another is
    hashed."""
    if not one.get("have"):
        return False
    path = Path(str(one["folder"])) / str(one["path"])
    if not exact and one.get("stamp") and signature(path) == one["stamp"]:
        return True
    return file_md5(path) == one["have"]


def pending(game_id: str = "", marks: vps_marks.Marks | None = None) -> list[dict[str, Any]]:
    """The pictures worth offering now, of the game `game_id` or of every game: the last
    check's answer less what has since changed under it and what the person set aside. A game's
    own are checked against the file's hash; all games' by the file's size and time. Empty
    while VPinMediaDB is switched off, and while the replacement is automatic: a picture the
    next check will replace is not for a person to decide."""
    from common.online import vpinmediadb

    kept = held()
    if not kept or not sources.enabled(vpinmediadb.SOURCE) \
            or cfg_bool(get_ini_config(), *AUTOMATIC, True):
        return []
    marks = marks or vps_marks.read()
    return [{**{name: one[name] for name in FIELDS}, "id": key(one["vps_id"], one["kind"])}
            for one in kept
            if (not game_id or one["game_id"] == game_id)
            and not set_aside(one, marks) and _unchanged(one, exact=bool(game_id))]


def upgrades(who: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """The newer picture the last check found for the game `who` names, in the slot `kind`,
    or nothing: its hash and size, and the source it is published by."""
    game_id = str(who.get("game_id") or "")
    # Most games and kinds have no change; settings and marks are read for those that do.
    if not any(one["game_id"] == game_id and one["kind"] == kind for one in held()):
        return {}
    found = next((one for one in pending(game_id) if one["kind"] == kind), None)
    if found is None:
        return {}
    return {"source": found["source"], "size": found["size"], "md5": found["md5"]}

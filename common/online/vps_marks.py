"""What a person has said about a VPS entry or release: Wanted, or Ignored.

One store for both, so a thing is unmarked, Wanted or Ignored and never two. Kept in
STATE_DIR: a mark is what only a person can say, and clearing the cache must not lose it.

A mark names its thing by scope, VPS id and detail:

| scope     | id             | detail           | can be   |
|-----------|----------------|------------------|----------|
| `game`    | the entry      |                  | both     |
| `release` | the release    |                  | both     |
| `gap`     | the entry      | the Home tab     | ignored  |
| `updates` | the release    |                  | ignored  |
| `version` | the release    | the version      | ignored  |

A gap's detail is the key of the Home tab it is ignored from: the VPS kind for a tab VPS
lists (`b2sFiles`), the media kind for a tab only a source fills. `updates` keeps a release's
later versions off Updates, and `version` only the one version named. On a picture a source
has replaced they take the entry and the media kind as their id (`<entry>:<kind>`), since
`updates` takes no detail; `picture_changes` reads them.

Setting a state on a thing that holds the other replaces it. Nothing here reads the
catalog: whether an id still exists is for the caller, and a mark for an id VPS drops is
simply never drawn.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import threading
from collections.abc import Callable, Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

from common import service_errors, timestamps
from common.atomic_write import write_atomic
from common.config.paths import STATE_DIR
from common.i18n import t

logger = logging.getLogger("vpinfe.common.online.vps_marks")

PATH = STATE_DIR / "marks.json"
SCHEMA = 1
ASIDE_NAME = "marks.json.unreadable"

WANTED = "wanted"
IGNORED = "ignored"
STATES = (WANTED, IGNORED)

GAME = "game"
RELEASE = "release"
GAP = "gap"
UPDATES = "updates"
VERSION = "version"
SCOPES = (GAME, RELEASE, GAP, UPDATES, VERSION)
# The scopes that name a kind or a version beside the id.
NEEDS_DETAIL = (GAP, VERSION)
# What can be Wanted: a game, or one release. The rest only say what to leave out.
WANTABLE = (GAME, RELEASE)

_LOCK = threading.RLock()


def _locked[F: Callable[..., Any]](work: F) -> F:
    @functools.wraps(work)
    def held(*args: Any, **kwargs: Any) -> Any:
        with _LOCK:
            return work(*args, **kwargs)
    return held  # type: ignore[return-value]


def key(scope: str, vps_id: str, detail: str = "") -> str:
    return "|".join((scope, vps_id, detail))


def _parse() -> dict[str, dict[str, str]] | None:
    """The marks by `key`, or None for a file that is there and cannot be used: unreadable,
    or from another schema."""
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("Could not read %s; treating it as empty", PATH)
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        logger.warning("%s is not a schema %s marks file; treating it as empty", PATH, SCHEMA)
        return None
    held: dict[str, dict[str, str]] = {}
    for one in data.get("marks") or []:
        if not isinstance(one, dict):
            continue
        mark = {name: str(one.get(name) or "") for name in ("state", "scope", "id", "detail",
                                                           "since")}
        if mark["state"] in STATES and mark["scope"] in SCOPES and mark["id"]:
            held[key(mark["scope"], mark["id"], mark["detail"])] = mark
    return held


def _load() -> dict[str, dict[str, str]]:
    """The marks by `key`. A store that cannot be used reads as empty."""
    return _parse() or {}


def aside() -> Path:
    """Where a file that cannot be used is moved before the next write."""
    return PATH.with_name(ASIDE_NAME)


def _set_aside() -> None:
    """Move a file that cannot be used out of the way before it is written over, so the
    first mark after a bad read does not destroy what it could not read. One copy is kept."""
    if _parse() is not None:
        return
    try:
        os.replace(PATH, aside())
    except OSError:
        logger.exception("Could not move %s aside", PATH)
        raise
    logger.warning("Moved %s aside to %s", PATH, aside())


def _save(held: Mapping[str, dict[str, str]]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    _set_aside()
    try:
        write_atomic(PATH, lambda out: json.dump(
            {"schema": SCHEMA, "marks": list(held.values())}, out, indent=2))
    except Exception:
        logger.exception("Could not write %s", PATH)
        raise


def _refuse_unless_sound(state: str, scope: str, vps_id: str, detail: str) -> None:
    if state not in STATES:
        raise service_errors.RefusedError(t("said.mark_unknown"))
    _refuse_unless_named(scope, vps_id, detail)
    if state == WANTED and scope not in WANTABLE:
        raise service_errors.RefusedError(t("said.mark_not_wantable"))


def _refuse_unless_named(scope: str, vps_id: str, detail: str) -> None:
    """A thing is named by a known scope, an id, and a detail where its scope takes one and
    none where it does not. Ids and details arrive stripped."""
    if scope not in SCOPES:
        raise service_errors.RefusedError(t("said.mark_unknown"))
    if not vps_id:
        raise service_errors.RefusedError(t("said.mark_no_id"))
    if (scope in NEEDS_DETAIL) != bool(detail):
        raise service_errors.RefusedError(t("said.mark_detail"))


@_locked
def listed() -> list[dict[str, str]]:
    """Every mark, the newest first."""
    return sorted(_load().values(), key=lambda one: one["since"], reverse=True)


@_locked
def mark(state: str, scope: str, vps_id: str, detail: str = "") -> str:
    """Put `state` on the thing, and answer the state it held before: "" if it was
    unmarked. Marking a thing the state it already has changes nothing, so it keeps the
    day it was first marked."""
    vps_id, detail = vps_id.strip(), detail.strip()
    _refuse_unless_sound(state, scope, vps_id, detail)
    held = _load()
    at = key(scope, vps_id, detail)
    before = (held.get(at) or {}).get("state", "")
    if before != state:
        held[at] = {"state": state, "scope": scope, "id": vps_id, "detail": detail,
                    "since": timestamps.utc_now_iso()}
        _save(held)
    return before


@_locked
def clear(scope: str, vps_id: str, detail: str = "") -> str:
    """Take the mark off the thing, and answer the state it held: "" if it held none. The
    thing is named as `mark` names it, and refused the same way."""
    vps_id, detail = vps_id.strip(), detail.strip()
    _refuse_unless_named(scope, vps_id, detail)
    held = _load()
    gone = held.pop(key(scope, vps_id, detail), None)
    if gone is not None:
        _save(held)
    return (gone or {}).get("state", "")


@_locked
def met(release_ids: Collection[str]) -> list[str]:
    """Take Wanted off each release named, because the library holds it and the want is met.
    Answers the ids it cleared. A release that is Ignored, or unmarked, is left as it is."""
    held = _load()
    gone = [one for one in held.values()
            if one["scope"] == RELEASE and one["state"] == WANTED and one["id"] in release_ids]
    for one in gone:
        del held[key(RELEASE, one["id"], one["detail"])]
    if gone:
        _save(held)
    return [one["id"] for one in gone]


class Marks:
    """The marks as they stood when read, asked one thing at a time.

    A pass over every release asks about each one, so the lookups are sets."""

    def __init__(self, found: Iterable[Mapping[str, Any]] = ()) -> None:
        self.found = [dict(one) for one in found]
        self._by_key = {key(one["scope"], one["id"], one.get("detail") or ""): one["state"]
                        for one in self.found}

    def state(self, scope: str, vps_id: str, detail: str = "") -> str:
        return self._by_key.get(key(scope, vps_id, detail), "")

    def ignored(self, scope: str, vps_id: str, detail: str = "") -> bool:
        return self.state(scope, vps_id, detail) == IGNORED

    def wanted(self, scope: str, vps_id: str) -> bool:
        return self.state(scope, vps_id) == WANTED

    def game_ignored(self, vps_id: str) -> bool:
        return self.ignored(GAME, vps_id)

    def release_ignored(self, vps_id: str, game_id: str = "") -> bool:
        """Whether a release is left out: ignored itself, or its game is."""
        return self.ignored(RELEASE, vps_id) or bool(game_id and self.game_ignored(game_id))

    def gap_ignored(self, game_id: str, listing: str) -> bool:
        """Whether a game no longer shows as missing a kind: that kind, or the game, is
        ignored."""
        return self.ignored(GAP, game_id, listing) or self.game_ignored(game_id)

    def with_state(self, state: str, scopes: Collection[str] = SCOPES) -> list[dict[str, Any]]:
        return [one for one in self.found if one["state"] == state and one["scope"] in scopes]

    def count(self, state: str) -> int:
        return len(self.with_state(state))


def read() -> Marks:
    return Marks(listed())

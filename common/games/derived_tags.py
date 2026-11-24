"""Tags an extension's Community list puts on what this library holds from it."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from common import service_errors
from common.atomic_write import write_atomic
from common.config import paths
from common.games.game import GameRecord
from common.games.game_identity import game_id
from common.games.game_metadata import game_tags as own_game_tags
from common.games.game_metadata import game_vps_id, normalize_tag
from common.games.game_metadata import table_tags as own_table_tags
from common.games.tables import table_entries, table_id
from common.i18n import t
from common.timestamps import utc_now_iso

logger = logging.getLogger("vpinfe.common.games.derived_tags")

SCHEMA = 1

_lock = threading.RLock()
_held: dict[str, dict[str, Any]] | None = None
_indexed: tuple[Any, dict[tuple[str, str], set[str]]] | None = None
_generation = 0


def _path() -> Path:
    return paths.DERIVED_TAGS_PATH


def _load() -> dict[str, dict[str, Any]]:
    global _held
    with _lock:
        if _held is None:
            try:
                raw = json.loads(_path().read_text(encoding="utf-8"))
            except FileNotFoundError:
                raw = {}
            except (OSError, ValueError):
                logger.warning("derived tags: could not read %s", _path(), exc_info=True)
                raw = {}
            lists = raw.get("lists") if isinstance(raw, dict) else None
            _held = {str(key): dict(one) for key, one in (lists or {}).items()
                     if isinstance(one, dict)}
        return _held


def _save(held: dict[str, dict[str, Any]]) -> None:
    body = {"schema": SCHEMA, "lists": dict(sorted(held.items()))}
    _path().parent.mkdir(parents=True, exist_ok=True)
    write_atomic(_path(), lambda handle: json.dump(body, handle, indent=2,
                                                   ensure_ascii=False))


def forget() -> None:
    """Drop what is held in memory, so the next read comes from the file."""
    global _held, _indexed, _generation
    with _lock:
        _held = None
        _indexed = None
        _generation += 1


def list_key(extension: str, key: str) -> str:
    return f"{extension}/{key}"


def declared() -> list[dict[str, Any]]:
    """Every tagged list a running extension declares, with what its last read found."""
    from common import extensions

    held = _load()
    found = []
    for record in extensions.records():
        if not record.running:
            continue
        for listing in record.lists():
            relation = listing.get("relation") or {}
            if not listing.get("tag") or not relation:
                continue
            key = list_key(record.name, listing["key"])
            said = held.get(key) or {}
            found.append({
                "key": key, "extension": record.name,
                "display_name": record.display_name, "list": listing["key"],
                "title": listing["title"],
                "base": listing.get("base") or "", "tag": listing["tag"],
                "field": relation.get("field") or "", "keys": relation.get("keys") or "",
                "ids": [str(one) for one in said.get("ids") or []],
                "read_at": str(said.get("read_at") or ""),
                "stale": bool(said.get("stale")), "error": str(said.get("error") or ""),
            })
    return found


def _signature() -> tuple:
    from common import extensions

    return (_generation, tuple((record.name, record.state, id(record.declared_lists))
                               for record in extensions.records()))


def _index() -> dict[tuple[str, str], set[str]]:
    """The tags of each (relation keys, id) a tagged list reads."""
    global _indexed
    with _lock:
        signature = _signature()
        if _indexed is None or _indexed[0] != signature:
            found: dict[tuple[str, str], set[str]] = {}
            for one in declared():
                for each in one["ids"]:
                    found.setdefault((one["keys"], each), set()).add(one["tag"])
            _indexed = (signature, found)
        return _indexed[1]


def _sorted(tags: set[str]) -> list[str]:
    return sorted(tags, key=str.casefold)


def _tags_of(*asked: tuple[str, str]) -> list[str]:
    held = _index()
    return _sorted(set().union(*(held.get(one, set()) for one in asked if one[1])))


def game_tags(game: GameRecord) -> list[str]:
    carried = getattr(game, "derived_tags", None)
    if carried is not None:
        return list(carried)
    return _tags_of(("vps_entry", game_vps_id(game)), ("vpinfe_game", game_id(game)))


def table_tags(table: dict) -> list[str]:
    if "derived_tags" in table:
        return list(table.get("derived_tags") or [])
    release = str(((table.get("source") or {}).get("vps_file_id")) or "").strip()
    return _tags_of(("vps_release", release), ("vpinfe_table", table_id(table)))


def names() -> set[str]:
    return {one["tag"] for one in declared()}


def refuse(tags: Iterable[str]) -> None:
    """A derived tag is its extension's to put on and take off."""
    kept = names() & {normalize_tag(one) for one in tags}
    if kept:
        raise service_errors.RefusedError(
            t("error.tags.derived", tag=sorted(kept, key=str.casefold)[0]))


def refuse_added(game: GameRecord, tags: Iterable[str]) -> None:
    """Refuse a derived name somebody is putting on by hand. One the game or its tables
    already held is left alone, so an edit to the rest of the set still saves."""
    held = set(own_game_tags(game))
    for entry in table_entries(getattr(game, "meta_config", {})).values():
        if isinstance(entry, dict):
            held |= set(own_table_tags(entry))
    refuse({normalize_tag(one) for one in tags} - held)


def sources() -> dict[str, list[dict[str, Any]]]:
    """Where each derived tag comes from, keyed by the tag."""
    found: dict[str, list[dict[str, Any]]] = {}
    for one in declared():
        found.setdefault(one["tag"], []).append(
            {"extension": one["extension"], "display_name": one["display_name"],
             "list": one["list"], "title": one["title"], "read_at": one["read_at"],
             "stale": one["stale"], "error": one["error"]})
    return found


def _changed(key: str, **fields: Any) -> bool:
    global _generation
    with _lock:
        held = _load()
        before = held.get(key) or {}
        after = {**before, **fields}
        held[key] = after
        _generation += 1
        _save(held)
        return list(before.get("ids") or []) != list(after.get("ids") or [])


def record(key: str, ids: list[str]) -> bool:
    """A good read of one list. True when it holds different ids from the last."""
    return _changed(key, ids=sorted(set(ids)), read_at=utc_now_iso(), stale=False,
                    error="")


def failed(key: str, error: str) -> None:
    """A read that did not answer. The ids stay, and are said to be stale."""
    _changed(key, stale=True, error=error)

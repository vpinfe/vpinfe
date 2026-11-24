"""The order a ranked view of a Community list puts what it relates to in, and the place
it gives each game of this library."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterable
from typing import Any

from common.games.game_identity import game_id
from common.games.game_metadata import game_vps_id, vpinfe_section
from common.games.tables import offered_tables, recorded_default, table_entries, table_id
from common.timestamps import iso_to_epoch

DIRECTION = "asc"

_UNRANKED = (1, 0)

_lock = threading.RLock()
_indexed: tuple[tuple, dict[str, tuple[str, dict[str, int]]]] | None = None
_generation = 0


def views_of(listing: dict[str, Any]) -> list[dict[str, Any]]:
    """The views of a declared list that rank. A list that relates to nothing ranks
    nothing here, whatever its views say."""
    if not listing.get("relation"):
        return []
    return [one for one in listing.get("views") or [] if one.get("ranks")]


def _value(raw: Any, kind: str) -> float | None:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    if kind == "date":
        return iso_to_epoch(raw)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def ranks(rows: Iterable[dict[str, Any]], listing: dict[str, Any],
          view: dict[str, Any]) -> dict[str, int]:
    """Each related id's rank, 1 the best. A row with no value in the view's first sort
    field ranks nowhere, rows that sort the same share a rank, and an id on several rows
    takes the best of them."""
    kinds = {one["field"]: str(one.get("kind") or "text")
             for one in listing.get("columns") or []}
    field = str((listing.get("relation") or {}).get("field") or "")
    sort = list(view.get("sort") or [])
    keyed: list[tuple[tuple, str]] = []
    for row in rows:
        said = str(row.get(field) or "").strip()
        values = [_value(row.get(one["field"]), kinds.get(one["field"], "")) for one in sort]
        if not said or not values or values[0] is None:
            continue
        keyed.append((tuple((value is None,
                             0.0 if value is None else -value if one.get("desc") else value)
                            for value, one in zip(values, sort, strict=True)), said))
    keyed.sort(key=lambda pair: pair[0])
    found: dict[str, int] = {}
    rank, last = 0, None
    for key, said in keyed:
        if key != last:
            rank, last = rank + 1, key
        found.setdefault(said, rank)
    return found


def token(extension: str, key: str, view: str) -> str:
    """The order a collection stores for a ranked view."""
    return f"{extension}/{key}/{view}"


def is_token(order_by: str) -> bool:
    return "/" in (order_by or "")


def offered() -> list[dict[str, Any]]:
    """Every ranked view of a running extension, in the language now set."""
    from common import extensions

    return [{"order_by": token(record.name, listing["key"], view["key"]),
             "extension": record.name, "list": listing["key"], "view": view["key"],
             "title": listing["title"], "name": view["name"]}
            for record in extensions.records() if record.running
            for listing in record.lists() for view in views_of(listing)]


def described(order_by: str) -> dict[str, Any] | None:
    """What a collection's resource says about its ranked order, or None for any other."""
    if not is_token(order_by):
        return None
    from common import extensions
    from common.games.community_lists import kept

    extension = order_by.split("/", 1)[0]
    record = extensions.registry().get(extension)
    display_name = record.display_name if record is not None else extension
    one = next((one for one in offered() if one["order_by"] == order_by), None)
    if one is None:
        return {"extension": extension, "display_name": display_name, "list": "",
                "view": "", "title": "", "name": "", "read_at": "", "offered": False}
    return {**{key: one[key] for key in ("extension", "list", "view", "title", "name")},
            "display_name": display_name,
            "read_at": kept(one["extension"], one["list"])["read_at"], "offered": True}


def forget() -> None:
    """Drop the ranks held in memory, so the next order reads the kept lists again."""
    global _indexed, _generation
    with _lock:
        _indexed = None
        _generation += 1


def _signature() -> tuple:
    from common import extensions

    return (_generation, tuple((record.name, record.state, id(record.declared_lists))
                               for record in extensions.records()))


def _index() -> dict[str, tuple[str, dict[str, int]]]:
    """Each ranked view's (relation keys, rank of each related id)."""
    global _indexed
    from common import extensions
    from common.games.community_lists import kept

    with _lock:
        signature = _signature()
        if _indexed is None or _indexed[0] != signature:
            found: dict[str, tuple[str, dict[str, int]]] = {}
            for record in extensions.records():
                if not record.running:
                    continue
                for listing in record.declared_lists:
                    ranked = views_of(listing)
                    rows = (kept(record.name, listing["key"])["rows"] or []) if ranked else []
                    for view in ranked:
                        found[token(record.name, listing["key"], view["key"])] = (
                            str(listing["relation"].get("keys") or ""),
                            ranks(rows, listing, view))
            _indexed = (signature, found)
        return _indexed[1]


def _release(table: dict) -> str:
    return str(((table.get("source") or {}).get("vps_file_id")) or "").strip()


def _table_of(item: Any) -> dict:
    table = getattr(item, "table", None)
    if table:
        return table
    meta = getattr(item, "meta_config", {}) or {}
    offered = offered_tables(table_entries(meta), recorded_default(vpinfe_section(meta)))
    return offered[0][1] if offered else {}


def rank_key(order_by: str) -> Callable[[Any], tuple[int, int]]:
    """A key taking a game or an entry: `(0, rank)` where the view ranks it, `(1, 0)`
    where it does not, and `(1, 0)` for all under a view no running extension offers."""
    keys, found = _index().get(order_by, ("", {}))

    def placed(said: str) -> tuple[int, int]:
        return (0, found[said]) if said in found else _UNRANKED

    if keys == "vps_release":
        return lambda item: placed(_release(_table_of(item)))
    if keys == "vpinfe_table":
        return lambda item: placed(table_id(_table_of(item)))
    if keys == "vpinfe_game":
        return lambda item: placed(game_id(item))
    return lambda item: placed(game_vps_id(item))

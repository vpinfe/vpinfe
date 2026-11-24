"""The rows the Games page is showing, cached so a redraw is not a rescan."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from common.games.game_repository import get_game_rows, get_missing_games


@dataclass
class GameIndex:
    rows: list[dict] = field(default_factory=list)
    missing_rows: list[dict] = field(default_factory=list)
    by_path: dict[str, dict] = field(default_factory=dict)
    by_game_id: dict[str, dict] = field(default_factory=dict)
    searchable: list[tuple[str, dict]] = field(default_factory=list)


# What a typed word is matched against. Named once because the index builds this blob
# and an ad-hoc search rebuilds it, and two lists here would search different fields.
_SEARCHABLE = ("name", "filename", "manufacturer", "year", "rom")

_index = GameIndex()
_loaded = False
_missing_loaded = False


def _normalize_path(path: str | Path) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return str(path)


def _build_index(rows: list[dict], missing_rows: list[dict] | None = None) -> GameIndex:
    by_path = {}
    by_game_id = {}
    searchable = []

    for row in rows:
        game_dir = row.get("game_dir", "")
        normalized_path = _normalize_path(game_dir)
        if normalized_path:
            by_path[normalized_path] = row

        game_id = row.get("vpinfe_id")
        if game_id:
            by_game_id[str(game_id)] = row

        search_blob = " ".join(
            str(row.get(key, "") or "") for key in _SEARCHABLE
        ).lower()
        searchable.append((search_blob, row))

    return GameIndex(
        rows=rows,
        missing_rows=list(missing_rows if missing_rows is not None else _index.missing_rows),
        by_path=by_path,
        by_game_id=by_game_id,
        searchable=searchable,
    )


def set_rows(rows: list[dict]) -> list[dict]:
    global _index, _loaded
    _index = _build_index(list(rows))
    _loaded = True
    return _index.rows


def set_missing_rows(rows: list[dict]) -> list[dict]:
    global _index, _missing_loaded
    _index.missing_rows = list(rows)
    _missing_loaded = True
    return _index.missing_rows


def set_game_data(rows: list[dict], missing_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    global _index, _loaded, _missing_loaded
    _index = _build_index(list(rows), list(missing_rows))
    _loaded = True
    _missing_loaded = True
    return _index.rows, _index.missing_rows


def invalidate() -> None:
    global _index, _loaded, _missing_loaded
    _index = GameIndex()
    _loaded = False
    _missing_loaded = False


def get_rows() -> list[dict] | None:
    return _index.rows if _loaded else None


def get_missing_rows() -> list[dict] | None:
    return _index.missing_rows if _missing_loaded else None


def scan_rows(reload: bool = False) -> list[dict]:
    if reload or not _loaded:
        return set_rows(get_game_rows(reload=reload))
    return _index.rows


def scan_missing_rows(reload: bool = False) -> list[dict]:
    if reload or not _missing_loaded:
        return set_missing_rows(get_missing_games(reload=reload))
    return _index.missing_rows


def scan_game_data(reload: bool = False) -> tuple[list[dict], list[dict]]:
    rows = get_game_rows(reload=reload)
    missing_rows = get_missing_games(reload=False)
    return set_game_data(rows, missing_rows)


def find_by_path(game_dir: Path) -> dict | None:
    return _index.by_path.get(_normalize_path(game_dir))


def find_by_game_id(game_id: str) -> dict | None:
    return _index.by_game_id.get(str(game_id))


def search_rows(term: str, *, limit: int = 20, rows: list[dict] | None = None) -> list[dict]:
    term = (term or "").strip().lower()
    if not term:
        return []
    if rows is not None:
        searchable = [
            (" ".join(str(row.get(key, "") or "") for key in _SEARCHABLE).lower(), row)
            for row in rows
        ]
    else:
        scan_rows(reload=False)
        searchable = _index.searchable
    return [row for blob, row in searchable if term in blob][:limit]


def update_row_by_path(game_dir: Path, updates: dict) -> dict | None:
    row = find_by_path(game_dir)
    if row is None:
        return None
    row.update(updates)
    set_rows(_index.rows)
    return row


def sync_collection_memberships(collections_map: dict[str, list[str]]) -> None:
    if not _loaded:
        return
    for row in _index.rows:
        row["collections"] = collections_map.get(row.get("vpinfe_id", ""), [])
    set_rows(_index.rows)


def add_collection_membership(game_id: str, collection_name: str) -> None:
    row = find_by_game_id(game_id)
    if row is None:
        return
    row.setdefault("collections", [])
    if collection_name not in row["collections"]:
        row["collections"].append(collection_name)

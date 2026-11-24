"""What an extension is told about a game, a table or a file.

A plain description and never our object: an extension holding one could reach the whole
library through it, which is the thing the context exists to prevent.
"""

from __future__ import annotations

from typing import Any

from common.games import game_identity
from common.games.game import GameRecord
from common.games.game_metadata import (
    game_ipdb_id,
    game_manufacturer,
    game_title,
    game_vps_id,
    game_year,
)


def game(record: GameRecord) -> dict[str, Any]:
    return {"game_id": game_identity.game_id(record), "vps_id": game_vps_id(record),
            "name": game_title(record), "manufacturer": game_manufacturer(record),
            "year": game_year(record), "ipdb_id": game_ipdb_id(record)}


def table(record: GameRecord, row: dict[str, Any]) -> dict[str, Any]:
    """`row` is the table as the tables lens reports it."""
    source = row.get("source") or {}
    return {**game(record), "table_id": str(row.get("id") or ""),
            "filename": str(row.get("filename") or ""),
            "version": str(row.get("version") or ""),
            "authors": [str(one) for one in (row.get("authors") or [])],
            "vps_file_id": str(source.get("vps_file_id") or "")}


def file(record: GameRecord, *, path: str, vps_file_id: str) -> dict[str, Any]:
    """`path` is relative to the game's folder, as the file lenses report it."""
    return {**game(record), "path": path, "vps_file_id": vps_file_id}

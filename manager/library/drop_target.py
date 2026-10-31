"""Which target a dropped file means.

The page script reports where the drop landed as plain facts - the grid row and column,
the cell's classes, the nearest element that declared a target - and nothing here trusts
it to have decided anything. The decision is made in Python, once, so the grids, the
panels and the tests agree on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.games.collection_store import BUILTIN_ALL
from common.i18n import t
from manager.kit import grid

MEDIA_COLUMN = ("media_", "thumb_")
ASSET_COLUMN = "asset_"
MEDIA_CELL = "manager-drop-media"
ASSET_CELL = "manager-drop-asset"
NO_DROP_ROW = grid.NO_DROP_CLASS
COLLECTION = "collection"
PLAYER = "player"

# Every view the Manager registers. A new view has to be added here, and
# `test_every_view_the_manager_registers_is_in_the_table` fails until it is.
VIEWS: dict[str, bool] = {
    "overview": True, "games": True, "tables": True, "media": True, "assets": True,
    "collections": True,
    "tags": False, "discovery": False, "players": False, "launchers": False,
    "themes": False, "settings": False, "devices": False, "metrics": False,
    "logs": False, "jobs": False, "backups": False, "about": False, "system": False,
    "community": False, "extensions": False, "setup": False,
}


@dataclass(frozen=True)
class Where:
    """What the page script saw. `declared` is the parsed `data-drop` of the nearest
    element that has one."""

    row_id: str = ""
    col_id: str = ""
    cell: tuple[str, ...] = ()
    declared: dict[str, Any] | None = None


@dataclass(frozen=True)
class Target:
    """Where a file goes. No field set is the library."""

    game: str = ""
    folder: str = ""
    table: str = ""
    slot: str = ""
    page: int = 1
    asset: str = ""
    picture: str = ""
    picture_of: str = ""
    covered: bool = False


@dataclass(frozen=True)
class Refusal:
    key: str
    params: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        return t(self.key, **self.params)


_UNKNOWN = Refusal("manager.page.row_unknown")


def resolve(library: Any, view: str, where: Where) -> Target | Refusal:
    """Reads the library's rows, so it runs off the loop."""
    if where.declared:
        return _declared(library, where.declared)
    if not VIEWS.get(view, False):
        return Refusal("manager.drop.not_here")
    if not where.row_id:
        return Target()
    if view == "collections":
        if where.row_id == BUILTIN_ALL:
            return Refusal("manager.drop.not_here")
        return Target(picture=where.row_id, picture_of=COLLECTION)
    if view == "overview":
        return Target()
    if view == "games":
        return _on_game(library, where.row_id, where)
    if view == "tables":
        return _on_table(library, where)
    if view == "media":
        return _on_media(library, where.row_id)
    return _on_asset(library, where.row_id)


def _game(library: Any, game_id: str) -> dict[str, Any] | None:
    return next((one for one in library.games if str(one.get("id")) == game_id), None)


def _row(rows: list[dict[str, Any]], row_id: str) -> dict[str, Any] | None:
    return next((one for one in rows if str(one.get("id")) == row_id), None)


def _folder(game: dict[str, Any]) -> str:
    return str(game.get("folder") or "")


def _from_cell(where: Where) -> tuple[str, str]:
    """The slot and the asset kind a grid cell is for, from its column id; one at most."""
    if MEDIA_CELL in where.cell:
        for prefix in MEDIA_COLUMN:
            if where.col_id.startswith(prefix):
                return where.col_id[len(prefix):], ""
    if ASSET_CELL in where.cell and where.col_id.startswith(ASSET_COLUMN):
        return "", where.col_id[len(ASSET_COLUMN):]
    return "", ""


def _on_game(library: Any, game_id: str, where: Where) -> Target | Refusal:
    game = _game(library, game_id)
    if game is None:
        return _UNKNOWN
    slot, asset = _from_cell(where)
    return Target(game=game_id, folder=_folder(game), slot=slot, asset=asset)


def _on_table(library: Any, where: Where) -> Target | Refusal:
    row = _row(library.load_tables(), where.row_id)
    game = _game(library, str((row or {}).get("game_id") or ""))
    if row is None or game is None:
        return _UNKNOWN
    slot, asset = _from_cell(where)
    return Target(game=str(game["id"]), folder=_folder(game), table=where.row_id,
                  slot=slot, asset=asset)


def _on_media(library: Any, row_id: str) -> Target | Refusal:
    row = _row(library.load_media_rows(), row_id)
    game = _game(library, str((row or {}).get("game_id") or ""))
    if row is None or game is None:
        return _UNKNOWN
    if row.get("via") == "orphan":
        return Refusal("manager.drop.orphan")
    return Target(game=str(game["id"]), folder=_folder(game),
                  table=str(row.get("table") or ""), slot=str(row.get("kind") or ""),
                  page=int(row.get("page") or 1), covered=row.get("via") == "unused")


def _on_asset(library: Any, row_id: str) -> Target | Refusal:
    row = _row(library.load_asset_rows(), row_id)
    game = _game(library, str((row or {}).get("game_id") or ""))
    if row is None or game is None:
        return _UNKNOWN
    return Target(game=str(game["id"]), folder=_folder(game),
                  table=str(row.get("table") or ""), asset=str(row.get("kind") or ""))


def _declared(library: Any, declared: dict[str, Any]) -> Target | Refusal:
    picture = str(declared.get("picture") or "")
    if picture:
        owner = str(declared.get("of") or "")
        if owner not in (COLLECTION, PLAYER):
            return _UNKNOWN
        return Target(picture=picture, picture_of=owner)
    game_id = str(declared.get("game") or "")
    game = _game(library, game_id)
    if game is None:
        return _UNKNOWN
    table = str(declared.get("table") or "")
    if table and not any(str(one.get("id")) == table and str(one.get("game_id")) == game_id
                         for one in library.load_tables()):
        return _UNKNOWN
    return Target(game=game_id, folder=_folder(game), table=table,
                  slot=str(declared.get("slot") or ""),
                  page=int(declared.get("page") or 1),
                  asset=str(declared.get("asset") or ""))

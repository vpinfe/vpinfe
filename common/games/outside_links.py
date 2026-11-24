"""Where a game, one of its tables or one of its files can be reached outside VPinFE, as
extensions have contributed them."""

from __future__ import annotations

from pathlib import Path

from common import service_errors
from common.extensions import catalogs, descriptors
from common.games import asset_lens, asset_origin, game_lens, table_lens
from common.games.game_metadata import game_title
from common.games.game_repository import game_to_row
from common.i18n import t


def links(game_id: str, table_id: str = "", path: str = "") -> dict:
    """The game's, or a table's with `table_id`, or a file's with `path` - relative to
    the game's folder, as the file lenses report it."""
    record = game_lens.game_or_refuse(game_id)
    if path:
        game_dir = Path(record.full_path_game or "")
        target = asset_lens.inside(game_dir, path)
        bound = asset_origin.match_of(asset_origin.sources(game_dir), game_dir, target)
        return {"links": catalogs.links("file", descriptors.file(
            record, path=target.relative_to(game_dir.resolve()).as_posix(),
            vps_file_id=bound or ""))}
    if table_id:
        row = next((one for one in table_lens.table_rows(record, game_to_row(record))
                    if one.get("id") == table_id), None)
        if row is None:
            raise service_errors.NotFoundError(
                t("error.games.table_id_unknown", table_id=table_id, game=game_title(record)))
        return {"links": catalogs.links("table", descriptors.table(record, row))}
    return {"links": catalogs.links("game", descriptors.game(record))}

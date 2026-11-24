"""A game's Pictures by its id: listed, served, removed and put back."""

from __future__ import annotations

from pathlib import Path

from common import service_errors
from common.games import game_lens, pictures
from common.i18n import t
from common.media import sized_media


def _folder(game_id: str) -> Path:
    return Path(game_lens.game_or_refuse(game_id).full_path_game or "")


def _folder_for_writing(game_id: str) -> Path:
    return Path(game_lens.game_for_writing(game_id).full_path_game or "")


def _named(name: str) -> str:
    if not pictures.is_name(name):
        raise service_errors.RefusedError(t("error.games.not_png", name=name))
    return name


def _found(game_id: str, name: str) -> Path:
    path = pictures.folder_of(_folder(game_id)) / _named(name)
    if not path.is_file():
        raise service_errors.NotFoundError(t("error.games.no_picture", name=name))
    return path


def listing(game_id: str, table_id: str = "") -> dict:
    """Newest first. Only `table_id`'s where one is named."""
    rows = pictures.listed(_folder(game_id))
    if table_id:
        rows = [row for row in rows if row["table_id"] == table_id]
    return {"pictures": rows}


def picture_file(game_id: str, name: str, size: int | None = None) -> sized_media.Served:
    size = sized_media.size_or_refuse(size, "image")
    return sized_media.served(_found(game_id, name), size)


def remove(game_id: str, name: str) -> dict:
    _folder_for_writing(game_id)
    _found(game_id, name).unlink()
    return {"removed": name}


def put(game_id: str, name: str, data: bytes) -> dict:
    """Write `data` as the picture `name`, where no picture has that name."""
    folder = _folder_for_writing(game_id)
    try:
        path = pictures.put(folder, _named(name), data)
    except ValueError as exc:
        raise service_errors.RefusedError(t("error.games.picture_not_png")) from exc
    except FileExistsError as exc:
        raise service_errors.BlockedError(t("error.games.file_exists"),
                                          details={"name": name}) from exc
    return pictures.row(path)


__all__ = ["listing", "picture_file", "put", "remove"]

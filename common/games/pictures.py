"""A game's Pictures: what a player took of its screens during play."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from common.atomic_write import staged_for
from common.media import sized_media

FOLDER = "pictures"
SUFFIX = ".png"

# PNG text keywords. The first two are the format's own.
SOFTWARE = "Software"
TAKEN = "Creation Time"
TABLE = "VPinFE Table"


def folder_of(game_dir: str | Path) -> Path:
    return Path(game_dir) / FOLDER


def _free(folder: Path, stem: str) -> Path:
    path = folder / f"{stem}{SUFFIX}"
    count = 2
    while path.exists():
        path = folder / f"{stem} {count}{SUFFIX}"
        count += 1
    return path


def keep(image: Image.Image, game_dir: str | Path, *, table_id: str,
         taken: datetime) -> Path:
    """Write `image` into the game's pictures. Raises OSError where the folder cannot be
    written."""
    folder = folder_of(game_dir)
    folder.mkdir(exist_ok=True)
    path = _free(folder, taken.strftime("%Y-%m-%d %H-%M-%S"))
    said = PngInfo()
    said.add_text(SOFTWARE, "VPinFE")
    said.add_text(TAKEN, taken.isoformat(timespec="seconds"))
    if table_id:
        said.add_text(TABLE, table_id)
    with staged_for(path) as staged:
        image.save(staged, "PNG", pnginfo=said)
    return path


def is_name(name: str) -> bool:
    """Whether `name` could be a picture: a PNG's file name, directly in the folder."""
    return (bool(name) and Path(name).name == name and "\\" not in name
            and not name.startswith(".") and Path(name).suffix.lower() == SUFFIX)


def listed(game_dir: str | Path) -> list[dict[str, Any]]:
    """Every picture in the game's folder, newest first."""
    try:
        found = [(path, path.stat()) for path in folder_of(game_dir).iterdir()
                 if is_name(path.name) and path.is_file()]
    except OSError:
        return []
    # Two taken in one second differ only by the file's own time.
    found.sort(key=lambda each: each[1].st_mtime_ns, reverse=True)
    return sorted((row(path, stat) for path, stat in found),
                  key=lambda row: row["taken"], reverse=True)


def row(path: Path, stat: os.stat_result | None = None) -> dict[str, Any]:
    """One picture as a listing shows it."""
    stat = stat or path.stat()
    said: dict[Any, Any] = {}
    size = (0, 0)
    try:
        # The text is read with the header: `keep` writes it ahead of the pixels.
        with Image.open(path) as opened:
            said, size = dict(opened.info), opened.size
    except (OSError, SyntaxError, ValueError):
        pass
    return {"name": path.name, "taken": _taken(said.get(TAKEN), stat.st_mtime),
            "table_id": str(said.get(TABLE) or ""), "size_bytes": stat.st_size,
            "width": size[0], "height": size[1],
            "version": sized_media.version(path) or ""}


def _taken(said: Any, modified: float) -> str:
    """In UTC. A time with no offset is this device's own, which is how `keep` writes it."""
    try:
        at = datetime.fromisoformat(str(said)) if said else None
    except ValueError:
        at = None
    if at is None:
        at = datetime.fromtimestamp(modified, UTC)
    return at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def put(game_dir: str | Path, name: str, data: bytes) -> Path:
    """Write a PNG's bytes as the picture `name`. Raises ValueError where they are not a
    PNG, FileExistsError where the name is taken, and OSError where it cannot be written."""
    try:
        with Image.open(BytesIO(data)) as opened:
            if opened.format != "PNG":
                raise ValueError(opened.format)
            opened.verify()
    except (OSError, SyntaxError) as exc:
        raise ValueError from exc
    folder = folder_of(game_dir)
    folder.mkdir(exist_ok=True)
    path = folder / name
    if path.exists():
        raise FileExistsError(str(path))
    with staged_for(path) as staged:
        staged.write_bytes(data)
    return path

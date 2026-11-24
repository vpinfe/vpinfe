"""A kept player's picture: one square WebP in a folder, named for the player's id.

The file is the record - `players.json` holds nothing about it. These are plain file
operations on a folder; `Roster` is what knows who may have one.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from common import service_errors
from common.atomic_write import staged_for
from common.i18n import t
from common.media import sized_media

FOLDER = "player_pictures"
SUFFIX = ".webp"
# Where the HTTP app shows the folder.
ROUTE = f"/{FOLDER}/"
LARGEST = 1024
_QUALITY = 90
# Pillow identifies a file by its bytes, not its name, so the formats it may open are
# named as well as the extensions an upload may carry.
_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})
_FORMATS = ("PNG", "JPEG", "WEBP", "BMP", "GIF")
_MAX_PIXELS = 40_000_000

# An id read from disk is not trusted as a path.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def path_for(folder: Path, player_id: str) -> Path | None:
    """Where this player's picture is kept, whether or not one is there. None for an id
    that cannot name a file."""
    wanted = (player_id or "").strip()
    return folder / f"{wanted}{SUFFIX}" if _SAFE_ID.fullmatch(wanted) else None


def find(folder: Path, player_id: str) -> Path | None:
    """The picture's file, or None where the player has none."""
    here = path_for(folder, player_id)
    return here if here is not None and here.is_file() else None


def version(folder: Path, player_id: str) -> str | None:
    """A token that changes whenever the picture does, or None where there is none."""
    return sized_media.version(find(folder, player_id))


def address(player_id: str, version: str | None) -> str | None:
    """What a page puts in an `<img>`, or None where there is no picture. The version
    keeps the address new whenever the picture is."""
    if not version or not _SAFE_ID.fullmatch(player_id):
        return None
    return f"{ROUTE}{player_id}{SUFFIX}?v={version}"


def prepare(filename: str, content: bytes) -> bytes:
    """What an upload becomes: the centre square, at most LARGEST, as WebP.

    An animated picture keeps its first frame. Refuses an empty file and anything Pillow
    cannot read as an image, which includes HEIC.
    """
    if not content:
        raise service_errors.RefusedError(t("error.players.picture_empty"))
    if Path(filename or "").suffix.lower() not in _EXTENSIONS:
        raise service_errors.RefusedError(t("error.players.picture_not_image"))
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(content), formats=_FORMATS) as opened:
            if opened.size[0] * opened.size[1] > _MAX_PIXELS:
                raise service_errors.RefusedError(t("error.players.picture_not_image"))
            opened.draft(None, (LARGEST * 2, LARGEST * 2))
            picture = ImageOps.exif_transpose(opened)
            alpha = (picture.mode in ("RGBA", "LA", "PA")
                     or (picture.mode == "P" and "transparency" in picture.info))
            picture = picture.convert("RGBA" if alpha else "RGB")
        side = min(picture.size[0], picture.size[1], LARGEST)
        picture = ImageOps.fit(picture, (side, side), Image.Resampling.LANCZOS)
        encoded = io.BytesIO()
        picture.save(encoded, format="WEBP", quality=_QUALITY, method=4)
    except Exception as exc:
        raise service_errors.RefusedError(t("error.players.picture_not_image")) from exc
    return encoded.getvalue()


def write(folder: Path, player_id: str, picture: bytes) -> None:
    """Keep `picture` - what `prepare` answered - as this player's, replacing any."""
    here = path_for(folder, player_id)
    if here is None:
        raise service_errors.RefusedError(
            t("error.players.picture_id_unusable", player_id=player_id))
    folder.mkdir(parents=True, exist_ok=True)
    with staged_for(here) as partial:
        partial.write_bytes(picture)


def remove(folder: Path, player_id: str) -> bool:
    """Delete this player's picture. Whether there was one."""
    here = find(folder, player_id)
    if here is None:
        return False
    here.unlink(missing_ok=True)
    sized_media.forget(here)
    return True


def served(folder: Path, player_id: str, size: int | None) -> sized_media.Served:
    """The picture at `size`, or a refusal."""
    size = sized_media.size_or_refuse(size, "image")
    here = find(folder, player_id)
    if here is None:
        raise service_errors.NotFoundError(t("error.players.no_picture"))
    return sized_media.served(here, size)

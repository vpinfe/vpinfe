"""Art at the size it is drawn, and the version a browser can keep it under."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import NamedTuple

from common import service_errors
from common.atomic_write import staged_for
from common.config.paths import CACHE_DIR
from common.i18n import t

logger = logging.getLogger("vpinfe.common.media.sized_media")

# The longest edge in pixels. Each one is another copy of every picture on disk.
THUMBNAIL = 256
SIZES = (THUMBNAIL, 1024)

ROOT = CACHE_DIR / "media_sized"

_QUALITY = 80

_UNREADABLE: set[tuple[Path, str]] = set()


class Served(NamedTuple):
    """What goes on the wire, and the version of the file it was made from."""

    path: Path
    version: str


def version(path: Path | None) -> str | None:
    """A token that changes whenever the file behind a slot does, or None for no file."""
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    key = f"{path.name}\0{stat.st_mtime_ns}\0{stat.st_size}"
    return hashlib.blake2b(key.encode("utf-8"), digest_size=6).hexdigest()


def size_or_refuse(size: int | None, family: str) -> int | None:
    """The size asked for, None for the original, or a refusal."""
    if size is None:
        return None
    if size not in SIZES:
        raise service_errors.RefusedError(
            t("error.games.unknown_size"),
            details={"unknown": size, "known": list(SIZES)})
    if family != "image":
        raise service_errors.RefusedError(
            t("error.games.size_unsupported"), details={"size": size})
    return size


def served(source: Path, size: int | None) -> Served:
    """The source itself, or its copy at `size`. An animated source is its first frame,
    still, at THUMBNAIL, and sent whole above it."""
    current = version(source) or ""
    if size is None:
        return Served(source, current)
    return Served(_copy(source, size, current) or source, current)


def _folder(source: Path) -> Path:
    return ROOT / hashlib.blake2b(str(source.resolve()).encode("utf-8"),
                                  digest_size=8).hexdigest()


def forget(source: Path) -> None:
    """Delete the sized copies of a source that is going away."""
    shutil.rmtree(_folder(source), ignore_errors=True)


def _copy(source: Path, size: int, current: str) -> Path | None:
    folder = _folder(source)
    target = folder / f"{size}-{current}.webp"
    if target.is_file():
        return target
    if (source, current) in _UNREADABLE:
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(source) as opened:
            if getattr(opened, "is_animated", False) and size > THUMBNAIL:
                return None
            opened.draft(None, (size, size))
            picture = ImageOps.exif_transpose(opened)
            alpha = (picture.mode in ("RGBA", "LA", "PA")
                     or (picture.mode == "P" and "transparency" in picture.info))
            picture = picture.convert("RGBA" if alpha else "RGB")
            picture.thumbnail((size, size), Image.Resampling.LANCZOS)
            folder.mkdir(parents=True, exist_ok=True)
            with staged_for(target) as partial:
                picture.save(partial, format="WEBP", quality=_QUALITY, method=4)
    except Exception as exc:
        logger.warning("Could not make a %spx copy of %s: %s", size, source, exc)
        _UNREADABLE.add((source, current))
        return None
    for older in folder.glob(f"{size}-*.webp"):
        if older != target:
            older.unlink(missing_ok=True)
    return target

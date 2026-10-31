"""What the jobs that rewrite a game's media files in place share: how a file is named, that
it is one of the game's, and that it is still the file that was read."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from common.i18n import t

# Where a game's media sits: its folder, or the one folder inside it.
MEDIAS = "medias"


@dataclass(frozen=True)
class Target:
    """A file by its game and its place in the game's folder, the key the media rows carry."""

    game_id: str
    path: str


class SkipError(Exception):
    """A file left as it was, with the reason in words."""


def stamp(path: Path) -> tuple[int, int]:
    """What says a file is still the one that was read: a long job is a window for another
    to write the same slot."""
    found = path.stat()
    return found.st_mtime_ns, found.st_size


def inside(game_dir: Path, relative: str) -> Path:
    """The file a key names, only where it is a file of the game's media: in the game's
    folder or under its `medias` folder, never reached through a link."""
    named = PurePosixPath(relative)
    parts = named.parts
    shaped = (parts and not named.is_absolute() and "\\" not in relative
              and not {"..", "."} & set(parts) and ":" not in parts[0]
              and (len(parts) == 1 or parts[0] == MEDIAS))
    if not shaped:
        raise SkipError(t("error.media_rotate.not_in_game"))
    source = game_dir.joinpath(*parts)
    if source.is_symlink() or not source.is_file():
        raise SkipError(t("error.media_rotate.missing"))
    if not source.resolve().is_relative_to(game_dir.resolve()):
        raise SkipError(t("error.media_rotate.not_in_game"))
    return source

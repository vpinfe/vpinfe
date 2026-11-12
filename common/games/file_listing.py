"""The files in a game folder that VPinFE recognizes as the game's, as manifest lines.

Built from the listings a scan has already read. A folder-relative path with forward
slashes names a file; a pack folder (PUP, alt color, alt sound, music) is one line, and a
PUP pack is one line for each pack inside the PUP folder.

The tables themselves are not here: they are the manifest's `tables` list.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

MEDIA_FOLDER = "medias"
PUP_FOLDER = "pupvideos"
ALT_COLOR_FOLDERS = ("serum", "vni")
MUSIC_FOLDER = "music"

# Kinds a bare file at the top of the folder can be, beyond media and rule sheets.
_ROOT_KINDS = frozenset({"backglass", "b2spro", "ini", "patch", "script", "pov", "scv",
                         "nix", "altcolor_serum", "altcolor_vni"})


def _recognized_in_root(name: str) -> bool:
    from common.games.asset_registry import classify_bare_extension, is_readme, match_media_kind

    spec = classify_bare_extension(name)
    return bool(spec and spec.kind in _ROOT_KINDS) or is_readme(name) \
        or match_media_kind(name) is not None


def _pup_packs(game_dir: Path, folder: str) -> list[str] | None:
    """The pack folders inside the PUP folder, or None where it cannot be read."""
    try:
        return [f"{folder}/{one.name}" for one in (game_dir / folder).iterdir() if one.is_dir()]
    except OSError:
        return None


def listing_of(game_dir: Path, names: Collection[str], subdirs: Mapping[str, str],
               medias: Collection[str] | None) -> frozenset[str] | None:
    """What the folder holds that the manifest lists.

    `names` are the files directly in the folder, `subdirs` its folders as {lowercase name:
    name as spelled}, and `medias` the files under `medias/` relative to it. `medias` is
    None where that folder could not be walked: a failed read is not an empty folder, and
    the whole listing is then None, since a manifest line dropped for it would be a file
    wrongly reported gone.
    """
    from common.games.asset_registry import altsound_folders, match_media_kind

    if medias is None:
        return None
    found: set[str] = {name for name in names if _recognized_in_root(name)}

    media_root = subdirs.get(MEDIA_FOLDER, MEDIA_FOLDER)
    found.update(f"{media_root}/{rel}" for rel in medias
                 if match_media_kind(rel.rsplit("/", 1)[-1]) is not None)

    if pup := subdirs.get(PUP_FOLDER):
        packs = _pup_packs(game_dir, pup)
        if packs is None:
            return None
        found.update(packs)
    found.update(subdirs[key] for key in ALT_COLOR_FOLDERS if key in subdirs)
    found.update(altsound_folders(game_dir, subdirs.values()))
    if music := subdirs.get(MUSIC_FOLDER):
        found.add(music)
    return frozenset(found)


def sync(assets: Mapping[str, Any] | None, listing: Collection[str], when: str,
         exists: Callable[[str], bool]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """The assets map the folder's listing calls for, and what changed.

    A listed file with no line gets one with nothing in it. A line the listing does not
    name is checked with `exists`, because the listing leaves out files the scan does not
    read (ROM archives) that a download recorded. A line whose file is gone and has no
    `source` is dropped, since the next scan writes it again; one with a `source` is kept
    and marked `absent_since`, and cleared when the file returns, the way a table is.
    """
    from common.games.tables import ABSENT_SINCE_KEY

    held = {str(path): dict(entry) for path, entry in (assets or {}).items()
            if isinstance(entry, dict)}
    present = {str(path).lower() for path in listing}
    lower = {path.lower(): path for path in held}
    news = {"listed": 0, "dropped": 0, "absent": 0, "returned": 0}

    for path in sorted(listing):
        if path.lower() not in lower:
            held[path] = {}
            news["listed"] += 1

    for path, entry in list(held.items()):
        here = path.lower() in present or exists(path)
        marked = bool(entry.get(ABSENT_SINCE_KEY))
        if here and marked:
            held[path] = {k: v for k, v in entry.items() if k != ABSENT_SINCE_KEY}
            news["returned"] += 1
        elif not here and not entry.get("source"):
            del held[path]
            news["dropped"] += 1
        elif not here and not marked:
            held[path] = {**entry, ABSENT_SINCE_KEY: when}
            news["absent"] += 1
    return held, news

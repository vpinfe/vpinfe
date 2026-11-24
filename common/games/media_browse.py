"""Walking this machine's directories, so artwork can be picked from where it landed.

Art arrives on a cabinet by every route there is - a browser download, a USB stick, a
share - and asking someone to move a file into the right game folder before the app will
look at it is asking them to do the app's job.

Reading a directory is a real capability, so it is bounded rather than trusted: the game
library, plus whatever folders the owner listed, and nothing else. The check is on the
resolved path, so a symlink out of a root is out of a root.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

from common import service_errors
from common.config import path_state
from common.games import library_folders
from common.i18n import t
from common.media.media_specs import AUDIO_FAMILY, DOC_FAMILY, IMAGE_FAMILY, VIDEO_FAMILY

logger = logging.getLogger("vpinfe.common.games.media_browse")

_FAMILIES = {"image": IMAGE_FAMILY, "video": VIDEO_FAMILY,
             "audio": AUDIO_FAMILY, "doc": DOC_FAMILY}


def family_of(path: Path) -> str:
    """Which kind of media this file is, or "" for anything that is not media."""
    suffix = path.suffix.lower()
    return next((name for name, exts in _FAMILIES.items() if suffix in exts), "")


def roots(game_dir: str = "") -> list[dict]:
    """Where browsing may start.

    A game's own folder first when one is named: that is where its art is most likely
    to already be, and it is the one starting point that is not a setting.
    """
    wanted: list[tuple[str, str]] = []

    def offer(raw: str, source: str) -> None:
        if raw:
            wanted.append((os.path.expanduser(raw), source))

    if game_dir:
        offer(game_dir, "game")
    for location in library_folders.configured():
        offer(location.path, "library")

    # Asked of every folder at once: one that is on a share that is down costs one wait,
    # not one per folder, and is left out rather than hanging the picker.
    states = path_state.look_all([path_state.Ask(raw) for raw, _ in wanted])
    found: list[dict] = []
    seen: set[Path] = set()
    for (raw, source), state in zip(wanted, states, strict=True):
        if not state.reachable:
            continue
        try:
            path = Path(raw).resolve()
        except OSError:
            continue
        if path in seen:
            continue
        seen.add(path)
        found.append({"path": str(path), "name": path.name or str(path),
                      "source": source})
    return found


def _escapes(entry: Path, allowed: list[Path]) -> bool:
    """Whether following this entry leaves every root.

    Only a symlink can: anything else under a resolved root is under it by
    construction, and resolving every entry would be a stat per file on a library
    that may well be over the network.
    """
    if not entry.is_symlink():
        return False
    try:
        target = entry.resolve()
    except OSError:
        return True
    return not any(target == root or root in target.parents for root in allowed)


def _extension_roots() -> list[Path]:
    """The folders a running extension works from.

    Read here and not in `roots()`: browsing starts where a person's own media is, and an
    importer's source folder in that picker would be somewhere they never put anything.
    What may be read and where it is worth looking are two questions.
    """
    from common import extensions

    return [Path(one) for one in extensions.read_roots()]


def within_roots(raw: str) -> Path:
    """The path as a real location under some root, or a refusal.

    Resolved before it is checked, so `..` and a symlink are the same question. A root
    itself counts as inside itself - that is where browsing starts.
    """
    try:
        path = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise service_errors.RefusedError(t("error.filesystem.path_unreadable"),
                                  details={"path": raw}) from exc
    allowed = [Path(item["path"]) for item in roots()] + _extension_roots()
    if not any(path == root or root in path.parents for root in allowed):
        raise service_errors.RefusedError(
            t("error.filesystem.folder_refused"),
            details={"path": raw, "allowed": [str(root) for root in allowed]})
    return path


def media_file(path: str) -> Path:
    """A file to look at before it is taken, or a refusal.

    Judging artwork means seeing it, and a name and a byte count do not do that. Bounded
    the same way the listing is, and to media on top of that: the containment check alone
    would happily hand out a .vpx or an .info.
    """
    here = within_roots(path)
    if not here.is_file() or not family_of(here):
        raise service_errors.RefusedError(t("error.filesystem.not_media"),
                                          details={"path": path})
    return here


def entries(path: str, *, extensions: Iterable[str] = ()) -> dict:
    """Folders and media files, folders first, both by name, and files ending in one of
    `extensions` where a caller asks for them.

    Nothing else is listed. A directory walker that returns every file is a file browser,
    and this exists to fill a slot - a .vpx in the list is noise at best and a way to get
    a table file into an <img> at worst.
    """
    also = frozenset(extension.lower() for extension in extensions)
    here = within_roots(path)
    state = path_state.look(str(here))
    if state.state in (path_state.MISSING, path_state.WRONG_KIND):
        raise service_errors.RefusedError(t("error.filesystem.not_folder"),
                details={"path": path})
    said = path_state.refusal(here.name or str(here), state)
    if said:
        raise service_errors.BlockedError(said, details={"path": path})

    folders, files = [], []
    found = roots()
    allowed = [Path(item["path"]) for item in found]
    try:
        for entry in sorted(here.iterdir(), key=lambda item: item.name.lower()):
            # A link out of the allowed folders is not offered, because importing it
            # would be refused - listing it would be offering something that cannot
            # work, and "sleeve.png" pointing at /etc/passwd should not be on a menu.
            if _escapes(entry, allowed):
                continue
            # A folder someone cannot open is still worth showing; the refusal comes
            # when they try, and naming it is better than pretending it is not there.
            if entry.is_dir():
                folders.append({"name": entry.name, "path": str(entry),
                                "kind": "folder", "family": "", "size_bytes": None})
                continue
            family = family_of(entry)
            if family or entry.suffix.lower() in also:
                files.append({"name": entry.name, "path": str(entry), "kind": "file",
                              "family": family, "size_bytes": entry.stat().st_size})
    except OSError as exc:
        raise service_errors.RefusedError(t("error.filesystem.folder_unreadable"),
                                  details={"path": path}) from exc

    # Null at a root, so a client knows where "up" stops without knowing the rules.
    parent = here.parent
    at_root = any(here == root for root in allowed)
    return {"path": str(here), "parent": None if at_root else str(parent),
            "entries": folders + files}

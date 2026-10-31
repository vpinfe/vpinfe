"""Listing this machine's folders and files, for a path field's Browse button.

Unbounded on purpose, unlike `common/games/media_browse.py`: picking a path means seeing
the whole disk, not just what a library already contains.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from common import service_errors
from common.i18n import t
from common.media.media_specs import AUDIO_FAMILY, IMAGE_FAMILY, VIDEO_FAMILY

# What a picker may look at before taking a file: the media a slot holds, and nothing
# else this route could be asked for.
_SHOWN = IMAGE_FAMILY + VIDEO_FAMILY + AUDIO_FAMILY

# Relative to home, so each resolves under whatever account runs VPinFE.
_PRIVATE_COMMON: tuple[str, ...] = (
    ".ssh", ".gnupg", ".aws", ".config/gcloud", ".kube", ".docker", ".password-store",
)
_PRIVATE_DARWIN: tuple[str, ...] = (
    "Library/Keychains",
    "Library/Cookies",
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    "Library/Application Support/1Password",
)
_PRIVATE_LINUX: tuple[str, ...] = (
    ".mozilla", ".config/google-chrome", ".config/chromium", ".local/share/keyrings",
)
_PRIVATE_WIN32: tuple[str, ...] = (
    "AppData/Roaming/Microsoft/Credentials",
    "AppData/Roaming/Microsoft/Protect",
    "AppData/Local/Google/Chrome/User Data",
    "AppData/Roaming/Mozilla/Firefox",
)
_PRIVATE_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "darwin": _PRIVATE_DARWIN,
    "linux": _PRIVATE_LINUX,
    "win32": _PRIVATE_WIN32,
}

_DEFAULT_PATHEXT = ".exe;.bat;.cmd;.com"


def _name(path: Path) -> str:
    return path.name or str(path)


def _private_roots() -> tuple[Path, ...]:
    home = _home()
    relative = _PRIVATE_COMMON + _PRIVATE_BY_PLATFORM.get(sys.platform, ())
    return tuple(candidate.resolve() for rel in relative
                 if (candidate := home / rel).exists())


def _comparable(path: Path) -> Path:
    """`resolve()` keeps the case it was given, and a macOS volume reads `.SSH` as `.ssh`.
    Windows paths already compare without case."""
    return Path(str(path).casefold()) if sys.platform == "darwin" else path


def _is_private(path: Path, private: tuple[Path, ...]) -> bool:
    """Resolved, so a symlink into one of these cannot read as outside it."""
    resolved = _comparable(path.resolve())
    return any(resolved.is_relative_to(_comparable(root)) for root in private)


def _is_bundle(path: Path) -> bool:
    """A macOS `.app`, which an `exe` field picks whole rather than walking into."""
    return sys.platform == "darwin" and path.suffix.lower() == ".app"


def _bundle_boundary(path: Path) -> Path | None:
    """The `.app` in `path`'s ancestry, if any - so a value saved from inside one still
    opens beside it rather than being asked for as a folder."""
    if sys.platform != "darwin":
        return None
    current = path
    while True:
        if _is_bundle(current):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _pathext() -> tuple[str, ...]:
    raw = os.environ.get("PATHEXT", _DEFAULT_PATHEXT)
    return tuple(ext.strip().lower() for ext in raw.split(";") if ext.strip())


def _is_executable(entry: os.DirEntry) -> bool:
    if sys.platform == "win32":
        return entry.name.lower().endswith(_pathext())
    try:
        return os.access(entry.path, os.X_OK)
    except OSError:
        return False


def _wanted_file(entry: os.DirEntry, kind: str, suffixes: tuple[str, ...]) -> bool:
    if kind == "exe":
        return _is_executable(entry)
    lowered = tuple(s.lower() for s in suffixes)
    return not lowered or entry.name.lower().endswith(lowered)


def _entries(path: Path, private: tuple[Path, ...], kind: str,
             suffixes: tuple[str, ...], hidden: bool) -> tuple[list[dict], list[dict]]:
    folders: list[dict] = []
    files: list[dict] = []
    try:
        with os.scandir(path) as scanned:
            for entry in scanned:
                if entry.name.startswith(".") and not hidden:
                    continue
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                if is_dir:
                    if _is_private(Path(entry.path), private):
                        continue
                    if kind == "exe" and _is_bundle(Path(entry.path)):
                        files.append({"name": entry.name, "path": entry.path})
                    else:
                        folders.append({"name": entry.name, "path": entry.path})
                elif kind != "dir" and _wanted_file(entry, kind, suffixes):
                    files.append({"name": entry.name, "path": entry.path})
    except PermissionError as exc:
        raise service_errors.RefusedError(t("error.folders.cannot_read")) from exc
    folders.sort(key=lambda item: item["name"].lower())
    files.sort(key=lambda item: item["name"].lower())
    return folders, files


def _home() -> Path:
    """Home, or the top of the disk where the account has none: `Path.home()` raises when
    neither HOME nor a user entry names one."""
    try:
        return Path.home()
    except (RuntimeError, KeyError, OSError):
        return Path(os.sep)


def _home_place() -> list[dict]:
    """Home as a place to restart from, or nothing where it is not a folder."""
    home = _home()
    return [{"name": t("word.home"), "path": str(home)}] if home.is_dir() else []


def _common_roots() -> list[dict]:
    if sys.platform == "win32":
        found = [{"name": f"{letter}:", "path": f"{letter}:\\"} for letter in
                 (chr(code) for code in range(ord("A"), ord("Z") + 1))
                 if Path(f"{letter}:\\").exists()]
        found += _home_place()
        return found
    found = [{"name": "/", "path": "/"}, *_home_place()]
    if sys.platform == "darwin" and Path("/Volumes").exists():
        found.append({"name": t("word.volumes"), "path": "/Volumes"})
    if sys.platform == "linux":
        found += [{"name": _name(Path(extra)), "path": extra}
                 for extra in ("/media", "/mnt") if Path(extra).exists()]
    return found


def _program_roots() -> list[dict]:
    home = _home()
    if sys.platform == "darwin":
        found = [{"name": _name(candidate), "path": str(candidate)} for candidate in
                (Path("/Applications"), home / "Applications",
                 Path("/opt/homebrew/bin"), Path("/usr/local/bin")) if candidate.exists()]
        found += _home_place()
        found.append({"name": "/", "path": "/"})
        return found
    if sys.platform == "win32":
        found = [{"name": _name(Path(candidate)), "path": candidate} for candidate in
                (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))
                if candidate and Path(candidate).exists()]
        found += _home_place()
        found += [{"name": f"{letter}:", "path": f"{letter}:\\"} for letter in
                 (chr(code) for code in range(ord("A"), ord("Z") + 1))
                 if Path(f"{letter}:\\").exists()]
        return found
    found = [{"name": _name(candidate), "path": str(candidate)} for candidate in
            (Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt")) if candidate.exists()]
    found += _home_place()
    found.append({"name": "/", "path": "/"})
    return found


def _shown(path: str) -> str:
    """A place's path as a person writes it: `~` for home where the system has one."""
    home = str(_home())
    if sys.platform != "win32" and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def _roots(kind: str) -> list[dict]:
    """The places browsing can restart from, named so no two read alike - places of one
    name are told apart by their paths."""
    found = _program_roots() if kind == "exe" else _common_roots()
    names = [one["name"].casefold() for one in found]
    return [{**one, "name": _shown(one["path"])} if names.count(one["name"].casefold()) > 1
            else one for one in found]


def _start(kind: str) -> Path:
    """Where an empty path opens: home for a folder or a file, an `exe` field's own
    first root for a program - never one VPinFE picked for somebody. An account whose
    home is not a folder opens at the top of the disk instead of failing to open."""
    if kind == "exe":
        found = _program_roots()
        if found:
            return Path(found[0]["path"])
    home = _home()
    for place in (home, Path(home.anchor), Path(os.sep)):
        if place.is_dir():
            return place
    return home


def listing(path: str = "", kind: str = "dir", suffixes: tuple[str, ...] = (),
            hidden: bool = False) -> dict:
    """One folder's contents, and where browsing can start over.

    `kind` says what `files` holds: nothing for `dir`, whatever this account can run for
    `exe` - an `.app` bundle included, on macOS, as one of its own entries rather than
    something to walk into - and whatever matches `suffixes` for `file`, or every file
    where `suffixes` is empty. A dot-named entry is listed only with `hidden`; a private
    folder never is.

    A `path` naming a file, or one inside a macOS `.app` bundle, lists the folder that
    holds it instead of failing to list a file as though it were one.
    """
    if kind not in ("dir", "file", "exe"):
        raise service_errors.RefusedError(t("error.folders.unknown_kind"))
    if not path:
        here = _start(kind)
    else:
        raw = Path(os.path.abspath(os.path.expanduser(path)))
        bundle = _bundle_boundary(raw)
        here = bundle.parent if bundle is not None else raw.parent if raw.is_file() else raw
    if not here.is_dir():
        raise service_errors.NotFoundError(t("error.folders.no_folder"))
    private = _private_roots()
    if _is_private(here, private):
        raise service_errors.RefusedError(t("error.folders.private"))
    parent = here.parent
    folders, files = _entries(here, private, kind, suffixes, hidden)
    return {"path": str(here), "parent": "" if parent == here else str(parent),
            "folders": folders, "files": files, "roots": _roots(kind)}


def _resolved(path: str) -> Path:
    """What `path` names once links are followed, unless that is a private place.

    Every check is on what the path resolves to: a link called `cover.png` that points at
    a key or a document is judged as the key or the document.
    """
    if "\0" in path:
        raise service_errors.RefusedError(t("error.filesystem.path_unreadable"))
    try:
        here = Path(os.path.abspath(os.path.expanduser(path))).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise service_errors.RefusedError(t("error.filesystem.path_unreadable")) from exc
    if _is_private(here, _private_roots()):
        raise service_errors.RefusedError(t("error.folders.private"))
    return here


def _on_this_device(path: str) -> Path:
    here = _resolved(path)
    if not here.is_file():
        raise service_errors.NotFoundError(t("error.folders.no_file"))
    return here


def source_here(path: str) -> Path:
    """A file or a folder on this device to import, or a refusal.

    The listing is the whole disk, so this is the same bound the previews and the slot
    pickers have: nothing under a private folder, links followed first. What may be done
    with the answer is the import's own limit - a session takes what its analysis
    recognizes and a table needs a program that plays it.
    """
    here = _resolved(path)
    if not here.exists():
        raise service_errors.NotFoundError(t("error.filesystem.nothing_there"))
    return here


def media_file(path: str) -> Path:
    """One image, video or sound on this device to look at, or a refusal."""
    here = _on_this_device(path)
    if here.suffix.lower() not in _SHOWN:
        raise service_errors.RefusedError(t("error.filesystem.not_media"))
    return here


def file_of_kind(path: str, suffixes: tuple[str, ...]) -> Path:
    """One file on this device to take, held to the extensions a slot accepts."""
    here = _on_this_device(path)
    if here.suffix.lower() not in tuple(one.lower() for one in suffixes):
        raise service_errors.RefusedError(t("error.folders.wrong_kind"))
    return here


def make_folder(parent: str, name: str) -> dict:
    """Make one folder inside another and answer with its listing, so a picker can go in.

    A name and not a path: a separator in it would make folders the person did not name,
    in places the picker never showed. The same refusals as a listing - a private folder
    is not written to any more than it is read.

    A parent is always named. A listing may fall back to somewhere to start, but an empty
    one here would resolve to the server's working directory and make the folder there.
    """
    wanted = str(name or "").strip()
    if not wanted:
        raise service_errors.RefusedError(t("error.folders.name_needed"))
    if wanted in (".", "..") or any(one in wanted for one in "/\\"):
        raise service_errors.RefusedError(t("error.folders.name_slash"))
    if any(ord(one) < 32 or ord(one) == 127 for one in wanted):
        raise service_errors.RefusedError(t("error.folders.name_has_control"))
    if not str(parent or "").strip():
        raise service_errors.RefusedError(t("error.folders.parent_needed"))
    here = Path(os.path.abspath(os.path.expanduser(str(parent))))
    if not here.is_dir():
        raise service_errors.NotFoundError(t("error.folders.no_folder"))
    if _is_private(here, _private_roots()):
        raise service_errors.RefusedError(t("error.folders.private"))
    made = here / wanted
    if made.exists():
        raise service_errors.RefusedError(t("error.folders.already_there", name=wanted))
    try:
        made.mkdir()
    except (OSError, ValueError) as exc:
        raise service_errors.RefusedError(t("error.folders.cannot_make")) from exc
    return listing(str(made))

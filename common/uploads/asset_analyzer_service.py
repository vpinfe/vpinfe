"""Working out what is inside something the user dropped on us.

A zip, a folder or a loose file all become the same answer: which of these are media,
which is the table, and which we do not recognize. Nothing is moved at this stage.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from common.failures import why
from common.games import info_codec, table_files
from common.games.asset_registry import (
    is_readme,
    match_media_kind,
    spec_for,
)
from common.host import tools
from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY
from common.uploads import working_folder
from common.uploads.conversion import Conversion

try:
    import rarfile
except ImportError:  # pragma: no cover - optional dependency
    # Absent is a state this module reads for; the name is a module or nothing.
    rarfile = None  # type: ignore[assignment]

try:
    import py7zr
except ImportError:  # pragma: no cover - optional dependency
    # Absent is a state this module reads for; the name is a module or nothing.
    py7zr = None  # type: ignore[assignment]


logger = logging.getLogger("vpinfe.common.uploads.asset_analyzer_service")

_CHUNK = 1024 * 1024

_JUNK_COMPONENTS = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}
_ROM_SUFFIXES = {".bin", ".rom", ".cpu", ".snd", ".dat"}
# Serum ships in two formats; .cROMc is as common as .cRZ. Read from the registry so the
# archive path and the bare-file path cannot drift - they had already drifted once.
_SERUM_SUFFIXES = frozenset(spec_for("altcolor_serum").extensions)
_VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".webm"}
_AUDIO_SUFFIXES = {".mp3", ".ogg", ".wav"}
_ALTSOUND_MARKERS = {"altsound.csv", "g-sound.csv"}
_PUP_MARKERS_EXACT = {"screens.pup", "editthispuppack.bat", "scriptonly.txt"}
_PUP_MARKER_GLOBS = ("*option*.bat", "*screen*.bat")
_PUP_FALLBACK_MIN_SUBDIRS = 10
_PUP_FALLBACK_MIN_VIDEOS = 10



@dataclass(frozen=True)
class SourceEntry:
    path: str        # key within the source, used for extraction
    arcname: str     # normalized path (wrapper stripped), used for layout and rules
    size: int
    is_dir: bool


@dataclass(frozen=True)
class DetectedAsset:
    kind: str
    label: str
    entries: tuple[SourceEntry, ...]
    root: str = ""          # arcname-space subtree root for folder kinds; "" for file kinds
    # Which media kind, when `kind` is "media". `kind` names the asset family, so the
    # media one keeps its qualifier.
    media_kind: str = ""
    size: int = 0
    detail: str = ""
    preview: str = ""       # inline text for readme kinds, shown in the confirm dialog
    # Set by the arrival engine, never by the rules: the page a paged media kind is written
    # as (0 leaves it to the planner) and the table a per-table file is named for.
    page: int = 0
    table_id: str = ""
    # Set by the arrival engine for a picture, sound or video the install converts: what it
    # is written as. None for every file placed as it arrived.
    convert: Conversion | None = None
    # A `.info` that is one table's own file, found by what it says: its name tells nothing
    # in a bundle with no folder around it. Set by the analyzer.
    own_table: bool = False


@dataclass(frozen=True)
class AnalysisResult:
    source_kind: str
    source_name: str
    assets: tuple[DetectedAsset, ...]
    has_game: bool
    notes: tuple[str, ...] = ()
    error: str = ""
    unrecognized: tuple[str, ...] = ()   # source-relative paths that no rule claimed
    bundle_info: dict | None = None      # parsed .info content when a bundle carries one


# --- Source backends -------------------------------------------------------

class AssetSource(Protocol):
    name: str
    kind: str

    def entries(self) -> list[SourceEntry]: ...
    def extract_member(self, entry_path: str, dest: Path) -> None: ...
    def archive_path(self) -> Path | None: ...
    def close(self) -> None: ...


def _clean_name(raw: str) -> str:
    name = raw.replace("\\", "/").strip()
    while name.startswith("/"):
        name = name[1:]
    return name.rstrip("/")


class ZipSource:
    """An AssetSource backed by a zip archive."""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.kind = "zip"
        self._path = path
        self._zip = zipfile.ZipFile(path)
        self._raw_by_path: dict[str, str] = {}
        for info in self._zip.infolist():
            if info.is_dir():
                continue
            cleaned = _clean_name(info.filename)
            if cleaned:
                self._raw_by_path.setdefault(cleaned, info.filename)

    def entries(self) -> list[SourceEntry]:
        out: list[SourceEntry] = []
        for info in self._zip.infolist():
            cleaned = _clean_name(info.filename)
            if not cleaned:
                continue
            out.append(SourceEntry(cleaned, cleaned, info.file_size, info.is_dir()))
        return out

    def extract_member(self, entry_path: str, dest: Path) -> None:
        raw = self._raw_by_path[entry_path]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._zip.open(raw) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, _CHUNK)

    def archive_path(self) -> Path | None:
        return self._path

    def close(self) -> None:
        self._zip.close()


class RarSource:
    """An AssetSource backed by a rar archive."""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.kind = "rar"
        self._path = path
        # Before rarfile falls back to a search of its own.
        rar_tool_available()
        self._rar = rarfile.RarFile(path)
        self._raw_by_path: dict[str, str] = {}
        for info in self._rar.infolist():
            if info.isdir():
                continue
            cleaned = _clean_name(info.filename)
            if cleaned:
                self._raw_by_path.setdefault(cleaned, info.filename)

    def entries(self) -> list[SourceEntry]:
        out: list[SourceEntry] = []
        for info in self._rar.infolist():
            cleaned = _clean_name(info.filename)
            if not cleaned:
                continue
            out.append(SourceEntry(cleaned, cleaned, info.file_size, info.isdir()))
        return out

    def extract_member(self, entry_path: str, dest: Path) -> None:
        raw = self._raw_by_path[entry_path]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._rar.open(raw) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, _CHUNK)

    def archive_path(self) -> Path | None:
        return self._path

    def close(self) -> None:
        self._rar.close()


class SevenZipSource:
    """An AssetSource backed by a 7z archive."""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.kind = "7z"
        self._path = path
        self._members: list[SourceEntry] = []
        self._raw_by_path: dict[str, str] = {}
        with py7zr.SevenZipFile(path, "r") as archive:
            for info in archive.list():
                cleaned = _clean_name(info.filename)
                if not cleaned:
                    continue
                size = int(getattr(info, "uncompressed", 0) or 0)
                self._members.append(SourceEntry(cleaned, cleaned, size, info.is_directory))
                if not info.is_directory:
                    self._raw_by_path.setdefault(cleaned, info.filename)

    def entries(self) -> list[SourceEntry]:
        return list(self._members)

    def extract_member(self, entry_path: str, dest: Path) -> None:
        raw = self._raw_by_path[entry_path]
        dest.parent.mkdir(parents=True, exist_ok=True)
        # py7zr has no cheap per-member stream, so extract into a scratch dir and copy out.
        with working_folder.scratch() as scratch:
            with py7zr.SevenZipFile(self._path, "r") as archive:
                archive.extract(path=scratch, targets=[raw])
            extracted = Path(scratch) / raw
            shutil.copyfile(extracted, dest)

    def archive_path(self) -> Path | None:
        return self._path

    def close(self) -> None:
        return None


class DirSource:
    """An AssetSource backed by a plain directory."""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.kind = "dir"
        self._root = path

    def entries(self) -> list[SourceEntry]:
        out: list[SourceEntry] = []
        for dirpath, dirnames, filenames in os.walk(self._root):
            rel_dir = Path(dirpath).relative_to(self._root)
            for name in dirnames:
                rel = (rel_dir / name).as_posix()
                out.append(SourceEntry(rel, rel, 0, True))
            for name in filenames:
                rel = (rel_dir / name).as_posix()
                try:
                    size = (Path(dirpath) / name).stat().st_size
                except OSError:
                    size = 0
                out.append(SourceEntry(rel, rel, size, False))
        return out

    def extract_member(self, entry_path: str, dest: Path) -> None:
        src = self._root / Path(*PurePosixPath(entry_path).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def archive_path(self) -> Path | None:
        return None

    def close(self) -> None:
        return None


class SingleFileSource:
    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.kind = "file"
        self._path = path

    def entries(self) -> list[SourceEntry]:
        try:
            size = self._path.stat().st_size
        except OSError:
            size = 0
        return [SourceEntry(self._path.name, self._path.name, size, False)]

    def extract_member(self, entry_path: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path, dest)

    def archive_path(self) -> Path | None:
        return self._path

    def close(self) -> None:
        return None


# rarfile's `tool_setup` switch for each of its backends.
_SETUP_SWITCHES = {"UNRAR": "unrar", "UNAR": "unar", "SEVENZIP": "sevenzip",
                   "SEVENZIP2": "sevenzip2", "BSDTAR": "bsdtar"}

# The program rarfile was last pointed at. Pointing it runs the program.
_pointed_at: Path | None = None


def rar_tool_available() -> bool:
    """Whether the registry finds a RAR tool that runs, with rarfile pointed at that one
    and no other."""
    global _pointed_at
    if rarfile is None:
        return False
    found = tools.resolve(tools.RAR)
    if found.state is not tools.State.FOUND or found.path is None:
        return False
    if found.path == _pointed_at:
        return True
    backend = tools.rar_backend(found.path)
    setattr(rarfile, f"{backend}_TOOL", str(found.path))
    try:
        rarfile.tool_setup(force=True, **{switch: name == backend
                                          for name, switch in _SETUP_SWITCHES.items()})
    except rarfile.RarCannotExec:
        return False
    _pointed_at = found.path
    return True


def _files(picked: list) -> str:
    return t("asset.analysis.files", count=len(picked))


def rar_tool_hint() -> str:
    return tools.hint(tools.RAR)


def open_source(path: Path) -> AssetSource:
    """Open a path as a uniform asset source (directory, archive, or single file)."""
    path = Path(path)
    if path.is_dir():
        return DirSource(path)
    ext = path.suffix.lower()
    if ext in {".zip", ".vpxz"}:
        return ZipSource(path)
    if ext == ".rar":
        if rarfile is None:
            raise _MissingBackendError(t("error.uploads.needs_package", package="rarfile"))
        return RarSource(path)
    if ext == ".7z":
        if py7zr is None:
            raise _MissingBackendError(t("error.uploads.needs_package", package="py7zr"))
        return SevenZipSource(path)
    return SingleFileSource(path)


class _MissingBackendError(RuntimeError):
    pass


# --- Normalization ---------------------------------------------------------

def _is_junk(arcname: str) -> bool:
    parts = [p.lower() for p in PurePosixPath(arcname).parts]
    if any(p in _JUNK_COMPONENTS for p in parts):
        return True
    base = PurePosixPath(arcname).name
    return base.startswith("._")


def _normalize(entries: list[SourceEntry]) -> list[SourceEntry]:
    # Drop OS junk only. A wrapper directory is intentionally left in place: folder
    # kinds carry a `root` and import lays them out relative to it, so a redundant
    # top folder never reaches the destination, while pack detection keeps its signal.
    return [e for e in entries if e.arcname and not _is_junk(e.arcname)]


# --- Detection helpers -----------------------------------------------------

def _parent(arcname: str) -> str:
    parent = PurePosixPath(arcname).parent
    return "" if str(parent) == "." else str(parent)


def _under(arcname: str, root: str) -> bool:
    if root == "":
        return True
    return arcname == root or arcname.startswith(root + "/")


def _suffix(arcname: str) -> str:
    return PurePosixPath(arcname).suffix.lower()


def _basename(arcname: str) -> str:
    return PurePosixPath(arcname).name


def _stem(arcname: str) -> str:
    """Lowercased: VPX matches a table's companions without regard to case."""
    return PurePosixPath(arcname).stem.lower()


# --- Detection -------------------------------------------------------------

def _analyze_entries(
        entries: list[SourceEntry],
) -> tuple[list[DetectedAsset], list[str], bool, tuple[str, ...]]:
    norm = _normalize(entries)
    files = [e for e in norm if not e.is_dir]
    claimed: set[str] = set()
    assets: list[DetectedAsset] = []
    notes: list[str] = []

    def unclaimed() -> list[SourceEntry]:
        return [e for e in files if e.path not in claimed]

    def claim_subtree(root: str) -> list[SourceEntry]:
        picked = [e for e in files if e.path not in claimed and _under(e.arcname, root)]
        for e in picked:
            claimed.add(e.path)
        return picked

    # 1. Table
    has_game = False
    vpx_dirs: set[str] = set()
    for e in list(unclaimed()):
        if _suffix(e.arcname) == ".vpx":
            claimed.add(e.path)
            has_game = True
            vpx_dirs.add(_parent(e.arcname))
            assets.append(DetectedAsset("table", spec_for("table").label, (e,),
                                        size=e.size, detail=_basename(e.arcname)))

    # 1b. Game metadata — bundle-scoped only: a .info beside a claimed .vpx. A lone
    # .info stays unrecognized (wholesale metadata replacement is never inferred).
    for e in list(unclaimed()):
        if _suffix(e.arcname) == ".info" and _parent(e.arcname) in vpx_dirs:
            claimed.add(e.path)
            assets.append(DetectedAsset("game_info", spec_for("game_info").label, (e,),
                                        size=e.size,
                                        detail=_basename(e.arcname)))

    # 1c. A table's own script, point of view and score view: beside a claimed table and
    # named for it. Any other .vbs is the player's, and placed as the table's it would
    # replace the script inside the table.
    tables = {(_parent(a.entries[0].arcname), _stem(a.entries[0].arcname))
              for a in assets if a.kind == "table"}
    by_suffix = {extension: kind for kind in ("script", "pov", "scv", "nix")
                 for extension in spec_for(kind).extensions}
    for e in list(unclaimed()):
        kind = by_suffix.get(_suffix(e.arcname), "")
        if kind and (_parent(e.arcname), _stem(e.arcname)) in tables:
            claimed.add(e.path)
            assets.append(DetectedAsset(kind, spec_for(kind).label, (e,), size=e.size,
                                        detail=_basename(e.arcname)))

    # 2. Backglass
    for e in list(unclaimed()):
        if _suffix(e.arcname) == ".directb2s":
            claimed.add(e.path)
            assets.append(DetectedAsset("backglass", spec_for("backglass").label, (e,),
                                        size=e.size, detail=_basename(e.arcname)))

    # Subtree claimers run before loose-file claimers so files inside a pack are not grabbed loose.
    # 3. AltSound
    altsound_roots: list[str] = []
    for e in unclaimed():
        if _basename(e.arcname).lower() in _ALTSOUND_MARKERS:
            altsound_roots.append(_parent(e.arcname))
    for root in _dedupe_roots(altsound_roots):
        picked = claim_subtree(root)
        if picked:
            assets.append(DetectedAsset(
                "altsound", spec_for("altsound").label, tuple(picked), root=root,
                size=sum(p.size for p in picked), detail=_files(picked)))

    # 4. PUP pack
    pup_roots = _find_pup_roots(unclaimed())
    for root in pup_roots:
        picked = claim_subtree(root)
        if picked:
            assets.append(DetectedAsset(
                "pup_pack", spec_for("pup_pack").label, tuple(picked), root=root,
                size=sum(p.size for p in picked), detail=_files(picked)))

    # 5. Music
    for root in _find_music_roots(unclaimed()):
        picked = claim_subtree(root)
        if picked:
            assets.append(DetectedAsset(
                "music", spec_for("music").label, tuple(picked), root=root,
                size=sum(p.size for p in picked), detail=_files(picked)))

    # 6. AltColor
    for e in list(unclaimed()):
        if _suffix(e.arcname) in _SERUM_SUFFIXES:
            claimed.add(e.path)
            assets.append(DetectedAsset("altcolor_serum", spec_for("altcolor_serum").label,
                                        (e,),
                                        size=e.size, detail=_basename(e.arcname)))
    vni_by_dir: dict[str, list[SourceEntry]] = {}
    for e in unclaimed():
        if _suffix(e.arcname) in {".vni", ".pal", ".pac"}:
            vni_by_dir.setdefault(_parent(e.arcname), []).append(e)
    for group in vni_by_dir.values():
        for e in group:
            claimed.add(e.path)
        assets.append(DetectedAsset(
            "altcolor_vni", spec_for("altcolor_vni").label, tuple(group),
            size=sum(g.size for g in group), detail=", ".join(_basename(g.arcname) for g in group)))

    # 7. ROM
    remaining = unclaimed()
    is_flat = not any(e.is_dir for e in norm) and all("/" not in e.arcname for e in remaining)
    if remaining and is_flat and all(_is_rom_suffix(e.arcname) for e in remaining):
        for e in remaining:
            claimed.add(e.path)
        assets.append(DetectedAsset(
            "rom", spec_for("rom").label, tuple(remaining),
            size=sum(e.size for e in remaining), detail=t("asset.analysis.whole_archive")))
    else:
        for e in list(unclaimed()):
            if _suffix(e.arcname) == ".zip":
                claimed.add(e.path)
                assets.append(DetectedAsset("rom", spec_for("rom").label, (e,),
                                            size=e.size, detail=_basename(e.arcname)))

    # 7b. Patch. A .dif is a delta against one exact base table, not an installable
    # artifact - it is
    # claimed so the user is told what it is, never so it can be installed alone.
    for e in list(unclaimed()):
        if _suffix(e.arcname) == ".dif":
            claimed.add(e.path)
            assets.append(DetectedAsset("patch", spec_for("patch").label, (e,), size=e.size,
                                        detail=_basename(e.arcname)))

    # 8. INI
    for e in list(unclaimed()):
        if _suffix(e.arcname) == ".ini":
            claimed.add(e.path)
            assets.append(DetectedAsset("ini", spec_for("ini").label, (e,),
                                        size=e.size, detail=_basename(e.arcname)))

    # 8b. The author's own notes - readme* any extension, and .nfo. Before media,
    # so a readme.png is the notes image, not wheel art. Narrow by design: a
    # blanket .txt would misfile alias.txt and its kin.
    for e in list(unclaimed()):
        if is_readme(_basename(e.arcname)):
            claimed.add(e.path)
            assets.append(DetectedAsset("readme", spec_for("readme").label, (e,),
                                        size=e.size, detail=_basename(e.arcname)))

    # 9. Media
    for e in list(unclaimed()):
        media_kind = match_media_kind(_basename(e.arcname))
        if media_kind:
            claimed.add(e.path)
            assets.append(DetectedAsset(
                "media", spec_for("media").label, (e,), media_kind=media_kind,
                size=e.size, detail=f"{_basename(e.arcname)} → {media_kind}"))

    unrecognized = tuple(e.arcname for e in files if e.path not in claimed)
    return assets, notes, has_game, unrecognized


def _dedupe_roots(roots: list[str]) -> list[str]:
    """Keep only outermost roots (drop any root nested under another)."""
    unique = sorted(set(roots), key=lambda r: (len(PurePosixPath(r).parts), r))
    kept: list[str] = []
    for root in unique:
        if any(_under(root, existing) and root != existing for existing in kept):
            continue
        kept.append(root)
    return kept


def _find_pup_roots(entries: list[SourceEntry]) -> list[str]:
    marker_roots: list[str] = []
    for e in entries:
        base = _basename(e.arcname).lower()
        if base in _PUP_MARKERS_EXACT or any(fnmatch.fnmatch(base, g) for g in _PUP_MARKER_GLOBS):
            marker_roots.append(_parent(e.arcname))
    if marker_roots:
        return _dedupe_roots(marker_roots)
    return _find_pup_roots_by_shape(entries)


def _find_pup_roots_by_shape(entries: list[SourceEntry]) -> list[str]:
    subdirs: dict[str, set[str]] = {}
    videos: dict[str, int] = {}
    for e in entries:
        parts = PurePosixPath(e.arcname).parts
        if len(parts) < 2:
            continue
        root = parts[0]
        subdirs.setdefault(root, set()).add(parts[1])
        if _suffix(e.arcname) in _VIDEO_SUFFIXES:
            videos[root] = videos.get(root, 0) + 1
    roots = [
        root for root, dirs in subdirs.items()
        if len(dirs) >= _PUP_FALLBACK_MIN_SUBDIRS
        and videos.get(root, 0) >= _PUP_FALLBACK_MIN_VIDEOS
    ]
    return _dedupe_roots(roots)


def _find_music_roots(entries: list[SourceEntry]) -> list[str]:
    by_dir: dict[str, list[SourceEntry]] = {}
    for e in entries:
        top = PurePosixPath(e.arcname).parts[0] if PurePosixPath(e.arcname).parts else ""
        if top == "" or "/" not in e.arcname:
            continue   # root-level files are ambiguous (e.g. table audio.mp3); leave to media
        by_dir.setdefault(top, []).append(e)
    roots: list[str] = []
    for root, members in by_dir.items():
        suffixes = {_suffix(m.arcname) for m in members}
        has_audio = bool(suffixes & _AUDIO_SUFFIXES)
        has_video = bool(suffixes & _VIDEO_SUFFIXES)
        if has_audio and not has_video and suffixes <= _AUDIO_SUFFIXES:
            roots.append(root)
    return roots


def _is_rom_suffix(arcname: str) -> bool:
    suffix = _suffix(arcname)
    if suffix in _ROM_SUFFIXES:
        return True
    # Media first: .mp3 and .mp4 would otherwise read as chip names below, and a flat
    # archive of audio would be claimed as a ROM.
    if suffix in _AUDIO_SUFFIXES or suffix in _VIDEO_SUFFIXES:
        return False
    # PinMAME chip names run to three characters - lahsnd.u21, lahdispa.106, lahcpua.112.
    # A two-character cap accepted .u7 and rejected its siblings, and since the caller
    # requires every member to match, one rejection sank the whole archive.
    ext = suffix.lstrip(".")
    return 1 <= len(ext) <= 3 and ext[-1:].isdigit()


_INFO_MAX_BYTES = 2 * 1024 * 1024


_README_PREVIEW_BYTES = 64 * 1024
_README_PREVIEW_CHARS = 4000


def _readme_preview(source: AssetSource, asset: DetectedAsset) -> str:
    """The text shown inline in the confirm dialog. Best-effort: an unreadable or
    binary readme just shows no preview, never blocks the import."""
    entry = asset.entries[0]
    if entry.size > _README_PREVIEW_BYTES or entry.arcname.lower().endswith(
            (*IMAGE_FAMILY, ".pdf")):
        return ""
    with working_folder.scratch() as scratch:
        dest = Path(scratch) / "readme"
        try:
            source.extract_member(entry.path, dest)
            text = dest.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    text = text.strip()
    if len(text) > _README_PREVIEW_CHARS:
        text = text[:_README_PREVIEW_CHARS] + "\n..."
    return text


def _read_info_json(source: AssetSource, asset: DetectedAsset) -> dict | None:
    """A bundle's .info as stored; None if oversized, unreadable, or not a JSON dict."""
    import json

    entry = asset.entries[0]
    if entry.size > _INFO_MAX_BYTES:
        return None
    with working_folder.scratch() as scratch:
        dest = Path(scratch) / "bundle.info"
        try:
            source.extract_member(entry.path, dest)
            data = json.loads(dest.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Unreadable bundle .info: %s", entry.arcname)
            return None
    return data if isinstance(data, dict) else None


# --- Public API ------------------------------------------------------------

def listed_files(entries: list[SourceEntry]) -> list[SourceEntry]:
    """The files of a listing that are somebody's: no directories, no OS junk."""
    return [e for e in _normalize(entries) if not e.is_dir]


def open_listed(path: Path) -> tuple[AssetSource | None, list[SourceEntry], str]:
    """Open a file, archive or directory and list it.

    The source is None, with the reason in words, where it cannot be read. A source that
    comes back is the caller's to close.
    """
    path = Path(path)
    try:
        source = open_source(path)
    except _MissingBackendError as exc:
        return None, [], why(exc)
    except Exception:
        logger.exception("Failed to open source: %s", path)
        return None, [], t("error.uploads.drop_unreadable")

    # Surface a missing RAR tool up front, before the confirm dialog, rather than
    # failing at extraction time after the user has committed to the import.
    if source.kind == "rar" and not rar_tool_available():
        source.close()
        return None, [], rar_tool_hint()
    try:
        return source, source.entries(), ""
    except _rar_exec_errors() as exc:
        logger.warning("RAR backend unavailable: %s", exc)
        source.close()
        return None, [], rar_tool_hint()
    except Exception:
        logger.exception("Failed to list source: %s", path)
        source.close()
        return None, [], t("error.uploads.drop_unreadable")


def analyze_members(
        source: AssetSource, entries: list[SourceEntry],
) -> tuple[list[DetectedAsset], tuple[str, ...], bool, tuple[str, ...], dict | None]:
    """What the rules find in these entries of `source`: the assets, the notes, whether a
    table is among them, the paths no rule claimed and the bundle's .info content."""
    assets, notes, has_game, unrecognized = _analyze_entries(entries)

    # A bundle .info is read up front (it is tiny) so its content can seed the
    # import dialog and be validated before anything is written.
    assets = [replace(a, preview=_readme_preview(source, a)) if a.kind == "readme" else a
              for a in assets]

    bundle_info = None
    infos = [(a, _read_info_json(source, a)) for a in assets if a.kind == "game_info"]
    own = {a.entries[0].path for a, raw in infos if table_files.is_ours(raw)}
    assets = [replace(a, own_table=True) if a.entries[0].path in own else a for a in assets]
    game_infos = [(a, raw) for a, raw in infos if a.entries[0].path not in own]
    if game_infos:
        first, raw = game_infos[0]
        bundle_info = info_codec.decode(raw) if raw is not None else None
        if bundle_info is None:
            assets = [a for a in assets if a.kind != "game_info" or a.own_table]
            unrecognized = unrecognized + tuple(e.arcname for e in first.entries)
            notes = list(notes) + [t("asset.analysis.info_skipped")]
    return assets, tuple(notes), has_game, unrecognized, bundle_info


def analyze_path(path: Path) -> AnalysisResult:
    """Analyze a file, archive, or directory and report the assets it contains."""
    path = Path(path)
    source, entries, error = open_listed(path)
    if source is None:
        return AnalysisResult(source_kind_of(path), path.name, (), False, error=error)
    try:
        assets, notes, has_game, unrecognized, bundle_info = analyze_members(source, entries)
    finally:
        source.close()

    if not assets:
        return AnalysisResult(source.kind, source.name, (), False, notes,
                              error=t("error.uploads.nothing_recognized"),
                              unrecognized=unrecognized)
    return AnalysisResult(source.kind, source.name, tuple(assets), has_game, notes,
                          unrecognized=unrecognized, bundle_info=bundle_info)


def analyze_upload_session(session_dir: Path) -> tuple[AnalysisResult, Path]:
    """Analyze a staged upload session and return the result plus the path to analyze/import from.

    A session holding a single archive analyzes that archive; a single non-archive file
    is analyzed on its own; anything else is treated as a dropped folder tree.
    """
    source = session_source(session_dir)
    return analyze_path(source), source


def session_source(session_dir: Path) -> Path:
    """What a staged session is read from: its one file, or else the folder itself."""
    session_dir = Path(session_dir)
    top_files = [p for p in session_dir.iterdir() if p.is_file()]
    top_dirs = [p for p in session_dir.iterdir() if p.is_dir()]
    if len(top_files) == 1 and not top_dirs:
        return top_files[0]
    return session_dir


def source_kind_of(path: Path) -> str:
    if path.is_dir():
        return "dir"
    ext = path.suffix.lower()
    if ext in {".zip", ".vpxz"}:
        return "zip"
    if ext == ".rar":
        return "rar"
    if ext == ".7z":
        return "7z"
    return "file"


def _rar_exec_errors() -> tuple[type[BaseException], ...]:
    if rarfile is None:
        return (RuntimeError,)
    return (rarfile.RarCannotExec, rarfile.BadRarFile)

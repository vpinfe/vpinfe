"""What a table would use on launch, and what the folder holds for whom.

Two families, split by how the engine finds the thing. An *asset* is resolved by
naming rule - VPX finds it with no help from the script. A *dependency* is declared
by the script and satisfied by content on disk. The rules here mirror VPX's own
lookup code per kind; citations in docs/conventions.md. Everything is computed from
directory listings - nothing here is stored, so nothing here can go stale.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from common.games.asset_registry import spec_for


# Asset kinds resolved by naming rule. `folder_fallback` mirrors VPX: the table's .ini
# and the backglass fall back to a folder-named file; a .pov is stem-only.
@dataclass(frozen=True)
class AssetKind:
    key: str
    extension: str
    folder_fallback: bool


# Which kinds VPX resolves per table, and whether a folder-named file stands in. That
# is this module's own knowledge; the extension is the registry's, so a kind has one
# declaration and adding an extension to it does not have to be remembered twice.
# A script named for the folder stands in for one named for the table, the same way an
# ini does. `pintable.cpp` falls back to `<folder>.vbs` when `<table>.vbs` is not there;
# saying otherwise here reads a folder-named script as absent while VPX is running it.
# `nix` is not folder-named on the assumption that Nixie matches `.pov`; its lookup has not been
# read, so this is the line to change if it falls back as `.scv` does. B2S Pro Server also
# tries the table's name cut after its first ")", which is the folder's name for a folder
# named `Name (Manufacturer Year)`, so `b2spro` falls back as the backglass does. It skips
# that step only when exact filename matching is switched on.
_PER_TABLE = (("backglass", True), ("b2spro", True), ("ini", True), ("script", True),
              ("pov", False), ("scv", True), ("nix", False))

VPX_ASSET_KINDS = tuple(
    AssetKind(kind, spec_for(kind).extensions[0], folder_fallback)
    for kind, folder_fallback in _PER_TABLE
)

PER_TABLE_KINDS = tuple(item.key for item in VPX_ASSET_KINDS)

RESOLUTION_DEDICATED = "dedicated"
RESOLUTION_SHARED = "shared"
RESOLUTION_NONE = "none"
# The file is there and the program will not read it, because something newer answers
# the same question. Its own answer rather than `none`, which would say there is nothing
# here - and the thing somebody does about it depends on knowing there is.
RESOLUTION_SUPERSEDED = "superseded"

# A kind the program stops reading once another kind resolves. Point of view moved into
# the settings system, and `pintable.cpp` imports a `.pov` only where the table has no
# settings file - so a table with an ini has a point of view its `.pov` had no part in.
_SUPERSEDED_BY = {"pov": "ini"}

BINDING_DEDICATED = "dedicated"
BINDING_SHARED = "shared"
BINDING_ORPHANED = "orphaned"


def folder_listing(game_dir: str | Path) -> tuple[list[str], list[str]]:
    """A game folder's files and its subfolders, in the shape everything here takes.

    One read per caller rather than one per kind: the resolvers below all answer from a
    listing, and a folder on a network share is expensive to walk twice.
    """
    files: list[str] = []
    subdirs: list[str] = []
    path = Path(game_dir)
    if path.is_dir():
        with os.scandir(path) as entries:
            for entry in entries:
                (files if _is_file(entry) else subdirs).append(entry.name)
    return files, subdirs


def _is_file(entry: os.DirEntry) -> bool:
    try:
        return entry.is_file()
    except OSError:
        return False


def _stem(name: str) -> str:
    return os.path.splitext(name)[0]


def _by_lower(names: Iterable[str]) -> dict[str, str]:
    """lowercase -> actual name. VPX matches companions case-insensitively."""
    return {name.lower(): name for name in names}


def resolve_for_table(table: str, folder_name: str, files: Iterable[str],
                      kinds: Sequence[AssetKind] = VPX_ASSET_KINDS) -> dict:
    """The launch lens: what this table would use, kind by kind.

    Mirrors VPX's search order - a file named for the table wins, a file named
    for the folder is the shared fallback, and a kind without a fallback (pov) is
    stem-or-nothing.

    A kind another one supersedes reports that rather than reporting that it resolved.
    Saying a file is in use when the program has stopped reading it is the worse of the
    two mistakes: somebody edits it and watches nothing happen.
    """
    lookup = _by_lower(files)
    stem = _stem(table)
    as_folder = stem.lower() == folder_name.lower()
    resolved = {}
    for kind in kinds:
        dedicated = (None if as_folder and kind.folder_fallback
                     else lookup.get((stem + kind.extension).lower()))
        if dedicated is not None:
            resolved[kind.key] = {"resolution": RESOLUTION_DEDICATED, "file": dedicated}
            continue
        if kind.folder_fallback:
            shared = lookup.get((folder_name + kind.extension).lower())
            if shared is not None:
                resolved[kind.key] = {"resolution": RESOLUTION_SHARED, "file": shared}
                continue
        resolved[kind.key] = {"resolution": RESOLUTION_NONE}

    for key, by in _SUPERSEDED_BY.items():
        found = resolved.get(key) or {}
        if (found.get("resolution") in (RESOLUTION_DEDICATED, RESOLUTION_SHARED)
                and (resolved.get(by) or {}).get("resolution")
                in (RESOLUTION_DEDICATED, RESOLUTION_SHARED)):
            resolved[key] = {**found, "resolution": RESOLUTION_SUPERSEDED,
                             "superseded_by": by}
    return resolved


def inventory(folder_name: str, files: Iterable[str], tables: Iterable[str],
              kinds: Sequence[AssetKind] = VPX_ASSET_KINDS) -> dict:
    """The inventory lens: every asset file present, attributed.

    `dedicated` names the table it serves; `shared` is the folder-named
    fallback, whatever the tables are called; `orphaned` is stem-named for a table
    that is not there - the residue of a deleted or renamed table, which is what an
    audit wants to see.
    """
    stems = {_stem(name).lower(): name for name in tables}
    folder_lower = folder_name.lower()
    result: dict[str, dict] = {kind.key: {"files": []} for kind in kinds}
    for kind in kinds:
        for name in sorted(files):
            if not name.lower().endswith(kind.extension):
                continue
            stem_lower = _stem(name).lower()
            if stem_lower in stems and not (kind.folder_fallback
                                            and stem_lower == folder_lower):
                entry = {"file": name, "binding": BINDING_DEDICATED,
                         "table": stems[stem_lower]}
            elif stem_lower == folder_lower:
                entry = {"file": name, "binding": BINDING_SHARED}
            else:
                entry = {"file": name, "binding": BINDING_ORPHANED}
            result[kind.key]["files"].append(entry)
    return result


def parse_alias_file(text: str) -> dict[str, str]:
    """pinmame's alias format: `alias, real` per line, `#` comments.

    Mirrors checkGameAlias in pinmame's Alias.cpp, including its case-insensitive
    match on the alias side.
    """
    aliases: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.replace(",", " ").split()]
        if len(parts) >= 2:
            aliases[parts[0].lower()] = parts[1]
    return aliases


def resolve_rom_chain(declared: str, aliases: dict[str, str], rom_files: Iterable[str],
                      required: bool | None = None) -> dict:
    """The pinmame dependency chain: declared -> alias -> effective -> installed.

    `required` is whether the script actually drives the emulator - measured from
    the script, not guessed from the name's shape. A declared name with
    required=False is a DOF key; required=True with installed=None is the case a
    doctor should surface. None means the table's metadata predates the detector.

    `installed` is deliberately True-or-None, never False: an unmerged clone set
    can need a parent zip we cannot see the need for, and global rom locations are
    not searched here - so "not found in the table folder" is not "missing".
    """
    declared = (declared or "").strip()
    if not declared:
        return {"declared": None, "alias_of": None, "effective": None,
                "required": required, "catalog": None, "clone_of": None,
                "audit": None, "installed": None}

    real = aliases.get(declared.lower())
    effective = real or declared
    chain = {
        "declared": declared,
        "alias_of": real,
        "effective": effective,
        "required": required,
        "catalog": None,
        "clone_of": None,
        "audit": None,
        "installed": None,
    }

    wanted = effective.lower()
    for name in rom_files:
        base, ext = os.path.splitext(name)
        if ext.lower() == ".zip" and base.lower() == wanted:
            chain["installed"] = True
            break
    return chain


def apply_audit(chain: dict, entry: dict | None) -> dict:
    """Fold PinMAME's own answer into the chain, when there is one.

    The audit outranks the name-match: `found` means the set would actually load,
    chip CRCs and parent zips included, so it is what finally makes
    installed=False sayable. No entry means no answer, and the chain keeps
    whatever the name-match concluded.
    """
    if entry is None:
        chain["audit"] = "unavailable"
        return chain
    if not entry.get("catalog"):
        chain["catalog"] = False
        chain["audit"] = "unknown_set"
        chain["installed"] = None
        return chain
    chain["catalog"] = True
    chain["clone_of"] = entry.get("clone_of")
    chain["description"] = entry.get("description")
    if entry.get("found"):
        chain["audit"] = "ok"
        chain["installed"] = True
    else:
        chain["audit"] = "missing"
        chain["installed"] = False
    return chain


def read_alias_map(game_dir: str) -> dict[str, str]:
    """The per-game alias file, when there is one. Standalone reads
    pinmame/alias.txt; the global VPMAlias.txt is a Windows VPinMAME concern."""
    path = os.path.join(game_dir, "pinmame", "alias.txt")
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return parse_alias_file(handle.read())
    except OSError:
        return {}


ROMS_FOLDER = "pinmame/roms"
# The two folders the emulator reads by their place in a game.
FIXED_PLACES = (ROMS_FOLDER, "pinmame/nvram")


def list_rom_files(game_dir: str) -> list[str]:
    try:
        return os.listdir(os.path.join(game_dir, *ROMS_FOLDER.split("/")))
    except OSError:
        return []


def nvram_state(game_dir: str, effective_rom: str | None) -> dict:
    """Play state keyed by the effective rom, computed per request so
    modified_at is live - a tournament harvester's question is "is there a score
    newer than my last visit", which a scan-time answer cannot serve."""
    if not effective_rom:
        return {"present": False}
    path = os.path.join(game_dir, "pinmame", "nvram", f"{effective_rom}.nv")
    try:
        stat = os.stat(path)
    except OSError:
        return {"present": False}
    return {"present": True, "file": os.path.basename(path),
            "modified_at": int(stat.st_mtime)}


def flexdmd_state(subdirs: Iterable[str], detected: bool | None) -> dict:
    """The flexdmd dependency: script-declared project folder, content on disk.

    `declared` stays None until the script extraction exists - the honest
    degradation, same as the rom detector. `installed` reports any .UltraDMD
    content folder in the meantime.
    """
    content = sorted(d for d in subdirs if d.lower().endswith(".ultradmd"))
    return {
        "detected": detected,
        "declared": None,
        "installed": bool(content),
        "content": content,
    }

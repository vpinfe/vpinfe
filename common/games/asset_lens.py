"""Every asset file in the library, as its own lens on it.

The same shape as the media lens and for the same reason: a row is a file, or the absence
of one, because that is what a gap is countable in and what a bulk action can be handed.

What differs is the resolution. Five kinds are found by VPX's own naming rule, so a file
named for a table beats a file named for the folder - and two of those five take no
folder-named fallback at all, which makes a folder-named `.vbs` or `.pov` a file nothing
will ever load. The rest belong to the folder and have no per-table answer.

Media is not here and assets are not there. The two lenses answer different questions, and
a matrix that mixes them is neither.
"""

from __future__ import annotations

import codecs
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import service_errors
from common.games import (
    asset_origin,
    asset_resolver,
    collation,
    game_lens,
    game_repository,
    media_probe,
    table_lens,
)
from common.games.asset_registry import altsound_folder, altsound_folders, is_readme, spec_for
from common.games.formats import directb2s_parser
from common.games.game_metadata import effective_vps_id
from common.games.game_repository import game_to_row
from common.games.tables import table_names
from common.i18n import t
from common.media import sized_media

logger = logging.getLogger("vpinfe.common.games.asset_lens")


# Kinds the folder holds as a whole, with no per-table naming. Not the registry's list:
# `rom` is a declared dependency whose `installed` is true-or-unknown by design, so a row
# calling it missing would call every EM table broken.
_FOLDER_KINDS = ("pup_pack", "alt_color", "alt_sound", "music")

# The one kind held as many files: a game can hold several ROM zips, each wanted by
# whichever of its tables name it.
ROM_KIND = "rom"

# The author's notes: one row per file in the game folder's root that reads as a readme.
README_KIND = "readme"

# A row with no file still carries every field one with a file does, so nothing reading
# these has to ask which shape it got.
_ABSENT = {"binding": "none", "present": False, "table": "", "table_file": "",
           "file": None, "path": None, "origin": None, "matched_to": None,
           "matched_by": None, "serves": None}

_BY_BINDING = {asset_resolver.BINDING_DEDICATED: "table",
               asset_resolver.BINDING_SHARED: "game",
               asset_resolver.BINDING_ORPHANED: "orphaned"}


# Every kind a row of this lens can be about: the five resolved per table, the four the
# folder holds whole, then ROM files and readmes. What a filter on the Kind column offers.
KINDS = (*asset_resolver.PER_TABLE_KINDS, *_FOLDER_KINDS, ROM_KIND, README_KIND)


def label_of(kind: str) -> str:
    """The word a row's Kind column holds for `kind`."""
    try:
        return spec_for(kind).label
    except Exception:
        return kind.replace("_", " ").title()


def _row(game_id: str, row: dict, kind: str) -> dict:
    return {"game_id": game_id, "game": str(row.get("name") or ""),
            "manufacturer": str(row.get("manufacturer") or ""),
            "year": str(row.get("year") or ""),
            "kind": kind, "label": label_of(kind),
            "label_key": f"asset.kind.{kind}.label",
            "vps_id": str(row.get("vps_id") or "")}


def _folder_state(kind: str, game_dir: Path, subdirs: list[str]) -> str:
    """Where this kind lives in the folder, or "" for not here.

    The same tests the game resource makes, so the two lenses cannot disagree about one
    folder. These kinds are directories rather than files, and the path is what the row
    shows: naming the kind again would be a column repeating its neighbour.
    """
    names = {name.lower(): name for name in subdirs}
    if kind == "pup_pack":
        return names.get("pupvideos", "")
    if kind == "alt_color":
        return next((names[key] for key in ("serum", "vni") if key in names), "")
    if kind == "alt_sound":
        return altsound_folder(game_dir, subdirs)
    if kind == "music":
        return names.get("music", "")
    return ""


def _pup_packs(game_dir: Path, folder: str) -> list[str]:
    """The packs inside the PUP folder, each by its folder-relative path. A pack is a
    folder of its own, named for the ROM or table it plays for."""
    if not folder:
        return []
    try:
        names = sorted((one.name for one in (game_dir / folder).iterdir() if one.is_dir()),
                       key=str.lower)
    except OSError:
        return []
    return [f"{folder}/{name}" for name in names]


def pup_pack_folders(game_dir: Path) -> set[str]:
    """The pack folders here, named as their rows name them, lowercased."""
    _, subdirs = asset_resolver.folder_listing(game_dir)
    folder = _folder_state("pup_pack", game_dir, subdirs)
    return {pack.lower() for pack in _pup_packs(game_dir, folder)}


def _pack_row(game_id: str, row: dict, kind: str, game_dir: Path, pack: str, tables: int,
              recorded: dict, hosts: dict) -> dict:
    path = game_dir / pack
    return {**_row(game_id, row, kind), **_ABSENT, "id": f"{game_id}:{kind}:{pack}",
            "binding": "game", "present": True, "file": Path(pack).name, "path": pack,
            "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
            "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
            "matched_by": asset_origin.basis_of(recorded, game_dir, path) or None,
            "serves": tables}


def whole_folders(game_dir: Path) -> set[str]:
    """The folders here that hold a kind whole, named as their rows name them."""
    _, subdirs = asset_resolver.folder_listing(game_dir)
    found = {here for kind in _FOLDER_KINDS if (here := _folder_state(kind, game_dir, subdirs))}
    return found | set(altsound_folders(game_dir, subdirs))


def _held(item: dict, kind: str, base: dict, game_dir: Path, tables: list[dict],
          by_filename: dict, recorded: dict, hosts: dict, fallback: bool,
          with_own: int) -> dict:
    """One file that is here: whose it is, and what is recorded about it.

    `with_own` is how many of the game's tables have a file of this kind of their own: they
    load that file, so the shared one serves only the rest.
    """
    path = game_dir / item["file"]
    owner = by_filename.get(str(item.get("table") or ""), {})
    binding = _BY_BINDING.get(item["binding"], item["binding"])
    # A folder-named file for a kind VPX resolves stem-only serves no table at all. It
    # reads as shared because of its name and is inert in fact, so the count says so.
    serves = (len(tables) - with_own if binding == "game" and fallback
              else 0 if binding in ("game", "orphaned") else 1)
    # An orphan names a table that is not here, so the inventory has no table to give
    # it. The stem is the name it was written for, which is the whole of what makes the
    # row actionable: it says which build went away.
    named_for = (str(item.get("table") or "")
                 or (Path(item["file"]).stem if binding == "orphaned" else ""))
    return {**base,
            "id": f"{base['game_id']}:{kind}:{item['file']}",
            "table": str(owner.get("id") or ""),
            "table_file": named_for,
            "binding": binding,
            "present": True,
            "serves": serves,
            "file": item["file"],
            "path": asset_origin.path_of(game_dir, path) or None,
            "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
            "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
            "matched_by": asset_origin.basis_of(recorded, game_dir, path) or None}


def rom_users(game: Any) -> dict[str, list[str]]:
    """Each ROM file in the game's folder, mapped to the ids of the tables that read it.

    A table reads the zip named for its effective ROM, and the parent set a clone names.
    A file no table reads maps to an empty list, which is what makes it Unused. Only zips
    are ROM files, so a stray note or system file in the folder is not a row.
    """
    row = game_to_row(game)
    roms = os.path.join(str(row.get("game_dir") or ""), *asset_resolver.ROMS_FOLDER.split("/"))
    held = {name: os.path.splitext(name)[0].lower()
            for name in asset_resolver.list_rom_files(str(row.get("game_dir") or ""))
            if name.lower().endswith(".zip") and os.path.isfile(os.path.join(roms, name))}
    users: dict[str, list[str]] = {name: [] for name in held}
    if not held:
        return users
    for table in table_lens.table_rows(game, row):
        chain = (table.get("dependencies") or {}).get("pinmame") or {}
        wanted = {str(chain.get(key) or "").lower() for key in ("effective", "clone_of")} - {""}
        for name, stem in held.items():
            if stem in wanted and table["id"]:
                users[name].append(table["id"])
    return users


def _rom_rows(game_id: str, row: dict, game: Any, game_dir: Path, recorded: dict,
              hosts: dict) -> list[dict]:
    base = _row(game_id, row, ROM_KIND)
    found = []
    for name, users in sorted(rom_users(game).items(), key=lambda item: item[0].lower()):
        path = game_dir / asset_resolver.ROMS_FOLDER / name
        found.append({**base, **_ABSENT,
                      "id": f"{game_id}:{ROM_KIND}:{name}",
                      "binding": "game", "present": True, "file": name,
                      "path": asset_origin.path_of(game_dir, path) or None,
                      "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
                      "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
                      "matched_by": asset_origin.basis_of(recorded, game_dir, path) or None,
                      "serves": len(users), "users": users,
                      "size_bytes": os.path.getsize(path)})
    return found


def _readme_rows(game_id: str, row: dict, game_dir: Path, files: list[str], recorded: dict,
                 hosts: dict) -> list[dict]:
    """A readme serves no table, so `serves` is None: 0 would read as Unused."""
    base = _row(game_id, row, README_KIND)
    found = []
    for name in sorted((one for one in files if is_readme(one)), key=str.lower):
        path = game_dir / name
        found.append({**base, **_ABSENT,
                      "id": f"{game_id}:{README_KIND}:{name}",
                      "binding": "game", "present": True, "file": name,
                      "path": asset_origin.path_of(game_dir, path) or None,
                      "origin": asset_origin.origin_of(hosts, game_dir, path) or None,
                      "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
                      "matched_by": asset_origin.basis_of(recorded, game_dir, path) or None,
                      "serves": None, "size_bytes": os.path.getsize(path)})
    return found


def listing(limit: int = 0, offset: int = 0, game: str = "",
            kind: str = "") -> dict[str, Any]:
    """One row per asset file the library holds, plus one per file it does not.

    A file named for a table gets its own row. A file named for nothing - the residue of
    a table that was renamed or deleted - gets one too, and says so: it is the thing an
    audit of a folder wants to see, and nothing has ever shown it.
    """
    kinds = [item.key for item in asset_resolver.VPX_ASSET_KINDS]
    folder_kinds = list(_FOLDER_KINDS)
    if kind:
        kinds = [item for item in kinds if item == kind]
        folder_kinds = [item for item in folder_kinds if item == kind]
    with_roms = not kind or kind == ROM_KIND
    with_readme = not kind or kind == README_KIND
    fallback = {item.key: item.folder_fallback for item in asset_resolver.VPX_ASSET_KINDS}

    found: list[dict] = []
    for game_id, entry in game_repository.catalog().items():
        if game and game != game_id:
            continue
        row = {**game_to_row(entry),
               "vps_id": effective_vps_id(entry.meta_config or {})}
        game_dir = Path(entry.full_path_game or "")
        try:
            files, subdirs = asset_resolver.folder_listing(game_dir)
        except OSError:
            logger.warning("assets: cannot read %s", game_dir)
            continue

        tables = [table for table in table_lens.table_files(game_dir, files) if table["id"]]
        by_filename = {str(table.get("filename") or ""): table for table in tables}
        inventory = asset_resolver.inventory(game_dir.name, files, table_names(files))
        recorded = asset_origin.sources(game_dir)
        hosts = {key: str(source.get("host", "") or "").strip()
                 for key, source in recorded.items()
                 if str(source.get("host", "") or "").strip()}

        for name in kinds:
            base = _row(game_id, row, name)
            held = (inventory.get(name) or {}).get("files") or []
            shared = [item for item in held
                      if item["binding"] == asset_resolver.BINDING_SHARED]
            others = [item for item in held
                      if item["binding"] != asset_resolver.BINDING_SHARED]
            with_own = len({str(item.get("table") or "") for item in others
                            if item["binding"] == asset_resolver.BINDING_DEDICATED
                            and str(item.get("table") or "") in by_filename})
            if shared:
                found.extend(_held(item, name, base, game_dir, tables, by_filename,
                                   recorded, hosts, fallback[name], with_own)
                             for item in shared)
            else:
                serves = len(tables) - with_own
                # No row where nothing would use one. A folder whose every table has a
                # file of its own is not missing the shared file, and reporting it as a
                # gap would be the same invention as an empty per-table row.
                if serves > 0:
                    found.append({**base, **_ABSENT, "id": f"{game_id}:{name}:"})
            found.extend(_held(item, name, base, game_dir, tables, by_filename,
                               recorded, hosts, fallback[name], with_own)
                         for item in others)

        if with_roms:
            found.extend(_rom_rows(game_id, row, entry, game_dir, recorded, hosts))

        if with_readme:
            found.extend(_readme_rows(game_id, row, game_dir, files, recorded, hosts))

        for name in folder_kinds:
            here = _folder_state(name, game_dir, subdirs)
            if name == "pup_pack" and (packs := _pup_packs(game_dir, here)):
                found.extend(_pack_row(game_id, row, name, game_dir, pack, len(tables),
                                       recorded, hosts) for pack in packs)
                continue
            found.append({**_row(game_id, row, name), **_ABSENT,
                          "id": f"{game_id}:{name}:",
                          "binding": "game" if here else "none", "present": bool(here),
                          "file": here or None, "path": here or None,
                          "serves": len(tables) if here else None})

    found.sort(key=lambda item: (collation.sort_key(item["game"]),
                                 collation.sort_key(item["label"]),
                                 collation.sort_key(str(item.get("table_file") or ""))))
    total = len(found)
    window = found[offset:offset + limit] if limit else found[offset:]
    return {"total": total, "offset": offset, "count": len(window), "assets": window}


# Files read as text for their first lines. A backglass is XML too, and is left out: its
# lines are embedded pictures.
_TEXT = frozenset({".vbs", ".ini", ".pov", ".txt", ".md", ".nfo", ".cfg"})
HEAD_LINES = 40
# Enough for a whole script, and a bound on what one request can make this read.
_ALL_LINES_BYTES = 4 * 1024 * 1024


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def inside(game_dir: Path, path: str) -> Path:
    """`path` under the game's folder, or a refusal where it leads out of it."""
    root = game_dir.resolve()
    if "\x00" in path:
        raise service_errors.RefusedError(t("error.assets.outside_folder"),
                                          details={"path": path})
    target = (root / path).resolve()
    if not path.strip() or target != root and root not in target.parents:
        raise service_errors.RefusedError(t("error.assets.outside_folder"),
                                          details={"path": path})
    return target


def legacy_codepage(path: Path) -> str:
    """The code page a text file that is not UTF-8 is read in: the DOS one an .nfo was
    drawn in, and otherwise the Windows one most scripts were written in."""
    return "cp437" if path.suffix.lower() == ".nfo" else "cp1252"


def _head(path: Path, lines: int) -> str:
    """The first `lines` lines, or all of them for 0. UTF-8 where it is, and otherwise
    the file's legacy code page."""
    with path.open("rb") as handle:
        data = handle.read(_ALL_LINES_BYTES if lines == 0 else 256 * max(lines, 1))
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
    except UnicodeDecodeError:
        text = data.decode(legacy_codepage(path), errors="replace")
    kept = text.lstrip("\ufeff").splitlines()
    return "\n".join(kept if lines == 0 else kept[:lines])


def file_detail(game_id: str, path: str, lines: int = HEAD_LINES) -> dict[str, Any]:
    """One asset file or folder, by the path the listing gives it."""
    game = game_lens.game_or_refuse(game_id)
    game_dir = Path(game.full_path_game or "")
    target = inside(game_dir, path)
    shown = {"path": target.relative_to(game_dir.resolve()).as_posix(), "file": target.name,
             "folder": False, "files": None, "size_bytes": None, "modified": None,
             "format": None, "head": None}
    if target.is_dir():
        count, total, newest = 0, 0, 0.0
        for root, _dirs, names in os.walk(target):
            for name in names:
                try:
                    stat = (Path(root) / name).stat()
                except OSError:
                    continue
                count, total = count + 1, total + stat.st_size
                newest = max(newest, stat.st_mtime)
        return {**shown, "folder": True, "files": count, "size_bytes": total,
                "modified": _stamp(newest) if count else None}
    try:
        stat = target.stat()
    except OSError:
        raise service_errors.NotFoundError(t("error.assets.no_such_file", path=path),
                                           details={"path": path}) from None
    detail = {**shown, "size_bytes": stat.st_size, "modified": _stamp(stat.st_mtime),
              "format": media_probe.probe(target)["format"],
              "head": _head(target, lines) if target.suffix.lower() in _TEXT else None}
    if target.suffix.lower() == ".directb2s":
        detail["backglass"] = _backglass_facts(target)
    return detail


def _backglass_facts(path: Path) -> dict[str, Any] | None:
    """What a backglass file says about itself, with the file's version token for the
    picture address. None where the file has gone."""
    facts = directb2s_parser.read(path)
    if facts is None:
        return None
    return {"author": facts.author, "artwork": facts.artwork, "rom": facts.rom,
            "players": facts.players, "grill": facts.grill,
            "two_versions": facts.two_versions,
            "size": list(facts.size) if facts.size else None, "lamps": facts.lamps,
            "still": facts.still,
            "scoreview_art": list(facts.scoreview_art) if facts.scoreview_art else None,
            "problems": list(facts.problems), "damaged": directb2s_parser.damaged(facts),
            "displays": [{"target": one.target, "x": one.placement.x, "y": one.placement.y,
                          "w": one.placement.w, "h": one.placement.h}
                         for one in facts.displays if not one.source],
            "version": sized_media.version(path) or ""}

"""Turning an analyzed drop into a plan, and only then into files on disk.

The plan is built and shown before anything is written, so an import that would
overwrite a file is something the user declines rather than undoes.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from common import tokens
from common.atomic_write import staged_for, write_atomic
from common.failures import why
from common.games import (
    asset_resolver,
    file_move,
    game_repository,
    library_folders,
    media_placement,
    table_swap,
)
from common.games.asset_registry import spec_for
from common.games.formats.vpx_parser import VPXParser
from common.games.game_repository import refresh_game
from common.games.game_service import (
    _find_vpx_file,
    _safe_upload_name,
    ensure_dir,
    record_arrived_table,
    sanitize_dir_name,
)
from common.games.identity_claims import DeclaredIdentity
from common.games.info_file import VPINFE_SECTION, MetaConfig
from common.games.tables import entry_filename, entry_for_filename, table_entries
from common.i18n import t
from common.ids import new_id
from common.kept_copies import IMPORT, copy_aside
from common.media.media_specs import PAGED_KINDS
from common.uploads.asset_analyzer_service import (
    AnalysisResult,
    AssetSource,
    DetectedAsset,
    SourceEntry,
    open_source,
    rar_tool_hint,
)

logger = logging.getLogger("vpinfe.common.uploads.asset_import_service")

@dataclass(frozen=True)
class PlannedItem:
    asset: DetectedAsset
    destination: str        # absolute path (file target, or base dir for tree kinds)
    action: str             # replace_vpx | add_table | replace_b2s | copy | zip_rom
                            # | extract_tree | replace_media | apply_patch | write_info
    default_enabled: bool = True
    # The arrival member this item comes from and how it was claimed (declared, named),
    # where the arrival engine planned it.
    member: str = ""
    basis: str = ""
    # Offered beside what was asked for, so left out of an import that names no selection.
    extra: bool = False
    # A replace_vpx item names the table it puts the file in the place of, the file that
    # table has now, the kinds of its own file carried to the new one, and the kinds the
    # drop brings of its own.
    table_id: str = ""
    replaces: str = ""
    carry: tuple[str, ...] = ()
    brings: tuple[str, ...] = ()
    instead: bool = False
    # A replace_media item names what the file is named for and the page it is written as;
    # the stem is where `page` is resolved, which for a later page is where page 1 is.
    stem: str = ""
    page: int = 1


@dataclass(frozen=True)
class BlockedItem:
    asset: DetectedAsset
    reason: str


@dataclass(frozen=True)
class ImportPlan:
    # A str, where a path in flight is a Path: this one is serialized onto the wire,
    # and "" carries "no target folder" - which Path("") would turn into ".".
    game_dir: str           # target game dir (existing, or the resolved new-game dir)
    new_game_dir_name: str  # non-empty only for new-game bundle imports
    rom_name: str
    items: tuple[PlannedItem, ...]
    blocked: tuple[BlockedItem, ...]
    # Where a per-table file fell back to the default table although the game has several:
    # the tables it could have been named for, as id, label and default. Empty otherwise.
    tables: tuple[dict, ...] = ()


def _basename(arcname: str) -> str:
    return PurePosixPath(arcname).name


def _is_rar_exec_error(exc: BaseException) -> bool:
    try:
        import rarfile
    except ImportError:  # pragma: no cover - optional dependency
        return False
    return isinstance(exc, rarfile.RarCannotExec)


def _safe_dest(base_dir: Path, relative: str) -> Path:
    """Resolve a relative path under base_dir, rejecting traversal or absolute paths."""
    rel = PurePosixPath(relative)
    drive_letter = len(relative) >= 2 and relative[1] == ":"
    if rel.is_absolute() or ".." in rel.parts or drive_letter:
        raise ValueError(t("error.uploads.unsafe_path", path=relative))
    dest = (base_dir / Path(*rel.parts)).resolve()
    try:
        dest.relative_to(base_dir.resolve())
    except ValueError as exc:
        raise ValueError(t("error.uploads.unsafe_path", path=relative)) from exc
    return dest


def _rel_under_root(arcname: str, root: str) -> str:
    if not root:
        return arcname
    if arcname == root:
        return _basename(arcname)
    if arcname.startswith(root + "/"):
        return arcname[len(root) + 1:]
    return _basename(arcname)


def _rom_dest_name(asset: DetectedAsset, source_name: str) -> tuple[str, str]:
    """Return (filename, action) for a ROM asset destination under pinmame/roms."""
    if len(asset.entries) == 1 and PurePosixPath(asset.entries[0].arcname).suffix.lower() == ".zip":
        return _safe_upload_name(_basename(asset.entries[0].arcname)), "copy"
    stem = Path(source_name).stem or "rom"
    return f"{_safe_upload_name(stem)}.zip", "zip_rom"


def _patched_vpx_name(asset: DetectedAsset, base: Path, vpx_stem: str) -> str:
    """The filename applying this patch will produce.

    The patch's own name, so a mod's sidecars and artwork still match it by stem. Tagged
    only where that would land on a .vpx already here: never overwrite the base, which is
    the one file the result cannot be rebuilt without.
    """
    name = _safe_upload_name(Path(_basename(asset.entries[0].arcname)).stem)
    # Case-insensitively: on macOS and Windows "table.vpx" IS "Table.vpx".
    if name.lower() == vpx_stem.lower() or (base / f"{name}.vpx").exists():
        name = f"{name} [patched]"
    return f"{name}.vpx"


def _sidecar_stem(assets: Iterable[DetectedAsset], base: Path, vpx_stem: str) -> str:
    """Which table a bundle's .directb2s and .ini belong to: the patched table when the
    bundle carries a patch, since they describe what the patch produces, not the base.

    Deselecting the patch afterwards leaves them named for a .vpx that never gets built.
    """
    for asset in assets:
        if asset.kind == "patch" and vpx_stem:
            return Path(_patched_vpx_name(asset, base, vpx_stem)).stem
    return vpx_stem


def _arriving_stem(assets: Iterable[DetectedAsset]) -> str:
    """The name of the table a drop brings, or "" where it brings none."""
    return next((Path(_basename(asset.entries[0].arcname)).stem
                 for asset in assets if asset.kind == "table"), "")


# Where each kind that lives in a folder of its own is placed. One rule, because there
# are two ways in - something uploaded and something imported from another frontend -
# and a second copy would be right until one of them changed. `{rom}` is filled from the
# game's ROM, which is why those kinds declare `requires_rom`.
_FOLDERS: dict[str, tuple[str, ...]] = {
    "rom": ("pinmame", "roms"),
    "altsound": ("altsound", "{rom}"),
    "altcolor_serum": ("serum", "{rom}"),
    "altcolor_vni": ("vni", "{rom}"),
    "music": ("music",),
    "pup_pack": ("pupvideos",),
    "nvram": ("pinmame", "nvram"),
    "cfg": ("pinmame", "cfg"),
    "user": ("user",),
    "scripts": ("scripts",),
    # Empty: the folder is a game's own, beside the table, named for what its script asks.
    "dmd_content": (),
}
# The ROM name map sits in the folder that holds the machine's saved state.
_FOLDERS["rom_alias"] = _FOLDERS["cfg"][:-1]


def require_plain_rom(rom: str) -> str:
    """The ROM name, trimmed, or a ValueError where it is more than one folder's name.

    A ROM name becomes a folder inside the game, so a separator or ".." in it would put
    the files somewhere else. An empty name passes: whether one is needed is the
    caller's question.
    """
    name = str(rom or "").strip()
    if name and (name in {".", ".."} or Path(name).name != name
                 or "\\" in name or "\0" in name):
        raise ValueError(t("error.uploads.rom_not_plain", name=name))
    return name


def folder_for(kind: str, game_dir: str | Path, rom: str = "") -> Path | None:
    """The folder inside a game that holds this kind, or None where it has no folder.

    None is an answer: a backglass and a table sit beside the game file and are named
    after it, so "which folder" is the wrong question for them and a made-up one would
    be worse than saying so.

    Raises ValueError for a ROM name that is not a plain name, where the kind uses it.
    """
    parts = _FOLDERS.get(str(kind or ""))
    if parts is None:
        return None
    if not any("{rom}" in one for one in parts):
        return Path(game_dir).joinpath(*parts)
    wanted = require_plain_rom(rom)
    if not wanted:
        return None
    return Path(game_dir).joinpath(*(tokens.fill(one, {"rom": wanted}) for one in parts))


def folder_kinds() -> tuple[str, ...]:
    """The kinds that live in a folder of their own."""
    return tuple(sorted(_FOLDERS))


def _in_game(kind: str, base: Path, rom: str = "") -> Path:
    """`folder_for` where the kind is known to have a folder.

    Every kind routed through here does, and a rom-keyed one is only reached after the
    ROM name has been checked, so None here would be a routing mistake rather than an
    answer about this asset.
    """
    found = folder_for(kind, base, rom)
    if found is None:
        raise RuntimeError(f"{kind} has no folder inside a game")
    return found


def _plan_asset(asset: DetectedAsset, base: Path, vpx_stem: str, rom_name: str,
                source_name: str, game_kind_action: str,
                sidecar_stem: str = "", media_stem: str = "") -> PlannedItem | BlockedItem:
    """Where one detected asset lands, or why it cannot."""
    kind = asset.kind
    spec = spec_for(kind)
    if spec.requires_rom and not rom_name:
        return BlockedItem(asset, t("error.uploads.no_rom"))

    if kind == "table":
        dest = base / _safe_upload_name(_basename(asset.entries[0].arcname))
        if game_kind_action == "add_table" and dest.exists():
            return BlockedItem(asset, t("error.games.file_exists"))
        return PlannedItem(asset, str(dest), game_kind_action)
    if kind == "game_info":
        # Always written as <folder>.info — the parser matches it by folder name.
        return PlannedItem(asset, str(_info_path(base)), "write_info")
    own = PurePosixPath(_basename(asset.entries[0].arcname))
    stem = sidecar_stem or vpx_stem or base.name
    if kind == "backglass":
        return PlannedItem(asset, str(base / f"{stem}.directb2s"), "replace_b2s")
    if kind == "ini":
        return PlannedItem(asset, str(base / f"{stem}.ini"), "copy")
    if kind in ("script", "pov", "scv", "nix"):
        return PlannedItem(asset, str(base / f"{stem}{own.suffix.lower()}"), "copy")
    # Which folder each of these lands in is asked of the registry, not repeated here:
    # an import from another frontend puts the same things in the same places, and two
    # copies of the rule would be right until one of them changed.
    if kind == "rom":
        name, action = _rom_dest_name(asset, source_name)
        return PlannedItem(asset, str(_in_game(kind, base) / name), action)
    if kind in _COLOR_KINDS:
        return _plan_color(asset, base, rom_name)
    if kind in ("altsound", "pup_pack", "music"):
        return PlannedItem(asset, str(_in_game(kind, base, rom_name)), "extract_tree")
    if kind == "readme":
        name = _safe_upload_name(_basename(asset.entries[0].arcname))
        return PlannedItem(asset, str(base / name), "copy")
    if kind == "patch":
        if not vpx_stem:
            return BlockedItem(asset, t("error.uploads.no_table"))
        return PlannedItem(asset, str(base / _patched_vpx_name(asset, base, vpx_stem)),
                           "apply_patch")
    if kind == "media":
        return _plan_media(asset, base, media_stem, asset.page or 1)
    return BlockedItem(asset, t("error.uploads.cannot_import_kind"))


# The plugins ask for the table's ROM by name: Serum for <rom>/<rom>.cRZ or .cROMc, VNI
# for <rom>.pal and <rom>.vni, else pin2dmd.pal and pin2dmd.vni. A file under any other
# name imports and is never loaded. VNI does not look for a .pac at all.
_COLOR_KINDS = ("altcolor_serum", "altcolor_vni")
_SERUM_NAMES = {".crz": ".cRZ", ".cromc": ".cROMc"}
_VNI_FALLBACK = "pin2dmd"


def _plan_color(asset: DetectedAsset, base: Path, rom_name: str) -> PlannedItem | BlockedItem:
    """Where one color file lands, named as its plugin loads it."""
    own = PurePosixPath(_basename(asset.entries[0].arcname))
    suffix = own.suffix.lower()
    rom = _safe_upload_name(rom_name)
    if asset.kind == "altcolor_vni" and suffix == ".pac":
        return BlockedItem(asset, t("error.uploads.pac_not_loaded"))
    if asset.kind == "altcolor_vni" and own.stem.lower() == _VNI_FALLBACK:
        name = f"{_VNI_FALLBACK}{suffix}"
    else:
        name = f"{rom}{_SERUM_NAMES.get(suffix, suffix)}"
    return PlannedItem(asset, str(_in_game(asset.kind, base, rom) / name), "copy")


def _one_file_each(assets: Iterable[DetectedAsset]) -> list[DetectedAsset]:
    """A VNI group is several files and `copy` carries one, so each gets its own item."""
    out: list[DetectedAsset] = []
    for asset in assets:
        if asset.kind != "altcolor_vni" or len(asset.entries) < 2:
            out.append(asset)
            continue
        out.extend(replace(asset, entries=(one,), size=one.size,
                           detail=_basename(one.arcname)) for one in asset.entries)
    return out


def _one_to_a_name(items: list[PlannedItem],
                   blocked: list[BlockedItem]) -> tuple[list[PlannedItem], list[BlockedItem]]:
    """Two files that would take one name: the first stays, the rest are refused. A name is
    the same whatever its case, as it is on a case-insensitive disk."""
    taken: set[str] = set()
    kept: list[PlannedItem] = []
    refused = list(blocked)
    for item in items:
        name = item.destination.lower()
        if name in taken:
            refused.append(BlockedItem(item.asset, t(
                "error.uploads.same_name", name=Path(item.destination).name)))
            continue
        taken.add(name)
        kept.append(item)
    return kept, refused


def _info_path(base: Path) -> Path:
    return base / f"{base.name}.info"


def _plan_media(asset: DetectedAsset, base: Path, stem: str = "", page: int = 1) -> PlannedItem:
    """A media file for the game, or for the table `stem` names: the name the panel's Add
    gives it."""
    convert = asset.convert
    suffix = convert.suffix if convert else PurePosixPath(asset.entries[0].arcname).suffix
    stem = stem or base.name
    where = media_placement.page_stem(base, asset.media_kind, stem, stem, page)
    name = media_placement.target_name(asset.media_kind, where, suffix, page,
                                       unsupported=bool(convert and convert.as_is))
    return PlannedItem(asset, str(base / "medias" / name), "replace_media",
                       stem=where, page=page)


def held_tables(base: Path) -> list[dict]:
    """The tables the library holds for the game in `base`, each as `table_swap.choices`
    gives it. Empty for a folder the library does not hold."""
    game_id = table_swap.game_id_of(base)
    return table_swap.choices(game_id) if game_id else []


def table_stem(base: Path, table_id: str) -> str:
    """The file stem of one of the game's tables, or a ValueError where it has no such
    table."""
    found = next((one for one in held_tables(base) if one["id"] == table_id), None)
    if found is None:
        raise ValueError(t("error.games.swap_not_contained"))
    return Path(found["filename"]).stem


def media_stem(base: Path, table_id: str, scope: str) -> str:
    """What media is named for: the game's folder, or the table's file where the drop
    named a table. A game with one table has nothing to tell apart, so a table the drop did
    not ask for by `scope` means the game there."""
    if not table_id or scope == "game":
        return base.name
    stem = table_stem(base, table_id)
    if scope != "table" and len(held_tables(base)) < 2:
        return base.name
    return stem


def _new_games_under(location_id: str = "") -> str:
    """The folder a new game is created in.

    The location marked "create new games here". When none of them will take it this is
    refused rather than landing somewhere else: the row on screen says where new games
    go, and writing to a different one makes that row a lie.
    """
    from common.games.library_folders import destination

    found = destination(location_id)
    if not found.path:
        raise ValueError(found.reason)
    return found.path


class SwapKeptError(ValueError):
    """An item failed after the table swap ran. `replaced` is what the swap did."""

    def __init__(self, message: str, replaced: list[dict]) -> None:
        super().__init__(message)
        self.replaced = replaced


@dataclass(frozen=True)
class _Swap:
    """The table a drop takes the place of."""

    table_id: str
    filename: str


def _swap_target(base: Path, replace_table: str) -> _Swap | None:
    """The table a drop replaces: the one named, else the game's default. None where the
    game has no table that can be swapped, so the drop joins the others."""
    game_id = table_swap.game_id_of(base)
    if not game_id:
        return None
    held = table_swap.choices(game_id)
    if replace_table:
        found = next((one for one in held if one["id"] == replace_table), None)
        if found is None:
            raise ValueError(t("error.games.swap_not_contained"))
    else:
        found = next((one for one in held if one["default"]), None)
    return _Swap(found["id"], found["filename"]) if found else None


def _swapping(items: list[PlannedItem], blocked: list[BlockedItem], analysis: AnalysisResult,
              base: Path, swap: _Swap, carry: Iterable[str] | None,
              instead: bool) -> tuple[list[PlannedItem], list[BlockedItem]]:
    """The table item made a swap of `swap`'s table, and the drop's own files of a kind that
    is carried left out: the old file follows the table, and two cannot take one name."""
    brings = {asset.kind for asset in analysis.assets
              if asset.kind in asset_resolver.PER_TABLE_KINDS}
    held = set(table_swap.named_files(base, swap.filename))
    kept: list[PlannedItem] = []
    refused = list(blocked)
    for item in items:
        if item.action != "replace_vpx":
            kept.append(item)
            continue
        target = Path(item.destination)
        if target.name != swap.filename and target.name.lower() != swap.filename.lower() \
                and target.exists():
            refused.append(BlockedItem(item.asset, t("error.games.file_exists")))
            continue
        chosen = (table_swap.default_carry(brings, instead) if carry is None else set(carry))
        chosen = (chosen & held) - table_swap.shared_by_name(base, target.name)
        kept.append(replace(item, table_id=swap.table_id, replaces=swap.filename,
                            carry=tuple(sorted(chosen)), brings=tuple(sorted(brings)),
                            instead=instead))
    carried = {kind for one in kept for kind in one.carry}
    return [one for one in kept
            if not (one.action in ("replace_b2s", "copy") and one.asset.kind in carried
                    and one.asset.kind in asset_resolver.PER_TABLE_KINDS)], refused


# What is named for one table's file and nothing else: the table's own backglass, settings,
# script, point of view, score view and NIX file.
SIDECAR_KINDS = ("backglass", "ini", "script", "pov", "scv", "nix")


def tables_to_choose(base: Path) -> tuple[dict, ...]:
    """The game's tables, where there is more than one to name a file for."""
    held = held_tables(base)
    if len(held) < 2:
        return ()
    return tuple({"id": one["id"], "label": one["label"], "default": one["default"]}
                 for one in held)


def build_import_plan(analysis: AnalysisResult, *, game_dir: Path | None = None,
                      game_row: dict | None = None, rom_name: str = "",
                      allow_new_game: bool = False,
                      games_path: str | None = None,
                      location_id: str = "", add_table: bool = False,
                      replace_table: str = "", carry: Iterable[str] | None = None,
                      instead: bool = False, table_id: str = "",
                      scope: str = "") -> ImportPlan:
    """Route detected assets to destinations for an existing game or a new game bundle.

    For an existing game, a table in the drop takes the place of `replace_table`, or of the
    game's default when none is named, or with `add_table` joins the game's tables as one
    more. `carry` is the kinds of file named for the replaced table that follow it to the
    new one; none given takes the defaults for an update, or for another release with
    `instead`. A game the library does not hold has no table to replace, so its drop joins.

    `table_id` is the table the drop landed on. What the drop brings that is named for a
    table follows it, except that a table the drop itself brings names its own. `scope`
    ("game" or "table") says what media is named for; empty lets the game's table count
    decide.
    """
    rom_name = require_plain_rom(rom_name)
    items: list[PlannedItem] = []
    blocked: list[BlockedItem] = []
    new_bundle = analysis.has_game and allow_new_game

    if new_bundle:
        game_asset = next(a for a in analysis.assets if a.kind == "table")
        vpx_stem = Path(_basename(game_asset.entries[0].arcname)).stem
        new_dir_name = _safe_upload_name(vpx_stem)
        base = Path(games_path or _new_games_under(location_id)).expanduser() \
            / new_dir_name
        sidecar_stem = _sidecar_stem(analysis.assets, base, vpx_stem)
        for asset in _one_file_each(analysis.assets):
            planned = _plan_asset(asset, base, vpx_stem, rom_name,
                                  analysis.source_name, game_kind_action="copy",
                                  sidecar_stem=sidecar_stem)
            if isinstance(planned, PlannedItem):
                items.append(planned)
            else:
                blocked.append(planned)
        items, blocked = _one_to_a_name(items, blocked)
        return ImportPlan(str(base), new_dir_name, "", tuple(items), tuple(blocked))

    if game_dir is not None:
        base = game_dir.expanduser()
        try:
            vpx_stem = _find_vpx_file(base).stem
        except (FileNotFoundError, OSError):
            vpx_stem = ""
        arriving = _arriving_stem(analysis.assets)
        landed_on = table_stem(base, table_id) if table_id else ""
        sidecar_stem = (arriving or landed_on
                        or _sidecar_stem(analysis.assets, base, vpx_stem))
        landed_media = media_stem(base, table_id, scope)
        named = {} if arriving or landed_on else {
            Path(one["filename"]).stem.lower(): Path(one["filename"]).stem
            for one in held_tables(base)}
        defaulted = False
        swap = None if add_table else _swap_target(base, replace_table or table_id)
        for asset in _one_file_each(analysis.assets):
            own = Path(_basename(asset.entries[0].arcname)).stem.lower()
            hit = named.get(own, "") if asset.kind in SIDECAR_KINDS else ""
            stem = hit or sidecar_stem
            for_media = landed_media
            if asset.table_id:
                stem = table_stem(base, asset.table_id)
                for_media = media_stem(base, asset.table_id, scope)
            planned = _plan_asset(asset, base, vpx_stem, rom_name,
                                  analysis.source_name,
                                  game_kind_action="replace_vpx" if swap else "add_table",
                                  sidecar_stem=stem, media_stem=for_media)
            if isinstance(planned, PlannedItem):
                items.append(planned)
                defaulted = defaulted or (
                    asset.kind in SIDECAR_KINDS and not hit and not asset.table_id
                    and not arriving and not landed_on)
            else:
                blocked.append(planned)
        if swap:
            items, blocked = _swapping(items, blocked, analysis, base, swap, carry, instead)
        items, blocked = _one_to_a_name(items, blocked)
        return ImportPlan(str(base), "", rom_name, tuple(items), tuple(blocked),
                          tables_to_choose(base) if defaulted else ())

    for asset in analysis.assets:
        if spec_for(asset.kind).requires_game:
            blocked.append(BlockedItem(asset, t("error.uploads.target_game")))
        else:
            blocked.append(BlockedItem(asset, t("error.uploads.target_library")))
    return ImportPlan("", "", rom_name, (), tuple(blocked))


def vps_folder_name(vps_entry: dict) -> str:
    """Derive the canonical game folder name from a VPS entry (same shape the
    Import Table dialog builds: "Name (Manufacturer Year)")."""
    name = vps_entry.get("name", "")
    mfg = vps_entry.get("manufacturer") or vps_entry.get("mfg") or ""
    year = vps_entry.get("year") or ""
    if mfg and year:
        folder = f"{name} ({mfg} {year})"
    elif mfg:
        folder = f"{name} ({mfg})"
    elif year:
        folder = f"{name} ({year})"
    else:
        folder = name
    return sanitize_dir_name(folder)


def find_vps_entry(vps_id: str) -> dict | None:
    """Look up a VPS entry by its id."""
    from common.games.game_service import load_vpsdb

    wanted = (vps_id or "").strip()
    if not wanted:
        return None
    for entry in load_vpsdb():
        if entry.get("id") == wanted:
            return entry
    return None


def select_plan_items(plan: ImportPlan, indices: list[int] | None = None,
                      new_game_dir_name: str | None = None) -> ImportPlan:
    """Narrow a plan to the chosen item indices and optionally rename a new-game target.

    indices=None keeps every item. For new-game bundles, passing a new name rebases all
    item destinations under the renamed folder. Shared by the confirm dialog and the HTTP
    import endpoint so both honor selection and rename identically.
    """
    if indices is None:
        chosen = plan.items
    else:
        wanted = set(indices)
        chosen = _closed_pages(plan, tuple(item for index, item in enumerate(plan.items)
                                           if index in wanted))

    if not plan.new_game_dir_name:
        return replace(plan, items=chosen)

    new_name = (sanitize_dir_name(new_game_dir_name) if new_game_dir_name is not None
                else plan.new_game_dir_name)
    if not new_name:
        raise ValueError(t("error.uploads.folder_unnamed"))
    if new_name == plan.new_game_dir_name:
        return replace(plan, items=chosen)

    old_base = plan.game_dir
    new_base = Path(old_base).parent / new_name
    rebased = tuple(_moved(item, old_base, new_base) for item in chosen)
    return replace(plan, game_dir=str(new_base), new_game_dir_name=new_name, items=rebased)


def _closed_pages(plan: ImportPlan, chosen: tuple[PlannedItem, ...]) -> tuple[PlannedItem, ...]:
    """The chosen pages of a paged kind, numbered one after another from where the plan began
    them: a page left out does not leave a gap the write would refuse."""
    def place(item: PlannedItem) -> tuple | None:
        if item.action == "replace_media" and item.asset.media_kind in PAGED_KINDS:
            return (item.asset.media_kind, item.stem)
        return None

    first: dict[tuple, int] = {}
    for item in plan.items:
        if (key := place(item)) is not None:
            first[key] = min(first.get(key, item.page), item.page)
    counted: dict[tuple, int] = {}
    out = []
    for item in chosen:
        key = place(item)
        if key is None:
            out.append(item)
            continue
        number = first[key] + counted.get(key, 0)
        counted[key] = counted.get(key, 0) + 1
        if number != item.page:
            moved = _plan_media(item.asset, Path(plan.game_dir), item.stem, number)
            item = replace(item, destination=moved.destination, stem=moved.stem, page=number)
        out.append(item)
    return tuple(out)


def _moved(item: PlannedItem, old_base: str, new_base: Path) -> PlannedItem:
    if item.action == "replace_media":
        stem = "" if item.stem == Path(old_base).name else item.stem
        moved = _plan_media(item.asset, new_base, stem, item.page)
        return replace(item, destination=moved.destination, stem=moved.stem)
    if item.action == "write_info":
        return replace(item, destination=str(_info_path(new_base)))
    return replace(item, destination=item.destination.replace(old_base, str(new_base), 1))


# Medias stays listed although nothing writes it any more: an imported .info written by
# a 2.x build still carries one, and dropping it from here would let it back in as an
# unmanaged section and be preserved forever.
_MANAGED_INFO_SECTIONS = {"Info", "User", VPINFE_SECTION, "tables", "assets", "Medias"}
_MACHINE_LOCAL_INFO_KEYS = {"alt_launcher", "plugin_profile"}


def _is_empty_value(value: object) -> bool:
    return value in (None, "", 0, [], {})


def _resolves_locally(key: str, value: object) -> bool:
    """Machine-specific override values must exist on this machine to be adopted."""
    text = str(value or "")
    if not text:
        return False
    if key == "alt_launcher":
        return Path(text).expanduser().exists()
    if key == "plugin_profile":
        from common.config.paths import PLUGIN_PROFILES_DIR

        try:
            return any(p.stem == text or p.name == text
                       for p in Path(PLUGIN_PROFILES_DIR).iterdir())
        except OSError:
            return False
    return True


def merge_info(incoming: dict, existing: dict) -> dict:
    """Merge an imported .info into an existing one: fill gaps, never replace.

    Info is adopted wholesale only when the existing game has no VPS association.
    User and VPinFE fill empty fields (machine-specific overrides only if they resolve
    locally). tables and assets always keep the local version: tables describes
    the builds on THIS disk and carries decisions made here — what is hidden, what has
    been patched, and later play stats — none of which an imported file can speak for.
    assets records what we placed in THIS folder, so an imported list describes files
    that are not here. Unknown sections are added when absent, never replaced.
    """
    merged = dict(existing)

    for section, value in incoming.items():
        if section not in merged and section not in _MANAGED_INFO_SECTIONS:
            merged[section] = value

    if not (existing.get("Info") or {}).get("VPSId") and incoming.get("Info"):
        merged["Info"] = incoming["Info"]

    local_user = dict(existing.get("User") or {})
    for key, value in (incoming.get("User") or {}).items():
        if _is_empty_value(local_user.get(key)) and not _is_empty_value(value):
            local_user[key] = value
    if local_user:
        merged["User"] = local_user

    local_vpinfe = dict(existing.get(VPINFE_SECTION) or {})
    for key, value in (incoming.get(VPINFE_SECTION) or {}).items():
        if not _is_empty_value(local_vpinfe.get(key)) or _is_empty_value(value):
            continue
        if key in _MACHINE_LOCAL_INFO_KEYS and not _resolves_locally(key, value):
            continue
        local_vpinfe[key] = value
    if local_vpinfe:
        merged[VPINFE_SECTION] = local_vpinfe

    return merged


def _import_game_info(source: AssetSource, asset: DetectedAsset, base: Path) -> None:
    import json

    entry = asset.entries[0]
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / "bundle.info"
        source.extract_member(entry.path, staged)
        incoming = json.loads(staged.read_text(encoding="utf-8"))
    if not isinstance(incoming, dict):
        raise ValueError(t("error.uploads.info_unreadable"))

    dest = _info_path(base)
    if dest.exists():
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Existing .info is unreadable; treating as empty: %s", dest)
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        copy_aside(dest, IMPORT)
        payload = merge_info(incoming, existing)
    else:
        payload = incoming

    write_atomic(dest, lambda handle: json.dump(payload, handle, indent=4))


# --- Execution -------------------------------------------------------------

def _extract_replace(source: AssetSource, entry: SourceEntry, dest: Path) -> None:
    ensure_dir(dest.parent)
    with staged_for(dest) as staged:
        source.extract_member(entry.path, staged)


def has_table(game_dir: Path) -> bool:
    try:
        _find_vpx_file(game_dir)
    except FileNotFoundError:
        return False
    return True


def _swap_table(source: AssetSource, item: PlannedItem, base: Path) -> dict:
    """Put the arriving table in the place of the one the item names, keeping its entry.
    The answer is the game, the table's row and the copies of what went to Backups."""
    entry = item.asset.entries[0]
    name = _safe_upload_name(_basename(entry.arcname))
    if not name.lower().endswith(".vpx"):
        raise ValueError(t("error.uploads.table_kind"))
    game_id = table_swap.game_id_of(base)
    if not game_id:
        raise ValueError(t("error.games.swap_not_contained"))
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / name
        source.extract_member(entry.path, staged)
        swapped = table_swap.replace(game_id, item.table_id, str(staged), carry=item.carry,
                                     instead=item.instead)
    return {**swapped, "game": game_id}


def _add_table_from_file(source: AssetSource, asset: DetectedAsset, base: Path,
                         dest: Path) -> str:
    """Import a table beside the game's others. Answers with its new id."""
    if dest.exists():
        raise ValueError(t("error.games.file_exists"))
    _extract_replace(source, asset.entries[0], dest)
    table_id = new_id()
    record_arrived_table(base, dest, table_id)
    return table_id


def _build_rom_zip(source: AssetSource, asset: DetectedAsset, dest: Path) -> None:
    ensure_dir(dest.parent)
    with (staged_for(dest) as staged,
          zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive):
        for entry in asset.entries:
            if entry.is_dir:
                continue
            with tempfile.NamedTemporaryFile(delete=False) as handle:
                scratch = Path(handle.name)
            try:
                source.extract_member(entry.path, scratch)
                archive.write(scratch, arcname=_basename(entry.arcname))
            finally:
                scratch.unlink(missing_ok=True)


def _extract_tree(source: AssetSource, asset: DetectedAsset, base_dir: Path) -> None:
    for entry in asset.entries:
        if entry.is_dir:
            continue
        rel = _rel_under_root(entry.arcname, asset.root)
        dest = _safe_dest(base_dir, rel)
        source.extract_member(entry.path, dest)


def _import_media(source: AssetSource, item: PlannedItem, game_dir: Path,
                  converted: Mapping[str, Path]) -> None:
    entry = item.asset.entries[0]
    convert = item.asset.convert
    suffix = convert.suffix if convert else PurePosixPath(entry.arcname).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        scratch = Path(handle.name)
    try:
        if entry.arcname in converted:
            shutil.copyfile(converted[entry.arcname], scratch)
        else:
            source.extract_member(entry.path, scratch)
        written = media_placement.place(game_dir, item.asset.media_kind,
                                        item.stem or game_dir.name, scratch, item.page,
                                        unsupported=bool(convert and convert.as_is))
        media_placement.record_origin(game_dir, written)
    finally:
        scratch.unlink(missing_ok=True)


def _record_patched_table(game_dir: Path, vpx: Path, base_file: str, base_hash: str) -> str:
    """Write the new table into the game's .info: what it says about itself, and where
    it came from. Best effort - the table is on disk and playable either way. Answers with
    the table's id, or "" where the .info could not be written.
    """
    try:
        meta = MetaConfig(str(game_dir / f"{game_dir.name}.info"))
        parsed = VPXParser().single_file_extract(str(vpx))
        if parsed:
            # A failed parse leaves the entry unparsed rather than filled with empties.
            meta.refresh_table(vpx.name, parsed)
        meta.record_patch_source(vpx.name, base_file, base_hash, "jojodiff")
        return entry_for_filename(table_entries(meta.data), vpx.name)[0]
    except Exception:
        logger.warning("Patched %s, but could not record it in the .info", vpx.name,
                       exc_info=True)
        return ""


def _patch_base(base: Path, dest: Path) -> Path | None:
    """The table in this folder a patch applies to. A .dif names nothing about its base,
    so the folder's own record decides, and size only where it says nothing.

    A table made here by an earlier patch is a result, never a base. Of the rest, one that
    an earlier result was made from comes first, if the file is still the one it was
    hashed as. Then the largest, since a folder can hold small helper .vpx files.
    """
    try:
        entries = table_entries(MetaConfig(str(base / f"{base.name}.info")).data)
    except Exception:  # noqa: BLE001 - an unreadable record leaves size to decide
        entries = {}
    made = {entry_filename(one): one["source"] for one in entries.values()
            if isinstance(one.get("source"), dict) and one["source"].get("patch")}
    used = {str(one["base"].get("file")): one["base"].get("hash")
            for one in made.values() if isinstance(one.get("base"), dict)}
    candidates = [one for one in base.glob("*.vpx") if one != dest and one.name not in made]
    candidates.sort(key=lambda one: one.stat().st_size, reverse=True)
    earlier = [one for one in candidates if one.name in used and _sha256(one) == used[one.name]]
    return next(iter(earlier or candidates), None)


def _apply_patch(source: AssetSource, asset: DetectedAsset, base: Path,
                 dest: Path) -> str:
    """Apply a .dif to the table already in this folder, writing a new .vpx beside it.
    Answers with the new table's id, or "" where it could not be recorded.

    Getting the base wrong produces a corrupt result rather than an error, so the base is
    recorded by name and hash - in the log for whoever has to work out what happened, and
    in the .info because the result cannot be rebuilt without it.
    """
    from common.uploads.jdiff_patch import PatchError, apply_patch

    original = _patch_base(base, dest)
    if original is None:
        raise ValueError(t("error.uploads.no_table"))

    with tempfile.NamedTemporaryFile(delete=False, suffix=".dif") as tmp:
        patch_tmp = Path(tmp.name)
    try:
        _extract_replace(source, asset.entries[0], patch_tmp)
        try:
            with open(original, "rb") as src, open(patch_tmp, "rb") as pat, \
                    open(dest, "wb") as out:
                apply_patch(src, pat, out)
        except PatchError as exc:
            dest.unlink(missing_ok=True)
            logger.warning("Patch does not apply to %s: %s", original.name, exc)
            raise ValueError(t("error.uploads.patch_mismatch",
                               table=original.name)) from exc
        base_hash = _sha256(original)
        logger.info("Patched %s -> %s (base sha256 %s)", original.name, dest.name,
                    base_hash[:16])
        return _record_patched_table(base, dest, original.name, base_hash)
    finally:
        patch_tmp.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def execute_import_plan(plan: ImportPlan, source_path: Path,
                        *, progress_cb: Callable[[str], None] | None = None,
                        declared: dict | None = None,
                        converted: Mapping[str, Path] | None = None) -> dict:
    """Execute an import plan, streaming each asset from source_path to its destination.

    `converted` is the files already made for the assets that are converted, keyed by the
    name the file has in the drop; each is placed from there instead of from the drop.

    `declared` is what the sender said each uploaded file is, keyed by the name it arrived
    under. Recorded after the files are on disk, so a failed import writes no claim.
    """
    base = Path(plan.game_dir)
    if plan.new_game_dir_name:
        if base.exists():
            raise ValueError(t("error.uploads.folder_exists", name=base.name))
        base.mkdir(parents=True)
    else:
        library_folders.writable_game_folder(base)

    first_table = bool(plan.new_game_dir_name) or not has_table(base)
    source = open_source(Path(source_path))
    imported: list[str] = []
    media_kinds: list[str] = []
    added: list[str] = []
    patched: list[str] = []
    replaced: list[dict] = []
    renamed: list[dict[str, str]] = []
    try:
        for item in sorted(plan.items, key=lambda one: one.action != "replace_vpx"):
            if progress_cb:
                progress_cb(spec_for(item.asset.kind).label)
            dest = Path(item.destination)
            if item.action == "replace_vpx":
                replaced.append(_swap_table(source, item, base))
            elif item.action == "add_table":
                added.append(_add_table_from_file(source, item.asset, base, dest))
            elif item.action == "replace_b2s":
                _extract_replace(source, item.asset.entries[0], dest)
            elif item.action == "copy":
                _extract_replace(source, item.asset.entries[0], dest)
                was = _basename(item.asset.entries[0].arcname)
                if item.asset.kind in _COLOR_KINDS and was != dest.name:
                    renamed.append({"was": was, "now": dest.name})
            elif item.action == "apply_patch":
                if made := _apply_patch(source, item.asset, base, dest):
                    patched.append(made)
            elif item.action == "zip_rom":
                _build_rom_zip(source, item.asset, dest)
            elif item.action == "extract_tree":
                _extract_tree(source, item.asset, dest)
            elif item.action == "write_info":
                _import_game_info(source, item.asset, base)
            elif item.action == "replace_media":
                _import_media(source, item, base, converted or {})
                media_kinds.append(item.asset.media_kind)
            else:
                raise RuntimeError(f"Unknown import action: {item.action}")
            imported.append(item.asset.kind)
    except Exception as exc:
        failure = ValueError(rar_tool_hint()) if _is_rar_exec_error(exc) else exc
        if replaced and isinstance(failure, (ValueError, FileNotFoundError)):
            with contextlib.suppress(Exception):
                refresh_game(base)
            raise SwapKeptError(why(failure), replaced) from exc
        if failure is not exc:
            raise failure from exc
        raise
    finally:
        source.close()

    declared_written = record_declared_identities(plan, base, declared)
    refresh_game(base)
    if first_table and any(item.action in ("replace_vpx", "add_table") for item in plan.items):
        _hand_pov_to_table(base)

    return {
        "imported": imported,
        "skipped": [b.asset.kind for b in plan.blocked],
        "game_dir": str(base),
        "new_game": bool(plan.new_game_dir_name),
        "media_kinds": media_kinds,
        "declared": declared_written,
        "added_tables": added,
        "patched_tables": patched,
        "replaced_tables": replaced,
        "renamed": renamed,
    }


def _hand_pov_to_table(game_dir: Path) -> None:
    """A POV that arrived before any table is the folder's, which no table reads. Once the
    game has its one table it goes there. Never fails the import."""
    if not (game_dir / f"{game_dir.name}.pov").is_file():
        return
    try:
        game_id = next((found for found, game in game_repository.catalog().items()
                        if Path(str(game.full_path_game)).resolve() == game_dir.resolve()), "")
        if not game_id:
            return
        tables = file_move.state(game_id, file_move.ASSET, "pov")["tables"]
        if len(tables) == 1 and not tables[0]["folder_named"] and not tables[0]["own"]:
            file_move.to_tables(game_id, file_move.ASSET, "pov", [tables[0]["id"]])
    except Exception:  # noqa: BLE001 - the POV stays on the folder and Move to a Table takes it
        logger.warning("import: could not hand the POV to the new table in %s", game_dir,
                       exc_info=True)


def record_declared_identities(
        plan: ImportPlan, base: Path,
        declared: Mapping[str, DeclaredIdentity | None] | None) -> list[str]:
    """Write what the sender said each file is into the game's `.info`.

    Keyed by the name the file arrived under, because that is the only name the sender
    knows - it cannot predict where the plan will put it. Anything it names that the plan
    did not import is ignored rather than rejected: a bundle may legitimately carry more
    than the user selected.

    Returns the destination paths actually recorded, so the caller can say what it did.
    """
    if not declared:
        return []

    by_name: dict[str, PlannedItem] = {}
    for item in plan.items:
        for entry in item.asset.entries:
            by_name.setdefault(_basename(entry.path), item)

    written = []
    meta = None
    for name, identity in declared.items():
        named = by_name.get(_basename(str(name)))
        if named is None or identity is None or identity.is_empty:
            continue
        if meta is None:
            # The .info is named for its folder, and MetaConfig wants the file.
            meta = MetaConfig(str(base / f"{base.name}.info"))
        meta.add_asset(named.destination, identity.host or "declared",
                       identity=identity)
        written.append(named.destination)
    return written

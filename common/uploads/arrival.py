"""What a drop is made of, file by file, before anything is planned.

Every file of an archive or folder lands in exactly one of four places: a claim (what it is
and where it goes), a group (several files for one place, of which one is picked), an
unclaimed file (offered the places it could go) or the left-out list (with the reason).
`read` makes that account, `apply` takes the sender's assignments into it and `plan`
turns it into an import plan. Nothing here writes.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from common.games import media_placement, media_probe
from common.games.asset_registry import (
    ASSET_SPECS,
    DOC_EXTENSIONS,
    IMAGE_EXTENSIONS,
    AssetSpec,
    match_media_kind,
    spec_for,
    specs_named,
)
from common.games.game_names import names_game
from common.games.game_service import _find_vpx_file
from common.i18n import t
from common.media.media_specs import (
    CONVERTIBLE_AUDIO,
    CONVERTIBLE_IMAGE,
    CONVERTIBLE_VIDEO,
    MEDIA_SPECS,
    PAGED_KINDS,
    MediaSpec,
    canonical_kind,
    convertible_family,
    media_family,
    unsupported_named,
)
from common.uploads import picture_shape, working_folder
from common.uploads.asset_analyzer_service import (
    AnalysisResult,
    AssetSource,
    DetectedAsset,
    SourceEntry,
    analyze_members,
    listed_files,
    open_listed,
    source_kind_of,
)
from common.uploads.asset_import_service import (
    SIDECAR_KINDS,
    ImportPlan,
    PlannedItem,
    build_import_plan,
    held_tables,
    media_stem,
    table_stem,
    tables_to_choose,
)
from common.uploads.conversion import Conversion
from common.uploads.picture_shape import Shape

logger = logging.getLogger("vpinfe.common.uploads.arrival")

# Kinds that take everything the arrival holds, because each is a folder (or a whole
# archive) rather than a file.
_WHOLE = frozenset({"altsound", "pup_pack", "music", "rom"})
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".html", ".rtf"})
_README_SUFFIXES = DOC_EXTENSIONS | {".nfo"}
_LONE_SIDECARS = {extension: kind for kind in ("pov", "scv", "nix")
                  for extension in spec_for(kind).extensions}
# Measuring a picture means extracting it from its archive first.
MEASURE_LIMIT = 64 * 1024 * 1024

DECLARED = "declared"
NAMED = "named"
# A file nothing has a place for: kept in the game folder as it is, and ticked by the sender.
OTHER = "other"

Converting = Callable[[DetectedAsset], Conversion | None]


@dataclass(frozen=True)
class Claim:
    members: tuple[str, ...]
    kind: str
    asset: DetectedAsset
    media_kind: str = ""
    basis: str = NAMED
    # The page a paged media kind is written as, and whether that page is there already.
    page: int = 0
    replaces: bool = False
    # An extra is offered beside what the target asked for and left unticked.
    extra: bool = False
    table_id: str = ""


@dataclass(frozen=True)
class Candidate:
    member: str
    name: str
    size: int
    basis: str
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class Group:
    key: str
    kind: str
    media_kind: str
    label: str
    candidates: tuple[Candidate, ...]
    preselected: str = ""
    extra: bool = False
    table_id: str = ""


@dataclass(frozen=True)
class Choice:
    kind: str
    media_kind: str
    label: str
    table_id: str = ""
    suggested: bool = False


@dataclass(frozen=True)
class Unclaimed:
    member: str
    name: str
    size: int
    choices: tuple[Choice, ...]


@dataclass(frozen=True)
class LeftOut:
    member: str
    name: str
    reason_key: str
    reason: str


@dataclass(frozen=True)
class Target:
    game_dir: Path | None = None
    media_kind: str = ""
    asset_kind: str = ""
    rom_name: str = ""
    table_id: str = ""
    scope: str = ""
    page: int = 1


@dataclass(frozen=True)
class Arrival:
    claims: tuple[Claim, ...] = ()
    groups: tuple[Group, ...] = ()
    unclaimed: tuple[Unclaimed, ...] = ()
    left_out: tuple[LeftOut, ...] = ()
    # The game's tables, where a file named for a table was named for none and went to
    # the default; a client asks which one it is for.
    tables: tuple[dict, ...] = ()
    refusal: str = ""
    notes: tuple[str, ...] = ()
    source_path: Path = Path()
    source_kind: str = ""
    source_name: str = ""
    has_game: bool = False
    bundle_info: dict | None = None
    target: Target = Target()
    # Every claim before grouping, extras included: what `claims` and `groups` are made from.
    pool: tuple[Claim, ...] = ()
    entries: Mapping[str, SourceEntry] = field(default_factory=dict)
    sizes: dict[str, tuple[int, int]] = field(default_factory=dict)


class ChooseOneError(Exception):
    """A group has no answer yet: `groups` are the ones that need one."""

    def __init__(self, groups: Sequence[Group]) -> None:
        super().__init__(t("error.uploads.choose_one"))
        self.groups = tuple(groups)


_Read = tuple[list[Claim], list[Unclaimed], list[LeftOut], str, bool, Sequence[str], dict | None]


def _name(member: str) -> str:
    return PurePosixPath(member).name


def _suffix(member: str) -> str:
    return PurePosixPath(member).suffix.lower()


def _asset_specs(kind: str) -> tuple[AssetSpec, ...]:
    try:
        return tuple(specs_named(kind))
    except KeyError as exc:
        raise ValueError(t("error.assets.unknown_kind")) from exc


def _media_spec(kind: str) -> MediaSpec:
    spec = next((one for one in MEDIA_SPECS if one.kind == canonical_kind(kind)), None)
    if spec is None:
        raise ValueError(t("error.uploads.unknown_slot", media_kind=kind))
    return spec


def _slot_suffixes(spec: MediaSpec) -> tuple[str, ...]:
    """What a slot takes: its own family, and what can be converted into it."""
    return (*spec.family, *convertible_family(spec.family))


def _family_phrase(kind: str) -> str:
    return t(f"arrival.family.{media_family(kind)}")


def _left(entry: SourceEntry, key: str, **fields: str) -> LeftOut:
    return LeftOut(entry.arcname, _name(entry.arcname), key, t(key, **fields))


# --- Claims ----------------------------------------------------------------

def _media_asset(entry: SourceEntry, media_kind: str) -> DetectedAsset:
    return DetectedAsset("media", spec_for("media").label, (entry,), media_kind=media_kind,
                         size=entry.size, detail=_name(entry.arcname))


def _claim(asset: DetectedAsset, basis: str = NAMED, extra: bool = False) -> Claim:
    return Claim(tuple(one.arcname for one in asset.entries if not one.is_dir), asset.kind,
                 asset, asset.media_kind, basis, extra=extra)


def _classify(files: Sequence[SourceEntry], source: AssetSource, *, extra: bool
              ) -> tuple[list[Claim], list[str], bool, tuple[str, ...], dict | None]:
    """What the rules find in these files: claims, the paths nothing claimed, whether a
    table is among them, the analyzer's notes and the bundle's `.info`."""
    assets, notes, has_game, unrecognized, bundle = analyze_members(source, list(files))
    return ([_claim(asset, extra=extra) for asset in assets], list(unrecognized),
            has_game, notes, bundle)


def _image_slots(target: Target, family: str, shape: Shape | None = None) -> list[Choice]:
    """Every slot a picture (or video) could be: the ones the game has nothing in first, and
    among those the ones its shape suits. A slot the game already has is never suggested."""
    stem = _stem_for(target, "")
    suits = picture_shape.fits(shape) if shape else ()
    wanted = picture_shape.suggestion(shape) if shape else ""
    out: list[tuple[tuple[bool, int], Choice]] = []
    for spec in MEDIA_SPECS:
        if not spec.token or media_family(spec.kind) != family:
            continue
        held = bool(target.game_dir and stem
                    and media_placement.page_count(target.game_dir, spec.kind, stem))
        rank = suits.index(spec.kind) if spec.kind in suits else len(suits)
        out.append(((held, rank), Choice("media", spec.kind, spec.label,
                                         suggested=spec.kind == wanted and not held)))
    return [choice for _order, choice in sorted(out, key=lambda one: one[0])]


# Shapes already read, by archive, member and size: every plan of the same drop asks again.
_SHAPES: dict[tuple[str, int, str, int], Shape | None] = {}
_SHAPES_KEPT = 512


def _shape_of(source: AssetSource | None, entry: SourceEntry) -> Shape | None:
    """The shape of a picture, read from a scratch copy that goes when the read is done."""
    if source is None or entry.size > MEASURE_LIMIT:
        return None
    archive = source.archive_path()
    key = None
    if archive is not None:
        try:
            key = (str(archive), archive.stat().st_mtime_ns, entry.path, entry.size)
        except OSError:
            key = None
    if key is not None and key in _SHAPES:
        return _SHAPES[key]
    with working_folder.scratch() as scratch:
        local = Path(scratch) / f"picture{_suffix(entry.arcname)}"
        try:
            source.extract_member(entry.path, local)
        except Exception:
            logger.debug("Could not read the shape of %s", entry.arcname, exc_info=True)
            return None
        shape = picture_shape.read(local)
    if key is not None:
        if len(_SHAPES) >= _SHAPES_KEPT:
            _SHAPES.clear()
        _SHAPES[key] = shape
    return shape


def _script_choices(target: Target) -> list[Choice]:
    if target.game_dir is None:
        return []
    held = held_tables(target.game_dir)
    if held:
        return [Choice("script", "", t("arrival.choice.script_for",
                                       table=Path(one["filename"]).stem), one["id"])
                for one in held]
    try:
        own = _find_vpx_file(target.game_dir)
    except (FileNotFoundError, OSError):
        return []
    return [Choice("script", "", t("arrival.choice.script_for", table=own.stem))]


def _unclaimed(entry: SourceEntry, target: Target,
               source: AssetSource | None = None) -> Unclaimed | None:
    suffix = _suffix(entry.arcname)
    if suffix in IMAGE_EXTENSIONS or suffix in CONVERTIBLE_IMAGE:
        choices = _image_slots(target, "image", _shape_of(source, entry))
    elif suffix in CONVERTIBLE_AUDIO:
        choices = _image_slots(target, "audio")
    elif suffix in CONVERTIBLE_VIDEO:
        choices = _image_slots(target, "video")
    elif suffix in spec_for("media").extensions and suffix not in IMAGE_EXTENSIONS:
        choices = _image_slots(target, "video") if media_family_of(suffix) == "video" else []
    elif suffix in spec_for("script").extensions:
        choices = _script_choices(target)
    else:
        return None
    if not choices and suffix not in IMAGE_EXTENSIONS and suffix not in CONVERTIBLE_IMAGE:
        return None
    choices.append(Choice("", "", t("arrival.choice.skip")))
    return Unclaimed(entry.arcname, _name(entry.arcname), entry.size, tuple(choices))


def _suggested_once(unclaimed: list[Unclaimed], claims: Sequence[Claim]) -> list[Unclaimed]:
    """A slot is suggested only when no file in the drop is already claimed for it and exactly
    one picture suits it."""
    taken = {one.media_kind for one in claims if one.media_kind}
    wanting = Counter(choice.media_kind for one in unclaimed for choice in one.choices
                      if choice.suggested)

    def doubtful(choice: Choice) -> bool:
        return choice.suggested and (choice.media_kind in taken or wanting[choice.media_kind] > 1)

    return [replace(one, choices=tuple(replace(choice, suggested=False) if doubtful(choice)
                                       else choice for choice in one.choices))
            for one in unclaimed]


def offers_places(name: str) -> bool:
    """Whether a file with no name the rules know is still offered places to go: a picture,
    sound or video."""
    suffix = _suffix(name)
    return (suffix in IMAGE_EXTENSIONS or suffix in CONVERTIBLE_IMAGE
            or suffix in CONVERTIBLE_AUDIO or suffix in CONVERTIBLE_VIDEO
            or bool(media_family_of(suffix)))


def media_family_of(suffix: str) -> str:
    for spec in MEDIA_SPECS:
        if suffix in spec.family:
            return media_family(spec.kind)
    return ""


def _no_place(entry: SourceEntry) -> LeftOut:
    key = ("arrival.left_out.text_file" if _suffix(entry.arcname) in _TEXT_SUFFIXES
           else "arrival.left_out.no_place")
    return _left(entry, key)


def _other_file(entry: SourceEntry) -> Claim:
    """A file with no place: written to the game folder as it is, when the sender ticks it."""
    asset = DetectedAsset("readme", t("arrival.other.label"), (entry,), size=entry.size,
                          detail=_name(entry.arcname))
    return Claim((entry.arcname,), "readme", asset, basis=OTHER, extra=True)


def _lone_sidecar(entry: SourceEntry) -> Claim | None:
    kind = _LONE_SIDECARS.get(_suffix(entry.arcname), "")
    if not kind:
        return None
    return _claim(DetectedAsset(kind, spec_for(kind).label, (entry,), size=entry.size,
                                detail=_name(entry.arcname)))


# --- Reading ---------------------------------------------------------------

def read(source_path: str | Path, *, game_dir: str | Path | None = None, media_kind: str = "",
         asset_kind: str = "", rom_name: str = "", table_id: str = "", scope: str = "",
         page: int = 1) -> Arrival:
    """Read a file, archive or folder into claims, groups, unclaimed files and left-out files.

    `media_kind` or `asset_kind` is the slot the drop named; neither is the general case.
    A ValueError names a slot, kind or table that does not exist. A drop that cannot be
    read, or that holds nothing the slot takes, is an `Arrival` with a `refusal`.
    """
    path = Path(source_path)
    target = Target(Path(game_dir) if game_dir else None, media_kind, asset_kind,
                    rom_name, table_id, scope, max(1, int(page or 1)))
    if media_kind:
        _media_spec(media_kind)
    elif asset_kind:
        _asset_specs(asset_kind)
    source, listing, error = open_listed(path)
    if source is None:
        return Arrival(refusal=error, source_path=path, source_kind=source_kind_of(path),
                       source_name=path.name, target=target)
    try:
        files = listed_files(listing)
        entries = {one.arcname: one for one in files}
        if media_kind:
            built = _read_media_slot(files, source, target)
        elif asset_kind:
            built = _read_asset_kind(files, source, target)
        else:
            built = _read_general(files, source, target)
        pool, unclaimed, left_out, refusal, has_game, notes, bundle = built
    finally:
        source.close()
    base = Arrival(
        unclaimed=tuple(unclaimed), left_out=tuple(left_out), refusal=refusal,
        notes=tuple(notes), source_path=path, source_kind=source.kind,
        source_name=source.name, has_game=has_game, bundle_info=bundle, target=target,
        pool=tuple(pool), entries=entries)
    return _settle(base)


def _read_general(files: list[SourceEntry], source: AssetSource, target: Target) -> _Read:
    claims, leftovers, has_game, notes, bundle = _classify(files, source, extra=False)
    by_path = {one.arcname: one for one in files}
    unclaimed: list[Unclaimed] = []
    left_out: list[LeftOut] = []
    for member in leftovers:
        entry = by_path[member]
        if lone := _lone_sidecar(entry):
            claims.append(lone)
        elif named := unsupported_named(_name(member)):
            claims.append(_claim(_media_asset(entry, named.kind)))
        elif offered := _unclaimed(entry, target, source):
            unclaimed.append(offered)
        else:
            claims.append(_other_file(entry))
    unclaimed = _suggested_once(unclaimed, claims)
    recognized = any(one.basis != OTHER for one in claims)
    refusal = ("" if recognized or unclaimed or target.game_dir is not None and claims
               else t("error.uploads.nothing_recognized"))
    return claims, unclaimed, left_out, refusal, has_game, notes, bundle


def _declared_claim(asset: DetectedAsset) -> Claim:
    return _claim(asset, DECLARED)


def _extras_and_leftovers(rest: list[SourceEntry], source: AssetSource,
                          doesnt_fit: Mapping[str, str]) -> tuple[
        list[Claim], list[LeftOut], bool, tuple[str, ...], dict | None]:
    """What a declared slot did not take: the analyzer's finds, unticked, and the rest said
    to be left out."""
    claims, leftovers, has_game, notes, bundle = _classify(rest, source, extra=True)
    by_path = {one.arcname: one for one in rest}
    left_out = [(_no_place(by_path[member]) if _suffix(member) in _TEXT_SUFFIXES
                 else _left(by_path[member], "arrival.left_out.doesnt_fit", **doesnt_fit))
                for member in leftovers]
    return claims, left_out, has_game, notes, bundle


def _read_media_slot(files: list[SourceEntry], source: AssetSource, target: Target) -> _Read:
    spec = _media_spec(target.media_kind)
    takes = _slot_suffixes(spec)
    fitting = [one for one in files if _suffix(one.arcname) in takes]
    rest = [one for one in files if _suffix(one.arcname) not in takes]

    def contradicts(entry: SourceEntry) -> str:
        named = match_media_kind(_name(entry.arcname)) or ""
        return named if named and canonical_kind(named) != spec.kind else ""

    agreeing = [one for one in fitting if not contradicts(one)]
    # Every file naming another slot is the sender's to place: the slot they dropped on wins.
    kept = agreeing or fitting
    claims = [_declared_claim(_media_asset(one, spec.kind)) for one in kept]
    demoted = [replace(_claim(_media_asset(one, contradicts(one)), NAMED), extra=True)
               for one in fitting if one not in kept]
    phrase = _family_phrase(spec.kind)
    extras, left_out, has_game, notes, bundle = _extras_and_leftovers(
        rest, source, {"slot": spec.label, "family": phrase})
    refusal = "" if claims else t("arrival.refusal.nothing_fits", slot=spec.label, what=phrase)
    return [*claims, *demoted, *extras], [], left_out, refusal, has_game, notes, bundle


def _whole_arrival(files: list[SourceEntry], kind: str, target: Target
                   ) -> tuple[list[Claim], list[LeftOut]]:
    spec = spec_for(kind)
    if spec.requires_rom and not target.rom_name:
        return [], [_left(one, "error.uploads.no_rom") for one in files]
    if not files:
        return [], []
    tops = {PurePosixPath(one.arcname).parts[0] for one in files
            if len(PurePosixPath(one.arcname).parts) > 1}
    wrapped = len(tops) == 1 and all(len(PurePosixPath(one.arcname).parts) > 1 for one in files)
    detail = (t("asset.analysis.whole_archive") if kind == "rom"
              else t("asset.analysis.files", count=len(files)))
    asset = DetectedAsset(kind, spec.label, tuple(files), root=next(iter(tops)) if wrapped else "",
                          size=sum(one.size for one in files), detail=detail)
    return [_declared_claim(asset)], []


def _kind_fits(spec: AssetSpec, suffix: str, lone: bool = False) -> bool:
    """Whether a file with this suffix is one the kind takes. A readme dropped as one file
    is whatever the sender says it is; among the files of an archive or folder it is text."""
    if spec.kind == "readme":
        return lone or suffix in _README_SUFFIXES
    return suffix in spec.extensions


def _read_asset_kind(files: list[SourceEntry], source: AssetSource, target: Target) -> _Read:
    specs = _asset_specs(target.asset_kind)
    if len(specs) == 1 and specs[0].kind in _WHOLE:
        whole, unplaced = _whole_arrival(files, specs[0].kind, target)
        refusal = "" if whole or unplaced else t("error.uploads.nothing_importable")
        return whole, [], unplaced, refusal, False, [], None
    lone = source.kind == "file"

    taken: dict[str, list[SourceEntry]] = {}
    rest: list[SourceEntry] = []
    for entry in files:
        suffix = _suffix(entry.arcname)
        spec = next((one for one in specs if _kind_fits(one, suffix, lone)), None)
        if spec is None:
            rest.append(entry)
        else:
            taken.setdefault(spec.kind, []).append(entry)
    claims: list[Claim] = []
    for kind, entries in taken.items():
        label = spec_for(kind).label
        if kind == "altcolor_vni":
            folders: dict[str, list[SourceEntry]] = {}
            for entry in entries:
                folders.setdefault(str(PurePosixPath(entry.arcname).parent), []).append(entry)
            claims += [_declared_claim(DetectedAsset(
                kind, label, tuple(group), size=sum(one.size for one in group),
                detail=", ".join(_name(one.arcname) for one in group)))
                for group in folders.values()]
        else:
            claims += [_declared_claim(DetectedAsset(
                kind, label, (one,), size=one.size, detail=_name(one.arcname)))
                for one in entries]
    slot = t(f"asset.kind.{specs[0].kind if len(specs) == 1 else target.asset_kind}.label")
    extensions = [extension for spec in specs for extension in spec.extensions]
    what = (", ".join(extensions) if extensions else t("arrival.family.doc"))
    extras, left_out, has_game, notes, bundle = _extras_and_leftovers(
        rest, source, {"slot": slot, "family": what})
    refusal = "" if claims else t("arrival.refusal.nothing_fits", slot=slot, what=what)
    return [*claims, *extras], [], left_out, refusal, has_game, notes, bundle


# --- Settling: pages and groups -------------------------------------------

def _stem_for(target: Target, table_id: str) -> str:
    if target.game_dir is None:
        return ""
    return media_stem(target.game_dir, table_id or target.table_id, target.scope)


def _natural(member: str) -> list:
    stem = PurePosixPath(member).stem.lower()
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", stem)]


def _pages(claims: list[Claim], target: Target) -> list[Claim]:
    """Number the pages of each paged kind, in the natural order of the names. The first
    is the page the target named, or the one after the last the game has."""
    buckets: dict[tuple, list[Claim]] = {}
    out: list[Claim] = []
    for claim in claims:
        if claim.kind == "media" and claim.media_kind in PAGED_KINDS:
            buckets.setdefault((claim.media_kind, claim.table_id, claim.extra), []).append(claim)
        else:
            out.append(claim)
    for (media_kind, table_id, _extra), bucket in buckets.items():
        stem = _stem_for(target, table_id)
        held = (media_placement.page_count(target.game_dir, media_kind, stem)
                if target.game_dir and stem else 0)
        taken = {one.page for one in bucket if one.page}
        number = min(target.page, held + 1)
        for claim in sorted(bucket, key=lambda one: _natural(one.members[0])):
            if not claim.page:
                while number in taken:
                    number += 1
                claim = replace(claim, page=number)
                taken.add(number)
            out.append(replace(claim, replaces=claim.page <= held))
    return out


def _sidecar_table(claim: Claim, arrival: Arrival) -> str:
    """Which table a per-table file is for, as far as the drop says."""
    target = arrival.target
    if claim.table_id or target.table_id:
        return claim.table_id or target.table_id
    if any(one.kind == "table" and not one.extra for one in arrival.pool):
        return "arriving"
    if target.game_dir is not None:
        own = PurePosixPath(claim.members[0]).stem.lower()
        for held in held_tables(target.game_dir):
            if Path(held["filename"]).stem.lower() == own:
                return own
    return ""


def _names_a_table(claim: Claim, arrival: Arrival) -> bool:
    """Whether the claim's table makes its media go anywhere the game's own would not."""
    if not claim.table_id:
        return False
    return _stem_for(arrival.target, claim.table_id) != _stem_for(arrival.target, "")


def _place(claim: Claim, arrival: Arrival) -> str:
    """The one place a claim writes, or "" where it writes nowhere that two could meet."""
    at = f"@{claim.table_id}" if _names_a_table(claim, arrival) else ""
    if claim.kind == "media":
        return "" if claim.media_kind in PAGED_KINDS else f"media:{claim.media_kind}{at}"
    if claim.kind in SIDECAR_KINDS:
        table = _sidecar_table(claim, arrival)
        return f"{claim.kind}@{table}" if table else claim.kind
    if claim.kind == "readme" and claim.basis == DECLARED:
        return "readme"
    return ""


def _preselected(group_claims: list[Claim], arrival: Arrival) -> str:
    game_dir = arrival.target.game_dir
    first = group_claims[0]
    if first.kind != "media" or game_dir is None:
        return ""
    fitting = [one.members[0] for one in group_claims
               if (match_media_kind(_name(one.members[0])) or "") == first.media_kind
               and names_game(PurePosixPath(one.members[0]).stem, game_dir.name)]
    return fitting[0] if len(fitting) == 1 else ""


def _measured(arrival: Arrival, members: list[str]) -> None:
    """Fill in the pixel size of these pictures, reading each only once."""
    wanted = [one for one in members if one not in arrival.sizes]
    if not wanted:
        return
    source, _listing, _error = open_listed(arrival.source_path)
    with working_folder.scratch() as scratch:
        try:
            for member in wanted:
                entry = arrival.entries[member]
                size = (0, 0)
                if _suffix(member) in IMAGE_EXTENSIONS and entry.size <= MEASURE_LIMIT \
                        and source is not None:
                    local = Path(scratch) / f"picture{_suffix(member)}"
                    try:
                        source.extract_member(entry.path, local)
                        facts = media_probe.probe(local)
                        size = (int(facts.get("width") or 0), int(facts.get("height") or 0))
                    except Exception:
                        logger.debug("Could not measure %s", member, exc_info=True)
                    finally:
                        local.unlink(missing_ok=True)
                arrival.sizes[member] = size
        finally:
            if source is not None:
                source.close()


def _settle(arrival: Arrival) -> Arrival:
    """Number the pages, and split the pool into single claims and groups."""
    pool = _pages(list(arrival.pool), arrival.target)
    places: dict[tuple[str, bool], list[Claim]] = {}
    for claim in pool:
        if place := _place(claim, arrival):
            places.setdefault((place, claim.extra), []).append(claim)
    grouped = {id(one) for found in places.values() if len(found) > 1 for one in found}
    singles = [one for one in pool if id(one) not in grouped]
    groups: list[Group] = []
    for (place, extra), found in places.items():
        if len(found) < 2:
            continue
        _measured(arrival, [one.members[0] for one in found])
        first = found[0]
        label = (_media_spec(first.media_kind).label if first.kind == "media"
                 else spec_for(first.kind).label)
        key = f"{place}#extra" if extra and len(places.get((place, False), ())) > 1 else place
        groups.append(Group(
            key, first.kind, first.media_kind, label,
            tuple(Candidate(one.members[0], _name(one.members[0]),
                            arrival.entries[one.members[0]].size, one.basis,
                            *arrival.sizes[one.members[0]]) for one in found),
            "" if extra else _preselected(found, arrival), extra, first.table_id))
    tables = _tables_to_choose(arrival, pool)
    has_game = any(one.kind == "table" and not one.extra for one in pool)
    return replace(arrival, claims=tuple(singles), groups=tuple(groups), tables=tables,
                   has_game=has_game)


def _tables_to_choose(arrival: Arrival, pool: list[Claim]) -> tuple[dict, ...]:
    """The game's tables, when a per-table file was named for none of them, or when media
    is dropped on one table and can be named for it or for the game."""
    game_dir = arrival.target.game_dir
    if game_dir is None or any(one.kind == "table" and not one.extra for one in pool):
        return ()
    if arrival.target.table_id:
        named_media = any(one.kind == "media" and not one.extra for one in pool)
        return tables_to_choose(game_dir) if named_media else ()
    held = {Path(one["filename"]).stem.lower() for one in held_tables(game_dir)}
    unnamed = any(one.kind in SIDECAR_KINDS and not one.table_id
                  and PurePosixPath(one.members[0]).stem.lower() not in held for one in pool)
    return tables_to_choose(game_dir) if unnamed else ()


# --- Assignments -----------------------------------------------------------

def _member_fits(kind: str, media_kind: str, member: str) -> bool:
    suffix = _suffix(member)
    if kind == "media":
        try:
            return suffix in _slot_suffixes(_media_spec(media_kind))
        except ValueError:
            return False
    if kind in _WHOLE or kind not in {spec.kind for spec in ASSET_SPECS}:
        return False
    return _kind_fits(spec_for(kind), suffix)


def apply(result: Arrival, assignments: Sequence[Mapping]) -> Arrival:
    """The arrival with the sender's assignments taken in.

    Each is `{member, kind?, media_kind?, page?, table_id?, skip?}`. A member the arrival
    does not hold is refused, as is a kind its file cannot be, a table the game does not
    hold, and a member of a folder. An assigned file is claimed on basis `declared`.
    """
    known = ({m for c in result.pool for m in c.members}
             | {one.member for one in result.unclaimed}
             | {one.member for one in result.left_out})
    pool = list(result.pool)
    unclaimed = list(result.unclaimed)
    left_out = list(result.left_out)
    for item in assignments:
        member = str(item.get("member", ""))
        if member not in known:
            raise ValueError(t("error.uploads.unknown_member", member=member))
        held = next((c for c in pool if member in c.members), None)
        if held is not None and len(held.members) > 1:
            raise ValueError(t("error.uploads.whole_folder", member=member))
        pool = [c for c in pool if member not in c.members]
        unclaimed = [u for u in unclaimed if u.member != member]
        left_out = [one for one in left_out if one.member != member]
        entry = result.entries[member]
        if item.get("skip"):
            left_out.append(_left(entry, "arrival.left_out.skipped"))
            continue
        kind = str(item.get("kind") or (held.kind if held else ""))
        media_kind = str(item.get("media_kind") or (held.media_kind if held else ""))
        if not kind or not _member_fits(kind, media_kind, member):
            raise ValueError(t("error.uploads.wrong_kind", member=member))
        table_id = str(item.get("table_id") or (held.table_id if held else ""))
        if table_id and result.target.game_dir is not None:
            table_stem(result.target.game_dir, table_id)
        asset = (_media_asset(entry, canonical_kind(media_kind)) if kind == "media"
                 else DetectedAsset(kind, spec_for(kind).label, (entry,), size=entry.size,
                                    detail=_name(member)))
        pool.append(Claim((member,), kind, asset, asset.media_kind, DECLARED,
                          page=int(item.get("page") or 0), table_id=table_id))
    refusal = "" if any(not c.extra for c in pool) else result.refusal
    return _settle(replace(result, pool=tuple(pool), unclaimed=tuple(unclaimed),
                           left_out=tuple(left_out), refusal=refusal))


# --- Planning --------------------------------------------------------------

def plan(result: Arrival, *, game_dir: str | Path | None = None,
         picks: Mapping[str, str] | None = None, require_picks: bool = True,
         rom_name: str | None = None, table_id: str | None = None,
         scope: str | None = None, converting: Converting | None = None,
         **options: Any) -> ImportPlan:
    """The import plan for what the arrival holds and the picks made among its groups.

    `picks` is `{group key: member}`, `""` for none of them. A group with no pick raises
    `ChooseOneError` unless it is only extras, which write nothing until picked. A pick that is
    not a member of its group is a ValueError; one for a group the arrival does not have
    is ignored. `converting` says what each media file is written as; it is how a file
    the slot cannot read is planned under the extension it will have. `options` go to the
    planner as they are.
    """
    if result.refusal:
        raise ValueError(result.refusal)
    picks = dict(picks or {})
    by_member = {c.members[0]: c for c in result.pool if len(c.members) == 1}
    for group in result.groups:
        wanted = picks.get(group.key, "")
        if wanted and wanted not in {c.member for c in group.candidates}:
            raise ValueError(t("error.uploads.wrong_pick", member=wanted))
    open_groups = [g for g in result.groups if not g.extra and g.key not in picks]
    if require_picks and open_groups:
        raise ChooseOneError(open_groups)

    chosen = list(result.claims)
    for group in result.groups:
        if member := picks.get(group.key, ""):
            chosen.append(replace(by_member[member], extra=False))
    chosen.sort(key=lambda one: one.extra)

    target = result.target
    assets = tuple(replace(c.asset, page=c.page or c.asset.page,
                           table_id=c.table_id or c.asset.table_id,
                           convert=converting(c.asset) if converting and c.kind == "media"
                           else None) for c in chosen)
    analysis = AnalysisResult(result.source_kind, result.source_name, assets,
                              any(c.kind == "table" and not c.extra for c in chosen),
                              result.notes, bundle_info=result.bundle_info)
    game = Path(game_dir) if game_dir else target.game_dir
    built = build_import_plan(
        analysis, game_dir=game, rom_name=target.rom_name if rom_name is None else rom_name,
        table_id=target.table_id if table_id is None else table_id,
        scope=target.scope if scope is None else scope, **options)
    claim_of = {one.arcname: c for c in chosen for one in c.asset.entries if not one.is_dir}
    items = tuple(_attributed(item, claim_of[next(one.arcname for one in item.asset.entries
                                                  if not one.is_dir)])
                  for item in built.items)
    return replace(built, items=items)


def _attributed(item: PlannedItem, claim: Claim) -> PlannedItem:
    destination = item.destination
    if claim.kind == "readme" and claim.basis == DECLARED:
        destination = str(Path(destination).with_name(
            f"readme{_suffix(claim.members[0])}"))
    files = [one.arcname for one in item.asset.entries if not one.is_dir]
    member = files[0] if len(files) == 1 else claim.members[0]
    held_back = claim.extra or bool(item.asset.convert and item.asset.convert.blocked)
    return replace(item, member=member, basis=claim.basis,
                   destination=destination, extra=claim.extra,
                   default_enabled=item.default_enabled and not held_back)

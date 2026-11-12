"""Every kind of media a game can have, and how to find one on disk.

The kind names follow Visual Pinball's window names - `playfield`, `backglass`,
`scoreview` - because a window name *is* a media kind here, and VPX is where those names
come from. The files never moved: `bg.png` and `dmd.png` are what VPinMediaDB ships and
what everyone has on disk, and the contract 1 payload keys are frozen too. Kinds
renamed, files frozen.

`MEDIA_SPECS` is the declaration the rest of the tree reads: each kind's name, its
attribute on a game, the key that attribute is published under, the filename it
resolves to and the tokens it accepts. Naming a kind is what contract 2 hands a
theme; the path is ours and stays here.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from common.i18n import t

# Extension families, ordered: resolution tries them in order and the first hit
# wins. Aligned with what import accepts, so a file import writes is never
# invisible to the scan - which it was, when resolution demanded one exact name.
IMAGE_FAMILY = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".apng")


def register_mime_types() -> None:
    """Teach `mimetypes` the image types Python lacks, so a file served by extension is not
    sent as text/plain. NiceGUI calls `mimetypes.init()` when it is imported, which drops
    this, so whatever imports NiceGUI calls this again afterwards."""
    mimetypes.add_type("image/apng", ".apng")


register_mime_types()

VIDEO_FAMILY = (".mp4", ".webm")
AUDIO_FAMILY = (".mp3", ".ogg")

# What the converter tries per class: not read by VPinFE, so a file with one of these
# extensions is never a slot's file. A file that does not open is skipped when it is
# converted, so the lists are what is attempted, not a promise.
CONVERTIBLE_IMAGE = (".tga", ".tif", ".tiff", ".ico", ".psd", ".dds", ".pcx", ".ppm", ".avif",
                     ".jfif", ".jpe", ".jp2")
CONVERTIBLE_AUDIO = (".wav", ".flac", ".m4a", ".aac", ".wma", ".aif", ".aiff", ".opus", ".oga")
CONVERTIBLE_VIDEO = (".mkv", ".avi", ".mov", ".m4v", ".flv", ".f4v", ".wmv", ".mpg", ".mpeg",
                     ".ogv", ".ts", ".3gp")


def convertible_family(family: tuple[str, ...]) -> tuple[str, ...]:
    """The extensions a kind's family can be made from, or none where nothing converts."""
    return {IMAGE_FAMILY: CONVERTIBLE_IMAGE, VIDEO_FAMILY: CONVERTIBLE_VIDEO,
            AUDIO_FAMILY: CONVERTIBLE_AUDIO}.get(family, ())


DOC_FAMILY = (".pdf", ".md", ".txt", ".html")
# A rule sheet is read as a document and is often published as a picture of one.
RULE_SHEET_FAMILY = (*DOC_FAMILY, *IMAGE_FAMILY)


@dataclass(frozen=True)
class MediaSpec:
    kind: str
    # The attribute on a Game. Ours, so it is snake_case like everything else.
    attr: str
    # What the same value is called on a theme's row, in contract 2's spelling; contract
    # 1 gets its own through `_LEGACY_ROW_KEYS`. Frozen: a published theme reads these
    # off the row, so the two names are separate things that carry the same path.
    payload_key: str
    filename_template: str
    # Which VPinMediaDB resolution bucket this kind is published under - "1k" for the
    # backglass and scoreview, the configured playfield resolution for the playfield.
    #
    # Read by the automatic art fill. `vpsdb_media.py`'s downloader hardcodes the same
    # answers at its own call sites, so a change here has to be made there too.
    asset_group: str | None = None
    # The token for tiers 1 and 2 - "(Wheel) Name.png". Video kinds share their
    # image counterpart's token; the extension family tells them apart.
    token: str | None = None
    # Also accepted, tried after token. Where VPX's published name is opaque we
    # lead with the plain-English one and keep theirs readable.
    alt_tokens: tuple[str, ...] = ()
    family: tuple[str, ...] = IMAGE_FAMILY
    # Another kind whose resolved file serves when this kind has none - below
    # every tier of this kind, so any real file of its own outranks it.
    fallback_kind: str | None = None
    # Whether medias/<kind>s/<set>/ folders participate in resolution.
    supports_sets: bool = False
    # Whether the kind is several pictures, read in order: "(Flyer) Name.png" is page 1
    # and "(Flyer 2) Name.png" page 2. Pages follow page 1 and never take sets.
    pages: bool = False

    @property
    def label(self) -> str:
        """What a person calls this art. BG and DMD stay: it is what the art is called,
        whatever the kind is called."""
        return t(f"media.kind.{self.kind}.label")

    def filename(self, playfield_variant: str = "table") -> str:
        return self.filename_template.format(playfield_variant=playfield_variant)

    def stem(self, playfield_variant: str = "table") -> str:
        return self.filename(playfield_variant).rsplit(".", 1)[0]


MEDIA_SPECS = (
    MediaSpec(
        "backglass",
        attr="bg_image_path",
        payload_key="BGImagePath",
        filename_template="bg.png",
        asset_group="1k",
        token="(Backglass)",
    ),
    MediaSpec(
        "scoreview",
        attr="dmd_image_path",
        payload_key="DMDImagePath",
        filename_template="dmd.png",
        asset_group="1k",
        token="(DMD)",
    ),
    # One kind, two variants: the filename follows [Media] playfieldvariant, the kind
    # never does. playfield_fss is the FSS render on its own, and is what the
    # playfield falls back to when it has none.
    MediaSpec(
        "playfield",
        attr="playfield_image_path",
        payload_key="PlayfieldImagePath",
        filename_template="{playfield_variant}.png",
        asset_group="table_resolution",
        token="(Playfield)",
    ),
    MediaSpec(
        "playfield_fss",
        attr="fss_image_path",
        payload_key="FSSImagePath",
        filename_template="fss.png",
        asset_group="table_resolution",
        token="(FSS)",
    ),
    MediaSpec(
        "wheel",
        attr="wheel_image_path",
        payload_key="WheelImagePath",
        filename_template="wheel.png",
        token="(Wheel)",
        fallback_kind="logo",
        supports_sets=True,
    ),
    MediaSpec(
        "cab",
        attr="cab_image_path",
        payload_key="CabImagePath",
        filename_template="cab.png",
        token="(Cabinet)",
    ),
    MediaSpec(
        "real_dmd",
        attr="real_dmd_image_path",
        payload_key="realDMDImagePath",
        filename_template="realdmd.png",
        token="(RealDMD)",
    ),
    MediaSpec(
        "real_dmd_color",
        attr="real_dmd_color_image_path",
        payload_key="realDMDColorImagePath",
        filename_template="realdmd-color.png",
        token="(RealColorDMD)",
    ),
    MediaSpec(
        "real_dmd_video",
        attr="real_dmd_video_path",
        payload_key="realDMDVideoPath",
        filename_template="realdmd.mp4",
        token="(RealDMD)",
        family=VIDEO_FAMILY,
    ),
    MediaSpec(
        "real_dmd_color_video",
        attr="real_dmd_color_video_path",
        payload_key="realDMDColorVideoPath",
        filename_template="realdmd-color.mp4",
        token="(RealColorDMD)",
        family=VIDEO_FAMILY,
    ),
    MediaSpec(
        "flyer",
        attr="flyer_image_path",
        payload_key="FlyerImagePath",
        filename_template="flyer.png",
        token="(Flyer)",
        alt_tokens=("(GameInfo)",),
        pages=True,
    ),
    MediaSpec(
        "playfield_video",
        attr="playfield_video_path",
        payload_key="PlayfieldVideoPath",
        filename_template="{playfield_variant}.mp4",
        asset_group="table_video_resolution",
        token="(Playfield)",
        family=VIDEO_FAMILY,
    ),
    MediaSpec(
        "backglass_video",
        attr="bg_video_path",
        payload_key="BGVideoPath",
        filename_template="bg.mp4",
        asset_group="table_video_resolution",
        token="(Backglass)",
        family=VIDEO_FAMILY,
    ),
    MediaSpec(
        "scoreview_video",
        attr="dmd_video_path",
        payload_key="DMDVideoPath",
        filename_template="dmd.mp4",
        asset_group="table_video_resolution",
        token="(DMD)",
        family=VIDEO_FAMILY,
    ),
    MediaSpec(
        "audio",
        attr="audio_path",
        payload_key="AudioPath",
        filename_template="audio.mp3",
        token="(Audio)",
        family=AUDIO_FAMILY,
    ),
    # The 3.0 additions - spec tokens except rule_sheet, which the spec keeps
    # outside its media scheme and we include so it gets the chain.
    MediaSpec(
        "instruction_card",
        attr="instruction_card_image_path",
        payload_key="InstructionCardImagePath",
        filename_template="instructioncard.png",
        token="(InstructionCard)",
        alt_tokens=("(RuleCard)", "(GameHelp)"),
        pages=True,
    ),
    MediaSpec(
        "topper",
        attr="topper_path",
        payload_key="TopperPath",
        filename_template="topper.png",
        token="(Topper)",
    ),
    MediaSpec(
        "topper_video",
        attr="topper_video_path",
        payload_key="TopperVideoPath",
        filename_template="topper.mp4",
        token="(Topper)",
        family=VIDEO_FAMILY,
    ),
    MediaSpec(
        "loading",
        attr="loading_video_path",
        payload_key="LoadingVideoPath",
        filename_template="loading.mp4",
        token="(Loading)",
        family=VIDEO_FAMILY,
    ),
    MediaSpec(
        "audio_launch",
        attr="audio_launch_path",
        payload_key="AudioLaunchPath",
        filename_template="audiolaunch.mp3",
        token="(AudioLaunch)",
        family=AUDIO_FAMILY,
    ),
    MediaSpec(
        "rule_sheet",
        attr="rule_sheet_path",
        payload_key="RuleSheetPath",
        filename_template="rulesheet.pdf",
        token="(RuleSheet)",
        family=RULE_SHEET_FAMILY,
    ),
    # The game's title art - usually the source a wheel is derived from, which
    # is why the wheel falls back to it.
    MediaSpec(
        "logo",
        attr="logo_image_path",
        payload_key="LogoImagePath",
        filename_template="logo.png",
        token="(Logo)",
    ),
)


# Spellings a kind has had. Themes, stored data and the media route all still use them,
# and a kind name is a published thing - once it has been asked for it answers forever.
MEDIA_KIND_ALIASES = {
    "table": "playfield",
    "table_video": "playfield_video",
    "fss": "playfield_fss",
    "bg": "backglass",
    "bg_video": "backglass_video",
    "dmd": "scoreview",
    "dmd_video": "scoreview_video",
    "realdmd": "real_dmd",
    "realdmd_color": "real_dmd_color",
    "rulecard": "instruction_card",
    "audiolaunch": "audio_launch",
    "rulesheet": "rule_sheet",
}


def canonical_kind(kind: str) -> str:
    """The kind a name means now, given any spelling it has been published under."""
    name = str(kind or "").strip().lower()
    return MEDIA_KIND_ALIASES.get(name, name)


_FAMILY_NAMES = {IMAGE_FAMILY: "image", VIDEO_FAMILY: "video",
                 AUDIO_FAMILY: "audio", DOC_FAMILY: "doc", RULE_SHEET_FAMILY: "doc"}


def media_family(kind: str) -> str:
    """What a kind's files are - "image", "video", "audio", "doc", or "" if unknown.

    Asked by anything that has to choose an element to present one with. The kind's
    name is not the answer: `loading` is video and says nothing about it, so a caller
    testing for a `_video` suffix gets that one wrong.
    """
    spec = next((item for item in MEDIA_SPECS
                 if item.kind == canonical_kind(kind)), None)
    return _FAMILY_NAMES.get(spec.family, "") if spec else ""


def held_family(kind: str, filename: str) -> str:
    """A rule sheet's kind is a document family that also accepts pictures, so a picture
    file draws as an image."""
    family = media_family(kind)
    if family == "doc" and PurePosixPath(filename).suffix.lower() in IMAGE_FAMILY:
        return "image"
    return family


PAGED_KINDS = frozenset(spec.kind for spec in MEDIA_SPECS if spec.pages)


def page_token(token: str, page: int) -> str:
    """The token a page carries: "(Flyer)" is page 1, "(Flyer 2)" is page 2."""
    return token if page == 1 else f"{token[:-1]} {page})"


def fixed_page_name(spec: MediaSpec, page: int, extension: str,
                    variant: str = "table") -> str:
    """The fixed-tier name of a page: "flyer.png", then "flyer 2.png"."""
    stem = spec.stem(variant)
    return f"{stem}{extension}" if page == 1 else f"{stem} {page}{extension}"


class SpecName(NamedTuple):
    """A spec-named file read back: whose kind it is, whose stem, and which page."""
    kind: str
    stem: str
    page: int = 1


def _page_rx(token: str) -> re.Pattern[str]:
    return re.compile(rf"\({re.escape(token[1:-1])} ([1-9]\d*)\) ", re.IGNORECASE)


def spec_named(filename: str) -> SpecName | None:
    """A spec-named file read back as `(kind, stem, page)`, or None if it is not one.

    The reverse of the naming rule `media_placement.target_name` writes, so a file this
    project put down can be read back and asked who it was for. Alt tokens are accepted
    the way resolution accepts them; a video and its still share a token and are told
    apart by the extension, which is why the family is part of the match. A name with no
    number is page 1.

    Nothing else is a spec name. A file the user called something of their own is not a
    defect and must not be reported as one, so it answers None rather than a guess.
    """
    name = Path(filename).name
    stem, dot, extension = name.rpartition(".")
    if not dot:
        return None
    extension = f".{extension.lower()}"
    for spec in MEDIA_SPECS:
        if extension not in spec.family:
            continue
        for token in (spec.token, *spec.alt_tokens):
            if not token:
                continue
            if stem.lower().startswith(f"{token.lower()} "):
                return SpecName(spec.kind, stem[len(token) + 1:])
            found = _page_rx(token).match(stem) if spec.pages else None
            if found is not None and int(found.group(1)) >= 2:
                return SpecName(spec.kind, stem[found.end():], int(found.group(1)))
    return None


def unsupported_named(filename: str, variant: str = "table") -> SpecName | None:
    """A file VPinFE would name for a kind if its extension were one the kind reads: a
    token name or the kind's fixed name, with an extension the converter can make into the
    kind's family. Judged from the name alone, so an unrelated file of the person's is never
    one. A fixed name answers an empty stem, as `media_placement.FIXED` does."""
    return _named(filename, variant, lambda spec: convertible_family(spec.family))


def supported_named(filename: str, variant: str = "table") -> SpecName | None:
    """The same reading for an extension the kind already reads: a token name or the kind's
    fixed name."""
    return _named(filename, variant, lambda spec: spec.family)


def _named(filename: str, variant: str,
           extensions: Callable[[MediaSpec], tuple[str, ...]]) -> SpecName | None:
    name = Path(filename).name
    stem, dot, extension = name.rpartition(".")
    if not dot:
        return None
    extension = f".{extension.lower()}"
    for spec in MEDIA_SPECS:
        if extension not in extensions(spec):
            continue
        fixed = spec.stem(variant).lower()
        if stem.lower() == fixed:
            return SpecName(spec.kind, "")
        numbered = re.fullmatch(rf"{re.escape(fixed)} ([2-9]|[1-9]\d+)", stem.lower())
        if spec.pages and numbered:
            return SpecName(spec.kind, "", int(numbered.group(1)))
        for token in (spec.token, *spec.alt_tokens):
            if not token:
                continue
            if stem.lower().startswith(f"{token.lower()} "):
                return SpecName(spec.kind, stem[len(token) + 1:])
            found = _page_rx(token).match(stem) if spec.pages else None
            if found is not None and int(found.group(1)) >= 2:
                return SpecName(spec.kind, stem[found.end():], int(found.group(1)))
    return None


def media_filename_map(playfield_variant: str = "table") -> dict[str, str]:
    """Kind to the filename it resolves. The variant changes filenames, not kinds."""
    return {spec.kind: spec.filename(playfield_variant) for spec in MEDIA_SPECS}


def media_label_map() -> dict[str, str]:
    """Kind to the name a person reads for it."""
    return {spec.kind: spec.label for spec in MEDIA_SPECS}


def media_attr_kind_map(playfield_variant: str = "table") -> dict[str, str]:
    return {spec.attr: spec.kind for spec in MEDIA_SPECS}


def media_attr_map(playfield_variant: str = "table") -> dict[str, str]:
    return {spec.attr: spec.filename(playfield_variant) for spec in MEDIA_SPECS}


def default_media_path(game_dir: str | Path, kind: str, playfield_variant: str = "table") -> Path:
    filenames = media_filename_map(playfield_variant)
    if kind not in filenames:
        raise KeyError(f"Unknown media kind: {kind}")
    return Path(game_dir) / "medias" / filenames[kind]


# Theme-side set overrides. common/ cannot import frontend/, so the frontend
# pushes its active theme's choice down here at boot; the ini default applies
# otherwise. kind -> set name.
_set_overrides: dict[str, str] = {}


def set_media_set_override(kind: str, set_name: str | None) -> None:
    if set_name:
        _set_overrides[kind] = set_name
    else:
        _set_overrides.pop(kind, None)


def active_set_for(kind: str, configured: str = "") -> str | None:
    return _set_overrides.get(kind) or (configured.strip() or None)


def available_sets(kind: str, medias_tree: set[str]) -> list[str]:
    """Set names present under medias/<kind>s/, from a relative-path listing."""
    prefix = f"{kind}s/"
    names = {rel.split("/", 2)[1] for rel in medias_tree
             if rel.lower().startswith(prefix) and rel.count("/") >= 2}
    return sorted(names)


def list_media_sets(game_dirs: Iterable[str | Path], kind: str = "wheel") -> list[str]:
    """Every set name across these game folders, plus the reserved virtual ones.

    "logo" is always offered for the wheel: it needs no wheels/ folder because
    it resolves from each table's logo media directly.
    """
    names: set[str] = set()
    for game_dir in game_dirs:
        sets_dir = Path(game_dir) / "medias" / f"{kind}s"
        try:
            names.update(d.name for d in sets_dir.iterdir() if d.is_dir())
        except OSError:
            continue
    if kind == "wheel":
        names.add("logo")
    return sorted(names, key=str.lower)


def resolve_media_files(game_dir: str | Path, game_contents: set[str],
                        medias_contents: set[str],
                        playfield_variant: str = "table",
                        table_stem: str | None = None,
                        active_sets: dict[str, str] | None = None) -> dict[str, Path | None]:
    """Canonical media kind -> the file that serves it, or None.

    The paths half of resolve_media_entries, which is where the tiers are documented.
    Kept because every caller but the API wants only the winner.
    """
    return {kind: hit.path for kind, hit
            in resolve_media_entries(game_dir, game_contents, medias_contents,
                                     playfield_variant, table_stem, active_sets).items()}


class MediaHit(NamedTuple):
    """The file serving a kind, and which tier it came from.

    `tier` answers "why this file", which is a different question from "who put it
    there" - that is origin, recorded per path in the .info assets ledger. A file named
    for the folder can still have been downloaded, and a file in the fixed-name slot can
    still be hand-placed, so neither answer implies the other.
    """
    path: Path | None
    tier: str | None


# What tier served a kind. Ordered most specific first, as the resolver tries them.
TIER_TABLE = "table"        # "(Token) <table-stem>.ext" - this table's own
TIER_SET = "set"            # from the kind's active set; reported as "set:<name>"
TIER_GAME = "game"          # "(Token) <folder-name>.ext" - shared by the game's tables
TIER_DEFAULT = "default"    # "wheel.png"-style fixed name; where vpinmediadb writes
TIER_FALLBACK = "fallback"  # borrowed from fallback_kind; reported as "fallback:<kind>"


def _finder(game_dir: Path, game_contents: set[str],
            medias_contents: set[str]) -> Callable[[str], Path | None]:
    """Case-insensitive lookup of one companion filename, medias/ before the root."""
    medias_dir = game_dir / "medias"
    in_medias = {name.lower(): name for name in medias_contents}
    in_root = {name.lower(): name for name in game_contents}

    def find(name: str) -> Path | None:
        hit = in_medias.get(name.lower())
        if hit is not None:
            return medias_dir / hit
        hit = in_root.get(name.lower())
        return game_dir / hit if hit is not None else None

    return find


def _tier_names(spec: MediaSpec, folder_name: str, playfield_variant: str,
                table_stem: str | None, active: str | None, page: int = 1
                ) -> tuple[list[str], list[str], list[str], list[str]]:
    """What each tier would call this kind's file - table, game, set, default.

    One builder for the resolver and for the candidate listing both, so the panel can
    never name a tier the resolver does not look in.

    A table whose stem is the folder's name has no tier of its own: its name is the
    game's, and a file under it serves every table in the folder.
    """
    # Tier outranks token preference: a table-specific alias still beats a
    # folder-level preferred token, or "most specific wins" would not hold.
    tokens = tuple(page_token(token, page)
                   for token in ((spec.token,) + spec.alt_tokens if spec.token else ()))
    own = table_stem if table_stem and table_stem.lower() != folder_name.lower() else None
    table_names = ([f"{token} {own}{ext}"
                    for token in tokens for ext in spec.family] if own else [])
    folder_names = [f"{token} {folder_name}{ext}"
                    for token in tokens for ext in spec.family]
    fixed_names = [fixed_page_name(spec, page, ext) if spec.pages
                   else f"{spec.stem(playfield_variant)}{ext}" for ext in spec.family]
    set_names = ([f"{spec.kind}s/{active}/{name}"
                  for name in table_names + folder_names + fixed_names]
                 if active and active != "logo" else [])
    return table_names, folder_names, set_names, fixed_names


def resolve_media_entries(game_dir: str | Path, game_contents: set[str],
                          medias_contents: set[str],
                          playfield_variant: str = "table",
                          table_stem: str | None = None,
                          active_sets: dict[str, str] | None = None,
                          only: Iterable[str] | None = None
                          ) -> dict[str, MediaHit]:
    """Canonical media kind -> the file that serves it, or None.

    Three tiers, most specific wins, per kind:

      1. "(Token) <table-stem>.<ext>"      - this table's own media
      2. "(Token) <folder-name>.<ext>"     - shared by every table in the folder
      3. "wheel.png"-style fixed names     - what vpinmediadb writes

    Within a tier the kind's extension family is tried in order, first hit wins -
    so a hand-placed wheel.jpg finally resolves instead of being invisible.
    Matching is case-insensitive, like every other companion lookup. The medias/
    folder is canonical and the folder root is the fallback at every tier, which
    keeps tier 3 exactly as it always behaved.

    Keyed by MEDIA_SPECS kinds, stable across variants - under playfield_variant "fss"
    the playfield's *filename* changes but its kind stays "playfield".

    `medias_contents` may carry relative paths (wheels/tarcisio/wheel.png) for
    set folders. For a set-supporting kind with an active set, the order is:
    the user's own spec-named files, then the set's files (its own full chain),
    then the plain fixed default - so activating a set never clobbers a
    hand-made per-version file, and a media refresh never beats the set. The
    reserved set name "logo" prefers the logo kind in that middle slot.

    `only` limits the answer to those kinds, which is what a list that shows one of them
    asks for. A kind answered is answered exactly as it is without `only`: the kinds it
    borrows from are resolved too and left out of the result only if not asked for.
    """
    game_dir = Path(game_dir)
    folder_name = game_dir.name
    find = _finder(game_dir, game_contents, medias_contents)
    asked = None if only is None else set(only)
    wanted = None if asked is None else _kinds_needed(asked, active_sets)

    resolved: dict[str, MediaHit] = {}
    virtual_pending: dict[str, Path | None] = {}
    for spec in MEDIA_SPECS:
        if wanted is not None and spec.kind not in wanted:
            continue
        active = (active_sets or {}).get(spec.kind) if spec.supports_sets else None
        # Each tier keeps its own list rather than one merged one: merged, the winner's
        # tier is unknowable, and "why is this file the one being used" is the question
        # a curation view exists to answer.
        table_names, folder_names, set_names, fixed_names = _tier_names(
            spec, folder_name, playfield_variant, table_stem, active)

        first = lambda names: next(  # noqa: E731
            (path for name in names if (path := find(name)) is not None), None)

        # first= is bound as a default because it is rebuilt each iteration; closing
        # over the loop variable would make every kind use the last spec's finder.
        def pick(names: list[str], tier: str,
                 first: Callable[[list[str]], Path | None] = first
                 ) -> MediaHit | None:  # noqa: B006
            hit = first(names)
            return MediaHit(hit, tier) if hit is not None else None

        if active == "logo":
            # The reserved virtual set: prefer the logo kind between the user's
            # own files and the plain default. Logo resolves later in the spec
            # order, so finish this kind in the post-pass.
            resolved[spec.kind] = (pick(table_names, TIER_TABLE)
                                   or pick(folder_names, TIER_GAME)
                                   or MediaHit(None, None))
            virtual_pending[spec.kind] = first(fixed_names)
        else:
            resolved[spec.kind] = (pick(table_names, TIER_TABLE)
                                   or pick(folder_names, TIER_GAME)
                                   or pick(set_names, f"{TIER_SET}:{active}")
                                   or pick(fixed_names, TIER_DEFAULT)
                                   or MediaHit(None, None))

    for kind, fixed_hit in virtual_pending.items():
        if resolved[kind].path is None:
            logo = resolved.get("logo")
            if logo is not None and logo.path is not None:
                resolved[kind] = MediaHit(logo.path, f"{TIER_SET}:logo")
            elif fixed_hit is not None:
                resolved[kind] = MediaHit(fixed_hit, TIER_DEFAULT)

    # Cross-kind fallbacks, after everything: a kind with no file of its own at
    # any tier borrows its fallback kind's winner.
    for spec in MEDIA_SPECS:
        if spec.fallback_kind and spec.kind in resolved and resolved[spec.kind].path is None:
            borrowed = resolved.get(spec.fallback_kind)
            if borrowed is not None and borrowed.path is not None:
                resolved[spec.kind] = MediaHit(borrowed.path,
                                               f"{TIER_FALLBACK}:{spec.fallback_kind}")
    if asked is None:
        return resolved
    return {kind: hit for kind, hit in resolved.items() if kind in asked}


def _kinds_needed(kinds: Iterable[str], active_sets: dict[str, str] | None) -> set[str]:
    """The kinds to resolve so that `kinds` come out as they would from all of them: each
    one's fallback, and the logo for a kind whose active set is the reserved `logo`."""
    wanted = set(kinds)
    grew = True
    while grew:
        grew = False
        for spec in MEDIA_SPECS:
            if spec.kind not in wanted:
                continue
            needs = [spec.fallback_kind] if spec.fallback_kind else []
            if spec.supports_sets and (active_sets or {}).get(spec.kind) == "logo":
                needs.append("logo")
            for kind in needs:
                if kind not in wanted:
                    wanted.add(kind)
                    grew = True
    return wanted



def page_numbers(spec: MediaSpec, stem: str | None, listing: Iterable[str]) -> list[int]:
    """The page numbers from 2 up that a listing holds at one tier, in order.

    Read off the names the folder has: the leading `(Token n)` is parsed out, so a gap in
    the numbers is simply a number nobody wrote. `stem` is the tier's name, or None for
    the fixed tier.
    """
    extensions = "|".join(re.escape(ext) for ext in spec.family)
    if stem is None:
        pattern = rf"{re.escape(spec.stem())} ([1-9]\d*)(?:{extensions})"
    else:
        tokens = "|".join(re.escape(token[1:-1])
                          for token in (spec.token, *spec.alt_tokens) if token)
        pattern = rf"\((?:{tokens}) ([1-9]\d*)\) {re.escape(stem)}(?:{extensions})"
    rx = re.compile(pattern, re.IGNORECASE)
    found = set()
    for name in listing:
        match = rx.fullmatch(name)
        if match is not None and int(match.group(1)) >= 2:
            found.add(int(match.group(1)))
    return sorted(found)



def resolve_media_pages(game_dir: str | Path, game_contents: set[str],
                        medias_contents: set[str],
                        resolved: Mapping[str, MediaHit],
                        playfield_variant: str = "table",
                        table_stem: str | None = None) -> dict[str, list[Path]]:
    """Kind to its pages in order, page 1 first, for each paged kind with two or more.

    Pages follow page 1: they are read at the tier where `resolved` found it and nowhere
    else, so a table's own flyer never borrows the folder's pages. A tier with numbered
    pages and no page 1 shows nothing, and does not make that tier resolve - page 1 is
    what `resolved` already says.
    """
    game_dir = Path(game_dir)
    folder_name = game_dir.name
    find = _finder(game_dir, game_contents, medias_contents)
    listing = [*game_contents, *(name for name in medias_contents if "/" not in name)]

    own = table_stem if table_stem and table_stem.lower() != folder_name.lower() else None
    # Each tier a page can follow: where its names sit in `_tier_names`' answer, and the
    # stem they carry (None for the fixed names).
    tiers = {TIER_TABLE: (0, own), TIER_GAME: (1, folder_name), TIER_DEFAULT: (3, None)}

    pages: dict[str, list[Path]] = {}
    for spec in MEDIA_SPECS:
        hit = resolved.get(spec.kind)
        if not spec.pages or hit is None or hit.path is None or hit.tier not in tiers:
            continue
        slot, stem = tiers[hit.tier]
        found = [hit.path]
        for number in page_numbers(spec, stem, listing):
            names = _tier_names(spec, folder_name, playfield_variant, table_stem,
                                None, number)[slot]
            page = next((path for name in names if (path := find(name)) is not None), None)
            if page is not None:
                found.append(page)
        if len(found) > 1:
            pages[spec.kind] = found
    return pages


def resolve_media_pages_by_table(game_dir: str | Path, game_contents: set[str],
                                 medias_contents: set[str],
                                 table_filenames: Iterable[str],
                                 playfield_variant: str = "table",
                                 active_sets: dict[str, str] | None = None
                                 ) -> dict[str, dict[str, list[str]]]:
    """`resolve_media_by_table` for pages: lowercased filename to kind to page paths.

    Only the kinds with two or more pages are recorded, as only the kinds that resolved
    are there.
    """
    found = {}
    for name in table_filenames:
        stem = Path(name).stem
        entries = resolve_media_entries(game_dir, game_contents, medias_contents,
                                        playfield_variant, stem, active_sets)
        found[name.lower()] = {
            kind: [str(path) for path in paths]
            for kind, paths in resolve_media_pages(
                game_dir, game_contents, medias_contents, entries,
                playfield_variant, stem).items()}
    return found


class MediaCandidate(NamedTuple):
    """A file that could serve a kind, and the tier whose name it carries."""
    path: Path
    tier: str


def media_candidates(game_dir: str | Path, game_contents: set[str],
                     medias_contents: set[str], kind: str,
                     playfield_variant: str = "table",
                     table_stem: str | None = None,
                     active_sets: dict[str, str] | None = None
                     ) -> list[MediaCandidate]:
    """Every tier that holds a file for this kind, most specific first.

    The resolver reports the winner, which is the right answer for playing a game and
    the wrong one for curating one: "I replaced the artwork and nothing changed" is
    always a more specific file sitting above the one that was edited, and that is only
    visible if the losers are listed too. Cross-kind fallbacks are left out on purpose -
    a borrowed file belongs to the kind it was named for, not to this one.
    """
    game_dir = Path(game_dir)
    spec = next((item for item in MEDIA_SPECS
                 if item.kind == canonical_kind(kind)), None)
    if spec is None:
        return []
    find = _finder(game_dir, game_contents, medias_contents)
    active = (active_sets or {}).get(spec.kind) if spec.supports_sets else None
    table_names, folder_names, set_names, fixed_names = _tier_names(
        spec, game_dir.name, playfield_variant, table_stem, active)
    tiers = [(TIER_TABLE, table_names), (TIER_GAME, folder_names),
             (f"{TIER_SET}:{active}", set_names), (TIER_DEFAULT, fixed_names)]

    found: list[MediaCandidate] = []
    for tier, names in tiers:
        # One per tier, not one per extension: within a tier the resolver stops at the
        # first hit, so a second file there is not a candidate for anything.
        path = next((hit for name in names if (hit := find(name)) is not None), None)
        if path is not None:
            found.append(MediaCandidate(path, tier))
    return found


def resolve_media_by_table(game_dir: str | Path, game_contents: set[str],
                           medias_contents: set[str],
                           table_filenames: Iterable[str],
                           playfield_variant: str = "table",
                           active_sets: dict[str, str] | None = None
                           ) -> dict[str, dict[str, str]]:
    """Every table in the folder, and the file each kind resolves to for it.

    Keyed by lowercased filename, not by table id: ids are backfilled after the scan,
    so during it the filename is the only handle a table has. The caller supplies the
    names rather than this module filtering for them, which keeps media_specs free of
    any dependency on the games package.

    Only the kinds that resolved are recorded - a folder is mostly gaps, and storing
    every miss for every table is a lot of None to carry around a library.
    """
    return {
        name.lower(): {
            kind: str(path)
            for kind, path in resolve_media_files(
                game_dir, game_contents, medias_contents, playfield_variant,
                Path(name).stem, active_sets).items()
            if path is not None
        }
        for name in table_filenames
    }


# `game` is a Game, which this layer may not name: nothing in common/ itself imports
# the games package. Any, because the folder is read off it directly.
def apply_media_specs(game: Any, game_contents: set[str], medias_contents: set[str],
                      playfield_variant: str = "table",
                      table_stem: str | None = None,
                      active_sets: dict[str, str] | None = None) -> None:
    root = str(game.full_path_game or "")
    entries = resolve_media_entries(root, game_contents, medias_contents,
                                    playfield_variant, table_stem, active_sets)
    for spec in MEDIA_SPECS:
        path = entries[spec.kind].path
        if path is not None:
            setattr(game, spec.attr, str(path))
    game.media_pages = {
        kind: [str(path) for path in paths]
        for kind, paths in resolve_media_pages(root, game_contents, medias_contents,
                                               entries, playfield_variant,
                                               table_stem).items()}


def game_media_payload(game: object) -> dict[str, str | None]:
    """The media half of a contract 1 row: the frozen key, the resolved path.

    `topper_video` is declared here and not on Game - it is only ever set by the scan -
    so the default is what a game that never resolved one answers with.
    """
    return {
        spec.payload_key: getattr(game, spec.attr, None)
        for spec in MEDIA_SPECS
    }

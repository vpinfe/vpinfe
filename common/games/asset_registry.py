"""What a file is, judged by its name: which media kind, or none of them."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from common import apps, icons
from common.games.media_service import IMAGE_EXTENSIONS
from common.i18n import t
from common.media.media_specs import MEDIA_SPECS, PAGED_KINDS, media_filename_map

logger = logging.getLogger("vpinfe.common.games.asset_registry")

# Archive containers are opened and inspected, never classified as a bare file.
ARCHIVE_EXTENSIONS = frozenset({".zip", ".vpxz", ".rar", ".7z"})

VIDEO_EXTENSIONS = frozenset({".mp4", ".webm"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".ogg"})
# Deliberately NOT folded into MEDIA_EXTENSIONS: a bare .txt must never classify
# as media - ROM and altsound archives carry alias.txt and friends. Doc
# extensions only match through an exact canonical name or a spec token.
DOC_EXTENSIONS = frozenset({".pdf", ".md", ".txt", ".html"})
MEDIA_EXTENSIONS = frozenset(IMAGE_EXTENSIONS) | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


@dataclass(frozen=True)
class AssetSpec:
    kind: str
    icon: str
    extensions: tuple[str, ...]     # lowercase; () for marker/folder-detected kinds
    requires_game: bool
    # This asset needs a ROM to mean anything - a color set or an alternative sound
    # bank has nothing to attach to without one. A dependency between assets, and not
    # the same question as `required_to_launch`.
    requires_rom: bool
    allow_multiple: bool
    # Whether the table will not start without it: an asset is either required to launch
    # or an experience enhancer. Undeclared, the difference has to be written by hand
    # wherever it matters, and coverage means nothing - "4 of 13" reads the same whether
    # the absent one is a PUP pack or the ROM the table needs to run.
    required_to_launch: bool = False

    @property
    def label(self) -> str:
        return t(f"asset.kind.{self.kind}.label")


def is_readme(name: str) -> bool:
    """Narrow on purpose: readme* (any extension) and .nfo. Never a blanket
    .txt - ROM and altsound archives carry alias.txt and its kin."""
    lower = name.lower()
    return lower.startswith("readme") or lower.endswith(".nfo")


ASSET_SPECS = (
    # The file itself. Nothing launches without it.
    AssetSpec("table", icons.TABLES, apps.table_suffixes(), False, False, False,
              required_to_launch=True),
    AssetSpec("game_info", "description", (), True, False, False),
    AssetSpec("backglass", "wallpaper", (".directb2s",), True, False, False),
    AssetSpec("ini", "tune", (".ini",), True, False, False),
    # A patch is a delta against one exact base table, not an installable artifact.
    # requires_game is doing real work here: applying it without the right base
    # produces a corrupt file rather than an error.
    AssetSpec("patch", "difference", (".dif",), True, False, True),
    # The three VPX resolves per table by naming rule. Declared here rather than only
    # in asset_resolver: a kind known to the launch path and not to this one leaves an
    # uploaded .vbs classified as nothing at all. None is required to launch - a table
    # whose script is inside the .vpx runs, and the sidecar is an override.
    AssetSpec("script", "code", (".vbs",), True, False, False),
    AssetSpec("pov", "3d_rotation", (".pov",), True, False, False),
    AssetSpec("scv", "scoreboard", (".scv",), True, False, False),
    AssetSpec("nix", "scoreboard", (".nix",), True, False, False),
    # Required only where the table declares one - an EM table needs none, and
    # calling it missing there would call every EM table broken. The flag says
    # the kind can block a launch; whether it does is per table.
    AssetSpec("rom", "memory", (), True, False, True,
              required_to_launch=True),
    AssetSpec("altcolor_serum", "palette", (".crz", ".cromc"), True, True, True),
    AssetSpec("altcolor_vni", "palette",
              (".vni", ".pal", ".pac"), True, True, True),
    AssetSpec("altsound", "volume_up", (), True, True, False),
    AssetSpec("pup_pack", "video_library", (), True, False, False),
    AssetSpec("music", "music_note", (), True, False, False),
    AssetSpec("media", "image", tuple(sorted(MEDIA_EXTENSIONS)), True, False, True),
    AssetSpec("readme", "description", (), True, False, True),
)

_SPECS_BY_KIND = {spec.kind: spec for spec in ASSET_SPECS}

_LENS_NAMES = {"altcolor_serum": "alt_color", "altcolor_vni": "alt_color",
               "altsound": "alt_sound"}


LEGACY_ALTSOUND = "pinmame/altsound"


def altsound_folders(game_dir: Path, subdirs: Iterable[str]) -> list[str]:
    """Every folder a game keeps AltSound packs in, the one Visual Pinball reads first.

    That is `altsound/` beside the table; `LEGACY_ALTSOUND` is where earlier versions put
    them and still counts. `subdirs` are the names directly inside the game folder, so the
    usual answer costs no extra look.
    """
    names = sorted(subdirs, key=lambda name: (name != "altsound", name))
    found = [name for name in names[:1] if name.lower() == "altsound"]
    beside = LEGACY_ALTSOUND.split("/")[0]
    if any(name.lower() == beside for name in names) and (game_dir / LEGACY_ALTSOUND).is_dir():
        found.append(LEGACY_ALTSOUND)
    return found


def altsound_folder(game_dir: Path, subdirs: Iterable[str]) -> str:
    """The first of `altsound_folders`, or "" where the game has none."""
    return next(iter(altsound_folders(game_dir, subdirs)), "")


def lens_kind(kind: str) -> str:
    """What the asset lens calls one of the registry's kinds."""
    return _LENS_NAMES.get(kind, kind)


def specs_named(name: str) -> tuple[AssetSpec, ...]:
    """The registry's kind by that name, or every kind the lens calls by it. KeyError
    for a name that is neither."""
    found = tuple(spec for spec in ASSET_SPECS
                  if name in (spec.kind, lens_kind(spec.kind)))
    if not found:
        raise KeyError(name)
    return found


def extensions_listed(name: str, archives: bool = False) -> list[str]:
    """KeyError for a name that is not a kind."""
    wanted = [extension for spec in specs_named(name) for extension in spec.extensions] \
        if name else []
    return wanted + (sorted(ARCHIVE_EXTENSIONS) if archives else [])

# The kinds whose absence stops a table running, as opposed to making it worse.
# The table file is the library rather than an accessory to it, so it is the one kind
# there is no sense in a library saying it does not collect. Everything else is optional
# including the ROM: an EM table declares none, and an all-EM library never wants the
# column. Not `REQUIRED_KINDS` - required-ness belongs to a kind and whether it applies
# belongs to a table, which is the distinction `launchable` exists to keep.
ALWAYS_KEPT = frozenset({"table"})

REQUIRED_KINDS = frozenset(spec.kind for spec in ASSET_SPECS if spec.required_to_launch)


def launchable(available: bool, rom_declared: bool, rom_installed: bool | None) -> bool | None:
    """Whether every required-to-launch asset resolves for one table.

    Three-valued, and the middle one is the point: `None` is "cannot tell", which is a
    table nothing has parsed yet. Only `False` is a fault.

    Required-ness is a property of the *kind* and whether it applies is per table -
    `rom` is required only where the table declares one, and an EM table declares
    none. Reading `REQUIRED_KINDS` as a checklist every table must satisfy would call
    every EM table broken, which is the same trap the missing-rom check had to avoid.
    """
    if not available:
        return False
    if not rom_declared:
        return True
    return rom_installed


def is_required_to_launch(kind: str) -> bool:
    """Whether this kind of asset can stop a table launching. Unknown kinds are not:
    a kind this build has never heard of cannot be something it depends on."""
    spec = _SPECS_BY_KIND.get(kind)
    return bool(spec and spec.required_to_launch)

# Canonical media filenames (bg.png, dmd.mp4, audio.mp3, ...) -> media kind.
_MEDIA_FILENAME_TO_KIND = {filename: kind for kind, filename in media_filename_map("table").items()}

# Spec tokens ("(Wheel) Name.png") -> media kind, image and video resolved by
# extension. Explicit, so spec-named files import by rule rather than by the
# keyword fallback happening to contain the right word.
def _bucket(ext: str) -> str:
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in DOC_EXTENSIONS:
        return "doc"
    return "image"


_TOKEN_TO_KIND: dict[str, dict[str, str]] = {}
for _spec in MEDIA_SPECS:
    if _spec.token:
        for _token in (_spec.token,) + _spec.alt_tokens:
            for _ext in _spec.family:
                _TOKEN_TO_KIND.setdefault(_token.lower(), {}).setdefault(
                    _bucket(_ext), _spec.kind)

_PAGE_NUMBER = re.compile(r" [1-9]\d*\)$")


def _token_kinds(token: str) -> dict[str, str] | None:
    """The kinds a "(Token)" names by extension family. "(Flyer 2)" is page 2 of the flyer,
    so a page number is read off a token of a kind that has pages, and only of one."""
    kinds = _TOKEN_TO_KIND.get(token)
    if kinds is None and _PAGE_NUMBER.search(token):
        paged = _TOKEN_TO_KIND.get(_PAGE_NUMBER.sub(")", token), {})
        kinds = {family: kind for family, kind in paged.items() if kind in PAGED_KINDS}
    return kinds or None


# Keyword-in-stem fallbacks when a media file is not named canonically.
# Ordered; realdmd is handled ahead of this table so the scoreview keyword never claims a
# realdmd file. The keyword people actually type stays as it is - it is what they name
# files - while the slot it fills is scoreview.
_MEDIA_KEYWORDS: tuple[tuple[tuple[str, ...], str, str | None], ...] = (
    (("wheel",), "wheel", None),
    (("logo",), "logo", None),
    (("backglass", "b2s"), "backglass", "backglass_video"),
    (("dmd",), "scoreview", "scoreview_video"),
    (("playfield", "table", "pf"), "playfield", "playfield_video"),
    (("cabinet", "cab"), "cab", None),
    (("flyer",), "flyer", None),
    (("fss",), "playfield_fss", None),
)


def spec_for(kind: str) -> AssetSpec:
    """Return the AssetSpec for a kind key, raising KeyError if unknown."""
    return _SPECS_BY_KIND[kind]


def classify_bare_extension(filename: str) -> AssetSpec | None:
    """Classify a single non-archive file by its extension, or None if unrecognized."""
    ext = Path(filename).suffix.lower()
    if not ext or ext in ARCHIVE_EXTENSIONS:
        return None
    for spec in ASSET_SPECS:
        if ext in spec.extensions:
            return spec
    return None


def match_media_kind(filename: str) -> str | None:
    """Resolve a media file to its canonical media kind (backglass, wheel, ...), or None.

    Exact canonical filenames win; otherwise a keyword that is a whole word of the stem,
    plus the extension family (image vs video vs audio), decides the slot.
    """
    ext = Path(filename).suffix.lower()
    if ext not in MEDIA_EXTENSIONS and ext not in DOC_EXTENSIONS:
        return None

    name = Path(filename).name.lower()
    if name in _MEDIA_FILENAME_TO_KIND:
        return _MEDIA_FILENAME_TO_KIND[name]

    # Spec naming: "(Token) Whatever.ext". The token decides the kind, the
    # extension family decides image vs video.
    if name.startswith("(") and ") " in name:
        token = name.split(") ", 1)[0] + ")"
        kinds = _token_kinds(token)
        if kinds:
            family = _bucket(ext)
            hit = kinds.get(family)
            if hit:
                return hit

    # Past the explicit names, a doc extension never matches: the keyword
    # fallback on .txt would misfile alias.txt and its kin.
    if ext in DOC_EXTENSIONS:
        return None

    stem = Path(filename).stem.lower()

    if ext in AUDIO_EXTENSIONS:
        # audio is the only audio slot, so any recognized audio file lands there.
        return "audio"

    if "realdmd" in stem or "real dmd" in stem or "real-dmd" in stem:
        color = "color" in stem
        if ext in IMAGE_EXTENSIONS:
            return "real_dmd_color" if color else "real_dmd"
        if ext in VIDEO_EXTENSIONS:
            return "real_dmd_color_video" if color else "real_dmd_video"
        return None

    words = set(re.split(r"[^a-z0-9]+", stem))
    for keywords, image_kind, video_kind in _MEDIA_KEYWORDS:
        if words.intersection(keywords):
            if ext in VIDEO_EXTENSIONS:
                return video_kind
            if ext in IMAGE_EXTENSIONS:
                return image_kind
    return None

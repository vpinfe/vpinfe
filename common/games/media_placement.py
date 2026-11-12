"""Putting a media file where it will be found, at the tier that was asked for.

The resolution chain has always read three tiers and only ever been able to write the
bottom one - `replace_media_file` writes the fixed name, which is where vpinmediadb
writes. So a user's own art landed in the slot a media refresh owns, and no amount of
resolution work could populate the tiers above it.

The tier is not a setting here. It is which name the file gets: the game's folder name
serves every table, a table's stem serves that build alone. A caller says which by
which thing it addressed, so nobody has to learn what a tier is to use one.

A write reports what it resolved to afterwards, because placing a file is not the same
as winning: a shared file is outranked by any table-specific one, and finding that out
by the art not changing is the worst way to learn it.

A kind with pages (a flyer, an instruction card) is several files read in order, and
the number is part of the name: page 2 is `(Flyer 2) Name.png`. A page is addressed by
its position, 1 for the first, whatever numbers the folder happens to carry.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from common.games import info_files
from common.i18n import t
from common.media.media_specs import (
    MEDIA_SPECS,
    MediaSpec,
    convertible_family,
    fixed_page_name,
    page_numbers,
    page_token,
)

logger = logging.getLogger("vpinfe.common.games.media_placement")

_SPEC_BY_KIND = {spec.kind: spec for spec in MEDIA_SPECS}

TIER_GAME = "game"
TIER_TABLE = "table"

# The stem that names the shared default tier (`flyer.png`, `flyer 2.png`), which has no
# stem of its own. Only a kind with pages is written there.
FIXED = ""


class UnplaceableError(ValueError):
    """The kind, the tier or the extension is not something we can write."""


def _spec_of(kind: str) -> MediaSpec:
    spec = _SPEC_BY_KIND.get(kind)
    if spec is None:
        raise UnplaceableError(t("error.games.unknown_kind"))
    return spec


def _name(spec: MediaSpec, token: str | None, stem: str, page: int, extension: str,
          variant: str = "table") -> str:
    """The filename of one page at one tier, in one token and one extension. `variant` is
    the playfield variant, which only the shared default's name depends on."""
    if stem == FIXED:
        return fixed_page_name(spec, page, extension, variant)
    return f"{page_token(token or '', page)} {stem}{extension}"


def target_name(kind: str, stem: str, extension: str, page: int = 1, *,
                unsupported: bool = False) -> str:
    """The filename that puts this kind at this stem's tier.

    With `unsupported` the extension may be one the kind's family can be converted from: a
    file kept as it arrived under the name it would have had.

    Always the spec token, never an alias: aliases exist so a file someone else named
    still resolves, and writing one would be recommending a spelling we do not. `page`
    is the number the name carries, and only a kind with pages takes one past 1.
    """
    spec = _spec_of(kind)
    if not spec.token:
        raise RuntimeError(f"{kind} has no spec name, so it can only be a default")
    extension = extension.lower()
    if extension not in (*spec.family, *(convertible_family(spec.family) if unsupported else ())):
        raise UnplaceableError(t("error.games.wrong_extension", slot=spec.label,
                                 extension=extension, extensions=", ".join(spec.family)))
    if page != 1 and not spec.pages:
        raise UnplaceableError(t("error.games.no_pages", slot=spec.label))
    if stem == FIXED and not spec.pages:
        raise RuntimeError(f"{kind} is not written at the shared default")
    return _name(spec, spec.token, stem, page, extension)


def _names_in(folder: Path) -> dict[str, str]:
    try:
        return {entry.name.lower(): entry.name for entry in folder.iterdir()
                if entry.is_file()}
    except OSError:
        return {}


def _members(game_dir: Path, kind: str, stem: str, page: int = 1, variant: str = "table"
             ) -> Iterator[tuple[Path, str | None, str]]:
    """Every file serving one page of this kind at this stem's tier, as the path, the
    token it is spelled with (None at the shared default) and its extension, in the order
    the resolver reads them: the first one is the one that shows.

    Both folders, because a library that predates `medias/` keeps its art beside the
    .vpx and the resolver still reads it.
    """
    spec = _SPEC_BY_KIND[kind]
    folders = [(folder, _names_in(folder)) for folder in (game_dir / "medias", game_dir)]
    tokens = (None,) if stem == FIXED else (spec.token, *spec.alt_tokens)
    for token in tokens:
        for extension in spec.family:
            wanted = _name(spec, token, stem, page, extension, variant).lower()
            for folder, names in folders:
                if wanted in names:
                    yield folder / names[wanted], token, extension


def _family_at_tier(game_dir: Path, kind: str, stem: str, page: int = 1,
                    variant: str = "table") -> Iterator[Path]:
    """Every file already serving this page at this stem's tier, whatever its token or
    extension. `(Flyer 2)` is not in page 1's family: each page has its own."""
    for path, _token, _extension in _members(game_dir, kind, stem, page, variant):
        yield path


def _present(game_dir: Path, kind: str, stem: str, variant: str = "table") -> list[int]:
    """The page numbers with a file at this tier, ascending. A number can be missing from
    the run - a folder somebody else filled - so these are numbers, not positions."""
    spec = _SPEC_BY_KIND[kind]
    first = [1] if next(_members(game_dir, kind, stem, 1, variant), None) is not None else []
    if not spec.pages:
        return first
    listing = [name for folder in (game_dir / "medias", game_dir)
               for name in _names_in(folder).values()]
    return first + page_numbers(spec, None if stem == FIXED else stem, listing)


def _pages(game_dir: Path, kind: str, stem: str, variant: str = "table") -> list[int]:
    """The numbers of the pages that show: none unless page 1 is there."""
    present = _present(game_dir, kind, stem, variant)
    return present if present[:1] == [1] else []


def page_count(game_dir: str | Path, kind: str, stem: str) -> int:
    """How many pages this kind has at this tier. Zero where page 1 is missing, because
    numbered pages without a page 1 are not shown."""
    _spec_of(kind)
    return len(_pages(Path(game_dir), kind, stem))


def page_tier(game_dir: str | Path, kind: str, table_stem: str = "") -> str | None:
    """The stem whose tier page 1 of this kind resolves at, for the table named by
    `table_stem` (empty for the game): the table's own name, the folder's, or `FIXED`.
    None where there is no page 1 at all.

    A page is added to the flyer the slot shows, so it goes where that flyer's page 1 is.
    """
    game_dir = Path(game_dir)
    spec = _spec_of(kind)
    own = [table_stem] if table_stem and table_stem.lower() != game_dir.name.lower() else []
    for stem in (*own, game_dir.name, *([FIXED] if spec.pages else [])):
        if _pages(game_dir, kind, stem):
            return stem
    return None


def page_stem(game_dir: str | Path, kind: str, lens_stem: str, table_stem: str = "",
              page: int = 1) -> str:
    """The stem to write or remove page `page` at. Page 1 goes at the tier asked for,
    `lens_stem`. A later page goes beside the page 1 the slot shows, whichever tier that
    is, and with no page 1 it stays at `lens_stem`, where the file becomes page 1.

    `table_stem` is the table the slot is shown for, or empty for the game.
    """
    if page == 1:
        return lens_stem
    tier = page_tier(game_dir, kind, table_stem)
    return lens_stem if tier is None else tier


def _number_for(game_dir: Path, kind: str, stem: str, page: int) -> int:
    """The number the file at position `page` carries, or the next number for a page added
    after the last. With no page 1 there is nothing to add to, and the file is page 1."""
    spec = _spec_of(kind)
    if page != 1 and not spec.pages:
        raise UnplaceableError(t("error.games.no_pages", slot=spec.label))
    pages = _pages(game_dir, kind, stem)
    if not pages:
        return 1
    if not 1 <= page <= len(pages) + 1:
        raise UnplaceableError(t("error.games.page_too_far", slot=spec.label, page=page,
                                 next=len(pages) + 1))
    return pages[page - 1] if page <= len(pages) else pages[-1] + 1


def displaced(game_dir: str | Path, kind: str, stem: str, extension: str,
              page: int = 1) -> list[Path]:
    """The files a `place` of this extension would overwrite or delete.

    Answered without the bytes, so a caller can ask before a large upload starts and
    still say what is about to go. It is not only the file with the same name: the
    whole family at this tier goes, so dropping a .jpg over a .png removes the .png.
    """
    game_dir = Path(game_dir)
    number = _number_for(game_dir, kind, stem, page)
    target_name(kind, stem, extension, number)  # refuses what `place` would refuse
    return sorted(_family_at_tier(game_dir, kind, stem, number))


def _holding(game_dir: Path, going: list[Path]) -> tuple[str, list[Path]]:
    """The game id to file copies under and the files among `going` that exist; nothing
    where there is no file, or the folder's `.info` holds no game id."""
    held = [path for path in going if path.is_file()]
    if not held:
        return "", []
    from common.games.info_maintenance import game_id_in

    game_id = game_id_in(info_files.game_info_path(game_dir))
    return (game_id, held) if game_id else ("", [])


def can_keep(game_dir: Path, going: list[Path]) -> None:
    """Raise what `keep_displaced` would raise for these files, copying nothing. Asked
    before a write, so a backups folder that cannot be written to refuses it whole."""
    if _holding(game_dir, going)[1]:
        from common.backups import folder as backup_folder

        backup_folder.for_writing()


def keep_displaced(game_dir: Path, going: list[Path]) -> None:
    """Copy the files a write is about to replace or remove into Backups.

    A copy that was taken stays if the write then fails: by then a sibling may already be
    gone, and the copy is what holds it.
    """
    game_id, held = _holding(game_dir, going)
    if not held:
        return
    from common.backups import folder as backup_folder
    from common.backups import media as backup_media
    from common.backups.copies import REASON_REPLACE

    folder = backup_folder.for_writing()
    for path in held:
        backup_media.keep(folder, game_id, path, path.relative_to(game_dir).as_posix(),
                          REASON_REPLACE)


def place(game_dir: str | Path, kind: str, stem: str, source: str | Path,
          page: int = 1, *, unsupported: bool = False) -> Path:
    """Install `source` as this kind's file for `stem`, and return where it landed.

    Siblings in the same family and at the same tier go: they would otherwise sit
    behind the new file forever, waiting for a family order to change and surface a
    file the user believed replaced. `displaced` reports the same set beforehand.

    Only the page being placed is replaced; the others stay. A page one past the last is
    added, and with no page 1 the file becomes page 1 whatever page was asked for.

    Every file this takes away is copied to Backups first, so a drop, an import, a picture
    chosen from a source and a download all leave something to put back. The write is
    refused before anything changes when the backups folder cannot be written to. A folder
    whose `.info` holds no game id has nothing to file a copy under and is written as it is.

    `unsupported` places a file the family cannot play as it arrived and displaces nothing:
    it sits beside whatever works, and the resolver never picks it.
    """
    game_dir = Path(game_dir)
    source = Path(source)
    number = _number_for(game_dir, kind, stem, page)
    name = target_name(kind, stem, source.suffix, number, unsupported=unsupported)
    medias = game_dir / "medias"
    displaced = [] if unsupported else list(_family_at_tier(game_dir, kind, stem, number))
    keep_displaced(game_dir, displaced)
    medias.mkdir(parents=True, exist_ok=True)
    target = medias / name

    for sibling in displaced:
        # By spelling: where the filesystem ignores case, a Path of another case compares
        # equal, and a file kept under its old spelling would be written over in place.
        if str(sibling) != str(target):
            try:
                sibling.unlink()
            except OSError:
                logger.warning("Could not remove %s", sibling)

    shutil.copy2(source, target)
    return target


def _renumber(game_dir: Path, kind: str, stem: str, numbers: list[int]) -> None:
    """Close the gaps: the page at each position takes that position as its number."""
    spec = _SPEC_BY_KIND[kind]
    for position, number in enumerate(numbers, 1):
        if number == position:
            continue
        for path, token, extension in list(_members(game_dir, kind, stem, number)):
            moved = path.with_name(_name(spec, token, stem, position, extension))
            try:
                path.rename(moved)
            except OSError:
                logger.warning("Could not renumber %s to %s", path, moved)


def move_page(game_dir: str | Path, kind: str, stem: str, page: int, to: int) -> Path:
    """Swap page `page` with page `to`, and return where the moved page's file landed.

    The numbers are in the names, so this renames; nothing is rewritten.
    """
    game_dir = Path(game_dir)
    spec = _spec_of(kind)
    pages = _pages(game_dir, kind, stem)
    for wanted in (page, to):
        if not 1 <= wanted <= len(pages):
            raise UnplaceableError(t("error.games.no_such_page", slot=spec.label,
                                     page=wanted))
    here, there = pages[page - 1], pages[to - 1]
    mine = list(_members(game_dir, kind, stem, here))
    if here == there:
        return mine[0][0]
    theirs = list(_members(game_dir, kind, stem, there))

    # Through a name nothing resolves, so the two never meet on one filename.
    parked = [path.with_name(path.name + ".swap") for path, _token, _extension in mine]
    for (path, _token, _extension), away in zip(mine, parked, strict=True):
        path.rename(away)
    for path, token, extension in theirs:
        path.rename(path.with_name(_name(spec, token, stem, here, extension)))
    landed = []
    for away, (path, token, extension) in zip(parked, mine, strict=True):
        moved = path.with_name(_name(spec, token, stem, there, extension))
        away.rename(moved)
        landed.append(moved)
    return landed[0]


@dataclass(frozen=True)
class Piece:
    """One file of a slot: where it is, which page it is, and `at(stem)`, the path it
    would have named for another table."""

    path: Path
    page: int
    at: Callable[[str], Path]


def pieces(game_dir: str | Path, kind: str, stem: str) -> list[Piece]:
    """Every file this kind has at this stem's tier: all its pages, and every spelling of
    each, in the folder it sits in. Empty where page 1 is missing, as the pages are not
    shown then.

    `at` keeps the folder, the token, the page number and the extension, so a move is a
    rename in place.
    """
    game_dir = Path(game_dir)
    spec = _spec_of(kind)
    found = []
    for number in _pages(game_dir, kind, stem):
        for path, token, extension in _members(game_dir, kind, stem, number):
            found.append(Piece(path, number,
                               partial(_renamed, spec, path, token, number, extension)))
    return found


def _renamed(spec: MediaSpec, path: Path, token: str | None, number: int, extension: str,
             to_stem: str) -> Path:
    return path.with_name(_name(spec, token, to_stem, number, extension))


def record_origin(game_dir: str | Path, path: Path, host: str = "user",
                  md5: str = "") -> None:
    """Note who placed the file, so the surface can say so later. `md5` is the
    publisher's own hash: while the file still matches it, the file is ours to update.
    """
    game_dir = Path(game_dir)
    info = info_files.game_info_path(game_dir)
    if not info.is_file():
        return
    try:
        from common.games.info_file import MetaConfig
        MetaConfig(str(info)).add_asset(str(path), host, md5)
    except Exception:
        # The bytes are on disk and resolution never consults the ledger, so a failure
        # here costs provenance, not the file.
        logger.exception("Placed %s but could not record where it came from", path)


def removable(game_dir: str | Path, kind: str, stem: str,
              page: int | None = None, variant: str = "table") -> list[Path]:
    """The files a `remove` at this stem takes: one page's, or with no page every file the
    kind has at this tier - the flyer is its pages."""
    game_dir = Path(game_dir)
    spec = _spec_of(kind)
    if not spec.token:
        raise RuntimeError(f"Cannot address {kind} by name")
    if page is None:
        numbers = _present(game_dir, kind, stem, variant)
        if 1 not in numbers:
            numbers = []
    else:
        pages = _pages(game_dir, kind, stem, variant)
        if not 1 <= page <= len(pages):
            raise UnplaceableError(t("error.games.no_such_page", slot=spec.label, page=page))
        numbers = [pages[page - 1]]
    return [path for number in numbers
            for path in _family_at_tier(game_dir, kind, stem, number, variant)]


def reached(game_dir: str | Path, kind: str, stem: str, variant: str = "table") -> str:
    """The stem a Remove at `stem` acts on. The folder's own tier, when it holds nothing
    and the shared default does: that file is what the game shows, and Remove takes what
    is shown. Any other stem, and a folder tier with a file in it, is returned as asked.
    """
    game_dir = Path(game_dir)
    if stem.lower() != game_dir.name.lower() or not _spec_of(kind).token:
        return stem
    if _pages(game_dir, kind, stem, variant):
        return stem
    return FIXED if next(_members(game_dir, kind, FIXED, 1, variant), None) else stem


def remove(game_dir: str | Path, kind: str, stem: str, page: int | None = None,
           variant: str = "table") -> list[str]:
    """Delete this kind's files at this stem's tier. Never touches another tier.

    With a page, only that page goes and the ones after it move down a number, so no gap
    stays on disk: removing page 1 makes page 2 the new page 1. With none, every page goes.

    Raises the OSError of the first file that will not go, and leaves the rest.
    """
    game_dir = Path(game_dir)
    before = _pages(game_dir, kind, stem, variant)
    removed = []
    for path in removable(game_dir, kind, stem, page, variant):
        path.unlink()
        # Forward-slashed: this list is an API payload, and `os.path.relpath`
        # answers in the host's separator - so the same library reported
        # "medias/bg.png" on Linux and "medias\\bg.png" on Windows.
        removed.append(path.relative_to(game_dir).as_posix())
    if page is not None:
        _renumber(game_dir, kind, stem, before[:page - 1] + before[page:])
    return removed

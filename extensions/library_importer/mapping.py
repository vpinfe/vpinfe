"""The one place that knows both vocabularies.

A reader reports what a source calls things; core stores what we call them. Everything
that turns one into the other is here, once, because a mapping decided in four places is
four things to keep in step.

The kind names are asked of core rather than written down, so a build that gains a media
kind does not leave a stale list behind here.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .source import SourceGame, SourceMedia

REPLACEMENT = "�"

# PinballY files a flyer's pages in a folder each, and this is the order it shows them in.
FLYER_PAGES = ("Front", "Inside1", "Inside2", "Inside3", "Inside4", "Inside5", "Inside6", "Back")

# A PinballX media folder is one of our kinds. The pairs are unambiguous in this
# direction: the source files a still and a moving version of one subject in two folders,
# and we hold those as two kinds, so each folder has exactly one answer.
PINBALLX_KINDS = {
    "Table Images": "playfield",
    "Table Videos": "playfield_video",
    "Backglass Images": "backglass",
    "Backglass Videos": "backglass_video",
    "DMD Images": "scoreview",
    "DMD Videos": "scoreview_video",
    "Topper Images": "topper",
    "Topper Videos": "topper_video",
    "Real DMD Images": "real_dmd",
    "Real DMD Color Images": "real_dmd_color",
    "Real DMD Videos": "real_dmd_video",
    "Real DMD Color Videos": "real_dmd_color_video",
    "FullDMD Videos": "full_dmd_video",
    "Instruction Cards": "instruction_card",
    **{f"Flyer Images/{page}": "flyer" for page in FLYER_PAGES},
    "Wheel Images": "wheel",
    "Table Audio": "audio",
    "Launch Audio": "audio_launch",
    "Logos": "logo",
}

# EmulationStation names the file on the game rather than filing it in a folder, so the
# element is the kind. Three of them have an answer here; a thumbnail is a smaller copy of
# a picture we already take, and fanart has no slot, so both are reported rather than put
# somewhere approximate.
EMULATIONSTATION_KINDS = {
    "image": "playfield",
    "video": "playfield_video",
    "marquee": "wheel",
}

# Popper files a folder per kind under the emulator's media directory, and unlike
# PinballX it does not split a still from a moving one - a PlayField folder holds both,
# and which we store it as follows the file's own extension.
POPPER_KINDS = {
    "PlayField": "playfield",
    "BackGlass": "backglass",
    "DMD": "scoreview",
    "Wheel": "wheel",
    "Topper": "topper",
    "Loading": "loading",
    "Menu": "full_dmd",
    "GameInfo": "flyer",
    "GameHelp": "instruction_card",
    "Audio": "audio",
    "AudioLaunch": "audio_launch",
}

# Where a source keeps stills and video together, the extension decides which of ours it
# is. Only the kinds that have both.
MOVING = {
    "playfield": "playfield_video",
    "backglass": "backglass_video",
    "scoreview": "scoreview_video",
    "topper": "topper_video",
    "full_dmd": "full_dmd_video",
}
VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mkv", ".avi", ".mov", ".webm", ".f4v"})
# These two kinds take pictures only, and a source's folder for them can hold a clip or a
# Flash file beside the pictures. They are also the two kinds with pages.
PICTURES_ONLY = frozenset({"flyer", "instruction_card"})
NOT_A_PICTURE = VIDEO_SUFFIXES | {".swf", ".wmv", ".mpg", ".mpeg", ".flv", ".ogv", ".3gp"}

KINDS_BY_SOURCE = {
    "pinballx": PINBALLX_KINDS,
    "folders": PINBALLX_KINDS,
    "popper": POPPER_KINDS,
    "emulationstation": EMULATIONSTATION_KINDS,
}


WHEELS = "wheel"
LOGOS = "logo"
WHEELS_AS = (WHEELS, LOGOS)


def media_for(source_id: str, game: SourceGame, known: tuple[str, ...],
              wheels_as: str = WHEELS) -> list[tuple[str, str]]:
    """(kind, path) for each file placed. A kind with pages has all of them, in page order,
    so a file's page is its place among those of its kind; any other kind has one file."""
    return _chosen(source_id, game, known, wheels_as)[0]


def held_back(source_id: str, game: SourceGame, known: tuple[str, ...],
              wheels_as: str = WHEELS) -> list[tuple[str, str, str]]:
    """(source folder, path, why) for each file of a kind we have a slot for that is not
    placed: `pages` for a later file of a kind that has no pages, `videos` for a clip or a
    Flash file in a folder of a kind that takes pictures only."""
    return _chosen(source_id, game, known, wheels_as)[1]


def _kind_of(source_id: str, item: SourceMedia, wheels_as: str, holds_logos: bool) -> str:
    """Wheel images stay wheels where the game has logos of its own."""
    kind = KINDS_BY_SOURCE.get(source_id, {}).get(item.source_kind, "")
    return LOGOS if kind == WHEELS and wheels_as == LOGOS and not holds_logos else kind


def _holds_logos(source_id: str, game: SourceGame) -> bool:
    table = KINDS_BY_SOURCE.get(source_id, {})
    return any(table.get(item.source_kind) == LOGOS for item in game.media)


def folder_of(game: SourceGame, path: str) -> str:
    """The source folder a game's file was found in."""
    return next((item.source_kind for item in game.media if item.path == path), "")


def _chosen(source_id: str, game: SourceGame, known: tuple[str, ...], wheels_as: str = WHEELS
            ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    holds_logos = _holds_logos(source_id, game)
    forms = _name_forms(game)
    by_kind: dict[str, list[SourceMedia]] = {}
    held: list[tuple[str, str, str]] = []
    for item in game.media:
        kind = _moving_form(_kind_of(source_id, item, wheels_as, holds_logos), item.path)
        if not kind or kind not in known:
            continue
        if kind in PICTURES_ONLY and Path(item.path).suffix.lower() in NOT_A_PICTURE:
            held.append((item.source_kind, item.path, "videos"))
            continue
        by_kind.setdefault(kind, []).append(item)
    placed = []
    for kind, items in by_kind.items():
        pages = _pages(forms, items, one_to_a_page=kind in PICTURES_ONLY)
        if kind not in PICTURES_ONLY:
            pages, later = pages[:1], pages[1:]
            held += [(one.source_kind, one.path, "pages") for one in later]
        placed += [(kind, one.path) for one in pages]
    return placed, held


_NOT_IN_A_FILE_NAME = str.maketrans("", "", '<>:"/\\|?*')


def _name_forms(game: SourceGame) -> list[str]:
    """What a source may have filed a game's files under, longest first: the key and the
    display name, as written and with the characters a Windows file name cannot hold
    dropped."""
    forms = set()
    for name in (game.key, game.display_name):
        written = unicodedata.normalize("NFC", name).lower().strip()
        forms.add(written)
        forms.add(" ".join(written.translate(_NOT_IN_A_FILE_NAME).split()))
    return sorted((one for one in forms if one), key=len, reverse=True)


def _pages(forms: list[str], items: list[SourceMedia], one_to_a_page: bool = True
           ) -> list[SourceMedia]:
    """A kind's files in page order, one for each page unless `one_to_a_page` is False.

    A page is told by where its folder comes in the source's own order and what follows
    the game's name: nothing means the first, a number or a space and a number is that
    page, and anything else sorts after the numbered ones, by what it says. A file whose
    name carries none of the game's comes last. Numbers compare as numbers, and zeros in
    front of one do not make it another page. Two files for one page are one, and the
    system's own beats a shared folder's, then the path settles it, so the order never
    depends on how a folder was listed.
    """
    if not one_to_a_page:
        return sorted(items, key=lambda one: (_page_of(forms, one), one.shared, one.path))
    chosen: dict[tuple[int, int, int, str], SourceMedia] = {}
    for item in sorted(items, key=lambda one: (one.shared, one.path)):
        chosen.setdefault(_page_of(forms, item), item)
    return [chosen[page] for page in sorted(chosen)]


def _page_of(forms: list[str], item: SourceMedia) -> tuple[int, int, int, str]:
    stem = unicodedata.normalize("NFC", Path(item.path).stem).lower()
    folder = item.source_kind.removeprefix("Flyer Images/")
    place = FLYER_PAGES.index(folder) if folder in FLYER_PAGES else 0
    rest = next((stem[len(name):] for name in forms if stem.startswith(name)), None)
    if rest is None:
        return (place, 3, 0, stem)
    rest = rest.strip()
    if not rest:
        return (place, 0, 0, "")
    if rest.isascii() and rest.isdigit():
        return (place, 1, int(rest), "")
    return (place, 2, 0, rest)


def _moving_form(kind: str, path: str) -> str:
    """The video kind where the file is one and the source did not say.

    PinballX files a still and a moving one apart, so its folder is the answer. Popper
    puts both in one folder, so the extension is - and a playfield video landing in the
    playfield slot would be a video where an image is expected.
    """
    if kind not in MOVING:
        return kind
    return MOVING[kind] if Path(path).suffix.lower() in VIDEO_SUFFIXES else kind


def unmapped_kinds(source_id: str, game: SourceGame, known: tuple[str, ...],
                   wheels_as: str = WHEELS) -> list[str]:
    """What the source held that we have nowhere to put, so it can be said out loud."""
    holds_logos = _holds_logos(source_id, game)
    return sorted({item.source_kind for item in game.media
                   if _moving_form(_kind_of(source_id, item, wheels_as, holds_logos),
                                   item.path) not in known})


def left_behind(source_id: str, games: list[SourceGame], known: tuple[str, ...],
                wheels_as: str = WHEELS, off: tuple[str, ...] = ()
                ) -> dict[tuple[str, str], int]:
    """Files that are not placed, counted by (source folder, why) across `games`.

    `why` is "" for a folder we have no slot for, `off` for a folder of a kind the library
    has switched off, and `pages` or `videos` as `held_back` gives them. A shared folder is
    read once per system, so a file is counted once however many systems list it.
    """
    seen: set[str] = set()
    counts: dict[tuple[str, str], int] = {}
    for game in games:
        holds_logos = _holds_logos(source_id, game)
        unplaced = []
        for item in game.media:
            kind = _moving_form(_kind_of(source_id, item, wheels_as, holds_logos), item.path)
            if kind not in known:
                unplaced.append((item.source_kind, item.path, "off" if kind in off else ""))
        for folder, path, why in [*unplaced, *held_back(source_id, game, known, wheels_as)]:
            if path in seen:
                continue
            seen.add(path)
            counts[(folder, why)] = counts.get((folder, why), 0) + 1
    return dict(sorted(counts.items()))


def folder_name(game: SourceGame) -> str:
    """What to call the game folder, with U+FFFD never in it.

    A name carrying it - a source written in one encoding and read as another - is mended
    from the game's key or its file name where one says what it was, and has the mark
    dropped where none does. A share can store the mark differently from how it was sent,
    and the folder can then not be read back by name.
    """
    return _mended(_named(game), (game.key, Path(game.table_file).stem))


def _named(game: SourceGame) -> str:
    """What the source shows a person, where it has one: PinballX's is already
    "Title (Manufacturer Year)", which is our own convention arrived at independently, and
    taking it whole keeps a converted library recognizable to whoever converted it.

    Where the source has no display name of its own it is built the way our own import
    builds one, out of the machine's name and what is known about it. The filename stem is
    the last resort, and only because something has to be.
    """
    if game.display_name.strip():
        return game.display_name.strip()

    title = game.title.strip()
    if not title:
        return game.key.strip()
    maker, year = game.manufacturer.strip(), game.year.strip()
    if maker and year:
        return f"{title} ({maker} {year})"
    if maker or year:
        return f"{title} ({maker or year})"
    return title


def _mended(name: str, references: tuple[str, ...]) -> str:
    name = unicodedata.normalize("NFC", name)
    if REPLACEMENT not in name:
        return name
    pieces = [re.escape(piece) for piece in re.split(f"{REPLACEMENT}+", name)]
    pattern = re.compile(".+?".join(pieces), re.DOTALL)
    for reference in references:
        reference = unicodedata.normalize("NFC", reference.strip())
        if reference and REPLACEMENT not in reference and pattern.fullmatch(reference):
            return reference
    return " ".join(name.replace(REPLACEMENT, "").split())


def _title_from(game: SourceGame, text: str = "") -> str:
    """The machine's name, out of a string that carries more than it.

    PinballX has no title of its own - it holds "Attack from Mars (Bally 1995)" and
    nothing else - so an import that carried it straight across would put the
    manufacturer and the year in the name column beside the columns that already hold
    them, on every imported game and on none of the matched ones.

    Only where the trailing bracket is exactly the manufacturer and year the source also
    gave, so this is a removal of something known rather than a guess at what a name
    ends with. Anything else is left whole.
    """
    described = (text or game.display_name or "").strip()
    maker, year = game.manufacturer.strip(), game.year.strip()
    if not described or not maker or not year:
        return ""
    suffix = f"({maker} {year})"
    if described.endswith(suffix):
        return described[:-len(suffix)].strip()
    return ""


def details_for(game: SourceGame) -> dict:
    """What the source knew about the machine, in our field names.

    Only what it actually said. Sending a field the source left blank would write an
    empty value over nothing, which reads the same on disk but says we examined it and
    found none - and for an unmatched import nobody examined anything.
    """
    found = {
        # Stripped whichever it came from. A source's own title field is not
        # necessarily a bare title: Popper's is usually the whole
        # "Title (Manufacturer Year)" and is sometimes only the title, in one database.
        "title": _title_from(game, game.title) or game.title or _title_from(game),
        "manufacturer": game.manufacturer,
        "year": game.year,
        "type": game.game_type,
        "ipdb_id": game.ipdb_id,
    }
    said: dict[str, str | list[str]] = {
        name: value for name, value in found.items() if str(value or "").strip()}
    if game.themes:
        said["themes"] = list(game.themes)
    return said

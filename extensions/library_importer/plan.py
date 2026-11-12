"""What an import would do, before it does any of it.

Two questions this answers, and they are the same question asked twice: where does each
kind of thing come from, and what is already here.

**Every source is an option with a derived default.** A reader works out where the tables
and the artwork are; the user can say otherwise, and can leave one empty. Empty means that
kind is not imported - stated rather than implied, because "no ROMs imported" should be
something somebody chose and can see they chose.

**And an import can be run more than once.** A first pass fails partway, or somebody
imports the games now and the ROMs when the old machine is back on the network. So this
says what is new and what is already here before anything is written, and an existing game
is left alone unless the user says to fill in what it is missing.
"""

from __future__ import annotations

import logging
import os
import shutil
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.extensions.contract import words

from . import finding, folders, gamestats, mapping, popper
from .saved import Saved
from .source import Note, SourceGame, SourceLibrary, SourceSystem

t = words("library_importer")
logger = logging.getLogger(__name__)

# The kinds of thing an import can bring, in the order they are asked about, by the key
# the wizard calls the field.
SOURCES = ("tables", "media", "roms", "altdata", "music", "pups", "user", "history")

# The kinds whose folder field says more than its label does.
EXPLAINED = frozenset({"altdata", "music", "user"})

# Derived and deliberately not offered. The reading works; there is nowhere settled to
# put what it reads, and a field that carries nothing is a promise the code cannot keep.
#
# Registry settings have no destination at all on this platform, which is upstream's
# decision rather than an omission: VPinMAME's per-ROM values live in memory, set by a
# table's own script and falling back to a table of Windows defaults. Nothing persists
# them, so anything written would be read by nothing.
NOT_YET = ("registry",)

# What VPinMAME keeps beside its ROM folder, by the kind core places it as.
SCORE_FOLDERS = ("nvram", "cfg")

# What a folder of sound and color sets holds: one folder of sets per kind.
ALT_FOLDERS = ("altsound", "altcolor", "serum")

# Whose name a matched game is called by: the catalog's (`vps`) or the source's (`source`).
# A game nothing matched keeps the source's either way.
NAMES = ("vps", "source")
DEFAULT_NAMES = "vps"

# Whose details a matched game's `.info` holds: only the catalog's (`vps`), the source's
# (`source`), or the source's for the fields chosen (`some`).
DETAILS = ("vps", "source", "some")
DEFAULT_DETAILS = "vps"
DETAIL_FIELDS = ("title", "manufacturer", "year", "type")

RENAMES_SHOWN = 3


@dataclass
class Source:
    """Where one kind of thing comes from, and how we came to think so."""

    key: str
    label: str
    help: str
    path: str = ""
    derived: bool = False
    # Looked for and not there, as against left empty on purpose: the summary tells the
    # two apart.
    not_found: bool = False

    @property
    def active(self) -> bool:
        return bool(self.path)


# The reasons a game is left for the person to settle, each with a line in the catalog under
# `why.`. A game found in no catalog has no reason, only no candidate.
WHY = ("two_entries", "ipdb_differs")


@dataclass
class Match:
    """One game the source holds, against what the library already has."""

    key: str
    folder: str
    game_id: str = ""
    how: str = ""
    # What the look found for a game made new: the catalog entry it is, or why none was
    # settled. Both empty where nothing was looked for.
    vps_id: str = ""
    reason: str = ""
    found_by: str = ""
    # Where the entry's name and the source's part, for the page to show and count: the
    # folder the source would have made where the entry's name is used instead, and the
    # details the two disagree on.
    was: str = ""
    # The folder the entry's name would make where it is not the source's, whichever of
    # the two the games are called by: what the Names page counts and shows.
    called: str = ""
    differs: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    # Counted here because here is where the game is in hand. Looking them up later by
    # key cannot work: a real library held two entries with byte-identical keys, and a
    # dict keyed on them silently counts one.
    tables: int = 0
    media: int = 0
    companions: int = 0
    history: int = 0
    # What a fill measures the game against: the game file's name, the kind of each
    # artwork file, and what the source remembers.
    table_name: str = ""
    kinds: tuple[str, ...] = ()
    played: gamestats.Played | None = None
    # The kind of machine the look settled the entry as (SS, EM, ME), and the ROM the
    # game file names once that has been read.
    machine: str = ""
    rom: str = ""
    # What the Tables page calls the game by, where it is among the source's games (see
    # `row_keys`), whether the source hides it, and whether this run acts on it. A game
    # already here is acted on only where it was ticked, and then it is filled in.
    row: str = ""
    name: str = ""
    hidden: bool = False
    taken: bool = False
    # The entry the look settled on, or the one it would offer: name, manufacturer, year.
    candidate: dict[str, Any] = field(default_factory=dict)

    @property
    def existing(self) -> bool:
        return bool(self.game_id)


@dataclass(frozen=True)
class Held:
    """What the library already holds of a game a fill is aimed at."""

    tables: frozenset[str] = frozenset()
    media: frozenset[str] = frozenset()
    rom: str = ""
    user: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Missing:
    """The part of a game's play history that a fill adds: only what VPinFE has none of,
    so nothing somebody has played, rated or tagged here since is overwritten."""

    counters: dict[str, int] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    rating: int = 0
    favorite: bool = False

    @property
    def counts(self) -> bool:
        """Whether it adds what the report counts as history: counters and tags."""
        return bool(self.counters or self.tags)


def missing_history(played: gamestats.Played, user: dict[str, Any]) -> Missing:
    """What of `played` the library, holding `user`, does not have yet."""
    counters = {name: value for name, value in (
        ("play_count", played.play_count),
        ("play_time_seconds", played.play_time_seconds),
        ("last_played", played.last_played))
        if value and not user.get(name)}
    held_tags = {one.casefold() for one in user.get("tags") or []}
    return Missing(counters=counters,
                   tags=tuple(one for one in played.tags if one.casefold() not in held_tags),
                   rating=0 if user.get("rating") else int(played.rating or 0),
                   favorite=bool(played.favorite and not user.get("favorite")))


@dataclass(frozen=True)
class Adds:
    """What ticking a game that is already here would bring it."""

    tables: int = 0
    pictures: int = 0
    rom: bool = False
    history: bool = False

    @property
    def nothing(self) -> bool:
        return not (self.tables or self.pictures or self.rom or self.history)


def adds(match: Match, held: Held, roms_dir: str = "", tables: bool = True,
         media: bool = True) -> Adds:
    """What a fill of `match` brings, given what its game holds: the same test `expected`
    counts by, so the page and the report agree. `tables` and `media` say whether those
    sources are being imported at all."""
    brings_table = bool(match.tables) and match.table_name.lower() not in held.tables
    return Adds(
        tables=match.tables if tables and brings_table else 0,
        pictures=sum(1 for kind in match.kinds if kind not in held.media) if media else 0,
        rom=bool(roms_dir and match.rom and held.rom != match.rom
                 and finding.rom_file(roms_dir, match.rom) is not None),
        history=bool(match.played and missing_history(match.played, held.user).counts))


@dataclass
class Plan:
    sources: list[Source] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    history: dict[str, gamestats.Played] = field(default_factory=dict)
    notes: list[Note] = field(default_factory=list)
    # By game id, for a fill: set by `measure_fill`. A game not in it is counted whole.
    held: dict[str, Held] = field(default_factory=dict)
    # The catalog the look was made in: `enabled` is the Match new games switch, `ready`
    # that there was a catalog to look in.
    catalog: dict = field(default_factory=dict)
    # Whose name a matched game's folder carries and whose details its `.info` holds; see
    # `NAMES` and `DETAILS`. `detail_fields` is the chosen few where details is `some`.
    names: str = DEFAULT_NAMES
    details: str = DEFAULT_DETAILS
    detail_fields: tuple[str, ...] = ()
    # Whether the source's manufacturer logos are offered to the makers here.
    logos: bool = True
    # What the source's wheel images are kept as (`mapping.WHEELS_AS`), logos then showing
    # in place of wheels; whether missing art is fetched for the games this run touches.
    wheels_as: str = mapping.WHEELS
    fetch_media: bool = True
    # Whether high scores and game settings come with a game. They sit with the ROM and
    # in the files its script saved, so no source folder stands for them.
    scores: bool = True

    @property
    def new(self) -> list[Match]:
        return [one for one in self.matches if not one.existing]

    @property
    def already(self) -> list[Match]:
        return [one for one in self.matches if one.existing]

    @property
    def making(self) -> list[Match]:
        """The games this run makes: new, and not left out."""
        return [one for one in self.matches if one.taken and not one.existing]

    @property
    def filling(self) -> list[Match]:
        """The games already here that this run adds to."""
        return [one for one in self.matches if one.taken and one.existing]

    def source(self, key: str) -> Source | None:
        return next((one for one in self.sources if one.key == key), None)

    def renamed(self, shown: int = RENAMES_SHOWN) -> tuple[int, list[tuple[str, str]]]:
        """How many games the entry's name would change the folder of, with the first few
        as (as the source named it, as the entry would). One per folder: the games that
        share a folder are one game."""
        seen: set[str] = set()
        changed: list[tuple[str, str]] = []
        for one in self.making:
            if one.called and one.called.lower() not in seen:
                seen.add(one.called.lower())
                changed.append((one.was or one.folder, one.called))
        return len(changed), changed[:shown]

    def differing(self) -> dict[str, int]:
        """How many matched games the entry and the source disagree on, by detail. A game
        that shares its folder with an earlier one is not counted twice."""
        seen: set[str] = set()
        counts = dict.fromkeys(DETAIL_FIELDS, 0)
        for one in self.making:
            if not one.vps_id or one.folder.lower() in seen:
                continue
            seen.add(one.folder.lower())
            for name in one.differs:
                counts[name] += 1
        return counts


# What the report counts, in the order it reads. The same keys come back from the run,
# so expected and actual are one shape and a difference is a subtraction rather than a
# comparison somebody has to make by eye.
COUNTS = ("games", "tables", "media", "companions", "history", "matched")


def expected(plan: Plan) -> dict:
    """What this import should produce, counted before it runs.

    Counted the way the run counts, not the way the source reads, because those differ
    in two places and a report that ignores either reads as a shortfall for working
    correctly. Several builds of one machine become one game, and the first of them brings
    its artwork; a later one brings only the kinds the game is still missing.

    A source that is not set contributes nothing and says zero rather than being left
    out: "no artwork" is a number somebody chose, and an absent row reads as an oversight.
    """
    wanted = _wanted(plan)
    has = {source.key: bool(source.active) for source in plan.sources}

    folders, tables, media, companions, history, matched = set(), 0, 0, 0, 0, 0
    filled: dict[str, set[str]] = {}
    for one in wanted:
        first = one.folder.lower() not in folders
        folders.add(one.folder.lower())
        # A game being filled counts what the fill adds, which is what it is missing.
        held = plan.held.get(one.game_id) if one.existing else None
        brings_table = bool(one.tables) and not (
            held and one.table_name.lower() in held.tables)
        if has.get("tables") and brings_table:
            tables += one.tables
            # They ride with the table, so they arrive only where it does.
            companions += one.companions
        # Artwork rides with the game: the first build brings what it has, and a later
        # one only the slots still empty, which is what the run puts with no replace.
        if has.get("media"):
            if first:
                media += (sum(1 for kind in one.kinds if kind not in held.media)
                          if held else one.media)
                filled[one.folder.lower()] = set(one.kinds) | (
                    set(held.media) if held else set())
            else:
                taken = filled[one.folder.lower()]
                still = [kind for kind in one.kinds if kind not in taken]
                media += len(still)
                filled[one.folder.lower()] |= set(still)
        # History rides with the game, and the game is made once. So does its match, and
        # a game filled in is never matched: its details may be a person's own.
        if first:
            matched += int(bool(one.vps_id) and not one.existing)
            history += (int(bool(one.played and missing_history(
                one.played, held.user).counts)) if held else one.history)

    return {"games": len(folders), "tables": tables, "media": media,
            "companions": companions, "history": history, "matched": matched, "roms": 0}


def not_matched(plan: Plan) -> dict[str, int]:
    """The games this run makes new that the look could not settle, counted by why.

    One count per game made, as `expected` counts them: builds sharing a folder are one.
    """
    found: dict[str, int] = {}
    folders: set[str] = set()
    for one in _wanted(plan):
        first = one.folder.lower() not in folders
        folders.add(one.folder.lower())
        if first and not one.existing and one.reason:
            found[one.reason] = found.get(one.reason, 0) + 1
    return found


def measure_fill(plan: Plan, read: Callable[[str], Held]) -> None:
    """Read what each game a fill is aimed at already holds, so `expected` can count what
    the fill adds. A game that cannot be read is counted whole, which only over-counts."""
    plan.held.clear()
    for one in plan.filling:
        try:
            plan.held[one.game_id] = read(one.game_id)
        except Exception:
            logger.warning("Could not read %s to measure a fill", one.folder, exc_info=True)


def _wanted(plan: Plan) -> list[Match]:
    """The games this run will act on, which is not every game the source holds."""
    return [one for one in plan.matches if one.taken]


def going(library: SourceLibrary, plan: Plan,
          systems: list[str] | None = None, offered: bool = False) -> list[SourceGame]:
    """The source games this run will act on, in the order the source holds them.

    Paired with the plan's matches by position, which is how they were made: a lookup by
    key would merge the entries that share one. `offered` adds the games already in the
    library, which ticking would act on.
    """
    return [game for game, _match in pairs(library, plan, systems, offered)]


def pairs(library: SourceLibrary, plan: Plan, systems: list[str] | None = None,
          offered: bool = False) -> list[tuple[SourceGame, Match]]:
    """`going`, each game with the match it was made into."""
    return [(game, match)
            for game, match in zip(_games(library, systems), plan.matches, strict=True)
            if match.taken or (offered and match.existing)]


def systems_going(library: SourceLibrary, games: list[SourceGame]) -> list[SourceSystem]:
    """The systems at least one of these games belongs to, in the order the source holds
    them. A system somebody deselected, or one with nothing left to import, is not
    among them, and nothing about it is to be touched."""
    going_ids = {id(one) for one in games}
    return [system for system in library.systems
            if any(id(one) in going_ids for one in system.games)]


def _beside(companions_of: Callable[..., Any], table_file: str) -> tuple[str, ...]:
    """What travels with a game file, or nothing where the source lists one that is not
    there. A frontend's list outlives the files it names; that game fails when it is
    imported, and the rest of the check does not stop for it."""
    try:
        return tuple(companions_of(table_file))
    except FileNotFoundError:
        return ()


def bytes_to_copy(games: list[SourceGame], source_id: str, kinds: tuple[str, ...],
                  companions_of: Callable[..., Any], move: bool = False,
                  sized: Callable[[list[Path]], dict[Path, int]] | None = None,
                  roms: dict[str, str] | None = None, roms_dir: str = "",
                  named: dict[str, tuple[str, ...]] | None = None,
                  saved: Saved | None = None,
                  real: Callable[[str], str] | None = None,
                  wheels_as: str = mapping.WHEELS) -> int:
    """What a run writes, in bytes: the game files and what rides with them, unless they
    are moved, the artwork, the ROM set each game's file names where `roms_dir` has it
    with its scores and settings, and the saved files its script names.

    A ROM is copied for every game that names it, moved or not, so two tables on one ROM
    count it twice. A second build of one machine does the same, which counts a ROM the
    run will find already there: the total only ever errs high. Sound banks, music and
    PUP packs are not counted, being folders of sets rather than files.

    `roms` is the ROM each game file names, by path, as `ExtensionGames.roms_of` read it,
    and `named` the saved names its script mentions, as `reads_of` read them. `real` turns
    a name the script asks for into the ROM set it loads, where the source keeps a map.
    `sized` measures a list of files at once, for a caller that can do it faster than one
    after another.
    """
    wanted: list[Path] = []
    for game in games:
        if game.table_file and not move:
            table = Path(game.table_file)
            wanted.append(table)
            wanted += [table.parent / name
                       for name in _beside(companions_of, game.table_file)]
        wanted += [Path(path) for _kind, path
                   in mapping.media_for(source_id, game, kinds, wheels_as)]
        rom = (roms or {}).get(game.table_file, "") if game.table_file else ""
        rom = (real(rom) if real and rom else "") or rom
        found = finding.rom_file(roms_dir, rom) if rom and roms_dir else None
        if found is not None:
            wanted.append(found)
            wanted += [one for folder in score_folders(roms_dir).values()
                       if (one := finding.rom_file(folder, rom)) is not None]
        if saved and game.table_file:
            wanted += saved.files_for((named or {}).get(game.table_file, ()))
    sizes = (sized or _measured)(wanted)
    return sum(sizes.get(path, 0) for path in wanted)


def attach_roms(plan: Plan, library: SourceLibrary, roms: dict[str, str],
                systems: list[str] | None = None,
                real: Callable[[str], str] | None = None) -> None:
    """Put on each match the ROM set its game file names, by position as `going` pairs
    them: the one the source's name map points to where `real` knows the name."""
    for game, match in zip(_games(library, systems), plan.matches, strict=True):
        asked = roms.get(game.table_file, "") if game.table_file else ""
        match.rom = (real(asked) if real and asked else "") or asked


def missing_roms(plan: Plan) -> int:
    """How many games this run makes that need a ROM the ROM folder does not hold.

    Only a game the look settled as solid state: an electromechanical machine plays
    without one, and the ROM name its script gives is a name for something else. A game
    the look could not settle is not counted either, because nothing says it needs one.
    One count per game made, as `expected` counts them.
    """
    roms = plan.source("roms")
    if roms is None or not roms.active:
        return 0
    folders_seen: set[str] = set()
    short = 0
    for one in _wanted(plan):
        first = one.folder.lower() not in folders_seen
        folders_seen.add(one.folder.lower())
        if (first and not one.existing and one.machine == "SS" and one.rom
                and finding.rom_file(roms.path, one.rom) is None):
            short += 1
    return short


def _measured(paths: list[Path]) -> dict[Path, int]:
    return {path: file_bytes(path) for path in dict.fromkeys(paths)}


def tables_folder(system: SourceSystem) -> str:
    """The folder a system's game files sit in: the one they all share, else the folder
    the source names for them.

    A source can name a folder that holds no table itself - EmulationStation's is its own
    root, and its tables are wherever each game says - so the files are asked first.
    """
    parents = {str(Path(game.table_file).parent) for game in system.games if game.table_file}
    return parents.pop() if len(parents) == 1 else system.tables_dir


def file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


_SIZE_UNITS = ("bytes", "kilobytes", "megabytes", "gigabytes", "terabytes")


def size_of(count: int) -> str:
    """A byte count as it reads on screen: powers of 1024, bytes whole, the rest to one
    decimal. The same figures core shows elsewhere; an extension cannot import core's, so
    the units are this extension's own words."""
    value, step = float(count), 0
    while value >= 1024 and step < len(_SIZE_UNITS) - 1:
        value /= 1024
        step += 1
    return t(f"size.{_SIZE_UNITS[step]}", value=f"{value:.0f}" if step == 0 else f"{value:.1f}")


def room_at(path: str) -> tuple[int, str]:
    """Free bytes where `path` would be written, and what to call that place.

    A folder that is not made yet is asked as its nearest parent that is. The place is
    the drive on Windows and the mount point elsewhere, which is what a person reads when
    something runs out.
    """
    here = Path(path).expanduser()
    while not here.exists() and here != here.parent:
        here = here.parent
    try:
        free = shutil.disk_usage(here).free
    except OSError:
        return -1, ""
    if here.drive:
        return free, here.drive
    while not os.path.ismount(here) and here != here.parent:
        here = here.parent
    return free, str(here)


def same_drive(first: str, second: str) -> bool:
    """Whether two places are on one filesystem, each asked as its nearest parent that is
    there, so a folder not made yet counts as where it will be made."""
    def device(path: str) -> int:
        here = Path(path).expanduser()
        while not here.exists() and here != here.parent:
            here = here.parent
        try:
            return here.stat().st_dev
        except OSError:
            return -1

    return device(first) == device(second) != -1


def against(expected_counts: dict, actual: dict) -> list[dict]:
    """Expected beside actual, with the difference, for the report.

    Both are shown even where they agree: a row that only appears when something went
    wrong makes a clean import look like a report with things missing from it.
    """
    rows = []
    for key in COUNTS:
        want = int(expected_counts.get(key, 0) or 0)
        got = int(actual.get(key, 0) or 0)
        rows.append({"key": key, "label": t(f"count.{key}"), "expected": want,
                     "actual": got, "short": want - got})
    return rows


def derive_sources(library: SourceLibrary, chosen: dict | None = None) -> list[Source]:
    """Where each kind of thing appears to live, and whatever the user said instead.

    Derived first so the common case is one press. A value the user typed always wins,
    including an empty one - clearing a source is how somebody says "not that".
    """
    chosen = chosen or {}
    finding.forget()
    tables = next((one.tables_dir for one in library.systems if one.tables_dir), "")
    media = ""
    for system in library.systems:
        for game in system.games:
            own = next((item for item in game.media if not item.shared), None)
            if own:
                media = str(Path(own.path).parent.parent)
                break
        if media:
            break
    if not media:
        # Only shared folders were found. Their root is the media folder itself, up
        # past the source folder's own name: `Flyer Images/Front` is two levels.
        shared = next((item for system in library.systems for game in system.games
                       for item in game.media), None)
        if shared:
            media = str(Path(shared.path).parents[len(Path(shared.source_kind).parts)])
    # Nothing derives a registry export: a share does not carry one unless somebody
    # exported it on purpose, so this stays empty until it is pointed at.
    # VPinMAME keeps its own folders beside the emulator, not in it - a real machine
    # recorded `rompath` as `<install>\VPinMame\roms`, and the sound and color banks
    # sit beside that. So the emulator's own directory is where the search starts rather
    # than where it ends, and an empty answer leaves the field for somebody to fill.
    working = next((one.working_path for one in library.systems if one.working_path), "")
    # Where the emulator's folders may be: beside it, or beside the tables, or up from
    # either, because an install lays them out in more than one way.
    roots = _roots(working, tables, library.root)
    starts = [one for one in (working, tables, library.root) if one]
    guessed = {"tables": tables, "media": media,
               "roms": (_first(roots, (("VPinMAME", "roms"), ("PinMAME", "roms")))
                        or _first(starts, (("roms",),))),
               "altdata": _first(roots, (("VPinMAME",), ()),
                                 holding=ALT_FOLDERS),
               "music": _not_home(_first(roots, (("Music",),))),
               "pups": _first(roots, (("PUPVideos",), ("PinUPSystem", "PUPVideos"))),
               "user": _first(roots, (("User",),)),
               "history": _file_at(library.root, popper.DATABASE
                                   if library.source_id == popper.SOURCE_ID
                                   else gamestats.STATS_FILE),
               "registry": ""}

    found = []
    for key in SOURCES:
        # Nothing keeps a play history for a folder of tables, so there is none to point at.
        if key == "history" and library.source_id == folders.SOURCE_ID:
            continue
        said = chosen.get(key)
        guess = str(guessed.get(key, "") or "").strip()
        path = str(said if said is not None else guess).strip()
        found.append(Source(key=key, label=t(f"kind.{key}.label"),
                            help=t(f"kind.{key}.help") if key in EXPLAINED else "", path=path,
                            derived=said is None and bool(path),
                            not_found=not path and not guess))
    return found


def score_folders(roms_dir: str) -> dict[str, str]:
    """The folders of high scores and game settings beside a ROM folder, by the kind core
    places each as. VPinMAME keeps `nvram` and `cfg` next to `roms`, and a ROM's file in
    each is named for it."""
    parent = Path(roms_dir).parent if roms_dir else None
    found = {kind: finding.child(parent, kind) if parent else None for kind in SCORE_FOLDERS}
    return {kind: str(one) for kind, one in found.items() if one is not None and one.is_dir()}


def _file_at(root: str, name: str) -> str:
    """A file beside the frontend, where it is the source for a whole kind rather than
    a folder of them."""
    if not root:
        return ""
    wanted = Path(root) / name
    try:
        return str(wanted) if wanted.is_file() else ""
    except OSError:
        return ""


def _roots(*starts: str) -> list[str]:
    """Each place an emulator's folders may sit beside: the given folders, then the
    folder above each, without repeats."""
    roots: dict[str, None] = {}
    for start in starts:
        if not start:
            continue
        roots[start] = None
        roots[str(Path(start).parent)] = None
    return list(roots)


def _first(roots: list[str], candidates: tuple[tuple[str, ...], ...],
           holding: tuple[str, ...] = ()) -> str:
    """The first folder that is actually there, under any of the roots, or nothing.

    Nothing rather than a guess: an empty field reads as "not being imported", which is
    a state somebody can see and correct. A path that does not exist reads as a promise.
    `holding` names what the folder must contain, for one that is only a folder if it
    holds something.
    """
    for root in roots:
        found = _under(root, *candidates, holding=holding)
        if found:
            return found
    return ""


def _not_home(found: str) -> str:
    """Nothing where the folder is the person's own Music, which is theirs and not a game's."""
    return "" if found and Path(found).parent == Path.home() else found


def _under(root: str, *candidates: tuple[str, ...], holding: tuple[str, ...] = ()) -> str:
    """The first of these under `root` that is there, in any case, or nothing."""
    if not root:
        return ""
    for parts in candidates:
        here: Path | None = Path(root)
        for part in parts:
            here = finding.child(here, part) if here else None
        if here is None or not here.is_dir():
            continue
        if holding and not any(finding.child(here, name) for name in holding):
            continue
        return str(here)
    return ""


def match_existing(library: SourceLibrary, existing: list[dict],
                   systems: list[str] | None = None,
                   folder_name_for: Callable[[str], str] | None = None,
                   source_id: str = "",
                   kinds: tuple[str, ...] = (),
                   companions_of: Callable[..., Any] | None = None,
                   history: dict[str, gamestats.Played] | None = None,
                   looked: dict[int, dict] | None = None,
                   names: str = DEFAULT_NAMES,
                   wheels_as: str = mapping.WHEELS) -> list[Match]:
    """Which of the source's games the library already holds.

    By the folder this import would create first, because that is what a previous run of
    this import made. Then by catalog id - the source's own or the one the look found
    (`looked`, by the game's identity), which catches a game somebody has since renamed -
    a rename should not turn one game into two. Last by the name of its game file, for a
    game nothing could be matched: only where one game here holds a file of that name,
    because a name two games share says nothing about either.

    Folder names are compared with their quotes taken off. A real library held
    `\'300\' (Gottlieb 1975)` while the source called it `"300" (Gottlieb 1975)`, and
    the quote a filesystem cannot keep is the one thing separating them - matched
    literally, one machine ends up with the game twice.
    """
    by_folder = {_folded(one.get("folder_name")): one for one in existing}
    by_vps = {str(one.get("vps_id") or "").lower(): one
              for one in existing if one.get("vps_id")}
    by_file: dict[str, list[dict]] = {}
    for one in existing:
        for name in one.get("table_files") or ():
            by_file.setdefault(str(name).lower(), []).append(one)

    rows = row_keys(library)
    found = []
    for game in _games(library, systems):
        source_folder = mapping.folder_name(game)
        if folder_name_for:
            source_folder = folder_name_for(source_folder)
        answer = (looked or {}).get(id(game)) or {}
        entry = str(answer.get("vps_id") or "")
        said = answer.get("entry") or {}
        folder, was, called = source_folder, "", ""
        if entry and said.get("folder_name"):
            entry_folder = (folder_name_for(said["folder_name"]) if folder_name_for
                            else said["folder_name"])
            differs = _folded(entry_folder) != _folded(source_folder)
            called = entry_folder if differs else ""
            if names == "vps":
                folder, was = entry_folder, source_folder if differs else ""
        held = by_folder.get(_folded(folder))
        if held is None and (held := by_folder.get(_folded(source_folder))):
            # Called what is on disk, which is the name it was made under.
            folder, was, called = source_folder, "", ""
        how = "folder" if held else ""
        for wanted in (game.vps_id, entry):
            if held is None and wanted:
                held = by_vps.get(wanted.lower())
                how = "vps_id" if held else ""
        if held is None and game.table_file:
            sharing = by_file.get(Path(game.table_file).name.lower(), [])
            if len(sharing) == 1:
                held, how = sharing[0], "table_file"
        played = history_for(history or {}, game)
        pictures = mapping.media_for(source_id, game, kinds, wheels_as)
        found.append(Match(key=game.key, folder=folder,
                           game_id=str(held.get("game_id") or "") if held else "",
                           how=how, vps_id=entry, was=was, called=called,
                           differs=disagreements(game, said) if entry else (),
                           gaps=gaps(game, said) if entry else (),
                           found_by=str(answer.get("how") or ""),
                           reason=str(answer.get("reason") or ""),
                           tables=1 if game.table_file else 0,
                           table_name=(Path(game.table_file).name
                                       if game.table_file else ""),
                           kinds=tuple(kind for kind, _path in pictures),
                           played=played,
                           machine=str((answer.get("entry") or {}).get("type") or ""),
                           media=len(pictures),
                           companions=(len(_beside(companions_of, game.table_file))
                                       if companions_of and game.table_file else 0),
                           history=1 if played and played.counts_as_history else 0,
                           row=rows[id(game)], name=game.display_name or game.key,
                           hidden=game.hidden, candidate=_candidate(answer)))
    return found


def row_keys(library: SourceLibrary) -> dict[int, str]:
    """What each game of the source is called on the Tables and Matches pages, by the
    game's identity: its key, and for a later game with the same key `#2`, `#3`.

    Over every game of the source, not only the systems chosen, so choosing another system
    does not rename the rows the person has already answered about."""
    seen: dict[str, int] = {}
    keys: dict[int, str] = {}
    for system in library.systems:
        for game in system.games:
            seen[game.key] = seen.get(game.key, 0) + 1
            keys[id(game)] = (game.key if seen[game.key] == 1
                              else f"{game.key}#{seen[game.key]}")
    return keys


def _candidate(answer: dict) -> dict[str, Any]:
    """The entry the look settled on, or the first it would offer."""
    entry: dict = answer.get("entry") or next(iter(answer.get("candidates") or ()), {})
    if not entry.get("name"):
        return {}
    return {key: entry.get(key) for key in ("name", "manufacturer", "year")}


def _entry_says(entry: dict, name: str) -> str:
    return _folded(entry.get("name" if name == "title" else name))


def disagreements(game: SourceGame, entry: dict) -> tuple[str, ...]:
    """The details the source states that the catalog entry states differently."""
    stated = mapping.details_for(game)
    return tuple(name for name in DETAIL_FIELDS
                 if str(stated.get(name) or "").strip() and _entry_says(entry, name)
                 and _folded(stated[name]) != _entry_says(entry, name))


def gaps(game: SourceGame, entry: dict) -> tuple[str, ...]:
    """The details the source states that the catalog entry leaves blank. Nothing else
    supplies them, so they are the source's whichever way details are chosen."""
    stated = mapping.details_for(game)
    return tuple(name for name in (*DETAIL_FIELDS, "ipdb_id")
                 if str(stated.get(name) or "").strip() and not _entry_says(entry, name))


# Quotes and the characters a filesystem substitutes for them. Stopping here on purpose:
# stripping punctuation generally would make `Taxi` and `Taxi 2` the same game, and a
# library that merges two machines is worse than one that holds a duplicate.
_QUOTES = str.maketrans("", "", "\"'‘’“”`")


def _folded(name: Any) -> str:
    """A folder name as it is compared: cased down, quotes taken off, spaces collapsed."""
    said = unicodedata.normalize("NFC", str(name or "")).translate(_QUOTES).lower()
    return " ".join(said.split())


def _games(library: SourceLibrary,
           systems: list[str] | None) -> list[SourceGame]:
    """The games in the systems being imported, which is not always all of them.

    A system somebody deselected has to drop out here rather than at the run, or what
    the summary counts and what the import does are two different libraries.
    """
    wanted = set(systems or [])
    return [game for system in library.systems
            if not wanted or system.name in wanted
            for game in system.games]


def build(library: SourceLibrary, existing: list[dict], chosen: dict | None = None,
          ticked: Sequence[str] | None = None,
          systems: list[str] | None = None,
          folder_name_for: Callable[[str], str] | None = None,
          source_id: str = "", kinds: tuple[str, ...] = (),
          companions_of: Callable[..., Any] | None = None,
          looked: dict[int, dict] | None = None,
          names: str = DEFAULT_NAMES, details: str = DEFAULT_DETAILS,
          detail_fields: list[str] | None = None,
          wheels_as: str = mapping.WHEELS) -> Plan:
    names = names if names in NAMES else DEFAULT_NAMES
    wheels_as = wheels_as if wheels_as in mapping.WHEELS_AS else mapping.WHEELS
    details = details if details in DETAILS else DEFAULT_DETAILS
    sources = derive_sources(library, chosen)
    history, notes = _history(next((one.path for one in sources if one.key == "history"),
                                   ""))
    matches = match_existing(library, existing, systems, folder_name_for, source_id, kinds,
                             companions_of, history, looked, names, wheels_as)
    wanted = None if ticked is None else {str(one) for one in ticked}
    for one in matches:
        one.taken = not one.existing if wanted is None else one.row in wanted
    return Plan(sources=sources, matches=matches, history=history, notes=notes,
                names=names, details=details, wheels_as=wheels_as,
                detail_fields=tuple(one for one in DETAIL_FIELDS
                                    if one in (detail_fields or ())))


def _history(path: str) -> tuple[dict[str, gamestats.Played], list[Note]]:
    """What the source remembers, by the name it files a game under.

    Keyed on the display name because that is what the stats file writes - the same name
    the artwork is filed under, not the table's filename. Folded, because two frontends
    disagree about case and nobody typed either of them twice.
    """
    if not path:
        return {}, []
    read = popper.read_stats if Path(path).suffix.lower() == ".db" else gamestats.read
    found, notes = read(path)
    return {_filed_as(one.name): one for one in found}, notes


def history_for(history: dict[str, gamestats.Played],
                game: SourceGame) -> gamestats.Played | None:
    """The row the source remembers this game by, if it has one."""
    return history.get(_filed_as(game.display_name))


def _filed_as(name: str) -> str:
    return name.strip().lower()

"""Turning what a source holds into entries of ours.

It creates, and it writes to the source only when told to move. Without that nothing of
the source is written to and nothing already in the library is changed, so an import that
fails partway leaves both exactly as they were apart from the folders it had already made -
and those are new, complete, and named. A move takes the game files out of where they
were, and is the one thing here that does.

Every write goes through the context. Nothing here opens a file of ours or knows where
the library is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from . import beside, cabinet, finding, logos, mapping, saved
from . import report as asks
from .plan import Held, Missing, history_for, missing_history, score_folders
from .source import Note, SourceGame, SourceLibrary

# Where a ROM set and the folders keyed on it are placed, by the kind the registry knows
# them as. A color set comes in two formats and the file decides which, so both are
# tried against what is actually there.
ALT_KINDS = {".crz": "altcolor_serum", ".cromc": "altcolor_serum",
             ".vni": "altcolor_vni", ".pal": "altcolor_vni"}


class AdoptedRow(TypedDict):
    """One game and what became of it. `joined` is only on a second build
    of a machine the run has already made a game for, `existing` on a game the library
    already held and the run filled in."""

    key: str
    name: str
    game_id: str
    table: bool
    media: int
    companions: int
    roms: int
    altdata: int
    music: int
    pups: int
    scores: int
    history: int
    skipped_media: list[str]
    refused: list[str]
    error: Note
    matched: NotRequired[bool]
    joined: NotRequired[bool]
    existing: NotRequired[bool]
    table_id: NotRequired[str]
    rom: NotRequired[str]
    rom_there: NotRequired[bool]
    wheel: NotRequired[bool]


@dataclass(frozen=True)
class Standing:
    """How far a run has got, said as it starts each game."""

    games_done: int
    games_total: int
    bytes_done: int
    bytes_total: int
    name: str


def _refuse(ctx: Any, refused: list[str] | None, what: str, exc: Exception) -> None:
    """Log what core would not take and name it in `refused`, which the report lists."""
    ctx.logger.warning("%s was not imported: %s", what, exc)
    if refused is not None and what not in refused:
        refused.append(what)


def _rom_state(ctx: Any, game_id: str, rom: str, roms_dir: str,
               refused: list[str] | None = None) -> str:
    """The ROM set itself, if that folder has one by this name in any case: `placed`,
    `there` where the game already held it, `failed`, or `absent` where the folder has
    none."""
    found = finding.rom_file(roms_dir, rom)
    if found is None:
        return "absent"
    try:
        ctx.games.put_asset(game_id, "rom", found)
        return "placed"
    except FileExistsError:
        return "there"
    except Exception as exc:
        _refuse(ctx, refused, found.name, exc)
        return "failed"


def _bring_rom(ctx: Any, game_id: str, rom: str, roms_dir: str) -> int:
    return int(_rom_state(ctx, game_id, rom, roms_dir) == "placed")


def _bring_scores(ctx: Any, game_id: str, rom: str, sources: dict[str, str],
                  kept: saved.Saved, named: tuple[str, ...], rom_there: bool = False,
                  refused: list[str] | None = None) -> int:
    """High scores, game settings and what the table's script saved.

    A ROM's `nvram` and `cfg` come only where the ROM does (`rom_there`: it was placed or
    the game already held it): VPX reads a table's own `pinmame` folder only once
    `pinmame/roms` is in it, so either one alone is dead weight. `default.cfg` is named
    for no ROM and stays. The saved text files go to every game whose script names them,
    and the `VPReg.stg` sets it names are written as the game's own `VPReg.ini` rather
    than copied, which would carry every table's values.
    """
    brought = 0
    roms_dir = sources.get("roms", "")
    if rom and roms_dir and rom_there:
        for kind, here in score_folders(roms_dir).items():
            found = finding.rom_file(here, rom)
            if found is not None:
                brought += _put(ctx, game_id, kind, found, "", refused)
    for one in kept.files_for(named):
        brought += _put(ctx, game_id, "user", one, "", refused)
    wanted = kept.sets_for(named)
    if wanted:
        try:
            brought += 1 if ctx.games.put_saved_sets(game_id, kept.stg, wanted) else 0
        except Exception as exc:
            _refuse(ctx, refused, ctx.t("refused.saved_values"), exc)
    return brought


def _bring_alt_data(ctx: Any, game_id: str, rom: str, alt_dir: str,
                    refused: list[str] | None = None) -> int:
    """Sound banks and color sets, which sit in folders named for the ROM.

    The layout is the one PIN2DMD and altsound document: `altcolor/<rom>/` and
    `altsound/<rom>/`, beside each other under one root.
    """
    brought = 0
    for folder, kind in (("altsound", "altsound"), ("altcolor", ""), ("serum", "")):
        under = finding.child(alt_dir, folder)
        here = finding.child(under, rom) if under else None
        if here is None or not here.is_dir():
            continue
        if kind:
            brought += _put(ctx, game_id, kind, here, rom, refused)
            continue
        # A color set's format is the file's to declare, not the folder's.
        for one in sorted(here.iterdir()):
            said = ALT_KINDS.get(one.suffix.lower())
            if said:
                brought += _put(ctx, game_id, said, one, rom, refused)
    return brought


def _bring_named(ctx: Any, game_id: str, kind: str, folder: str, names: list[str],
                 refused: list[str] | None = None) -> int:
    """The set in `folder` that is named for the game, as a ROM, a folder, a game file or
    a title. A set named for none of them belongs to no game this can tell, and stays."""
    for name in names:
        found = finding.child(folder, name)
        if found is not None and found.is_dir():
            return _put(ctx, game_id, kind, found, "", refused)
    return 0


def names_of(game: SourceGame, folder: str, rom: str, asked: str = "") -> list[str]:
    """What a set kept for a game may be called: its ROM, the folder it becomes, its game
    file, its title. In that order, because a ROM names one machine and a title names many."""
    stem = Path(game.table_file).stem if game.table_file else ""
    title = str(mapping.details_for(game).get("title") or "")
    return [one for one in dict.fromkeys((rom, asked, folder, stem, title)) if one]


def _bring_keyed(ctx: Any, game_id: str, game: SourceGame, folder: str, rom: str,
                 sources: dict[str, str], row: AdoptedRow,
                 kept: saved.Saved | None = None, named: tuple[str, ...] = (),
                 asked: str = "", scores: bool = True) -> None:
    """What a source keeps for a game by its ROM or its name, once the game file is in.

    A game with no ROM has nothing keyed on one, which is most of a library and not a
    failure; music and PUP packs are named for the game as often as for a ROM. `rom` is
    the set PinMAME loads and `asked` the name the table gives where that differs.
    """
    rom_there = False
    if rom and sources.get("roms"):
        state = _rom_state(ctx, game_id, rom, sources["roms"], row["refused"])
        row["roms"] = int(state == "placed")
        rom_there = state in ("placed", "there")
    row["rom_there"] = rom_there
    if rom and sources.get("altdata"):
        row["altdata"] = _bring_alt_data(ctx, game_id, rom, sources["altdata"], row["refused"])
    names = names_of(game, folder, rom, asked)
    if sources.get("music"):
        row["music"] = _bring_named(ctx, game_id, "music", sources["music"], names,
                                    row["refused"])
    if sources.get("pups"):
        row["pups"] = _bring_named(ctx, game_id, "pup_pack", sources["pups"], names,
                                   row["refused"])
    if scores and (sources.get("roms") or sources.get("user")):
        row["scores"] = _bring_scores(ctx, game_id, rom, sources, kept or saved.Saved(),
                                      named, rom_there, row["refused"])


def _bring_beside(ctx: Any, game_id: str, game: SourceGame, table_id: str, rom: str,
                  named: tuple[str, ...], here: beside.Beside,
                  refused: list[str] | None = None) -> None:
    """What the table needs to start that the source kept beside its tables.

    The name map's line for the ROM the table asks for, the scripts and DMD folders its
    script names, and the backglass server's entry as the table's own settings. The line,
    scripts and folders are left as they are where the game already holds one. The settings
    go in only for a table this run added, and values the launcher already has make no
    file, so a table that was already there keeps what it has.
    """
    real = here.real_rom(rom)
    if rom and real:
        try:
            ctx.games.put_rom_alias(game_id, rom, real)
        except FileExistsError:
            pass
        except Exception as exc:
            _refuse(ctx, refused, ctx.t("refused.rom_map"), exc)
    for one in here.scripts_for(named):
        _put(ctx, game_id, "scripts", one, "", refused)
    for one in here.dmd_for(game.table_file, named):
        _put(ctx, game_id, "dmd_content", one, "", refused)
    keys = (rom,) if rom else (Path(game.table_file).stem,) if game.table_file else ()
    wanted = here.settings_for(*keys, program=_program_of(ctx, table_id)) if table_id else {}
    if wanted:
        try:
            ctx.games.write_table_settings(table_id, wanted)
        except Exception as exc:
            _refuse(ctx, refused, ctx.t("refused.backglass"), exc)


def _program_of(ctx: Any, table_id: str) -> dict[str, Any] | None:
    """What the program that plays the table is, or None where it cannot be asked."""
    try:
        found = ctx.games.table_program(table_id)
    except Exception:  # noqa: BLE001 - the settings are written as the server spelled them
        ctx.logger.debug("No program for %s", table_id, exc_info=True)
        return None
    return found if isinstance(found, dict) else None


def _remember(ctx: Any, game_id: str, played: Any, held: Held | None = None,
              refused: list[str] | None = None) -> int:
    """What the old frontend remembered about playing this game.

    The counters go through the one call that sets them; rating, favorite and tags are
    opinions with routes of their own and go through those. A value the source never
    wrote is left alone rather than sent as a zero - a game nobody played and a game
    whose count was never recorded are different, and only one of them should overwrite
    what is already here.

    For a game being filled (`held`), only what the library has none of is written: a
    counter at nothing, a game with no rating, tags added to the ones it has, a favorite
    turned on and never off.
    """
    if held is not None:
        return _add_missing(ctx, game_id, missing_history(played, held.user), held, refused)
    try:
        if played.has_play_record:
            ctx.games.set_play_record(
                game_id, play_count=played.play_count,
                play_time_seconds=played.play_time_seconds,
                last_played=played.last_played)
        if played.tags:
            ctx.games.set_tags(game_id, list(played.tags))
        if played.rating:
            ctx.games.rate_game(game_id, played.rating * 2)
        if played.favorite:
            ctx.games.set_favorite(game_id, True)
    except Exception as exc:
        _refuse(ctx, refused, ctx.t("refused.history"), exc)
        return 0
    return 1 if played.counts_as_history else 0


def _add_missing(ctx: Any, game_id: str, adds: Missing, held: Held,
                 refused: list[str] | None = None) -> int:
    """Each kind of history is written on its own, so one the library refuses (a tag it
    derives itself) does not keep the others from arriving. Answers 1 where counters or
    tags were written, the two the report counts."""
    writes = (
        (True, adds.counters,
         lambda: ctx.games.set_play_record(game_id, **adds.counters)),
        (True, adds.tags,
         lambda: ctx.games.set_tags(game_id, [*(held.user.get("tags") or []), *adds.tags])),
        (False, adds.rating, lambda: ctx.games.rate_game(game_id, adds.rating * 2)),
        (False, adds.favorite, lambda: ctx.games.set_favorite(game_id, True)),
    )
    counted = 0
    for reported, wanted, write in writes:
        if not wanted:
            continue
        try:
            write()
        except Exception as exc:
            _refuse(ctx, refused, ctx.t("refused.history"), exc)
            continue
        counted = counted or int(reported)
    return counted


def _held(ctx: Any, game_id: str) -> Held | None:
    """What the library holds of this game now, or nothing where it cannot be read, in
    which case a fill writes nothing it cannot tell is missing."""
    try:
        one = ctx.games.get_game(game_id)
    except Exception as exc:
        ctx.logger.warning("Could not read %s before filling it: %s", game_id, exc)
        return None
    return Held(rom=str(one.get("rom") or ""), user=dict(one.get("user") or {}))


def _put(ctx: Any, game_id: str, kind: str, path: Path, rom: str,
         refused: list[str] | None = None) -> int:
    try:
        ctx.games.put_asset(game_id, kind, path, rom)
        return 1
    except FileExistsError:
        return 0
    except Exception as exc:
        _refuse(ctx, refused, path.name, exc)
        return 0


def _place_media(ctx: Any, game_id: str, source_id: str, game: SourceGame,
                 kinds: tuple[str, ...], stem: str, replace: bool, row: AdoptedRow,
                 wheels_as: str = mapping.WHEELS) -> None:
    """The game's artwork. Counts what was placed; a folder core would not take a file from
    is named in `skipped_media` beside the folders with no kind. A wheel or a logo, placed
    or already in the slot, is `wheel` on the row.

    A kind with pages goes in page by page. Once its first page is held without a replace,
    the later ones are not tried, so one build's pages never join another's."""
    refused: set[str] = set()
    placed: dict[str, int] = {}
    stopped: set[str] = set()
    for kind, path in mapping.media_for(source_id, game, kinds, wheels_as):
        if kind in stopped:
            continue
        try:
            ctx.games.put_media(game_id, kind, path, stem, replace=replace,
                                page=placed.get(kind, 0) + 1)
            placed[kind] = placed.get(kind, 0) + 1
            row["media"] += 1
            row["wheel"] = row.get("wheel", False) or kind in mapping.WHEELS_AS
        except FileExistsError:
            stopped.add(kind)
            row["wheel"] = row.get("wheel", False) or kind in mapping.WHEELS_AS
        except Exception as exc:
            ctx.logger.warning("%s: %s was not imported: %s", row["name"], kind, exc)
            refused.add(mapping.folder_of(game, path))
    row["skipped_media"] = sorted(
        {*mapping.unmapped_kinds(source_id, game, kinds, wheels_as), *refused} - {""})
    row["refused"].extend(sorted(refused - {""} - set(row["refused"])))


def _kept(details: dict, mode: str, chosen: tuple[str, ...],
          gaps: tuple[str, ...] = ()) -> dict:
    """Of the source's details, those a game matched to the catalog keeps. What is not
    written is the entry's, which the match supplies, except where the entry leaves it
    blank (`gaps`) and the source's is all there is."""
    if mode == "source":
        return details
    chosen = (*chosen, *gaps) if mode == "some" else gaps
    return {name: value for name, value in details.items() if name in chosen}


def _asked(kept: saved.Saved | None, here: beside.Beside | None = None,
           table_file: str = "") -> tuple[str, ...]:
    """What to look for in a table's script as it lands."""
    return (*(kept.names if kept else ()), *(here.asked_of(table_file) if here else ()))


def _named_in(ctx: Any, game: SourceGame, kept: saved.Saved | None,
              here: beside.Beside | None = None) -> tuple[str, ...]:
    """What a table that is already in the library mentions, for a fill that does not add
    its file again."""
    asked = _asked(kept, here, game.table_file)
    if not game.table_file or not asked:
        return ()
    try:
        read = ctx.games.reads_of([game.table_file], asked)
    except Exception as exc:
        ctx.logger.warning("%s could not be read for what it saved: %s", game.key, exc)
        return ()
    return tuple(read.get(str(game.table_file), ("", ()))[1])


def _one(ctx: Any, source_id: str, game: SourceGame, kinds: tuple[str, ...],
         location: str, name: str = "", sources: Any = None,
         history: Any = None, move: bool = False,
         cleared: frozenset[str] = frozenset(), existing: str = "",
         vps_id: str = "", guessed: bool = False,
         kept: tuple[str, tuple[str, ...], tuple[str, ...]] = ("source", (), ()),
         saved_here: saved.Saved | None = None,
         beside_here: beside.Beside | None = None,
         wheels_as: str = mapping.WHEELS, scores: bool = True) -> AdoptedRow:
    """One game, and what became of it. Returns a row for the report.

    `kept` is whose details a matched game keeps - the plan's `details` and
    `detail_fields`, and the match's `gaps` - and says nothing of a game nothing matched,
    which keeps the source's.

    `vps_id` is the catalog entry the look found for a game made new, and `guessed` says
    it was found by name alone. It is written once the game file is in, and a failure to
    write it is logged and leaves the game made and unmatched, which is a state the
    library has a word for.

    `existing` is the id of the game the library already holds for this one. It is filled
    in rather than created: media only into a slot that is empty, the game file only if no
    file of that name is there, and its details are not written, since they may be the
    person's own edits.

    `cleared` holds the kinds whose source the person emptied. Those are not brought, which
    is what the summary told them and what the report expects.

    The name is handed in because the caller has already asked core what folder it
    becomes. Working it out again here would give the unsanitized one, and then the same
    game is counted under two names.
    """
    name = name or mapping.folder_name(game)
    row: AdoptedRow = {"key": game.key, "name": name, "game_id": "", "table": False,
                       "media": 0, "companions": 0, "roms": 0, "altdata": 0,
                       "music": 0, "pups": 0, "scores": 0, "history": 0,
                       "skipped_media": [], "refused": [], "error": ""}
    if existing:
        game_id = existing
        row["existing"] = True
    else:
        try:
            game_id = ctx.games.create(name, location)
        except Exception as exc:
            ctx.logger.warning("No folder for %s: %s", name, exc)
            row["error"] = {"text": ctx.t("error.no_folder"), "detail": ctx.why(exc)}
            return row

    row["game_id"] = game_id
    details = mapping.details_for(game)
    wanted = _kept(details, *kept) if vps_id else details
    if wanted and not existing:
        ctx.games.set_details(game_id, **wanted)

    # The table first, because a media file named for a game file needs that file's name
    # to exist. Its absence is not a failure: a source whose tables are still on the old
    # machine imports as entries with artwork and no game file, which is a state the
    # library has a word for.
    stem, rom, table_id = "", "", ""
    named: tuple[str, ...] = ()
    if game.table_file and "tables" not in cleared:
        try:
            landed = ctx.games.add_table(
                game_id, game.table_file, move=move,
                names=_asked(saved_here, beside_here, game.table_file))
            row["table"] = True
            row["companions"] = len(landed["companions"])
            rom = landed.get("rom", "")
            table_id = landed.get("table_id", "")
            named = tuple(landed.get("named", ()))
        except Exception as exc:
            # The copy already holding this file is the copy staying as it is, and its
            # ROM was read when it arrived. Not a failure, and nothing to count.
            if not (existing and isinstance(exc, FileExistsError)):
                ctx.logger.warning("%s: the game file was not imported: %s", name, exc)
                row["error"] = {"text": ctx.t("error.game_file"), "detail": ctx.why(exc)}

    if vps_id and not existing:
        try:
            ctx.games.match_game(game_id, vps_id, guessed)
            row["matched"] = True
        except Exception as exc:
            ctx.logger.warning("%s: the match did not get written: %s", name, exc)
            # Left unmatched, the game has only the source to describe it.
            if details != wanted:
                try:
                    ctx.games.set_details(game_id, **details)
                except Exception as again:
                    ctx.logger.warning("%s: its details did not get written: %s", name, again)

    if "media" not in cleared:
        _place_media(ctx, game_id, source_id, game, kinds, stem, not existing, row, wheels_as)

    # After the table, because the ROM comes out of the table. A game with no ROM has
    # nothing keyed on it, which is most of a library and not a failure. A filled game's
    # ROM falls back to the one the game holds.
    held = _held(ctx, game_id) if existing else None
    if held and not rom:
        rom = held.rom
    if existing and not row["table"] and _asked(saved_here, beside_here, game.table_file):
        named = _named_in(ctx, game, saved_here, beside_here)
    asked, rom = rom, (beside_here.real_rom(rom) if beside_here else "") or rom
    row["table_id"], row["rom"] = table_id, rom
    if (row["table"] or existing) and sources:
        _bring_keyed(ctx, game_id, game, name, rom, sources, row, saved_here, named, asked,
                     scores)
    if (row["table"] or existing) and beside_here:
        _bring_beside(ctx, game_id, game, table_id, asked, named, beside_here,
                      row["refused"])

    played = history_for(history or {}, game)
    if played is not None and (held or not existing):
        row["history"] = _remember(ctx, game_id, played, held, row["refused"])
    return row


def _another_build(ctx: Any, source_id: str, game: SourceGame, kinds: tuple[str, ...],
                   name: str, game_id: str, move: bool = False,
                   cleared: frozenset[str] = frozenset(),
                   existing: bool = False,
                   sources: dict[str, str] | None = None,
                   saved_here: saved.Saved | None = None,
                   beside_here: beside.Beside | None = None,
                   wheels_as: str = mapping.WHEELS, scores: bool = True) -> AdoptedRow:
    """A second build of a machine the run has already made a game for.

    Its file joins that game rather than starting another one. Its artwork fills the
    slots the first build left empty and replaces nothing: what is already there was
    placed for the same machine, and a second build's playfield would replace it with a
    picture of the same table. In a game the library already held, a file of the same
    name is already there and stays. What it needs keyed on its own ROM comes too: a build
    that names another ROM than the first needs that one, and one that names the same ROM
    finds it already there.
    """
    row: AdoptedRow = {"key": game.key, "name": name, "game_id": game_id,
                       "table": False, "media": 0, "companions": 0, "roms": 0,
                       "altdata": 0, "music": 0, "pups": 0, "scores": 0, "history": 0,
                       "skipped_media": [], "refused": [], "error": "",
                       "joined": True}
    if existing:
        row["existing"] = True
    rom, table_id = "", ""
    named: tuple[str, ...] = ()
    if game.table_file and "tables" not in cleared:
        try:
            landed = ctx.games.add_table(
                game_id, game.table_file, move=move,
                names=_asked(saved_here, beside_here, game.table_file))
            row["table"] = True
            row["companions"] = len(landed["companions"])
            rom = landed.get("rom", "")
            table_id = landed.get("table_id", "")
            named = tuple(landed.get("named", ()))
        except Exception as exc:
            if not (existing and isinstance(exc, FileExistsError)):
                ctx.logger.warning("%s: the game file was not imported: %s", name, exc)
                row["error"] = {"text": ctx.t("error.game_file"), "detail": ctx.why(exc)}
    if "media" not in cleared:
        _place_media(ctx, game_id, source_id, game, kinds, "", False, row, wheels_as)
    asked, rom = rom, (beside_here.real_rom(rom) if beside_here else "") or rom
    if row["table"] and sources:
        _bring_keyed(ctx, game_id, game, name, rom, sources, row, saved_here, named, asked,
                     scores)
    if row["table"] and beside_here:
        _bring_beside(ctx, game_id, game, table_id, asked, named, beside_here,
                      row["refused"])
    return row


def _turns_wheels_to_logos(source_id: str, games: list[SourceGame],
                           kinds: tuple[str, ...]) -> bool:
    """Whether any of these games has a wheel image that is kept as a logo."""
    return any(set(mapping.media_for(source_id, game, kinds, mapping.LOGOS))
               != set(mapping.media_for(source_id, game, kinds, mapping.WHEELS))
               for game in games)


def _media_after(ctx: Any, plan: Any, made: list[AdoptedRow], converted: bool) -> dict:
    """What the Media page asked for once the games exist: the wheel showing logos, which
    waits for a wheel image to have become one, and a fill for what the matched games are
    missing. Each says whether it happened; neither stops the report."""
    done = {"wheel_logos": False, "media_started": False}
    if not made or not plan:
        return done
    if plan.wheels_as == mapping.LOGOS and converted:
        try:
            ctx.games.use_logos_for_wheels()
            done["wheel_logos"] = True
        except Exception as exc:
            ctx.logger.warning("The wheel did not switch to logos: %s", exc)
    if plan.fetch_media:
        try:
            ids = list(dict.fromkeys(row["game_id"] for row in made if row.get("matched")))
            done["media_started"] = bool(ids) and bool(
                ctx.games.get_missing_media(ids).get("started"))
        except Exception as exc:
            ctx.logger.warning("Missing art was not asked for: %s", exc)
    return done


def run(ctx: Any, library: SourceLibrary, systems: list[str], location: str = "",
        plan: Any = None, *, move: bool = False,
        places: dict[str, str] | None = None,
        weigh: Callable[[SourceGame], int] | None = None,
        report: Callable[[Standing], None] | None = None,
        halted: Callable[[], bool] | None = None) -> dict:
    """Convert the chosen systems. Answers with a row per game and the counts.

    `move` takes each game file out of where it was; artwork, ROMs and the rest are
    copied either way. `places` names the location a system's games are made in, by the
    system's name, where that is not `location`.

    A game that fails is recorded and the next one is tried. An import of six hundred
    stopping on the one folder somebody already had would be worse than useless: it is
    the case this exists for, and the answer is to say which one and carry on.

    A game the library already holds is left alone unless the plan says to fill in what
    it is missing. Rewriting what somebody has curated since the last run is the worse
    mistake, so it is not the default. Filling goes to the game it matched, adds what is
    absent and never replaces what is there.

    `report` is told where the run has got to before each game, with `weigh` giving what
    a game is worth in bytes. `halted` is asked between games: a run that is stopped ends
    after the game in hand, so every folder it made is whole, and says so in `stopped`.
    """
    finding.forget()
    kinds = ctx.games.kinds()
    wanted = [system for system in library.systems
              if not systems or system.name in systems]
    # Paired by position, as the plan was made: two source games can share a key.
    games = [game for system in wanted for game in system.games]
    matches = plan.matches if plan else []
    by_key = {one.key: one for one in matches}
    held = (dict(zip(map(id, games), matches, strict=True)) if len(matches) == len(games)
            else {id(game): by_key[game.key] for game in games if game.key in by_key})
    sources = {one.key: one.path for one in (plan.sources if plan else ()) if one.active}
    cleared = frozenset(one.key for one in (plan.sources if plan else ())
                        if not one.active)
    history = plan.history if plan else {}
    wheels_as = plan.wheels_as if plan else mapping.WHEELS
    scores = plan.scores if plan else True
    kept_here = saved.read(ctx, sources.get("user", ""))
    beside_here = beside.read(ctx, sources)
    cabinet_here = cabinet.read({**sources, "root": library.root})

    rows, skipped, items = [], [], []
    # What this run has already made, by folder name. A source holds several builds of
    # the same machine - three of Kiss (Bally 1979), by different authors - and they are
    # one game with three tables here, not three games. Without this the first wins the
    # folder and the rest fail on a name that is already taken.
    made_here: dict[str, str] = {}
    # The games this run fills in rather than makes, by the game itself.
    held_ids: dict[int, str] = {}
    to_do: list[tuple[str, SourceGame]] = []
    for system in wanted:
        where = (places or {}).get(system.name, location)
        for game in system.games:
            match = held.get(id(game))
            if match is not None and not match.taken:
                if match.existing:
                    skipped.append({"key": game.key, "name": match.folder,
                                    "game_id": match.game_id,
                                    "how": ctx.t(f"match.{match.how}")})
                continue
            if match is not None and match.existing:
                held_ids[id(game)] = match.game_id
            to_do.append((where, game))

    weights = [weigh(game) if weigh else 0 for _where, game in to_do]
    total_bytes, bytes_done, stopped = sum(weights), 0, False
    for at, ((where, game), weight) in enumerate(zip(to_do, weights, strict=True)):
        if halted and halted():
            stopped = True
            break
        # Asked of core, not worked out here: the folder a name becomes is core's
        # rule, and a copy of it drifts without saying so.
        found = held.get(id(game))
        name = found.folder if found else ctx.games.folder_name_for(
            mapping.folder_name(game))
        if report:
            report(Standing(at, len(to_do), bytes_done, total_bytes, name))
        bytes_done += weight
        seen = made_here.get(name.lower())
        if seen:
            rows.append(_another_build(ctx, library.source_id, game, kinds, name, seen,
                                       move, cleared, seen in held_ids.values(), sources,
                                       kept_here, beside_here, wheels_as, scores))
            continue
        row = _one(ctx, library.source_id, game, kinds, where, name, sources,
                   history, move, cleared, held_ids.get(id(game), ""),
                   found.vps_id if found else "",
                   found.found_by == "name" if found else False,
                   (plan.details, plan.detail_fields, found.gaps if found else ())
                   if plan else ("source", (), ()), kept_here, beside_here, wheels_as,
                   scores)
        if row["game_id"]:
            made_here[name.lower()] = row["game_id"]
        rows.append(row)
        items.extend(asks.items_for(ctx, row, found, cabinet_here))
    made = [row for row in rows if row["game_id"]]
    # After the games, so the makers it looks for include the ones this run made.
    lost = [*kept_here.lost, *beside_here.lost]
    brought = (logos.bring(ctx, sources.get("media", ""), lost)
               if made and (plan.logos if plan else True) else {"placed": 0, "held": 0})
    after = ({"wheel_logos": False, "media_started": False} if stopped else _media_after(
        ctx, plan, made, wheels_as == mapping.LOGOS and _turns_wheels_to_logos(
            library.source_id, [game for _where, game in to_do], kinds)))
    filled = {row["name"].lower() for row in made if row.get("existing") and any(
        row[one] for one in ("table", "media", "companions", "roms", "altdata", "music", "pups",
                            "scores", "history"))}
    return {
        "games": len({row["name"].lower() for row in made}),
        "filled": len(filled),
        "tables": sum(1 for row in made if row["table"]),
        "media": sum(row["media"] for row in made),
        "companions": sum(row["companions"] for row in made),
        "roms": sum(row["roms"] for row in made),
        "altdata": sum(row["altdata"] for row in made),
        "music": sum(row["music"] for row in made),
        "pups": sum(row["pups"] for row in made),
        "scores": sum(row["scores"] for row in made),
        "history": sum(row["history"] for row in made),
        "logos": brought["placed"],
        **after,
        "matched": sum(1 for row in made if row.get("matched")),
        "failed": len(rows) - len(made),
        "stopped": stopped,
        "left": len(to_do) - len(rows),
        # Named rather than counted: after a partial run somebody wants to know which
        # ones were left, not how many.
        "already_here": skipped,
        "rows": rows,
        "items": items,
        "left_behind": [*cabinet_here.left_behind, *lost],
    }

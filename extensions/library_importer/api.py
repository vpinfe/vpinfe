"""What the importer offers over the API.

Every route is gated on a scope this extension declared; core attaches the gate. Reading
a source is a read, and pointing at a different one is a write - saying where an install
will read from is the decision worth being a separate permission from looking at what is
there.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import APIRouter

from . import (
    adopt,
    beside,
    folders,
    logos,
    mapping,
    pinballx,
    popper,
    saved,
    shapes,
    things,
)
from . import plan as plan_for
from .held import Held, Progress
from .plan import Plan
from .saved import Saved
from .source import SourceGame, SourceLibrary

# Asked in order, first to claim a folder wins. PinballX is looked for first
# because it is the source somebody converting a pinball library actually has. Folders
# is last: a frontend's own folder holds game folders too, and the reader that knows
# its database should be the one that claims it.
READERS = (pinballx, popper, folders)

# Where the source is, in this extension's own settings. Not a core setting: it is a
# fact about somebody's old machine, and it has no meaning to anything else here.
SOURCE_KEY = "source_root"

# The order the questions come in. Which of them get asked depends on what is found, but
# never on how somebody arrived: going Back and forward again asks the same things in
# the same order rather than jumping over what is already answered.
STEPS = ("source", "sources", "games", "matches", "names", "media", "things", "how", "summary")

# The answers kept in this extension's own settings for the next import, as one JSON text.
# The source, its folders and the ticks on the Tables page are about one import and stay out,
# and so does the wheel images' kind: it is measured from the images each time.
ANSWERS_KEY = "answers"
# Where games go on an install that has no library folder yet, inside the home folder.
SUGGESTED_FOLDER = "VPinFE Tables"
REMEMBERED = ("names", "details", "details_some", "fetch_media", "logos",
              *[things.switch(key) for key in things.THINGS])


def _after(step: str) -> tuple[str, ...]:
    """The steps still to come after the one being left."""
    at = STEPS.index(step) if step in STEPS else 0
    return STEPS[at + 1:] or (STEPS[-1],)


def reader_for(root: Path, survey: Callable[..., dict] | None = None) -> ModuleType | None:
    """The reader that claims a folder, or None. First to claim it wins, and readers are
    asked in the order they are declared. `survey` is what tells a game folder from
    any other, for the readers that have no database to ask."""
    return next((one for one in READERS if one.detect(root, survey)), None)


def _once(survey: Callable[..., dict]) -> Callable[..., dict]:
    """`survey`, asking core about a folder once however many times it is asked.

    Made for one request and dropped with it, so a folder changed since is looked at
    again by the next.
    """
    held: dict[str, dict] = {}

    def ask(path: str | Path) -> dict:
        key = str(path)
        if key not in held:
            held[key] = survey(path)
        return held[key]

    return ask


def _trouble_with(ctx: Any, path: Path) -> str:
    """Why a folder cannot be read from, or "" where it can. A folder that is there but
    not open to this install reads as empty to everything below, and "nothing this
    build can read" would be the wrong thing to tell somebody who simply lacks access."""
    if not path.is_dir():
        return ctx.t("reason.unreachable")
    if not os.access(path, os.R_OK | os.X_OK):
        return ctx.t("reason.cannot_read")
    return ""


def _name(ctx: Any, reader: ModuleType) -> str:
    """What a source is called on screen: the catalog's word where it has one, and its
    own name where it is a product's."""
    return ctx.t("source.folders") if reader.SOURCE_ID == folders.SOURCE_ID \
        else reader.SOURCE_NAME


def _rooted(reader: ModuleType | None, values: dict, typed: str) -> str:
    """The folder a source is read from.

    A frontend is read from the folder asked for first. No frontend has no such folder: the
    game files are where the second step says they are, so that answer is the root once it
    has been given.
    """
    if reader is folders and "tables" in values:
        return str(values["tables"] or "").strip()
    return typed


def _systems(values: dict) -> list[str]:
    """Which of the source's systems, or all of them where it was never asked."""
    return [str(one) for one in (values.get("systems") or [])]


def _chosen(values: dict) -> dict | None:
    held = {key: values[key] for key in plan_for.SOURCES if key in values}
    return held or None


def _switched(values: dict) -> dict | None:
    """`_chosen`, with a kind of thing the ROMs and more page switched off read as a folder
    left empty on purpose."""
    chosen = dict(_chosen(values) or {})
    for key in things.FOLDER:
        if values.get(things.switch(key)) is False:
            chosen[key] = ""
    return chosen or None


def _on(values: dict, key: str) -> bool:
    return values.get(key, True) is not False


def _mode(library: SourceLibrary, values: dict, games: list[SourceGame]) -> str:
    """What happens to the game files, given what is going.

    `copy` or `move` where there is something to carry and nobody has already decided:
    a source that holds game folders is a library folder as it stands, so its loose
    tables get folders where they lie (`in_place`) and a folder with none of those has
    nothing to do but be used (`as_is`). `plain` is a source that names no game file
    at all, which is entries with artwork and nothing to copy or move.
    """
    if library.in_place:
        return "in_place" if games else "as_is"
    if not any(game.table_file for game in games):
        return "plain"
    return "move" if values.get("how") == "move" else "copy"


def _state(ctx: Any, survey: Callable[..., dict] | None = None) -> dict:
    configured = ctx.config.get(SOURCE_KEY, "")
    if not configured:
        return {"path": "", "reachable": False, "source_id": "", "source_name": "",
                "reason": ctx.t("reason.no_source")}
    path = Path(configured)
    trouble = _trouble_with(ctx, path)
    if trouble:
        return {"path": configured, "reachable": False, "source_id": "",
                "source_name": "", "reason": trouble}
    reader = reader_for(path, survey or _once(ctx.games.survey))
    if reader is None:
        return {"path": configured, "reachable": True, "source_id": "",
                "source_name": "", "reason": ctx.t("reason.nothing_readable")}
    return {"path": configured, "reachable": True, "source_id": reader.SOURCE_ID,
            "source_name": _name(ctx, reader), "reason": ""}


# Games asked of the catalog at a time, so the wait has somewhere to be said to have got to.
ROM_BATCH = 25
LOOK_BATCH = 50

def _asked(game: SourceGame) -> dict:
    """What a source knows of a game that tells the catalog which one it is."""
    return {"vps_id": game.vps_id, "ipdb_id": game.ipdb_id,
            "title": str(mapping.details_for(game).get("title") or ""),
            "manufacturer": game.manufacturer, "year": game.year,
            "folder": mapping.folder_name(game)}


def _preview(library: SourceLibrary) -> dict:
    """What was found, counted. The whole library would be megabytes and nobody reads a
    thousand rows to decide whether to go ahead."""
    systems = []
    for system in library.systems:
        with_media = sum(1 for game in system.games if game.media)
        with_table = sum(1 for game in system.games if game.table_file)
        matched = sum(1 for game in system.games if game.vps_id)
        systems.append({
            "name": system.name,
            "games": len(system.games),
            "with_artwork": with_media,
            "with_a_game_file": with_table,
            "already_matched": matched,
            "tables_dir": system.tables_dir,
            "enabled": system.enabled,
        })
    return {"source_id": library.source_id, "root": library.root,
            "systems": systems, "notes": list(library.notes)}


def build(ctx: Any) -> None:
    """Register the routes, and keep what core may read in step with the setting."""
    def follow_the_setting(*extra: str) -> None:
        """Tell core every folder this import will actually read from.

        The install folder is not enough, and assuming it is costs a whole run: a
        frontend keeps its database and artwork under its own roof, but the tables sit
        wherever the emulator was installed, which can be anywhere. Core refuses every
        one of them where the importer resolved the path and never declared it.

        So the roots follow what was resolved, not what was configured. Anything the
        plan points at is a folder this will open, and core cannot know that from the
        source root alone.
        """
        configured = ctx.config.get(SOURCE_KEY, "")
        wanted = [configured] if configured else []
        wanted += [one for one in extra if one]
        ctx.files.set_roots(wanted)

    def declare_roots(library: SourceLibrary, values: dict) -> None:
        """Tell core where this import will read, before anything tries to read there.

        Before the plan, not after it: the plan counts what travels with each table,
        which means opening the tables folder, which core refuses until it has been told
        about it. The source map is derived from the library alone, so it is available
        first and does not need the plan that needs it.
        """
        found = plan_for.derive_sources(library, _chosen(values))
        roms = next((source.path for source in found if source.key == "roms"), "")
        follow_the_setting(*[source.path for source in found if source.active],
                           *plan_for.score_folders(roms).values(),
                           *beside.roots({source.key: source.path for source in found
                                          if source.active}))

    follow_the_setting()

    held = Held()
    progress = Progress()
    shaped: dict[tuple, str] = {}

    def remembered() -> dict:
        """The answers kept from an earlier import, which start this one."""
        try:
            found = json.loads(ctx.config.get(ANSWERS_KEY, "") or "{}")
        except ValueError:
            return {}
        if not isinstance(found, dict):
            return {}
        return {key: found[key] for key in REMEMBERED if key in found}

    def answers(values: dict) -> dict:
        return {**remembered(), **values}

    def wheel_files(library: SourceLibrary, values: dict) -> list[str]:
        wanted = _systems(values)
        return shapes.wheel_images(
            [game for system in library.systems if not wanted or system.name in wanted
             for game in system.games], library.source_id, ctx.games.kinds())

    def wheels_as_of(library: SourceLibrary, values: dict) -> str:
        """What the wheel images are kept as: what was answered, else what their shape says."""
        said = str(values.get(answer_key("wheels_as")) or "")
        if said in mapping.WHEELS_AS:
            return said
        return looks_like(library, values) or mapping.WHEELS

    def looks_like(library: SourceLibrary, values: dict) -> str:
        """What the wheel images' shapes say they are, whatever was answered; empty where
        none of them could be measured."""
        key = (library.source_id, library.root, tuple(_systems(values)))
        if key not in shaped:
            files = wheel_files(library, values)
            shaped[key] = shapes.measured_as(files) if shapes.readable(files) else ""
        return shaped[key]

    def real_of(roms: str) -> Callable[[str], str]:
        """The ROM set a name the script asks for stands for, from the map beside `roms`."""
        if roms not in held.name_maps:
            held.name_maps[roms] = beside.name_map(ctx, roms)
        return held.name_maps[roms].real_rom

    def point_at(wanted: str) -> None:
        """Make this the source and declare it, before core is asked anything about it.

        Asking core what a folder holds is refused for a folder not declared, and an
        uncaught refusal in a route disables the extension.
        """
        ctx.config.set(SOURCE_KEY, wanted)
        follow_the_setting()

    def read(reader: ModuleType, path: Path, values: dict, survey: Callable[..., dict],
             fresh: bool = False, keep: bool = True) -> SourceLibrary:
        """The source, read once for the steps of one import.

        Only the Folders reader looks at what was chosen, so only for that one does the
        answer to a step change what was read. `fresh` reads again whatever is held.
        """
        key = (reader.SOURCE_ID, str(path),
               json.dumps(_chosen(values), sort_keys=True, default=str)
               if reader is folders else "")
        if fresh:
            shaped.clear()
        found = held.library_for(key, fresh) if keep else None
        if found is not None:
            return found
        progress.stage("reading", ctx.t("progress.reading", source=_name(ctx, reader)))
        library = reader.read(path, ctx.apps.plays, ctx.apps.names(),
                              survey=survey, chosen=_chosen(values))
        if keep:
            held.keep(key, library)
        return library

    def look_beside(library: SourceLibrary) -> None:
        """Ask core what sits beside every game file not yet asked about, in one call.

        Core has to have been told where the source is first, which `declare_roots` does.
        """
        asked = held.unasked([game.table_file for system in library.systems
                              for game in system.games if game.table_file])
        if asked:
            progress.stage("checking", ctx.t("progress.checking"))
            held.remember_companions(asked, ctx.games.companions_for(asked))

    def known(library: SourceLibrary) -> list[dict]:
        """Every game already here, and the game folders a source holds in place.

        Those are listed under their folder name, first so a real entry of the same name
        replaces them: a loose table beside a folder of its own name is recognized as
        having one before the folder is a library folder and can say so itself.
        """
        held = [{"game_id": f"in_place:{name}", "folder_name": name}
                for name in library.in_place]
        return held + ctx.games.existing()

    def look_up(library: SourceLibrary) -> tuple[dict, dict[int, dict]]:
        """Ask the catalog which entry each game is, once for the library held.

        Every game is asked, not only the systems chosen: the choice can change on the
        next step, and a look that had to be made again for it would be the slow part
        twice. The catalog is fetched first where there is none, with the wait said.
        Where there is none to be had, or matching is switched off, nothing is matched and
        the state returned says which.
        """
        kept = held.look_of(library)
        if kept is not None:
            return kept
        games = [game for system in library.systems for game in system.games]
        state = ctx.games.vps_catalog(
            lambda: progress.stage("fetching", ctx.t("progress.fetching")))
        looked: dict[int, dict] = {}
        if state.get("ready"):
            progress.stage("matching", ctx.t("progress.matching"), len(games))
            for start in range(0, len(games), LOOK_BATCH):
                batch = games[start:start + LOOK_BATCH]
                answers = ctx.games.vps_settle([_asked(game) for game in batch])
                looked.update((id(game), answer)
                              for game, answer in zip(batch, answers, strict=True))
                progress.advance(start + len(batch))
        held.remember_look(library, dict(state), looked)
        return dict(state), looked

    def picked_over(library: SourceLibrary, looked: dict[int, dict],
                    values: dict) -> dict[int, dict]:
        """The look, with what the person settled on the Matches page laid over it.

        A pick is an entry id, answered by the catalog the way the look answers a source's
        own id, and `""` is a game the person said is in no catalog.
        """
        picks = values.get(answer_key("picks")) or {}
        if not picks:
            return looked
        rows = plan_for.row_keys(library)
        by_game = {key: str(picks[rows[key]] or "") for key in rows if rows[key] in picks}
        ids = sorted({one for one in by_game.values() if one})
        answers = dict(zip(ids, ctx.games.vps_settle([{"vps_id": one} for one in ids]),
                           strict=True)) if ids else {}
        over = dict(looked)
        for key, vps in by_game.items():
            answer = answers.get(vps)
            over[key] = ({**answer, "how": "picked"} if answer and answer.get("vps_id") == vps
                         else {"vps_id": "", "how": "picked", "reason": "",
                               "candidates": [], "entry": {}})
        return over

    def answer_key(name: str) -> str:
        """The key the Tables and Matches answers are held under for what is being read."""
        return f"{name}.{held.generation}" if held.generation else name

    def _ticked(library: SourceLibrary, values: dict) -> Sequence[str] | None:
        """The rows ticked on the Tables page. Before that page has been answered, `only`
        (source game keys, from a report's retry) ticks those games and nothing else."""
        said = values.get(answer_key("games"))
        if said is not None or not values.get("only"):
            return said
        only = {str(one) for one in values["only"]}
        rows = plan_for.row_keys(library)
        return [rows[id(game)] for game in library.games if game.key in only]

    def plan(library: SourceLibrary, values: dict, existing: list[dict],
             deciding: bool = False) -> Plan:
        """The plan for these answers. `deciding` is the Tables page being left: the games
        already here at that moment are the ones the person saw as such and ticked or not."""
        values = answers(values)
        held.asked_about((library.source_id, str(values.get("path") or ""),
                          tuple(_systems(values)
                                or (one.name for one in library.systems if one.games))))
        look_beside(library)
        catalog, looked = look_up(library)
        made = plan_for.build(library, existing, _switched(values),
                              _ticked(library, values), _systems(values),
                              ctx.games.folder_name_for, library.source_id, ctx.games.kinds(),
                              held.companions_of, picked_over(library, looked, values),
                              str(values.get("names") or plan_for.DEFAULT_NAMES),
                              str(values.get("details") or plan_for.DEFAULT_DETAILS),
                              [str(one) for one in values.get("details_some") or []],
                              wheels_as_of(library, values))
        made.catalog = catalog
        made.logos = _on(values, "logos")
        made.fetch_media = _on(values, "fetch_media")
        made.scores = _on(values, things.switch("scores"))
        if deciding:
            held.deliberate = {one.row for one in made.matches if one.existing}
        for one in made.matches:
            # A pick that lands on a game already here does not tick it: the person ticks a
            # copy on the Tables page, where it is shown as one.
            if one.taken and one.existing and one.found_by == "picked" \
                    and one.row not in held.deliberate:
                one.taken = False
        read_roms(library, made, _systems(values))
        return made

    def read_roms(library: SourceLibrary, made: Plan, systems: list[str]) -> None:
        """Find out which ROM each game file names, when ROMs are being imported.

        Opening a table is the only way to know, and a library is hundreds of them on a
        share, so they are read in batches with the count said, and kept for the steps
        that follow. A game that cannot be read has no ROM here, which counts as none
        needed rather than as an error.
        """
        folder, user = made.source("roms"), made.source("user")
        reading_roms = folder is not None and folder.active
        saving = user is not None and user.active
        if not (reading_roms or saving):
            return
        kept = saved_of(user.path if user is not None and saving else "")
        held.ask_for(kept.names)
        wanted = [game.table_file
                  for game in plan_for.going(library, made, systems, offered=True)
                  if game.table_file]
        todo = held.unread_roms(wanted)
        if todo:
            progress.stage("roms", ctx.t("progress.roms"), len(todo))
            for start in range(0, len(todo), ROM_BATCH):
                batch = todo[start:start + ROM_BATCH]
                try:
                    reads = ctx.games.reads_of(batch, kept.names)
                    held.remember_roms(batch,
                                       {path: rom for path, (rom, _) in reads.items() if rom},
                                       {path: named for path, (_, named) in reads.items()})
                except Exception:
                    ctx.logger.warning("ROM names were not read for %d tables", len(batch),
                                       exc_info=True)
                    held.remember_roms(batch, {})
                progress.advance(start + len(batch))
        plan_for.attach_roms(made, library, held.roms, systems,
                             real_of(folder.path if reading_roms and folder else ""))

    def saved_of(folder: str) -> Saved:
        """The saved files in a folder, read once per library."""
        return held.saved_in(folder, lambda one: saved.read(ctx, one))

    def held_of(game_id: str) -> plan_for.Held:
        """What a game already holds, for a fill to count what it would add."""
        one = ctx.games.get_game(game_id)
        files = ctx.games.game_tables(game_id).get("tables") or []
        slots = ctx.games.game_media(game_id).get("media") or {}
        return plan_for.Held(
            tables=frozenset(str(row.get("filename") or "").lower() for row in files
                             if row.get("available") is not False) - {""},
            media=frozenset(kind for kind, slot in slots.items() if slot.get("via") == "game"),
            rom=str(one.get("rom") or ""), user=dict(one.get("user") or {}))

    measured: dict[str, tuple[float, plan_for.Held]] = {}

    def held_lately(game_id: str) -> plan_for.Held:
        """`held_of` for a summary redrawn while somebody reads it: each held game costs
        three reads, a folder scan among them, so an answer under half a minute old
        stands. The run itself reads afresh."""
        at, found = measured.get(game_id, (0.0, None))
        if found is None or time.monotonic() - at > 30:
            found = held_of(game_id)
            measured[game_id] = (time.monotonic(), found)
        return found

    def path_of(location: str) -> str:
        """Where a location is, or "" where it is not somewhere games can be made.

        Core answers by where games can go: the place new ones go, and every other
        writable library folder.
        """
        found = ctx.games.new_game_destination()
        if not location:
            return "" if found.get("reason") else str(found.get("path") or "")
        return next((str(one.get("path") or "")
                     for one in [found, *(found.get("alternatives") or [])]
                     if one.get("location_id") == location), "")

    def into_of(values: dict, location: str = "") -> str:
        """Where copies go: what was typed, else the location a caller named, else the
        place new games already go."""
        if "into" in values:
            return str(values["into"] or "").strip()
        return path_of(location)

    def tables_folder_of(library: SourceLibrary, games: list[SourceGame]) -> str:
        """The first folder the games going are read from, or the source's own."""
        return next((plan_for.tables_folder(one)
                     for one in plan_for.systems_going(library, games)
                     if plan_for.tables_folder(one)), library.root)

    def carries(mode: str, source_folder: str, into: str) -> bool:
        """Whether the game files are written rather than renamed. A move is a rename only
        where the files stay on one drive; with no folder named it stays where it is."""
        return mode == "copy" or (mode == "move" and bool(into)
                                  and not plan_for.same_drive(source_folder, into))

    def carried_game(mode: str, game: Any, into: str) -> bool:
        """`carries` for one game, asked of the folder its own file is in: systems on
        different drives are not one answer."""
        return carries(mode, str(Path(game.table_file).parent) if game.table_file else "",
                       into)

    def suggested_into(values: dict) -> str:
        """What the Library Folder field starts as: what was typed, else the place new
        games already go, else a folder of its own in the home folder. A field that was
        cleared stays cleared, with its error beside it."""
        if "into" in values:
            return into_of(values)
        return into_of(values) or str(Path.home() / SUGGESTED_FOLDER)

    def can_be_made(path: str) -> bool:
        """Whether a folder is there, or is one level below a folder that is and can be
        written to. One level only: a mount that is not there must not be made up."""
        folder = Path(path).expanduser()
        if folder.is_dir():
            return True
        return (not folder.exists() and folder.parent.is_dir()
                and os.access(folder.parent, os.W_OK))

    def space(library: SourceLibrary, values: dict, mode: str,
              games: list[SourceGame]) -> tuple[int, int, str]:
        """Bytes this will write, the room there is for them, and where.

        Asked of the place the games will actually be made, which is the `into` in
        `values`, or the default where there is none. A caller that names a location
        instead has it put in `values` first. Room is -1 where it cannot be told. A move
        writes only the artwork, beside the files, unless it changes drive; a source with
        systems on different drives is asked at the first.
        """
        if mode not in ("copy", "move", "in_place"):
            return 0, -1, ""
        values = answers(values)
        folders_of = {one.key: one.path
                      for one in plan_for.derive_sources(library, _switched(values))}
        source_folder = tables_folder_of(library, games)
        into = into_of(values) if "into" in values else ""
        measure = held.measured(progress, ctx.t("progress.measuring"))
        saved = saved_of(folders_of.get("user", ""))
        written = [one for one in games if carried_game(mode, one, into)]
        renamed = [one for one in games if not carried_game(mode, one, into)]
        need = sum(
            plan_for.bytes_to_copy(
                part, library.source_id, ctx.games.kinds(), held.companions_of,
                move=not carried, sized=measure,
                roms=held.roms, roms_dir=folders_of.get("roms", ""),
                named=held.named, saved=saved,
                real=real_of(folders_of.get("roms", "")),
                wheels_as=wheels_as_of(library, values))
            for carried, part in ((True, written), (False, renamed)) if part)
        target = (into_of(values) if mode == "copy" or (mode == "move" and into)
                  else source_folder)
        free, where = plan_for.room_at(target) if target else (-1, "")
        return need, free, where

    def short_of_room(need: int, free: int, where: str) -> str:
        """Why this cannot start for want of room, or "". Said the same at the summary
        and at the run, so the one is not a promise the other breaks."""
        if need > free >= 0:
            return ctx.t("reason.no_room", size=plan_for.size_of(need),
                         free=plan_for.size_of(free), where=where)
        return ""

    reading = APIRouter()

    @reading.get("/source")
    def source() -> dict:
        """Where this install is set to import from, and what is there."""
        return _state(ctx)

    @reading.get("/preview")
    def preview() -> dict:
        """What a scan of the chosen source found. Reads it, writes nothing."""
        survey = _once(ctx.games.survey)
        state = _state(ctx, survey)
        if not state["source_id"]:
            return {"source_id": "", "root": state["path"], "systems": [],
                    "notes": [state["reason"]]}
        reader = next(one for one in READERS if one.SOURCE_ID == state["source_id"])
        try:
            return _preview(read(reader, Path(state["path"]), {}, survey, keep=False))
        finally:
            progress.clear()

    writing = APIRouter()

    @writing.put("/source")
    def set_source(body: dict) -> dict:
        """Point at a folder.

        Its own permission, and not because it writes a setting: core reads files under
        this folder while it is set, so choosing it is the moment somebody widens what
        this install will open. Looking at what is already there is not the same act.
        """
        wanted = str(body.get("path") or "").strip()
        ctx.config.set(SOURCE_KEY, wanted)
        follow_the_setting()
        return _state(ctx)

    def wizard_form(step: str = "") -> dict:
        """Step one: what are you importing from, and where is it.

        The kind comes from the form's source_type; a caller that sends none still gets
        the folder sniffed.
        """
        state = _state(ctx)
        said = {pinballx.SOURCE_ID: ctx.t("wizard.source.pinballx.help"),
                popper.SOURCE_ID: ctx.t("wizard.source.popper.help"),
                folders.SOURCE_ID: ctx.t("wizard.source.folders.help")}
        return {
            "step": "source",
            "title": ctx.t("wizard.source.title"),
            "help": ctx.t("wizard.source.help"),
            "fields": [
                {"key": "source_type", "type": "select",
                 "label": ctx.t("wizard.source.source_type.label"),
                 "value": READERS[0].SOURCE_ID,
                 "choices": [[one.SOURCE_ID, _name(ctx, one), said[one.SOURCE_ID]]
                             for one in READERS],
                 "help": {"follows": "source_type", "says": said}},
                {"key": "path", "type": "path", "wants": "dir",
                 "label": ctx.t("wizard.source.path.label"),
                 "value": state["path"],
                 "help": {"follows": "source_type", "says": {
                     "pinballx": ctx.t("wizard.source.path.pinballx"),
                     "popper": ctx.t("wizard.source.path.popper"),
                     "folders": ctx.t("wizard.source.path.folders")}}},
            ],
        }

    def _reader_for(values: dict, path: Path,
                    survey: Callable[..., dict]) -> ModuleType | None:
        """The reader this source is to be read with.

        What somebody chose, where they chose; otherwise the first that claims the
        folder. A chosen reader that does not claim it is still used - being told "that
        is not a Popper install" is more useful than quietly reading it as something
        else.
        """
        wanted = str(values.get("source_type") or "").strip()
        if wanted:
            return next((one for one in READERS if one.SOURCE_ID == wanted), None)
        return reader_for(path, survey)

    def wizard_progress() -> dict:
        """Where a check or a run in progress has got to: its stage, in words, and how
        far through it is. Empty where nothing is going on."""
        return progress.snapshot()

    def wizard_check(values: dict, step: str = "") -> dict:
        """Whatever comes next: another question, or what it all adds up to."""
        try:
            return _check(values or {}, step)
        finally:
            progress.clear()

    def _check(values: dict, step: str) -> dict:
        leaving = step or "source"
        typed = str(values.get("path") or "").strip()
        no_frontend = str(values.get("source_type") or "") == folders.SOURCE_ID

        if no_frontend and leaving == "source" and not typed:
            return _sources_step(folders, plan_for.derive_sources(
                SourceLibrary(source_id=folders.SOURCE_ID, root=""), _chosen(values)), [])
        if not typed and not no_frontend:
            return _again(ctx.t("reason.say_where"), values)

        survey = _once(ctx.games.survey)
        reader: ModuleType | None = folders if no_frontend else None
        on_tables = no_frontend and leaving != "source" and "tables" in values

        def refused(reason: str) -> dict:
            if on_tables:
                return _sources_step(folders, plan_for.derive_sources(
                    SourceLibrary(source_id=folders.SOURCE_ID, root=""), _chosen(values)),
                    [], {"tables": reason})
            return _again(reason, values)

        # No frontend reads the tables folder, so a refusal about it belongs to the
        # step that asks for it; a path left over from step one is not what is read.
        checked = ""
        first = _rooted(reader, values, typed)
        if first:
            point_at(first)
            checked = first
            trouble = _trouble_with(ctx, Path(first))
            if trouble:
                return refused(trouble)
        if reader is None:
            reader = _reader_for(values, Path(typed), survey)
        if reader is None:
            return _again(ctx.t("reason.nothing_readable"), values)
        source: ModuleType = reader

        wanted = _rooted(source, values, typed)
        if not wanted:
            return refused(ctx.t("reason.say_tables") if on_tables
                           else ctx.t("reason.say_where"))
        path = Path(wanted)
        if wanted != checked:
            point_at(wanted)
            trouble = _trouble_with(ctx, path)
            if trouble:
                return refused(trouble)
        if reader is folders:
            if leaving != "source" and not reader.detect(path, survey):
                return refused(ctx.t("reason.no_tables"))
        elif not reader.detect(path, survey):
            return _again(ctx.t("reason.not_this_source", source=_name(ctx, reader)),
                          values)

        library = read(reader, path, values, survey, fresh=leaving == "source")
        declare_roots(library, values)
        made = plan(library, values, known(library), deciding=leaving == "games")
        games = plan_for.going(library, made, _systems(values))
        mode = _mode(library, values, games)

        if leaving == "how" and mode in ("copy", "move"):
            refused_how = _refuse_how(values, mode)
            if refused_how:
                return _how_step(source, library, values, games, refused_how)

        for step in _after(leaving):
            if step == "sources":
                return _sources_step(reader, made.sources, library.systems)
            # `skip_existing` is how a caller that knows nothing here can matter, a first
            # run, starts past this list instead of being asked to tick it.
            if step == "games" and made.matches and not values.get("skip_existing"):
                return _tables_step(reader, made)
            if step == "matches" and _to_look_at(made):
                return _matches_step(made, values)
            if step == "names" and _names_matter(made):
                return _names_step(reader, library, made, values)
            if step == "media" and (media := _media_found(library, made, values)):
                return _media_step(reader, library, made, values, media)
            if step == "things":
                counts, short = _things_found(library, values, known(library))
                if any(counts.values()) or short:
                    return _things_step(counts, short, values)
            if step == "how" and mode in ("copy", "move"):
                return _how_step(reader, library, values, games)
            if step == "summary":
                break
        return _summary_step(reader, library, made, values, games)

    def _again(reason: str, values: dict) -> dict:
        """The source step again - every refusal here is about the folder, so it lands
        under that field rather than under the whole form."""
        found = wizard_form()
        found["errors"] = {"path": reason}
        for field in found["fields"]:
            if field["key"] in values:
                field["value"] = values[field["key"]]
        return found

    def _sources_step(reader: ModuleType, sources: list[plan_for.Source],
                      systems: Sequence[Any], errors: dict[str, str] | None = None) -> dict:
        no_frontend = reader is folders
        fields: list[dict[str, Any]] = [{
            "key": source.key, "type": "path", "label": source.label,
            "value": source.path,
            # Play history is one file - GameStats.csv or the Popper database - where
            # every other source is a folder, and a folder check marks it missing.
            **({"wants": "file"} if source.key == "history" else {}),
            "help": (ctx.t("wizard.sources.worked_out", help=source.help)
                     if source.derived else source.help),
        } for source in sources]

        # Only where there is a choice to make. One system is not a decision, and a
        # control with a single option in it is a question that answers itself.
        named = [one.name for one in systems if one.games]
        if len(named) > 1:
            fields.insert(0, {
                "key": "systems", "type": "multi",
                "label": ctx.t("wizard.sources.systems.label"),
                "value": named,
                "choices": [[one, one] for one in named],
                "help": ctx.t("wizard.sources.systems.help"),
            })

        found: dict[str, Any] = {
            "step": "sources",
            "title": (ctx.t("wizard.sources.no_frontend.title") if no_frontend
                      else ctx.t("wizard.sources.title", source=_name(ctx, reader))),
            "help": (ctx.t("wizard.sources.no_frontend.help") if no_frontend
                     else ctx.t("wizard.sources.help")),
            "fields": fields,
        }
        if errors:
            found["errors"] = errors
        return found

    def _adds_line(found: plan_for.Adds) -> str:
        """What ticking a game already here brings it, in a few words."""
        if found.nothing:
            return ctx.t("wizard.games.adds.nothing")
        parts = []
        if found.tables:
            parts.append(ctx.t("wizard.games.adds.tables", count=found.tables))
        if found.pictures:
            parts.append(ctx.t("wizard.games.adds.pictures", count=found.pictures))
        if found.rom:
            parts.append(ctx.t("wizard.games.adds.rom"))
        if found.history:
            parts.append(ctx.t("wizard.games.adds.history"))
        said = (ctx.t("wizard.games.adds.and", rest=", ".join(parts[:-1]), last=parts[-1])
                if len(parts) > 1 else parts[0])
        return ctx.t("wizard.games.adds", things=said)

    def _game_row(one: plan_for.Match, source: str, roms_dir: str,
                  importing: dict[str, bool]) -> dict:
        """One game on the Tables page: a copy already here says how it matched and what
        ticking would add to it; a new one says only what is unusual about it."""
        groups = ["here" if one.existing else "new"]
        notes = []
        if one.existing:
            try:
                brings = _adds_line(plan_for.adds(
                    one, held_lately(one.game_id), roms_dir,
                    tables=importing.get("tables", False),
                    media=importing.get("media", False)))
            except Exception:
                ctx.logger.warning("Could not read %s to say what it is missing", one.folder,
                                   exc_info=True)
                brings = ""
            how = ctx.t(f"match.{one.how}")
            notes.append(ctx.t("wizard.games.here", how=how, adds=brings) if brings else how)
        if one.hidden:
            groups.append("hidden")
            notes.append(ctx.t("wizard.games.hidden", source=source))
        return {"key": one.row, "name": one.name, "note": ", ".join(notes), "groups": groups}

    def _tables_step(reader: ModuleType, made: Plan) -> dict:
        """Every game the source holds, to tick. New ones start ticked and the ones already
        in VPinFE do not, which leaves them alone."""
        source = _name(ctx, reader)
        roms = made.source("roms")
        roms_dir = roms.path if roms is not None and roms.active else ""
        importing = {one.key: bool(one.active) for one in made.sources}
        progress.stage("holding", ctx.t("progress.holding"), len(made.already))
        rows, looked_at = [], 0
        for one in made.matches:
            rows.append(_game_row(one, source, roms_dir, importing))
            if one.existing:
                looked_at += 1
                progress.advance(looked_at)
        return {
            "step": "games",
            "title": ctx.t("wizard.games.title"),
            "help": ctx.t("wizard.games.help"),
            "fields": [{
                "key": answer_key("games"), "type": "checklist",
                "label": ctx.t("wizard.games.label"),
                "rows": rows,
                "chips": [["new", ctx.t("wizard.games.chip.new")],
                          ["here", ctx.t("wizard.games.chip.here")],
                          ["hidden", ctx.t("wizard.games.chip.hidden", source=source)]],
                "value": [one.row for one in made.matches if one.taken],
            }],
        }

    def _to_look_at(made: Plan) -> bool:
        """Whether any game this run makes was left for the person to settle."""
        return any(one.reason or one.found_by == "picked" for one in made.making)

    def _matches_step(made: Plan, values: dict) -> dict:
        """The games the look could not settle, each with the entry it would offer, and the
        rest behind Show all."""
        key = answer_key("picks")
        rows = [{"key": one.row, "name": one.name, "entry": one.candidate,
                 "vps_id": one.vps_id,
                 "reason": ctx.t(f"why.{one.reason}") if one.reason in plan_for.WHY else "",
                 "settled": bool(one.vps_id) or one.found_by == "picked"}
                for one in made.making]
        return {
            "step": "matches",
            "title": ctx.t("wizard.matches.title"),
            "help": " ".join((
                ctx.t("wizard.matches.about"), ctx.t("wizard.matches.gains"),
                ctx.t("wizard.matches.ask", count=sum(1 for one in rows if not one["settled"])),
                ctx.t("wizard.matches.leave"))),
            "fields": [{"key": key, "type": "settle",
                        "label": ctx.t("wizard.matches.label"), "rows": rows,
                        "value": dict(values.get(key) or {})}],
        }

    def _names_matter(made: Plan) -> bool:
        """Whether a game made here was matched and the entry would have it called, or
        described, otherwise than the source does."""
        return any(one.vps_id for one in made.making) and bool(
            made.renamed()[0] or any(made.differing().values()))

    def _names_step(reader: ModuleType, library: SourceLibrary, made: Plan,
                    values: dict) -> dict:
        """Whose names and details the matched games take: VPS's, or the source's."""
        said = answers(values)
        source = _name(ctx, reader)
        plain = library.source_id == folders.SOURCE_ID
        count, examples = made.renamed()
        differing = made.differing()
        first = [name for name in plan_for.DETAIL_FIELDS if differing[name]]
        counted = ", ".join(ctx.t("wizard.names.count", name=ctx.t(f"wizard.names.field.{name}"),
                                  count=differing[name]) for name in first)
        return {
            "step": "names",
            "title": ctx.t("wizard.names.title"),
            "help": (ctx.t("wizard.names.help_plain") if plain
                     else ctx.t("wizard.names.help", source=source)),
            "fields": [
                {"key": "names", "type": "choice",
                 "label": ctx.t("wizard.names.names.label"),
                 "value": str(said.get("names") or plan_for.DEFAULT_NAMES),
                 "choices": [
                     ["vps", ctx.t("wizard.names.vps"), ctx.t("wizard.names.vps.help"),
                      "cloud_done"],
                     ["source", (ctx.t("wizard.names.source_plain") if plain
                                 else ctx.t("wizard.names.source", source=source)),
                      ctx.t("wizard.names.source.help"), "folder"]],
                 "help": ctx.t("wizard.names.examples", count=count,
                               examples="; ".join(ctx.t("wizard.names.example", was=was, now=now)
                                                  for was, now in examples))
                 if count else ctx.t("wizard.names.same")},
                {"key": "details", "type": "choice",
                 "label": ctx.t("wizard.names.details.label"),
                 "value": str(said.get("details") or plan_for.DEFAULT_DETAILS),
                 "choices": [
                     ["vps", ctx.t("wizard.names.vps"), ctx.t("wizard.names.details.vps.help"),
                      "cloud_done"],
                     ["source", (ctx.t("wizard.names.details.source_plain") if plain
                                 else ctx.t("wizard.names.details.source", source=source)),
                      ctx.t("wizard.names.details.source.help"), "folder"],
                     ["some", ctx.t("wizard.names.details.some"),
                      ctx.t("wizard.names.details.some.help"), "checklist"]],
                 "help": ctx.t("wizard.names.differ", counts=counted) if counted
                 else ctx.t("wizard.names.agree")},
                {"key": "details_some", "type": "multi",
                 "label": ctx.t("wizard.names.some.label"),
                 "value": [str(one) for one in (said["details_some"]
                                                if "details_some" in said else first)],
                 "choices": [[name, ctx.t("wizard.names.some.choice",
                                          name=ctx.t(f"wizard.names.field.{name}"),
                                          count=differing[name])]
                             for name in plan_for.DETAIL_FIELDS],
                 "help": ctx.t("wizard.names.some.help")},
            ],
        }

    def _media_found(library: SourceLibrary, made: Plan, values: dict) -> dict[str, Any] | None:
        """What the source has for pictures, or nothing where the page has no question."""
        media = made.source("media")
        if media is None or not media.active:
            return None
        makers = logos.candidates(media.path)
        pictures = sum(len(one.kinds) for one in (*made.making, *made.filling))
        if not (pictures or makers):
            return None
        return {"images": wheel_files(library, answers(values)), "makers": len(makers)}

    def _media_step(reader: ModuleType, library: SourceLibrary, made: Plan, values: dict,
                    media: dict[str, Any]) -> dict:
        """What a wheel and a logo are, what the source's wheel images are, and whether to
        fill the gaps afterwards."""
        said = answers(values)
        source = _name(ctx, reader)
        images = media["images"]
        kinds = Counter(kind for one in (*made.making, *made.filling) for kind in one.kinds)
        found = ", ".join(ctx.t("wizard.media.kind", name=ctx.t(f"media.kind.{kind}"), count=count)
                          for kind, count in kinds.most_common() if count)
        fields: list[dict[str, Any]] = []
        if images:
            sample = images[::max(1, len(images) // 3)][:3]
            looks = looks_like(library, said)

            def option(kind: str, name: str, help_text: str) -> list[Any]:
                if kind == looks:
                    help_text = ctx.t("wizard.media.looks_like", help=help_text)
                return [kind, name, help_text, sample]

            fields.append(
                {"key": answer_key("wheels_as"), "type": "choice",
                 "label": (ctx.t("wizard.media.wheels_as.label_plain")
                           if library.source_id == folders.SOURCE_ID
                           else ctx.t("wizard.media.wheels_as.label", source=source)),
                 "value": wheels_as_of(library, said),
                 "choices": [
                     option(mapping.LOGOS, ctx.t("wizard.media.logos"),
                            ctx.t("wizard.media.logos.help")),
                     option(mapping.WHEELS, ctx.t("wizard.media.wheels"),
                            ctx.t("wizard.media.wheels.help"))]})
        fields.append({"key": "fetch_media", "type": "switch",
                       "label": ctx.t("wizard.media.fetch.label"),
                       "value": _on(said, "fetch_media"),
                       "help": " ".join((ctx.t("wizard.media.fetch.what"),
                                         ctx.t("wizard.media.fetch.about"),
                                         ctx.t("wizard.media.fetch.fills"),
                                         ctx.t("wizard.media.fetch.matched"),
                                         ctx.t("wizard.media.fetch.later")))})
        if media["makers"]:
            fields.append({"key": "logos", "type": "switch",
                           "label": ctx.t("wizard.media.logos.label"),
                           "value": _on(said, "logos"),
                           "help": ctx.t("wizard.media.logos.switch_help",
                                         count=media["makers"])})
        return {
            "step": "media",
            "title": ctx.t("wizard.media.title"),
            "help": ctx.t("wizard.media.found", kinds=found) if found else "",
            "fields": fields,
        }

    def _things_found(library: SourceLibrary, values: dict,
                      existing: list[dict]) -> tuple[dict[str, int], int]:
        """How many games each thing comes with, and how many need a ROM that is not
        there. Planned with every folder switched on, so one switched off can be switched
        back."""
        everything = plan(library, {**values, **{things.switch(key): True
                                                 for key in things.THINGS}}, existing)
        user, roms = everything.source("user"), everything.source("roms")
        counts = things.counted(
            library, everything, _systems(values), held.roms, held.named,
            saved_of(user.path if user is not None and user.active else ""),
            real_of(roms.path if roms is not None and roms.active else ""),
            _on(answers(values), things.switch("roms")))
        return counts, plan_for.missing_roms(everything)

    def _things_step(counts: dict[str, int], short: int, values: dict) -> dict:
        """A switch for each thing the source keeps beside its games, with how many games
        it comes with."""
        said = answers(values)
        return {
            "step": "things",
            "title": ctx.t("wizard.things.title"),
            "help": " ".join(part for part in (
                ctx.t("wizard.things.help"),
                ctx.t("wizard.things.missing", count=short) if short else "") if part),
            "fields": [{"key": things.switch(key), "type": "switch",
                        "label": ctx.t(f"wizard.things.{key}.label"),
                        "value": _on(said, things.switch(key)),
                        "help": ctx.t("wizard.things.count", count=counts[key])}
                       for key in things.THINGS if counts.get(key)],
        }

    def _refuse_how(values: dict, mode: str = "copy") -> dict[str, str]:
        """What is wrong with the answer to the how step, by field. Both ways need a
        folder to put the games in; it is made if it is not there yet. A caller that
        names none and moves leaves the games where they are."""
        if mode == "move" and "into" not in values:
            return {}
        into = str(values.get("into") or "").strip()
        if not into:
            return {"into": ctx.t("reason.say_into")}
        if not can_be_made(into):
            return {"into": ctx.t("reason.unreachable")}
        return {}

    def _how_step(reader: ModuleType, library: SourceLibrary, values: dict,
                  games: list[SourceGame], errors: dict[str, str] | None = None) -> dict:
        """Copy or move, and where the copies go.

        Copy is the default: moving leaves whatever else opens those files, a frontend's
        list among it, pointing at files that are gone, and the choice says so before it
        is made.
        """
        folder = Path(library.root).name or library.root
        moving_help = (ctx.t("wizard.how.move.folders_help")
                       if library.source_id == folders.SOURCE_ID
                       else ctx.t("wizard.how.move.frontend_help", source=_name(ctx, reader)))
        into = suggested_into(values)
        made = not Path(into).expanduser().exists() if into else False

        def says(how: str) -> str:
            need, free, where = space(library, {**values, "into": into}, how, games)
            return " ".join(part for part in (
                ctx.t("wizard.how.into.what"),
                ctx.t("wizard.how.into.new") if made else "",
                ctx.t("wizard.how.into.room", size=plan_for.size_of(need),
                      free=plan_for.size_of(free), where=where)
                if into and free >= 0
                else ctx.t("wizard.how.into.needs", size=plan_for.size_of(need)))
                if part)

        step: dict[str, Any] = {
            "step": "how",
            "title": ctx.t("wizard.how.title"),
            "help": ctx.t("wizard.how.help", count=sum(1 for one in games if one.table_file),
                          folder=folder),
            "fields": [
                {"key": "how", "type": "choice",
                 "label": ctx.t("wizard.how.how.label"),
                 "value": str(values.get("how") or "copy"),
                 "choices": [
                     ["copy", ctx.t("wizard.how.copy"),
                      ctx.t("wizard.how.copy.help", folder=folder), "content_copy"],
                     ["move", ctx.t("wizard.how.move"),
                      moving_help, "drive_file_move"]]},
                {"key": "into", "type": "path", "wants": "dir",
                 "label": ctx.t("wizard.how.into.label"), "value": into,
                 "opens_at": str(Path(library.root).parent),
                 "help": {"follows": "how", "says": {"copy": says("copy"),
                                                      "move": says("move")},
                          "else": says("copy")}},
            ],
        }
        if errors:
            step["errors"] = errors
        return step

    def _matching_rows(made: Plan, games: list[SourceGame]) -> list[list[str]]:
        """What the look made of the games, or why it made nothing of them."""
        if not games:
            return []
        if not made.catalog.get("enabled", True):
            return [[ctx.t("wizard.summary.matching"), ctx.t("wizard.summary.matching.off")]]
        if not made.catalog.get("ready"):
            return [[ctx.t("wizard.summary.matching"),
                     ctx.t("wizard.summary.matching.no_catalog")]]
        left = plan_for.not_matched(made)
        if not left:
            return []
        return [[ctx.t("wizard.summary.not_matched"),
                 ", ".join(ctx.t(f"unmatched.{why}", count=count)
                           for why, count in sorted(left.items()))]]

    def _left_behind(folder: str, why: str, count: int) -> str:
        if why == "pages":
            return ctx.t("wizard.summary.left_behind_pages", folder=folder, count=count)
        if why == "videos":
            return ctx.t("wizard.summary.left_behind_videos", folder=folder, count=count)
        return ctx.t("wizard.summary.left_behind_folder", folder=folder, count=count)

    def _choice_rows(reader: ModuleType, library: SourceLibrary, made: Plan,
                     values: dict) -> list[list[str]]:
        """What the Names and details, Art and ROMs and more pages decided."""
        said = answers(values)
        source = _name(ctx, reader)
        plain = library.source_id == folders.SOURCE_ID
        rows: list[list[str]] = []
        if _names_matter(made):
            rows.append([ctx.t("wizard.summary.names"),
                         (ctx.t("wizard.names.source_plain") if plain
                          else ctx.t("wizard.names.source", source=source))
                         if made.names == "source" else ctx.t("wizard.names.vps")])
            if made.details == "some":
                kept = ", ".join(ctx.t(f"wizard.names.field.{name}")
                                 for name in made.detail_fields)
                shown = ctx.t("wizard.summary.details_some", fields=kept) if kept \
                    else ctx.t("wizard.names.vps")
            else:
                shown = ((ctx.t("wizard.names.details.source_plain") if plain
                          else ctx.t("wizard.names.details.source", source=source))
                         if made.details == "source" else ctx.t("wizard.names.vps"))
            rows.append([ctx.t("wizard.summary.details"), shown])
        media = _media_found(library, made, values)
        if media:
            if media["images"]:
                rows.append([ctx.t("wizard.summary.wheel_images"),
                             ctx.t("wizard.media.logos.summary")
                             if made.wheels_as == mapping.LOGOS
                             else ctx.t("wizard.media.wheels")])
            rows.append([ctx.t("wizard.media.fetch.label"), _yes(made.fetch_media)])
            if media["makers"]:
                rows.append([ctx.t("wizard.media.logos.label"), _yes(made.logos)])
        counts, _short = _things_found(library, values, known(library))
        for key in things.THINGS:
            if counts.get(key):
                rows.append([ctx.t(f"wizard.things.{key}.label"),
                             ctx.t("wizard.things.count", count=counts[key])
                             if _on(said, things.switch(key))
                             else ctx.t("wizard.summary.skipped_thing")])
        return rows

    def _yes(on: bool) -> str:
        return ctx.t("wizard.summary.yes") if on else ctx.t("wizard.summary.no")

    def _where(source: Any) -> str:
        if source.path:
            return str(source.path)
        if source.not_found:
            return ctx.t(f"kind.{source.key}.not_found")
        return ctx.t("wizard.summary.not_imported")

    def _summary_step(reader: ModuleType, library: SourceLibrary, made: Plan,
                      values: dict, games: list[SourceGame]) -> dict:
        """Everything the previous steps decided, and what it comes to."""
        found = _preview(library)
        plan_for.measure_fill(made, held_lately)
        counts = plan_for.expected(made)
        mode = _mode(library, values, games)
        tables = sum(1 for one in games if one.table_file)
        need, free, where = space(library, values, mode, games)
        short = short_of_room(need, free, where)

        rows = [[ctx.t("wizard.summary.reads_as"), _name(ctx, reader)],
                [ctx.t("wizard.summary.folder"), library.root]]
        rows += [[source.label if source.active else ctx.t(f"kind.{source.key}.none"),
                  _where(source)] for source in made.sources]
        short_roms = plan_for.missing_roms(made)
        if short_roms:
            rows.append([ctx.t("wizard.summary.roms_missing"),
                         ctx.t("wizard.summary.roms_missing_count", count=short_roms)])
        rows += _matching_rows(made, games)
        rows += _choice_rows(reader, library, made, values)
        if library.in_place:
            rows.append([ctx.t("wizard.summary.as_is"),
                         ctx.t("wizard.summary.game_folders", count=len(library.in_place))])
        filled = len(made.filling)
        if filled or len(made.already) > filled:
            rows.append([ctx.t("wizard.summary.already_here"), ", ".join(
                ctx.t(f"wizard.summary.already.{kind}", count=count)
                for kind, count in (("fill", filled),
                                    ("skip", len(made.already) - filled)) if count)])
        left_out = sum(1 for one in made.matches if not one.existing and not one.taken)
        if left_out:
            rows.append([ctx.t("wizard.summary.skipped"),
                         ctx.t("wizard.summary.skipped_count", count=left_out)])
        inside = ", ".join(dict.fromkeys(
            plan_for.tables_folder(one) for one in plan_for.systems_going(library, games)
            if plan_for.tables_folder(one))) or library.root
        how, going_into = ctx.t("wizard.summary.how"), ctx.t("wizard.summary.going_into")
        if mode == "copy":
            rows += [[how, ctx.t("wizard.summary.how.copy")], [going_into, into_of(values)]]
        elif mode == "move":
            rows += [[how, ctx.t("wizard.summary.how.move")],
                     [going_into,
                      into_of(values) if "into" in values
                      else ctx.t("wizard.how.move.where", folder=inside)]]
        elif mode == "in_place":
            rows += [[how, ctx.t("wizard.summary.how.in_place")],
                     [going_into,
                      ctx.t("wizard.summary.library_folder", folder=library.root)]]
        elif mode == "as_is":
            rows.append([going_into,
                         ctx.t("wizard.summary.library_folder", folder=library.root)])
        else:
            rows.append([going_into, ctx.t("wizard.summary.default_folder")])
        if need:
            rows.append([ctx.t("wizard.summary.space"),
                         ctx.t("wizard.summary.space_free", size=plan_for.size_of(need),
                               free=plan_for.size_of(free), where=where)
                         if free >= 0 else plan_for.size_of(need)])
        rows.append([ctx.t("wizard.summary.imported")])
        rows += [[ctx.t(f"count.{key}"), str(counts[key])] for key in plan_for.COUNTS]
        if any(source.key == "media" and source.active for source in made.sources):
            left = mapping.left_behind(library.source_id, games, ctx.games.kinds())
            if left:
                rows.append([ctx.t("wizard.summary.left_behind"),
                             ", ".join(_left_behind(folder, why, count)
                                       for (folder, why), count in left.items())])

        ready = bool(games) or mode == "as_is"
        reason = short or ("" if ready else ctx.t("reason.nothing_left"))
        confirm = {
            "copy": lambda: ctx.t("wizard.summary.confirm_copy", count=tables),
            "move": lambda: ctx.t("wizard.summary.confirm_move", count=tables),
            "in_place": lambda: ctx.t("wizard.summary.confirm_in_place", count=tables),
            "as_is": lambda: ctx.t("wizard.summary.confirm_as_is"),
        }.get(mode, lambda: ctx.t("wizard.summary.confirm", count=len(games)))()

        return {
            "step": "summary",
            "title": ctx.t("wizard.summary.title"),
            "ready": ready and not short,
            "reason": reason,
            "summary": rows,
            "notes": [*found["notes"], *made.notes],
            "confirm": confirm,
        }

    def start_import(values: dict) -> dict:
        """Convert the chosen source into game folders. Answers with a job.

        A job because it is slow and because it is the shape everything slow here takes:
        an import of six hundred games is minutes of copying, and a request that waited
        for it would time out somewhere in the middle with no way to ask what happened.
        Progress and the outcome are on /api/v1/jobs, the same as a library scan.
        """
        try:
            return _start(values or {})
        finally:
            progress.clear()

    def _start(values: dict) -> dict:
        typed = str(values.get("path") or "").strip()
        location = str(values.get("location") or "")
        systems = _systems(values)

        def refused(reason: str) -> dict:
            """The shape the Manager reads a refusal by: not started, and why."""
            return {"started": False, "ok": False, "reason": reason}

        survey = _once(ctx.games.survey)
        no_frontend = str(values.get("source_type") or "") == folders.SOURCE_ID
        if typed:
            point_at(typed)
        reader = folders if no_frontend else (
            _reader_for(values, Path(typed), survey) if typed else None)
        if reader is None:
            return refused(ctx.t("reason.nothing_readable"))
        wanted = _rooted(reader, values, typed)
        path = Path(wanted)
        if not wanted or not path.is_dir():
            return refused(ctx.t("reason.nothing_readable"))
        point_at(wanted)

        library = read(reader, path, values, survey)
        declare_roots(library, values)
        checked = plan_for.going(library, plan(library, values, known(library)), systems)
        mode = _mode(library, values, checked)
        typed_into = "into" in values
        if mode == "copy" or (mode == "move" and typed_into):
            values = {**values, "into": into_of(values, location)}
        if mode in ("copy", "move"):
            wrong = _refuse_how(values, mode)
            if wrong:
                return refused(wrong["into"])
        short = short_of_room(*space(library, values, mode, checked))
        if short:
            return refused(short)
        ctx.config.set(ANSWERS_KEY, json.dumps(
            {**remembered(), **{key: values[key] for key in REMEMBERED if key in values}}))

        def work(job: Any) -> dict:
            job.log(f"Read {len(library.games)} games from {library.root}")
            # First, so the plan below sees the games that are already in it.
            rooted = (ctx.games.use_library_folder(library.root)["location_id"]
                      if library.in_place else "")
            made = plan(library, values, ctx.games.existing())
            games = plan_for.going(library, made, systems)
            mode = _mode(library, values, games)
            where, places = location, {}
            if mode in ("copy", "move") and typed_into and not job.halt.is_set():
                Path(values["into"]).expanduser().mkdir(exist_ok=True)
                where = ctx.games.use_library_folder(values["into"])["location_id"]
            elif mode in ("move", "in_place"):
                # Only the systems this run acts on: a deselected one is not a place
                # anyone asked to have registered.
                places = {one.name: rooted or ctx.games.use_library_folder(
                    plan_for.tables_folder(one))["location_id"]
                    for one in plan_for.systems_going(library, games)
                    if plan_for.tables_folder(one)}
            into_now = str(values.get("into") or "") if typed_into else ""
            plan_for.measure_fill(made, held_of)
            want = plan_for.expected(made)
            measure = held.measured(Progress(), "")
            kinds = ctx.games.kinds()

            roms_folder, user_folder = made.source("roms"), made.source("user")
            roms_dir = roms_folder.path if roms_folder and roms_folder.active else ""
            kept = saved_of(user_folder.path if user_folder and user_folder.active else "")

            def weigh(game: Any) -> int:
                return plan_for.bytes_to_copy([game], library.source_id, kinds,
                                              held.companions_of,
                                              move=not carried_game(mode, game, into_now),
                                              sized=measure,
                                              roms=held.roms, roms_dir=roms_dir,
                                              named=held.named, saved=kept,
                                              real=real_of(roms_dir),
                                              wheels_as=made.wheels_as)

            def tell(standing: adopt.Standing) -> None:
                by_bytes = standing.bytes_total > 0
                job.progress(
                    standing.bytes_done if by_bytes else standing.games_done,
                    standing.bytes_total if by_bytes else standing.games_total,
                    ctx.t("job.importing", number=standing.games_done + 1,
                          count=standing.games_total, name=standing.name,
                          done=plan_for.size_of(standing.bytes_done),
                          size=plan_for.size_of(standing.bytes_total))
                    if by_bytes else
                    ctx.t("job.importing_games", number=standing.games_done + 1,
                          count=standing.games_total, name=standing.name))

            progress.clear()
            report = adopt.run(ctx, library, systems, where, made,
                               move=mode in ("move", "in_place"), places=places,
                               weigh=weigh, report=tell, halted=job.halt.is_set)
            # Counted again from the plan the run was handed, so the report says what
            # was asked for beside what happened rather than what was hoped for.
            # A stopped run is short by what it was stopped before, which `left` says.
            report["against"] = [] if report.get("stopped") else plan_for.against(want, report)
            report["expected"] = want
            report["program"] = _name(ctx, reader)
            # Every answer but the ticks and picks, which are held under a key of their own
            # for what was read: a second import starts from these and is asked those again.
            report["answers"] = {key: one for key, one in values.items()
                                 if "." not in key
                                 and key not in ("games", "picks", "only", "location")}
            job.stopped = bool(report.get("stopped"))
            # Counted from the rows the report lists, not from `failed`: a game whose
            # folder was made and whose file was not imported is in the report too.
            job.problems = sum(1 for row in report["rows"]
                               if row.get("error") or row.get("refused"))
            created = report["games"] - report["filled"]
            left, filled, failed = report.get("left", 0), report["filled"], report["failed"]
            if job.stopped:
                job.detail = (
                    ctx.t("job.detail_stopped_filled", made=created, filled=filled,
                          failed=failed, left=left) if filled else
                    ctx.t("job.detail_stopped", made=created, failed=failed, left=left))
            else:
                job.detail = (
                    ctx.t("job.detail_filled", made=created, filled=filled, failed=failed)
                    if filled else ctx.t("job.detail", made=created, failed=failed))
            job.log(f"Created {created}, filled {report['filled']}, "
                    f"failed {report['failed']}")
            if (made.fetch_media and report["matched"]
                    and not (job.stopped or report["media_started"])):
                job.log("Get missing media was not started: a fill was already running")
            return report

        job = ctx.jobs.submit("import", work, stoppable=True, keep_result=True)
        return {"started": True, "job_id": job.id,
                "links": {"job": f"/api/v1/jobs/{job.id}"}}

    class ImportDialog:
        """The dialog core draws for Import a Library. Stopping a run is core's: it asks
        `ctx.jobs.stop("import")`, which ends the import after the game in hand."""

        ask = staticmethod(wizard_form)
        check = staticmethod(wizard_check)
        run = staticmethod(start_import)
        progress = staticmethod(wizard_progress)

    ctx.ui.action("import", ImportDialog())
    ctx.add_router(reading)
    ctx.add_router(writing)

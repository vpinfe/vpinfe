"""The library, as an extension is allowed to see it.

A curated view over the same services the API's own routes call, so the two cannot
answer differently. An extension in this process cannot use the HTTP API - a synchronous
call into the server it is running inside deadlocks, which the Manager already pays for
elsewhere - and it may not import the library either. This is the door.

What it will do is bounded twice. The manifest's scopes decide which of these an
extension may call at all, which is what makes declaring them mean something. And a path
it hands over has to be inside a folder it said it works from, so an extension cannot use
core as a way to read somewhere it never declared.
"""

from __future__ import annotations

import errno
import logging
import os
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common.i18n import t

from .contract import ContractError
from .hardware import HARDWARE
from .library_collections import COLLECTIONS
from .offered import Offered

if TYPE_CHECKING:
    from .context import ExtensionFiles

logger = logging.getLogger("vpinfe.common.extensions.games")

GAMES_READ = "games:read"
GAMES_WRITE = "games:write"
CONFIG_WRITE = "config:write"

# What core has said an extension may do to the library, by the name an extension calls
# it: `name -> (scope, callable)`.
#
# Core fills this in, because these are core's own functions and the host is not the
# place that knows them. Filled in rather than imported so this module stays underneath
# the API rather than reaching up into it, and rather than reimplemented so the two
# cannot answer differently - a second copy of core's folder-naming rule drifts from it.
_OFFERED: dict[str, tuple[str, Callable[..., Any]]] = {}


def offer(name: str, scope: str, run: Callable[..., Any]) -> None:
    """Core: let extensions call this, for anything declaring `scope`."""
    _OFFERED[name] = (scope, run)


def offered() -> tuple[str, ...]:
    return tuple(sorted(_OFFERED))


def withdraw_all() -> None:
    """For tests, which build an app per case and must not inherit the last one's."""
    _OFFERED.clear()
    COLLECTIONS.clear()
    HARDWARE.clear()


class ExtensionGames(Offered):
    """The named methods below are worth having a shape for - they take a path and check
    it, or they answer with something an importer wants. Everything else core offered an
    extension calls the way the API names it, gated the same way."""

    def __init__(self, name: str, scopes: Iterable[str], files: ExtensionFiles) -> None:
        super().__init__(name, scopes, _OFFERED)
        self._files = files

    # -- the bound on a path --------------------------------------------------

    def _source(self, path: str | Path) -> Path:
        """A file the extension is handing over, checked against what it declared.

        Tighter than the same check on the HTTP routes, and it can be: this one knows
        which extension is asking, where a route only knows a path.
        """
        wanted = Path(str(path or "")).expanduser().resolve()
        self._files._require_inside(wanted)
        if not wanted.is_file():
            raise FileNotFoundError(t("error.games.missing_file", path=wanted))
        return wanted

    def _game(self, game_id: str) -> Any:
        from common.games import game_identity
        from common.games.game_repository import all_games

        found = game_identity.ensure_unique_ids(all_games()).get(str(game_id or ""))
        if found is None:
            raise LookupError(t("error.games.unknown_game", game_id=game_id))
        return found

    # -- reading --------------------------------------------------------------

    def kinds(self) -> tuple[str, ...]:
        """The media kinds this library collects: every kind this build stores that is not
        switched off. The vocabulary an importer maps onto, and the reason it does not have
        to hard-code a list that would go stale."""
        self._needs(GAMES_READ)
        from common.games import library_policy
        from common.media.media_specs import MEDIA_SPECS

        kept = library_policy.kept_kinds()["media"]
        return tuple(spec.kind for spec in MEDIA_SPECS if spec.kind in kept)

    def switched_off_kinds(self) -> tuple[str, ...]:
        """The media kinds this build stores that the library has switched off. `put_media`
        still takes one by name; only what an importer brings on its own leaves them out."""
        self._needs(GAMES_READ)
        from common.media.media_specs import MEDIA_SPECS

        kept = set(self.kinds())
        return tuple(spec.kind for spec in MEDIA_SPECS if spec.kind not in kept)

    def existing(self) -> list[dict]:
        """Every game already here, in the little an importer needs to recognize one.

        Not the whole library: what a second run is asking is "have I made this one
        before", and folder name, catalog id and title answer it. Anything more would be
        handing over the library to answer a question about names. `table_files` is the
        names of the game files each holds, cased down, for the one test that has no id
        or folder to go by.
        """
        self._needs(GAMES_READ)
        from common.games import game_identity
        from common.games.game_metadata import (
            game_ipdb_id,
            game_title,
            normalize_meta,
            section,
        )
        from common.games.game_repository import all_games
        from common.games.tables import entry_filename, table_entries

        found = []
        for game in all_games():
            meta = normalize_meta(getattr(game, "meta_config", {}))
            info = section(meta, "Info")
            files = sorted({entry_filename(entry).lower()
                            for entry in table_entries(meta).values()} - {""})
            found.append({
                "game_id": game_identity.game_id(game),
                "folder_name": Path(str(game.full_path_game)).name,
                "name": game_title(game),
                "vps_id": str(info.get("VPSId", "") or ""),
                "ipdb_id": game_ipdb_id(game),
                "table_files": files,
            })
        return found

    def folder_name_for(self, name: str) -> str:
        """The folder a game of this name would get.

        Asked rather than guessed. An importer needs this before it creates anything -
        to see what it already has, and to notice that two of its own games want the
        same folder - and a second copy of the rule drifts from this one silently.
        `Star Trek: The Next Generation` and `Star Trek The Next Generation` are one
        folder here, and an importer reading them as two fails on a name already taken.
        """
        self._needs(GAMES_READ)
        from common.games.game_service import sanitize_dir_name

        return sanitize_dir_name(name)

    def folder(self, game_id: str) -> str:
        self._needs(GAMES_READ)
        return str(self._game(game_id).full_path_game)

    def survey(self, path: str | Path) -> dict[str, list[str]]:
        """What a folder holds: `game_folders` the library would read as games, and the
        `loose` table files lying in it. Asked of core for the same reason as
        `folder_name_for`: what makes a folder a game is core's rule, and the answer
        depends on which programs this install plays.

        Only a folder this extension works from.
        """
        self._needs(GAMES_READ)
        from common.games import tables

        wanted = Path(str(path or "")).expanduser().resolve()
        self._files._require_inside(wanted)
        return tables.survey(wanted)

    # -- writing --------------------------------------------------------------

    def create(self, name: str, location: str = "") -> str:
        """Make an entry and answer with its id."""
        self._needs(GAMES_WRITE)
        from common.games import game_identity, game_service

        folder = game_service.create_game(name, location)
        from common.games.game_repository import all_games

        wanted = unicodedata.normalize("NFC", str(folder.resolve()))
        made = next((game for game in all_games()
                     if unicodedata.normalize(
                         "NFC", str(Path(str(game.full_path_game)).resolve())) == wanted),
                    None)
        if made is None:
            logger.warning("%s created %s, which is not among the games this device reads",
                           self._name, folder)
            raise LookupError(t("error.games.created_unseen"))
        return game_identity.ensure_id(made)

    def add_table(self, game_id: str, path: str | Path, move: bool = False,
                  names: Iterable[str] = ()) -> dict:
        """Copy a game file into an entry, with whatever belongs to it.

        Answers with the table's id, the companions that came, and the ROM the table
        turns out to need - a caller counting what an import produced needs to know what
        actually landed, and anything keyed on the ROM has no other way to learn it this
        early. `named` holds those of `names` the table's script mentions, read with the
        ROM rather than from a second open of the file.

        `move` takes the files out of where they were, which is writing to a folder the
        extension only declared it reads from, so it needs the `fs:write` capability.
        """
        self._needs(GAMES_WRITE)
        from common.games import backglass_order, game_service, library_folders
        from common.ids import new_id

        if move and not (self._files is not None and self._files.may_write()):
            raise ContractError(
                f"{self._name} asks core to move a file, which needs the fs:write "
                "capability its manifest does not declare")
        source = self._source(path)
        table_id = new_id()
        folder = library_folders.writable_game_folder(self.folder(game_id))
        found = game_service.add_table_file(folder, source, table_id, move=move,
                                            names=tuple(names))
        backglass_order.settle(game_id)
        return {"table_id": found.get("table_id", table_id),
                "companions": tuple(found["companions"]),
                "rom": found.get("rom", ""),
                "named": tuple(found.get("named", ()))}

    def companions_of(self, path: str | Path) -> tuple[str, ...]:
        """What would come with this table if it were added. For counting beforehand,
        so a plan and the run that follows it agree."""
        self._needs(GAMES_READ)
        from common.games import game_service

        return tuple(one.name for one in
                     game_service.companions_beside(self._source(path)))

    def companions_for(self, paths: Iterable[str | Path]) -> dict[str, tuple[str, ...]]:
        """`companions_of` for many tables at once, each folder listed once.

        Keyed by the path as it was given. A table that is not there is left out where
        `companions_of` raises for it, so the caller can tell a table with nothing beside
        it from one that is gone.
        """
        self._needs(GAMES_READ)
        from common.games import game_service

        roots = [Path(one).expanduser().resolve() for one in self._files.roots()]
        folders: dict[Path, Path] = {}
        found: dict[str, tuple[str, ...]] = {}
        for given in paths:
            wanted = Path(str(given or "")).expanduser()
            if wanted.is_symlink():
                try:
                    found[str(given)] = self.companions_of(given)
                except FileNotFoundError:
                    pass
                continue
            if wanted.parent not in folders:
                folders[wanted.parent] = wanted.parent.resolve()
            source = folders[wanted.parent] / wanted.name
            self._files._require_inside(source, roots)
            beside = game_service.files_in(source.parent)
            if source in beside:
                found[str(given)] = tuple(
                    one.name for one in game_service.companions_beside(source, beside))
                continue
            # A name that differs from the file's in case is still the file on a
            # case-insensitive mount, which the listing cannot say.
            try:
                found[str(given)] = self.companions_of(given)
            except FileNotFoundError:
                pass
        return found

    def roms_of(self, paths: Iterable[str | Path]) -> dict[str, str]:
        """The ROM each game file names, read from the file as `add_table` reads it, for
        counting what an import would bring before it brings anything.

        Keyed by the path as it was given. A file that is not there, or that names no ROM,
        is left out. Opening a table costs a read of the file, so a caller asks for a
        library in batches and keeps what it is told.
        """
        return {given: rom for given, (rom, _) in self.reads_of(paths).items() if rom}

    def reads_of(self, paths: Iterable[str | Path],
                 names: Iterable[str] = ()) -> dict[str, tuple[str, tuple[str, ...]]]:
        """`roms_of` and which of `names` each table's script mentions, from one read.

        Keyed by the path as it was given; a file that is not there is left out, and one
        that names no ROM answers an empty one.
        """
        self._needs(GAMES_READ)
        from common.games.formats.script_names import named_in
        from common.games.library_enrichment import read_one

        wanted = tuple(names)
        found: dict[str, tuple[str, tuple[str, ...]]] = {}
        for given in paths:
            try:
                source = self._source(given)
            except FileNotFoundError:
                continue
            parsed = read_one(source) or {}
            found[str(given)] = (
                str(parsed.get("rom") or "").strip(),
                tuple(sorted(named_in(parsed.get("game_data") or "", wanted))))
        return found

    def saved_sets(self, path: str | Path) -> tuple[str, ...]:
        """The names of the sets a `VPReg.stg` holds, for matching to the tables that
        save under them. Empty where the file is not one."""
        self._needs(GAMES_READ)
        from common.games.formats import vpreg

        return tuple(vpreg.sets_in(self._source(path)))

    def put_saved_sets(self, game_id: str, path: str | Path, sets: Iterable[str]) -> int:
        """Write the named sets of a `VPReg.stg` as the game's own `user/VPReg.ini`.

        Only these sets, not the file: a copy would hand every table the values every
        other table saved. Answers with how many sets were written. Never replaces: a
        set the game's `VPReg.ini` already holds is left as it is, and only the sets it
        lacks are added to the end.
        """
        self._needs(GAMES_WRITE)
        from common.games import library_folders
        from common.games.formats import vpreg
        from common.uploads.asset_import_service import folder_for

        source = self._source(path)
        game_dir = library_folders.writable_game_folder(self.folder(game_id))
        into = folder_for("user", game_dir)
        if into is None:
            raise ValueError(t("error.games.no_folder", kind="user"))
        landing = into / "VPReg.ini"
        before = landing.read_text(encoding="utf-8", errors="replace") if landing.exists() else ""
        text = vpreg.ini_text(vpreg.sets_in(source), sets, skip=vpreg.sections_in(before))
        if not text:
            return 0
        into.mkdir(parents=True, exist_ok=True)
        joined = before + ("" if not before or before.endswith("\n") else "\n")
        joined += ("\n" if before.strip() else "") + text
        landing.write_text(joined, encoding="utf-8", newline="\n")
        return text.count("\n[") + 1

    def asset_kinds(self) -> tuple[str, ...]:
        """The kinds that live in a folder of their own, and so can be placed whole."""
        self._needs(GAMES_READ)
        from common.uploads.asset_import_service import folder_kinds

        return folder_kinds()

    def put_asset(self, game_id: str, kind: str, path: str | Path,
                  rom: str = "") -> str:
        """Put a file or a whole folder where this kind of asset belongs.

        For the things that are neither the game file nor artwork: a ROM set, an
        alternative sound bank, a color set. Where each goes is the registry's answer,
        the same one an upload gets, so a library imported from another frontend puts
        them where an upload would have.

        Answers with the path it took, relative to the game. Never replaces: a second
        run, or something somebody put there on purpose, is not ours to overwrite - the
        same rule the import applies to games and to a table's companions.
        """
        self._needs(GAMES_WRITE)
        import shutil

        from common.games import library_folders
        from common.uploads.asset_import_service import folder_for

        source = Path(str(path or "")).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(t("error.games.nothing_at", path=source))
        self._files._require_inside(source)

        game_dir = library_folders.writable_game_folder(self.folder(game_id))
        into = folder_for(kind, game_dir, rom)
        if into is None:
            raise ValueError(t("error.games.rom_needed" if kind in self.asset_kinds()
                               else "error.games.no_folder", kind=kind))

        landing = into / source.name
        if landing.exists():
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(landing))
        into.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, landing)
        else:
            shutil.copy2(source, landing)
        logger.info("%s put %s into %s", self._name, source.name, into)
        # Forward slashes, because this is answered to a caller rather than used as a
        # path here - the same rule every other path this project hands out follows.
        return landing.relative_to(game_dir).as_posix()

    def put_rom_alias(self, game_id: str, alias: str, rom: str) -> str:
        """Write the game's ROM name map with one line: `alias` names `rom`.

        The name a table's script asks for, and the ROM set that answers it. Answers with
        the path it wrote, relative to the game. Never replaces: a game that has a name
        map keeps it whole, and the line is not merged into it.
        """
        self._needs(GAMES_WRITE)
        from common.games import library_folders
        from common.uploads.asset_import_service import folder_for

        names = (str(alias or "").strip(), str(rom or "").strip())
        if not all(names) or any("," in one or "\n" in one for one in names):
            raise ValueError(t("error.games.rom_needed", kind="rom_alias"))
        game_dir = library_folders.writable_game_folder(self.folder(game_id))
        into = folder_for("rom_alias", game_dir)
        if into is None:
            raise ValueError(t("error.games.no_folder", kind="rom_alias"))
        landing = into / "alias.txt"
        if landing.exists():
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(landing))
        into.mkdir(parents=True, exist_ok=True)
        landing.write_text(f"{names[0]},{names[1]}\n", encoding="utf-8", newline="\n")
        logger.info("%s wrote %s", self._name, landing)
        return landing.relative_to(game_dir).as_posix()

    def put_manufacturer_logos(self, makers: Iterable[object],
                               candidates: Iterable[tuple[str, str | Path]]) -> dict:
        """Add to the manufacturer logos folder the images that serve one of `makers`.

        A candidate is (the maker name the image stands for, the image). The folder is
        shared by every theme, so nothing in it is replaced and an image no maker asks
        for stays behind. Answers {"placed": [...], "held": [...]}, by file name.
        """
        self._needs(CONFIG_WRITE)
        from common.media.manufacturer_logos import add_logos

        return add_logos(makers, [(name, self._source(path)) for name, path in candidates])

    def put_media(self, game_id: str, kind: str, path: str | Path,
                  table_stem: str = "", replace: bool = True, page: int = 1) -> str:
        """Put a file in one of an entry's media slots. Answers with what it landed as.

        `replace=False` fills an empty slot and raises FileExistsError where something
        already serves it, the rule `put_asset` keeps: a copy already in the library may
        be the newer one, and a replace cannot be undone.

        `page` is for a kind with pages (a flyer, an instruction card). Page 2 and up go
        beside the page 1 the slot shows, at whatever tier that is, so a downloaded
        `flyer.png` gains `flyer 2.png`. The highest page is one past the last; with no
        page 1 the file becomes page 1.
        """
        self._needs(GAMES_WRITE)
        from common.games import library_folders, media_placement
        from common.media.media_specs import MEDIA_SPECS

        if kind not in {spec.kind for spec in MEDIA_SPECS}:
            raise ValueError(t("error.games.kind_not_found", kind=kind))
        source = self._source(path)
        game_dir = library_folders.writable_game_folder(self.folder(game_id))
        stem = media_placement.page_stem(game_dir, kind, table_stem or game_dir.name,
                                         table_stem, page)
        if not replace:
            held = media_placement.displaced(game_dir, kind, stem, source.suffix, page)
            if held:
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(held[0]))
        written = media_placement.place(game_dir, kind, stem, source, page)
        # Recorded as the user's, because it is: somebody's own library was imported, and
        # a later media refresh must leave it alone rather than treat it as ours to
        # replace.
        media_placement.record_origin(game_dir, written, "user", "")
        return written.name

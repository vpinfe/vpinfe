"""Walking the library folder and turning what is there into games.

Scanning is almost entirely waiting on the filesystem - a real library often sits on
a network share - so folders are read in parallel above a threshold.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from common.config.config_access import MediaConfig
from common.config.config_store import ConfigStore
from common.failures import why
from common.games import file_listing
from common.games import unreadable as unread_files
from common.games.game import Game
from common.games.game_metadata import vpinfe_section
from common.games.info_file import InvalidMetaConfigError, MetaConfig
from common.games.info_migration import restorable_backup
from common.games.tables import (
    default_entry,
    entry_file,
    recorded_default,
    table_entries,
    table_names,
)
from common.games.unreadable import UnreadableGame
from common.kept_copies import backup_names, backup_stamp
from common.media.media_specs import (
    active_set_for,
    apply_media_specs,
    resolve_media_by_table,
    resolve_media_pages_by_table,
)

# Below this many folders the pool costs more than it saves.
_PARALLEL_SCAN_THRESHOLD = 12
# Enough to cover network latency without flooding a share. Measured on NFS: 16 threads
# reached 0.11s where one took 1.02s, and 32 bought only 0.02s more.
_SCAN_WORKERS = 16

logger = logging.getLogger("vpinfe.common.games.game_parser")

# What one folder's scan reports: the game, the folders with no table, and the ones whose
# .info could not be read. Returned rather than appended to the parser, because the scan
# runs on a thread pool.
type ScanResult = tuple[Game | None, list[dict], list[UnreadableGame]]


def _resolved(path: str | Path) -> str:
    """One spelling per folder, for comparing two that may be written differently. A
    path that cannot be resolved is returned as it came rather than raising - it is
    being compared, not opened."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


class GameParser:
    # static terminal colors
    """One pass over the library folder, turning each subfolder into a Game."""

    RED_TERMINAL_TEXT = '\033[31m'
    RESET_TERMINAL_TEXT = '\033[0m'

    def __init__(self, games_root_file_path: str | Path,
                 ini_config: ConfigStore | None = None, *,
                 one_game: bool = False) -> None:
        self.games_root_file_path = Path(games_root_file_path)
        # Whether this path is a game folder rather than a folder of them.
        self.one_game = one_game
        self.playfieldvariant = "table"
        self.games: list[Game] = []
        self.missing_games: list[dict] = []
        self.unreadable_games: list[UnreadableGame] = []
        self.active_sets: dict[str, str] = {}
        if ini_config:
            self.read_media_settings(ini_config)
        # Constructing reads the library; a load_games(reload=True) after it reads it twice.
        self.load_games()

    def read_media_settings(self, ini_config: ConfigStore) -> bool:
        """Take the playfield variant and the active wheel set from `ini_config`. The games
        resolve their media under these when they are read, so a change shows after the
        next read. True when either changed."""
        media_cfg = MediaConfig.from_config(ini_config)
        wheelset = active_set_for("wheel", media_cfg.wheelset)
        active_sets = {"wheel": wheelset} if wheelset else {}
        changed = (media_cfg.playfield_variant, active_sets) != (self.playfieldvariant,
                                                                self.active_sets)
        self.playfieldvariant = media_cfg.playfield_variant
        self.active_sets = active_sets
        return changed

    def load_games(self, reload: bool = False) -> None:  # reload if you want to rescan the games
        if not reload and self.games:
            return

        started_at = perf_counter()
        self.games.clear()
        self.missing_games.clear()
        self.unreadable_games.clear()

        if not self.games_root_file_path.exists():
            return

        logger.debug("Loading games and image paths...")
        folders = ([self.games_root_file_path] if self.one_game
                   else [d for d in sorted(self.games_root_file_path.iterdir())
                         if d.is_dir() and not d.name.startswith('.')])

        # Reading a folder is almost entirely waiting: on a network share the directory
        # listings are nearly the whole scan and the parsing is a fraction of it. Threads
        # cover that wait, and the GIL costs nothing because nothing here is computing. A
        # local disk sees a smaller win from the same change.
        for game, missing, unreadable in self._scan_folders(folders):
            self.missing_games.extend(missing)
            self.unreadable_games.extend(unreadable)
            if game is not None:
                self.games.append(game)

        elapsed = perf_counter() - started_at
        logger.debug(
            "Load completed in %.3fs: loaded=%s missing_info=%s",
            elapsed,
            len(self.games),
            len(self.missing_games)
        )

    def _scan_folders(self, folders: list[Path]) -> list[ScanResult]:
        """Every folder read, in the order they were listed.

        Results are collected in order rather than as they finish: the library is sorted,
        and a scan whose output depended on which network reads returned first would make
        the wheel's order a race. `_build_game` reports what it found rather than
        appending to the parser, because two threads appending to one list is how a
        shared-state bug gets written.
        """
        if len(folders) < _PARALLEL_SCAN_THRESHOLD:
            return [self._scan_one(d) for d in folders]
        workers = min(_SCAN_WORKERS, len(folders))
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="library-scan") as pool:
            return list(pool.map(self._scan_one, folders))

    def _scan_one(self, game_dir: Path) -> ScanResult:
        """One folder, with what it found kept local to this call."""
        missing: list[dict] = []
        unreadable: list[UnreadableGame] = []
        game = self._build_game(game_dir, missing=missing, unreadable=unreadable)
        return game, missing, unreadable

    def _build_game(self, game_dir: Path, *, missing: list[dict] | None = None,
                    unreadable: list[UnreadableGame] | None = None) -> Game | None:
        """One game folder, read from disk. Returns None when it holds no table.

        The whole of what a scan does per game, so refreshing one costs one folder
        rather than the library.
        """
        game = Game()
        game.game_dir_name = game_dir.name
        game.full_path_game = str(game_dir)

        game_contents = set()
        game_subdirs = set()
        subdir_names: dict[str, str] = {}
        folder_read = True

        # Search with scandir to avoid per-entry pathlib stat calls on slow volumes.
        try:
            with os.scandir(game_dir) as entries:
                for entry in entries:
                    if entry.is_dir():
                        # Folded like the extension checks below, and like the
                        # API's own listing: a folder someone named PUPVideos
                        # holds a PUP pack whatever the shift key was doing.
                        game_subdirs.add(entry.name.lower())
                        subdir_names[entry.name.lower()] = entry.name
                        continue
                    game_contents.add(entry.name)
        except OSError:
            folder_read = False
            logger.exception("Failed to enumerate game directory: %s", game_dir)

        game.table_files = table_names(game_contents)
        game.info_names = (frozenset(name.lower() for name in game_contents
                                     if name.lower().endswith(".info"))
                           if folder_read else None)
        info_name = f"{game.game_dir_name}.info"
        # A record alone is enough: an entry keeps its media, its curation and its play
        # history whether or not there is something here to launch. A table alone has to
        # be enough too - discovery writes the record on the first pass that sees the
        # folder, and it only ever sees folders that are already games.
        if not game.table_files and info_name not in game_contents:
            return None
        # Only folders holding a backup open one.
        stamps = backup_names(game_contents, info_name)
        game.info_restorable = bool(
            stamps and restorable_backup(game_dir, names=game_contents))
        if stamps:
            game.info_backup_stamp = backup_stamp(stamps[0])
        if info_name not in game_contents:
            (self.missing_games if missing is None else missing).append({
                'folder': game.game_dir_name,
                'path': str(game_dir),
            })

        # Assets: what this game needs to play as intended, beyond the table
        # itself. Media is a different thing and is loaded below. See
        # docs/conventions.md.
        if any(name.lower().endswith(".directb2s") for name in game_contents):
            game.b2s_exists = True
        if "pupvideos" in game_subdirs:
            game.pup_pack_exists = True
        if "serum" in game_subdirs:
            game.alt_color_exists = True
        if "vni" in game_subdirs:
            game.vni_exists = True
        if "music" in game_subdirs:
            game.music_exists = True
        if any(name.lower().endswith(".ini") for name in game_contents):
            game.ini_exists = True
        from common.games.asset_registry import altsound_folder, is_readme
        if altsound_folder(game_dir, game_subdirs):
            game.alt_sound_exists = True
        if any(is_readme(name) for name in game_contents):
            game.readme_exists = True

        info = game_dir / f"{game_dir.name}.info"
        reason = unread_files.failed(info)
        if reason is None:
            try:
                self.load_metadata(game)
            except (InvalidMetaConfigError, OSError) as exc:
                reason = why(exc)
                unread_files.note(info, reason)
                logger.error("Skipping %s, its metadata could not be read: %s",
                             info, exc.__cause__ or exc)
        if reason is not None:
            # This used to stop the whole library loading. Excluded rather than loaded
            # empty, so nothing can write over a file we could not read.
            (self.unreadable_games if unreadable is None else unreadable).append({
                'folder': game.game_dir_name,
                'path': str(game_dir),
                'error': reason,
            })
            return None

        # After the metadata, so a folder with several .vpx launches the one its
        # metadata describes rather than whichever the filesystem listed first.
        _found, chosen = default_entry(table_entries(game.meta_config),
                                       recorded_default(vpinfe_section(game.meta_config)),
                                       game_contents)
        # Empty rather than the folder for a table with no file, because a reader that
        # stats the folder would be told there is a table here.
        game.full_path_vpx_file = entry_file(str(game_dir), chosen)

        # Media after the default pick: tier 1 of the resolution chain keys off
        # the table that actually launches.
        self.load_image_paths(
            game,
            game_contents=game_contents,
            has_medias_dir="medias" in game_subdirs,
            subdir_names=subdir_names,
            table_stem=Path(game.full_path_vpx_file).stem if game.full_path_vpx_file else None,
        )
        if not folder_read:
            game.file_listing = None
        # The folder when there is no table, so "recently added" still orders an entry
        # that has nothing to stat.
        stat_target = game.full_path_vpx_file or str(game_dir)
        try:
            stat = os.stat(stat_target)
            game.creation_time = getattr(stat, 'st_birthtime', stat.st_ctime)
        except OSError:
            logger.warning("Could not stat: %s", stat_target)

        return game


    def reload_game(self, game_dir: str | Path) -> Game | None:
        """Re-read one game folder in place. Returns the game, or None if it is gone.

        A rating, a rename or an import changes one folder, and rescanning the library
        to see it costs the whole library - on a network share, minutes of it.

        Folders are matched resolved, because the caller rarely spells one the way the
        listing did: a root reached through a symlink is the ordinary case, and on macOS
        every path under /var is one. Matching the spelling finds no game, so each
        refresh appends a second copy instead of replacing it.
        """
        folder = Path(game_dir)
        target = _resolved(folder)
        self.missing_games = [row for row in self.missing_games
                              if _resolved(row["path"]) != target]
        self.unreadable_games = [r for r in self.unreadable_games
                                 if _resolved(r["path"]) != target]

        game = self._build_game(folder) if folder.is_dir() else None
        for index, existing in enumerate(self.games):
            if _resolved(str(existing.full_path_game)) == target:
                if game is None:
                    del self.games[index]        # the folder went away
                else:
                    self.games[index] = game
                return game

        if game is not None:
            self.games.append(game)             # a folder that was not there before
        return game

    def load_image_paths(self, game: Game, game_contents: set[str] | None = None,
                         has_medias_dir: bool | None = None,
                         table_stem: str | None = None,
                         subdir_names: dict[str, str] | None = None) -> None:
        game_dir = Path(str(game.full_path_game))
        medias_dir = game_dir / "medias"

        # Batch directory listings to minimize disk calls
        if game_contents is None:
            try:
                game_contents = set(os.listdir(str(game_dir)))
            except Exception:
                game_contents = set()

        medias_contents: set[str] = set()
        unread: list[OSError] = []
        walked = True
        if has_medias_dir if has_medias_dir is not None else medias_dir.is_dir():
            try:
                for dirpath, _dirs, files in os.walk(str(medias_dir), onerror=unread.append):
                    rel = os.path.relpath(dirpath, str(medias_dir))
                    for fname in files:
                        medias_contents.add(
                            fname if rel == "." else f"{rel}/{fname}".replace(os.sep, "/"))
            except Exception:
                medias_contents = set()
                walked = False
        walked = walked and not unread
        if subdir_names is not None:
            game.file_listing = file_listing.listing_of(
                game_dir, game_contents, subdir_names, medias_contents if walked else None)
        apply_media_specs(game, game_contents, medias_contents, self.playfieldvariant,
                          table_stem, self.active_sets or None)
        # And again per table, off the same two listings. The walk is what costs; a
        # second resolution is set lookups, so a folder with one table pays almost
        # nothing and one with three answers honestly for all three.
        game.media_by_table = resolve_media_by_table(
            str(game.full_path_game), game_contents, medias_contents,
            table_names(game_contents), self.playfieldvariant,
            self.active_sets or None)
        game.media_pages_by_table = resolve_media_pages_by_table(
            str(game.full_path_game), game_contents, medias_contents,
            table_names(game_contents), self.playfieldvariant,
            self.active_sets or None)

    def load_metadata(self, game: Game) -> None:
        meta_path = Path(str(game.full_path_game)) / f"{game.game_dir_name}.info"
        try:
            meta = MetaConfig(str(meta_path))
        except InvalidMetaConfigError as exc:
            logger.error("Invalid metadata for game '%s': %s", game.game_dir_name, exc)
            raise
        game.meta_config = meta.data
        game.info_pending_upgrade = meta.pending_migration

    def get_game(self, index: int) -> Game:
        return self.games[index]

    def get_game_count(self) -> int:
        return len(self.games)

    def get_all_games(self) -> list[Game]:
        return list(self.games)

    def get_unreadable_games(self) -> list[UnreadableGame]:
        """Folders whose .info could not be read, so the game was left out."""
        return [UnreadableGame(**row) for row in self.unreadable_games]

    def get_missing_games(self) -> list[dict]:
        return [dict(row) for row in self.missing_games]


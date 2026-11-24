"""The score file an electromechanical table's own script names.

An EM table has no ROM to read. It saves its scores in a text file in the game's `user/`
folder, and its script holds that file's name. A `.txt` there that the script names and
that reads as scores is the table's high scores, with no list of which tables do this.

A reading is a guess: the file's format is the author's, and `smart_numeric` takes what
looks like scores out of it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.games import high_scores, score_parser, tables
from common.games.formats.script_names import named_in_strings, strings_in
from common.games.formats.vpx_parser import VPXParser
from common.games.game import Game
from common.games.game_metadata import load_game_meta

logger = logging.getLogger("vpinfe.common.games.em_scores")

# What a script names, by file and the file's state: reading a table costs far more than
# a lookup, and a table is looked at again every time its page is.
_script_names: dict[tuple[str, int, int, int, int], str] = {}
_CACHED_SCRIPTS = 256


@dataclass(frozen=True)
class Found:
    path: Path
    entries: list[score_parser.ParsedEntry]


def in_the_map(rom: str) -> bool:
    """Whether the score map knows `rom`, so its file is the map's to name."""
    try:
        return (score_parser.uses_special_text_score_file(rom)
                or score_parser.resolve_rom_name(rom) in score_parser.roms)
    except (OSError, ValueError):
        return False


def find(game: Game, entry: dict[str, Any] | None,
         prefer: Collection[str] = ()) -> Found | None:
    """The score file in `game`'s `user/` folder that `entry`'s script names and that
    reads as scores, or None. A name in `prefer`, or already kept for the game, is taken
    before a newer file, so what a table is kept under does not move with the clock;
    otherwise the newest. Never raises."""
    try:
        return _find(game, entry or {}, {*prefer, *_kept_names(game)})
    except Exception as exc:  # noqa: BLE001 - a table with no score file is the normal case
        logger.warning("Could not look for a score file of %s: %s",
                       getattr(game, "game_dir_name", ""), exc)
        return None


def key_of(game: Game, entry: dict[str, Any] | None) -> str:
    """What `entry`'s high scores are kept under: its ROM, or for a table with none the
    name of its score file, or ""."""
    rom = high_scores.rom_of(entry)
    if rom:
        return rom
    found = find(game, entry)
    return found.path.name if found else ""


def read_with_source(game: Game, entry: dict[str, Any],
                     prefer: Collection[str] = ()) -> tuple[Any, str]:
    """`score_parser.read_rom_with_source` for the table, and where a ROM the map does not
    know has no source the score file its script names. Raises what the parser raises."""
    rom = high_scores.rom_of(entry)
    if rom:
        try:
            return score_parser.read_rom_with_source(rom, str(game.full_path_game))
        except (FileNotFoundError, KeyError):
            found = None if in_the_map(rom) else find(game, entry, prefer)
            if found is None:
                raise
    else:
        found = find(game, entry, prefer)
        if found is None:
            raise FileNotFoundError(tables.entry_filename(entry))
    return found.entries, str(found.path)


def _find(game: Game, entry: dict[str, Any], prefer: Collection[str]) -> Found | None:
    game_dir = str(game.full_path_game or "")
    held = _text_files(Path(game_dir) / "user") if game_dir else []
    if not held:
        return None
    vpx = tables.entry_file(game_dir, entry)
    if not vpx.lower().endswith(".vpx"):
        return None
    named = named_in_strings(_strings_of(vpx), [path.name for path in held])
    for path in sorted((path for path in held if path.name in named),
                       key=lambda path: (path.name in prefer, _modified(path)), reverse=True):
        try:
            entries = score_parser.decode_smart_numeric_text_file(str(path))
        except (OSError, ValueError):
            continue
        if entries:
            return Found(path, entries)
    return None


def _kept_names(game: Game) -> set[str]:
    user = (load_game_meta(game) or {}).get("User")
    held = user.get(high_scores.KEY) if isinstance(user, dict) else None
    return set(held) if isinstance(held, dict) else set()


def _text_files(folder: Path) -> list[Path]:
    try:
        return [path for path in folder.iterdir()
                if path.suffix.lower() == ".txt" and path.is_file()]
    except OSError:
        return []


def _modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _strings_of(vpx: str) -> str:
    """The strings of the table's script, which a `.vbs` beside it replaces."""
    stat = os.stat(vpx)
    try:
        sidecar = os.stat(Path(vpx).with_suffix(".vbs"))
    except OSError:
        sidecar = None
    key = (vpx, stat.st_mtime_ns, stat.st_size,
           sidecar.st_mtime_ns if sidecar else 0, sidecar.st_size if sidecar else 0)
    if key not in _script_names:
        if len(_script_names) >= _CACHED_SCRIPTS:
            _script_names.clear()
        try:
            parsed = VPXParser().single_file_extract(vpx) or {}
            _script_names[key] = strings_in(parsed.get("game_data") or "")
        except Exception as exc:  # noqa: BLE001 - tried again only when the file changes
            logger.warning("Could not read the script of %s: %s", vpx, exc)
            _script_names[key] = ""
    return _script_names[key]

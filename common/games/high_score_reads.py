"""A machine's high score table, read again when somebody looks at it.

The launch reads the table after every game. A game played outside VPinFE, or a score file
copied in, leaves the file newer than the read, and the file's own time is what says so.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from common import service_errors
from common.failures import why
from common.games import (
    em_scores,
    game_identity,
    game_lens,
    high_scores,
    player_records,
    score_parser,
)
from common.games.game import Game
from common.games.game_metadata import default_table, load_game_meta, persist_game_meta
from common.games.game_repository import reread_game
from common.games.tables import TABLE_ID_KEY, table_entries
from common.i18n import t
from common.players import players
from common.timestamps import iso_to_epoch, utc_now_iso

logger = logging.getLogger("vpinfe.common.games.high_score_reads")


def for_game(game_id: str) -> dict[str, Any] | None:
    """The default table's, whose ROM a launch of the game plays."""
    game = game_lens.game_or_refuse(game_id)
    _name, entry = default_table(load_game_meta(game))
    return look(game, entry)


def for_table(game_id: str, table_id: str) -> dict[str, Any] | None:
    game = game_lens.game_or_refuse(game_id)
    entry = table_entries(load_game_meta(game)).get(table_id)
    if entry is None:
        raise service_errors.NotFoundError(t("error.games.table_not_found"),
                                           details={"table": table_id})
    return look(game, entry)


def look(game: Game, entry: dict[str, Any]) -> dict[str, Any] | None:
    """What can be shown for `entry`'s machine, as `high_scores.shown` gives it with each
    entry's `player` named, or None where there is nothing to show and nothing a game would
    change."""
    shown = _look(game, entry)
    if shown is not None:
        _name_players(game, shown)
    return shown


def _look(game: Game, entry: dict[str, Any]) -> dict[str, Any] | None:
    rom = high_scores.rom_of(entry)
    game_dir = str(game.full_path_game or "")
    source = _score_file(rom, game_dir) if rom else None
    found = None
    if source is None and not (rom and em_scores.in_the_map(rom)):
        found = em_scores.find(game, entry)
        source = str(found.path) if found else None
    key = rom or (found.path.name if found else "")
    if not key:
        return None
    table_id = str(entry.get(TABLE_ID_KEY, "") or "")
    held = high_scores.kept(load_game_meta(game), key)
    # A table with no ROM is found through the file its script names, so the theme payload,
    # which reads no file, is told which kept file is this table's.
    named_for = "" if rom else table_id

    if source and _newer(source, held):
        try:
            parsed, path = em_scores.read_with_source(game, entry)
            reading = score_parser.result_to_jsonable(key, parsed, path, "")
        except KeyError:
            return high_scores.shown(key, table_id, None, state=high_scores.UNSUPPORTED)
        except Exception as exc:  # noqa: BLE001 - the reason is the answer
            logger.warning("Could not read the high scores of %s for %s: %s", key,
                           game.game_dir_name, exc)
            return high_scores.shown(key, table_id, held, state=high_scores.UNREADABLE,
                                     reason=why(exc))
        if not reading:
            return high_scores.shown(key, table_id, None, state=high_scores.NONE_YET)
        held = high_scores.record(reading, high_scores.as_reading(held), utc_now_iso())
        _keep(game, key, held, named_for)
        named_for = ""

    if held is not None:
        if named_for and named_for not in high_scores.tables_of(held):
            _keep(game, key, held, named_for)
        return high_scores.shown(key, table_id, held)
    if source is None and not em_scores.in_the_map(key):
        return None
    return high_scores.shown(key, table_id, None, state=high_scores.NONE_YET)


def _score_file(rom: str, game_dir: str) -> str | None:
    try:
        return score_parser.resolve_score_input_path(rom, game_dir)
    except (FileNotFoundError, KeyError):
        return None


def _newer(path: str, held: dict[str, Any] | None) -> bool:
    read_at = iso_to_epoch((held or {}).get("read_at"))
    if read_at is None:
        return True
    try:
        return os.path.getmtime(path) > read_at
    except OSError:
        return False


def _keep(game: Game, key: str, held: dict[str, Any], table_id: str = "") -> None:
    config = load_game_meta(game)
    high_scores.keep(config, key, held, table_id=table_id)
    persist_game_meta(game, config)
    reread_game(game)
    logger.info("Kept the high scores of %s for %s", key, game.game_dir_name)


def _name_players(game: Game, shown: dict[str, Any]) -> None:
    """Put on each entry the name of the player it is for: whoever has its initials, or for
    a blank entry the player whose record holds that score. An entry no one is named for
    keeps `player` blank."""
    roster = players.get_roster()
    credited = _blank_entries_credited(game, roster, str(shown.get("rom") or ""))
    for section in shown.get("sections") or []:
        for entry in section.get("entries") or []:
            if players.names_nobody(entry.get("initials")):
                owner = credited.get((str(section.get("name") or ""), entry.get("score")))
            else:
                owner = roster.whose(entry["initials"])
            entry["player"] = owner.name if owner else ""


def _blank_entries_credited(game: Game, roster: players.Roster,
                            rom: str) -> dict[tuple[str, Any], players.Player]:
    """Who a score with no initials was credited to, by section and score. Only a
    player's best score on a ROM is kept in their record, and the owner keeps none."""
    game_id = game_identity.game_id(game)
    credited: dict[tuple[str, Any], players.Player] = {}
    if not game_id or not rom:
        return credited
    records = player_records.get_records()
    for player in roster.players():
        if player.owner:
            continue
        best = records.game(player, game_id).get(player_records.BEST_SCORE)
        if isinstance(best, dict) and best.get("rom") == rom:
            credited[(str(best.get("section") or ""), best.get("score"))] = player
    return credited

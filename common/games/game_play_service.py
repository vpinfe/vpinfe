"""What happened when a game was played: how long, how often, and the score it left.

The counters live in the `.info` under the keys the VPX spec fixes, so a stat written
here is the same one every other frontend reads.
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from pathlib import Path

from common.games import high_scores
from common.games.game import Game
from common.games.game_metadata import (
    get_or_create_table_user,
    get_or_create_user_meta,
    get_or_create_vpinfe_meta,
    load_game_meta,
    normalize_meta,
    persist_game_meta,
    run_time_seconds,
    vpinfe_section,
)
from common.timestamps import epoch_to_iso, utc_now_iso

logger = logging.getLogger("vpinfe.common.games.game_play_service")


def increment_start_count(game: Game, table: str = "") -> None:
    config = clone_game_meta(game)
    if not config:
        logger.warning(
            "Could not increment StartCount: invalid game metadata for %s", game.game_dir_name)
        return

    user = apply_start_count_update(config, table=table)
    persist_game_meta(game, config)
    logger.debug("Updated User.StartCount for %s -> %s", game.game_dir_name, user["StartCount"])


def add_play_time(game: Game, elapsed_seconds: float, table: str = "") -> None:
    config = clone_game_meta(game)
    if not config:
        logger.warning("Could not update RunTime: invalid game metadata for %s", game.game_dir_name)
        return

    apply_runtime_update(config, elapsed_seconds, table=table)
    persist_game_meta(game, config)
    logger.info(
        "Updated play time for %s: +%ss (total=%ss)",
        game.game_dir_name,
        max(0, int(round(float(elapsed_seconds)))),
        run_time_seconds(config),
    )


def clone_game_meta(game: Game) -> dict:
    config = load_game_meta(game)
    return deepcopy(config) if isinstance(config, dict) else {}


def _plus(mapping: dict, key: str, amount: int) -> None:
    try:
        mapping[key] = int(mapping.get(key, 0)) + amount
    except (TypeError, ValueError):
        mapping[key] = amount


def apply_start_count_update(config: dict, played_at: int | None = None,
                             table: str = "") -> dict:
    """Count a launch against the game, and against the table that was launched.

    The two accumulate independently rather than one being a rollup of the other:
    deleting a table would otherwise un-play hours that were played.
    """
    user = get_or_create_user_meta(config)
    _plus(user, "StartCount", 1)
    user["LastRun"] = int(played_at or time.time())

    if table:
        played = get_or_create_table_user(config, table)
        _plus(played, "start_count", 1)
        # ISO, where User.LastRun is an epoch integer it cannot stop being: it is a
        # specced key and goes to the VPinPlay API verbatim. Ours says what it is.
        played["last_run"] = epoch_to_iso(user["LastRun"])
    return user


def apply_runtime_update(config: dict, elapsed_seconds: float, table: str = "") -> dict:
    """Add one session to the play time. Seconds, at both levels.

    The seconds are the record; the minutes are a view of it. Rounding a session up to a
    whole minute makes a three-second look at a table cost a minute, and a run of them
    runs the total away from reality.
    """
    seconds = max(0, int(round(float(elapsed_seconds))))
    total = run_time_seconds(config) + seconds
    get_or_create_vpinfe_meta(config)["run_time_seconds"] = total

    # Derived, never accumulated: User.RunTime is a specced key carrying minutes, and it
    # goes to the VPinPlay API verbatim, so it cannot hold the seconds itself.
    user = get_or_create_user_meta(config)
    user["RunTime"] = total // 60
    if table:
        _plus(get_or_create_table_user(config, table), "run_time_seconds", seconds)
    return user


def parse_score_from_nvram(game: Game, rom: str, initials: str | None = None,
                           entry: dict | None = None) -> tuple[dict | None, str | None]:
    """`rom`'s high score table and where it was read from. `initials` goes on a blank
    score, the one player up's when it is None; "" leaves them blank. `entry`, the table,
    lets a score file its script names stand in where the ROM has no source; `rom` is
    then what the table's scores are kept under."""
    if not rom:
        logger.debug("No ROM name found for %s, skipping score update", game.game_dir_name)
        return None, None

    try:
        from common.games.em_scores import read_with_source
        from common.games.score_parser import result_to_jsonable

        parsed_result, score_path = read_with_source(game, entry or {"rom": rom}, prefer=(rom,))
        score_data = result_to_jsonable(rom, parsed_result, score_path, initials)
    except FileNotFoundError:
        logger.debug("No score source found for %s and ROM %s", game.game_dir_name, rom)
        return None, None
    except KeyError:
        logger.debug("ROM %s is not supported for score parsing", rom)
        return None, None
    except Exception:
        logger.exception("Failed to parse score data for %s", game.game_dir_name)
        return None, None

    if not score_data:
        logger.debug(
            "Parsed score data for %s was empty, skipping metadata update", game.game_dir_name)
        return None, None

    return score_data, score_path


def keep_high_scores(game: Game, rom: str, reading: dict, before: dict | None,
                     score_path: str | None, *, table_id: str = "") -> None:
    """Keep `reading`, read with blanks left blank, as `rom`'s high scores. `before` is
    the table as it stood before the game. `table_id` is the table's where it has no ROM
    of its own and `rom` is the name of its score file."""
    config = clone_game_meta(game)
    if not config:
        logger.warning("Could not keep high scores: invalid game metadata for %s",
                       game.game_dir_name)
        return

    high_scores.keep(config, rom, high_scores.record(reading, before, utc_now_iso()),
                     table_id=table_id)
    persist_game_meta(game, config)
    logger.info("Kept the high scores of %s for %s from %s", rom, game.game_dir_name,
                score_path)


def delete_nvram_if_configured(game: Game, entry: dict) -> None:
    """Delete the NVRAM of `entry`'s ROM where Clear NVRAM on Exit is on for that table."""
    from common.games.table_lens import table_overrides

    config = normalize_meta(getattr(game, "meta_config", {}))
    if not table_overrides(entry, vpinfe_section(config))["delete_nvram_on_close"]:
        return

    rom = high_scores.rom_of(entry)
    if not rom:
        logger.warning("No ROM name found for table, skipping NVRAM deletion")
        return

    nvram_path = Path(str(game.full_path_game)) / "pinmame" / "nvram" / f"{rom}.nv"
    if nvram_path.exists():
        nvram_path.unlink()
        logger.info("Deleted NVRAM file: %s", nvram_path)
    else:
        logger.info("NVRAM file not found (nothing to delete): %s", nvram_path)

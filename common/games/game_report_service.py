"""The two library reports the CLI prints: what is missing, and what we cannot place."""

from __future__ import annotations

import logging
from collections.abc import Callable

from common.config.config_store import ConfigStore
from common.config.paths import get_ini_config
from common.games import game_repository
from common.online.vpsdb import VPSdb

logger = logging.getLogger("vpinfe.common.games.game_report_service")


def _config(config: ConfigStore | None = None) -> ConfigStore:
    return config or get_ini_config()


def list_missing_games(iniconfig: ConfigStore | None = None,
                       log: Callable[..., None] | None = None) -> None:
    config = _config(iniconfig)
    log = log or logger.info
    games = game_repository.all_games()
    log("Listing tables missing from the library")
    log("Found %s tables in the library", len(games))

    vps = VPSdb(config)
    log("Found %s tables in VPSdb", len(vps))

    games_found = []
    for game in games:
        vps_search_data = vps.parse_game_name_from_dir(game.game_dir_name)
        vps_data = (
            vps.lookup_name(
                vps_search_data["name"],
                vps_search_data["manufacturer"],
                vps_search_data["year"],
            )
            if vps_search_data
            else None
        )
        if vps_data:
            games_found.append(vps_data)

    current = 0
    for vps_game in vps.games():
        if vps_game not in games_found:
            current += 1
            log(
                "Missing table %s: %s (%s %s)",
                current,
                vps_game["name"],
                vps_game["manufacturer"],
                vps_game["year"],
            )


def list_unknown_games(iniconfig: ConfigStore | None = None,
                       log: Callable[..., None] | None = None) -> None:
    config = _config(iniconfig)
    log = log or logger.info
    games = game_repository.all_games()
    log("Listing unknown tables in the library")
    log("Found %s tables in the library", len(games))

    vps = VPSdb(config)
    log("Found %s tables in VPSdb", len(vps))

    current = 0
    for game in games:
        vps_search_data = vps.parse_game_name_from_dir(game.game_dir_name)
        vps_data = (
            vps.lookup_name(
                vps_search_data["name"],
                vps_search_data["manufacturer"],
                vps_search_data["year"],
            )
            if vps_search_data
            else None
        )
        if vps_data is None:
            current += 1
            log("Unknown table %s: %s Not found in VPSdb", current, game.game_dir_name)

"""Building a game's metadata from everything that knows something about it."""

from __future__ import annotations

import logging
import os

from common.config.config_access import cfg_bool
from common.config.config_store import ConfigStore
from common.config.paths import get_ini_config
from common.games import game_identity, game_repository, media_fill
from common.games.formats.vpx_parser import VPXParser
from common.games.game_metadata import effective_vps_id, record_vps_match, vps_matched_by
from common.games.info_file import MetaConfig
from common.i18n import t
from common.online.vpsdb import VPSdb
from common.runtime.jobs import JobReporter, LogCallback, ProgressCallback

logger = logging.getLogger("vpinfe.common.games.metadata_service")


def _config(config: ConfigStore | None = None) -> ConfigStore:
    return config or get_ini_config()


def build_metadata(
    download_media: bool = True,
    update_all: bool = True,
    game_name: str | None = None,
    user_media: bool = False,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    iniconfig: ConfigStore | None = None,
) -> dict[str, int]:
    config = _config(iniconfig)

    reporter = JobReporter(logger, progress_cb=progress_cb, log_cb=log_cb)
    log = reporter.log

    not_found_games = 0
    parservpx = VPXParser()

    games = game_repository.all_games()

    if game_name:
        games = [game for game in games if game.game_dir_name == game_name]
        if not games:
            log(f"Table folder '{game_name}' not found")
            return {"found": 0, "not_found": 0}
        log(f"Processing single table: {game_name}")

    total = len(games)

    vps = VPSdb(config)
    log(f"Found {len(vps)} tables in VPSdb")

    refresh_art = None
    if (download_media and update_all and not user_media
            and cfg_bool(config, "updates", "update_downloaded_media", True)):
        refresh_art = media_fill.art_updater(config, vps.vpinmediadbjson)

    if progress_cb:
        reporter.progress(0, total, t("said.starting"))

    for current, game in enumerate(games, 1):
        info_path = os.path.join(game.full_path_game, f"{game.game_dir_name}.info")

        if os.path.exists(info_path) and not update_all:
            if progress_cb:
                reporter.progress(current, total,
                                  t("said.skipping_game", game=game.game_dir_name))
            continue

        meta = MetaConfig(info_path)

        log(f"Checking VPSdb for {game.game_dir_name}")
        if progress_cb:
            reporter.progress(current, total,
                              t("said.processing_game", game=game.game_dir_name))

        vps_data: dict | None = None
        if vps_matched_by(meta.data):
            log("  - Matched by a person; the match is left as it is")
            art_for = effective_vps_id(meta.data)
        else:
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
            if not vps_data:
                log("  - Not found in VPS")
                not_found_games += 1
                continue
            art_for = vps_data["id"]
            record_vps_match(meta.data, "")

        log(f"Parsing VPX file: {game.full_path_vpx_file}")
        vpx_data = parservpx.single_file_extract(game.full_path_vpx_file)

        if not vpx_data:
            log(f"  - VPX file not found or failed to parse: {game.full_path_vpx_file}")
            not_found_games += 1
            continue

        meta.write_config_meta({
            "vpsdata": vps_data,
            "vpxdata": vpx_data,
        })

        log(f"Created {game.game_dir_name}.info")

        # user_media suppresses the fetch outright, for somebody supplying the whole
        # library themselves. Media already on disk needs no such flag: the
        # downloader compares hashes and leaves anything it cannot prove is ours
        # alone (common/online/vpsdb_media.py).
        if download_media and not user_media and art_for:
            try:
                vps.download_media_for_game(game, art_for, meta_config=meta)
                log("Downloaded media")
            except KeyError:
                log("No media found")
            if refresh_art:
                try:
                    updated, _failed = refresh_art(game_identity.game_id(game), game, art_for)
                except Exception:
                    logger.exception("Updating art: could not read what %s has",
                                     game.game_dir_name)
                else:
                    if updated:
                        log("Updated downloaded media")

    if progress_cb:
        reporter.progress(total, total, t("word.done"))

    return {"found": total, "not_found": not_found_games}

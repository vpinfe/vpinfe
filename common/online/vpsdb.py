"""The Virtual Pinball Spreadsheet: the community database VPinFE matches games against."""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from common.config.config_access import MediaConfig, cfg_bool
from common.config.config_store import ConfigStore
from common.config.paths import CACHE_DIR
from common.games.game import Game
from common.games.info_file import MetaConfig
from common.online import vpsdb_media
from common.online.vpsdb_cache import VPinMediaDatabase, VPSDatabaseCache
from common.online.vpsdb_media import VPSMediaDownloader

logger = logging.getLogger("vpinfe.common.online.vpsdb")

_FOLDER_NAME = re.compile(r"^(.+?) \(([^()]+) (\d{4})\)(?:\s.*)?$")


def parse_folder_name(directory_name: str) -> dict[str, Any] | None:
    """Name, manufacturer and year from `Name (Manufacturer Year)`, ignoring anything
    after that block: `Attack From Mars (Bally 1995) (v2)`."""
    match = _FOLDER_NAME.match(directory_name)
    if not match:
        return None
    return {"name": match.group(1), "manufacturer": match.group(2),
            "year": int(match.group(3))}


def lookup(catalog: list[dict] | None, name: str, manufacturer: str,
           year: object) -> dict | None:
    """The entry closest to a name, manufacturer and year, or None past every gate.

    Each of the three has to be at least 0.8 alike; of the entries that pass, the
    highest sum wins, and a tie goes to the first in the catalog.
    """
    if not all((name, manufacturer, year)):
        return None

    best: dict | None = None
    top = 0.0
    wanted = name.lower()
    for game in catalog or []:
        matcher = SequenceMatcher(None, wanted, str(game.get("name") or "").lower())
        # Both are upper bounds on ratio(), so this drops nothing ratio() would keep.
        if matcher.real_quick_ratio() < 0.8 or matcher.quick_ratio() < 0.8:
            continue
        named = matcher.ratio()
        if named < 0.8:
            continue
        made = SequenceMatcher(None, manufacturer.lower(),
                               str(game.get("manufacturer") or "").lower()).ratio()
        if made < 0.8:
            continue
        dated = SequenceMatcher(None, str(year), str(game.get("year") or "")).ratio()
        if dated < 0.8:
            continue
        if named + made + dated > top:
            best, top = game, named + made + dated

    if best is None:
        logger.debug("No match found for: %s", name)
    return best


def guess(catalog: list[dict] | None, folder_name: str) -> dict | None:
    """The entry a folder's name points at, or None."""
    parsed = parse_folder_name(folder_name)
    return lookup(catalog, **parsed) if parsed else None


class VPSdb:
    """
    VPSdb class handles downloading, caching, and querying the Virtual Pinball
    Spreadsheet (VPS) database
    along with associated media assets via VPinMediaDB.
    """

    # Declared, not defaulted: __init__ always sets both, and `= None` gave each of
    # them a type it never actually holds.
    data: list[dict]
    _vpinfe_config_store: ConfigStore

    VPS_LAST_UPDATE_URL = "https://raw.githubusercontent.com/VirtualPinballSpreadsheet/vps-db/refs/heads/main/lastUpdated.json"
    VPS_DB_URL = "https://github.com/VirtualPinballSpreadsheet/vps-db/raw/refs/heads/main/db/vpsdb.json"
    VPINMDB_URL = vpsdb_media.MANIFEST_URL

    def __init__(self, vpinfe_config_store: ConfigStore) -> None:
        logger.info("Initializing VPSdb")

        self._vpinfe_config_store = vpinfe_config_store
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache = VPSDatabaseCache(
            CACHE_DIR,
            db_url=VPSdb.VPS_DB_URL,
            last_update_url=VPSdb.VPS_LAST_UPDATE_URL,
        )
        self._vpsdb_path = self._cache.path
        self.data = self._cache.ensure_current()
        logger.info("Total VPSdb entries: %s", len(self.data))
        self._write_manufacturer_reference()

        # Setup preferences
        media_config = MediaConfig.from_config(self._vpinfe_config_store)
        self.playfieldvariant = media_config.playfield_variant
        self.playfieldresolution = media_config.playfield_resolution
        self.playfieldvideoresolution = media_config.playfield_video_resolution
        logger.info(
            "Using %s/%s tables (video: %s)",
            self.playfieldresolution,
            self.playfieldvariant,
            self.playfieldvideoresolution,
        )

        self.vpinmediadbjson = self.download_media_json()
        self._media_downloader = VPSMediaDownloader(
            self.vpinmediadbjson,
            playfieldvariant=self.playfieldvariant,
            playfieldresolution=self.playfieldresolution,
            playfieldvideoresolution=self.playfieldvideoresolution,
            update_downloaded=cfg_bool(self._vpinfe_config_store, "updates",
                                       "update_downloaded_media", True),
        )

    def _write_manufacturer_reference(self) -> None:
        """Refresh the human-readable slug/alias/logo reference after a sync.

        Best-effort: a no-op when the shared assets root is not configured, and
        never allowed to break loading the database.
        """
        try:
            from common.media.manufacturer_logos import write_manufacturer_reference
            names = {str(t.get("manufacturer", "")) for t in self.data or []
                     if isinstance(t, dict)}
            write_manufacturer_reference(names)
        except Exception:
            logger.exception("Could not write the manufacturer reference")

    # ----------------------------------------------------------------------
    # Python container magic methods
    def __len__(self) -> int:
        return len(self.data) if self.data else 0

    def __contains__(self, item: object) -> bool:
        return item in self.data if self.data else False

    def games(self) -> list[dict]:
        return self.data

    # ----------------------------------------------------------------------
    # Game lookups
    def lookup_name(self, name: str, manufacturer: str, year: object) -> dict | None:
        return lookup(self.data, name, manufacturer, year)

    def parse_game_name_from_dir(self, directory_name: str) -> dict[str, Any] | None:
        return parse_folder_name(directory_name)

    # ----------------------------------------------------------------------
    # Remote content handling
    def download_media_json(self) -> dict | None:
        """Downloads the VPinMediaDB JSON index."""
        return VPinMediaDatabase(self.VPINMDB_URL).load()

    def download_db(self) -> None:
        """Downloads the VPS database JSON."""
        self._cache.download_db()

    def download_last_update(self) -> str | None:
        """Fetches the last update version string from VPSdb."""
        return self._cache.fetch_last_update()

    def download_media_file(self, game_id: str, url: str, filename: str) -> None:
        """Downloads a single media file by URL."""
        self._media_downloader.download_media_file(game_id, url, filename)

    # ----------------------------------------------------------------------
    # Local file helpers
    def file_exists(self, path: str | None) -> bool:
        return self._media_downloader.file_exists(path)

    def download_media_for_game(self, game: Game, id: str,
                               meta_config: MetaConfig | None = None) -> None:
        """Fetch the art the game has no file for, of the kinds the library keeps."""
        from common.games import media_fill

        self._media_downloader.download_media_for_game(game, id, meta_config,
                                                       kinds=media_fill.kept_kinds())


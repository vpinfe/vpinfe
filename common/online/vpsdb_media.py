"""Downloading a game's media from VPinMediaDB, at the resolution the display wants."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Collection
from pathlib import Path

import requests

from common.atomic_write import staged_for
from common.games import asset_origin, media_placement
from common.games.game import Game
from common.games.info_file import MetaConfig
from common.media.media_specs import default_media_path
from common.security.http_client import download_file

logger = logging.getLogger("vpinfe.common.online.vpsdb_media")

PUBLISHER = "vpinmediadb"


# Where the manifest is published. One name for it, so the downloader and anything
# browsing the catalog cannot end up pointed at different copies.
MANIFEST_URL = "https://github.com/superhac/vpinmediadb/raw/refs/heads/main/vpinmdb.json"

# Our media kinds against vpinmediadb's, and whether the entry files it under a
# resolution. This is the manifest's vocabulary, not ours: it calls the backglass "bg"
# and the score view "dmd", so a rename of our own windows sweeps these in with them if
# they are read as ours. Nothing complains - `download_media` returns None for a key an
# entry does not carry - and the only symptom is art that never arrives.
MANIFEST_KINDS: dict[str, tuple[str, bool]] = {
    "backglass": ("bg", True),
    "scoreview": ("dmd", True),
    "scoreview_video": ("dmd_video", True),
    "playfield": ("table", True),
    "playfield_fss": ("fss", True),
    "playfield_video": ("table_video", True),
    "wheel": ("wheel", False),
    "cab": ("cab", False),
    "flyer": ("flyer", False),
    "audio": ("audio", False),
    "real_dmd": ("realdmd", False),
    "real_dmd_color": ("realdmd_color", False),
}

REMOTE_KEYS = {kind: key for kind, (key, _) in MANIFEST_KINDS.items()}

# The resolutions an entry files art under, largest first - which is the order to
# offer them in, since the bigger one is the better one when it exists.
MANIFEST_SIZES = ("4k", "1k")


def offered(media_index: dict | None, vps_id: str) -> dict[str, list[dict]]:
    """What vpinmediadb publishes for one VPS id: our kind -> the files, largest first.

    Every size the entry actually carries, not the one the display is configured for.
    The configured size is right for an unattended refresh and wrong for someone
    standing at the screen choosing a picture.
    """
    entry = (media_index or {}).get(vps_id) or {}
    found: dict[str, list[dict]] = {}
    for kind, (key, bucketed) in MANIFEST_KINDS.items():
        options = []
        for size in (MANIFEST_SIZES if bucketed else ("",)):
            source = entry.get(size) if bucketed else entry
            if not isinstance(source, dict):
                continue
            url = source.get(key)
            if url:
                options.append({"size": size, "url": url,
                                "md5": source.get(f"{key}_md5", "")})
        if options:
            found[kind] = options
    return found


def published_url(media_index: dict | None, vps_id: str, kind: str,
                  size: str = "") -> tuple[str, str] | None:
    """The URL and hash vpinmediadb publishes for one kind, or None.

    The catalog is the only source of a URL this app will fetch. A caller names an
    entry, a kind and a size; it never hands over a link of its own.
    """
    options = offered(media_index, vps_id).get(kind) or []
    if not options:
        return None
    pick = next((item for item in options if item["size"] == size), options[0])
    return pick["url"], pick["md5"]


def went_stale(local_md5: str, recorded_md5: str, published: Collection[str]) -> bool:
    """Placed by us, unchanged since, and no longer among the `published` hashes."""
    return bool(recorded_md5) and local_md5 == recorded_md5 and recorded_md5 not in published


def published_md5s(entry: dict | None) -> set[str]:
    """Every hash an entry carries, of any kind and size."""
    found: set[str] = set()
    for bucket in (entry or {}, *((entry or {}).get(size) for size in MANIFEST_SIZES)):
        if isinstance(bucket, dict):
            found |= {str(value) for key, value in bucket.items()
                      if key.endswith("_md5") and value}
    return found


def file_md5(path: str | Path) -> str:
    """The file's own hash, or "" when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            digest = hashlib.md5()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        logger.debug("Could not hash %s", path, exc_info=True)
        return ""


def replace_file(url: str, target: Path) -> None:
    """Download over `target`, which stays as it was if the download raises."""
    with staged_for(target) as staged:
        download_file(url, staged)


class VPSMediaDownloader:
    """Downloads a game's media from VPinMediaDB."""

    def __init__(
            self, media_index: dict | None, *, playfieldvariant: str, playfieldresolution: str,
            playfieldvideoresolution: str, update_downloaded: bool = False) -> None:
        self.media_index = media_index or {}
        self.playfieldvariant = playfieldvariant
        self.playfieldresolution = playfieldresolution
        self.playfieldvideoresolution = playfieldvideoresolution
        self.update_downloaded = update_downloaded

    def new_file(self, game_dir: str, kind: str) -> Path:
        """Where art the game has none of goes: the game folder's tier, named as every other
        placement of this kind is. A file already on disk is found by its own path, so only
        what is missing takes this name."""
        extension = default_media_path(game_dir, kind, self.playfieldvariant).suffix
        folder = Path(game_dir)
        return folder / "medias" / media_placement.target_name(
            self._slot(kind), folder.name, extension, game_dir=folder)

    def _slot(self, kind: str) -> str:
        return "playfield_fss" if kind == "playfield" and self.playfieldvariant == "fss" else kind

    def held_file(self, game_dir: str, kind: str) -> str | None:
        """A file the game already has for this kind, at its own tier or the shared default,
        in any spelling. The caller may know no path for it, as when a folder is first
        associated, and a second file beside it would hide it."""
        folder = Path(game_dir)
        for stem in (folder.name, media_placement.FIXED):
            found = media_placement.held(folder, self._slot(kind), stem, self.playfieldvariant)
            if found is not None:
                return str(found)
        return None

    def file_exists(self, path: str | None) -> bool:
        return bool(path and os.path.exists(path))

    def download_media_file(self, game_id: str, url: str, filename: str) -> None:
        logger.info("Downloading %s from %s", filename, url)
        try:
            download_file(url, Path(filename))
            logger.info("Successfully downloaded %s from VPinMedia", filename)
        except requests.RequestException as exc:
            logger.warning("Failed to download %s for table %s: %s", filename, game_id, exc)

    def replace_media_file(self, game_id: str, url: str, filename: str) -> bool:
        try:
            replace_file(url, Path(filename))
        except (requests.RequestException, OSError) as exc:
            logger.warning("Failed to update %s for table %s: %s", filename, game_id, exc)
            return False
        logger.info("Updated %s from VPinMediaDB", filename)
        return True

    def download_media(self, game_id: str, metadata: dict | None, key: str,
                       filename: str | None, default_filename: str,
                       recorded_md5: str = "",
                       published: Collection[str] = ()) -> tuple[str, str] | None:
        """Fill the slot, or replace art we placed that went stale; None for anything
        declined, so nothing declined is recorded as ours."""
        if not metadata or key not in metadata:
            return None

        remote_md5 = metadata.get(f"{key}_md5", "")
        actual_path = filename if self.file_exists(filename) else None
        if actual_path is None and self.file_exists(default_filename):
            actual_path = default_filename

        if actual_path:
            local_md5 = file_md5(actual_path)
            if remote_md5 and local_md5 == remote_md5:
                return actual_path, remote_md5
            if not (self.update_downloaded and remote_md5
                    and went_stale(local_md5, recorded_md5, set(published) | {remote_md5})):
                logger.debug("Leaving %s alone: not the file vpinmediadb publishes",
                             actual_path)
                return None
            if self.replace_media_file(game_id, metadata[key], actual_path):
                return actual_path, remote_md5
            return None

        self.download_media_file(game_id, metadata[key], default_filename)
        if self.file_exists(default_filename):
            return default_filename, remote_md5
        return None

    def download_media_for_game(self, game: Game, game_id: str,
                                meta_config: MetaConfig | None = None,
                                kinds: Collection[str] | None = None) -> None:
        """Fetch what the game has no file for, of `kinds`, or of every kind when None."""
        if game_id not in self.media_index:
            logger.info("No media exists for %s (ID %s).", game.full_path_game, game_id)
            return

        game_media = self.media_index[game_id]
        game_dir = str(game.full_path_game or "")
        medias_dir = os.path.join(game_dir, "medias")
        os.makedirs(medias_dir, exist_ok=True)
        recorded = asset_origin.sources(game_dir) if self.update_downloaded else {}
        published = published_md5s(game_media)

        def record(result: tuple[str, str] | None) -> None:
            """Only files we actually placed. download_media returns None for anything
            it declined to touch, so a user's artwork is never claimed as ours."""
            if result and meta_config:
                path, md5hash = result
                meta_config.add_asset(path, PUBLISHER, md5hash)

        # `key` indexes the remote manifest, so it is vpinmediadb's word for the thing
        # and not ours. They differ for three of them - see REMOTE_KEYS. The kind is no
        # longer passed for the ledger, which is keyed by path.
        def process(kind: str, metadata: dict | None, key: str, filename: str | None) -> None:
            if kinds is not None and kind not in kinds:
                return
            if not self.file_exists(filename):
                filename = self.held_file(game_dir, kind)
            default_filename = str(self.new_file(game_dir, kind))
            on_disk = filename if filename and self.file_exists(filename) else default_filename
            held = recorded.get(asset_origin.path_of(game_dir, Path(on_disk))) or {}
            ours = str(held.get("host", "") or "") == PUBLISHER
            record(self.download_media(game_id, metadata, key, filename, default_filename,
                                       recorded_md5=str(held.get("hash", "") or "")
                                       if ours else "",
                                       published=published))

        process("backglass", game_media.get("1k"), REMOTE_KEYS["backglass"],
                game.bg_image_path)
        process("scoreview", game_media.get("1k"), REMOTE_KEYS["scoreview"],
                game.dmd_image_path)
        process("wheel", game_media, "wheel", game.wheel_image_path)
        process("cab", game_media, "cab", game.cab_image_path)
        process("real_dmd", game_media, "realdmd", game.real_dmd_image_path)
        process("real_dmd_color", game_media, "realdmd_color",
                game.real_dmd_color_image_path)
        process("flyer", game_media, "flyer", game.flyer_image_path)
        process("playfield", game_media.get(self.playfieldresolution),
                self.playfieldvariant, game.playfield_image_path)
        # Videos, and only the ones the index actually carries. There has never been
        # a bg_video at any resolution, so the backglass video is yours to supply.
        # Nor is there an fss_video: under table type fss the playfield video is
        # simply not offered, and asking would quietly fetch nothing.
        process("scoreview_video", game_media.get(self.playfieldvideoresolution),
                REMOTE_KEYS["scoreview_video"], game.dmd_video_path)
        if self.playfieldvariant == "table":
            process("playfield_video", game_media.get(self.playfieldvideoresolution),
                    "table_video", game.playfield_video_path)
        process("audio", game_media, "audio", game.audio_path)

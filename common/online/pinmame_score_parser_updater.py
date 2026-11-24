"""Keeping the community `roms.json` NVRAM map current, so new machines parse."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from common import timestamps
from common.atomic_write import staged_for
from common.config.paths import USER_ROMS_PATH
from common.online import cache_stamps
from common.security import http_client

logger = logging.getLogger("vpinfe.common.online.pinmame_score_parser_updater")


ROMS_JSON_PATH = USER_ROMS_PATH
LATEST_RELEASE_URL = "https://api.github.com/repos/superhac/pinmame-score-parser/releases/latest"
USER_AGENT = "VPinFE-pinmame-score-parser-updater"
STAMPS = "roms"
CHECK_EVERY_SECONDS = 24 * 60 * 60


def get_user_roms_path() -> Path:
    return ROMS_JSON_PATH


def _request_json(url: str) -> dict:
    payload = http_client.get_json(
        url,
        timeout=15,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}, got {type(payload).__name__}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(url: str, dest: Path) -> None:
    http_client.download_file(url, dest, timeout=60, headers={"User-Agent": USER_AGENT})


def _find_release_asset(release_payload: dict) -> dict:
    assets = release_payload.get("assets") or []
    exact = [asset for asset in assets if asset.get("name") == "roms.json"]
    if exact:
        return exact[0]

    fallback = [
        asset for asset in assets
        if str(asset.get("name", "")).lower().endswith("roms.json")
    ]
    if fallback:
        return fallback[0]

    raise ValueError("Latest pinmame-score-parser release does not contain a roms.json asset")


def _normalize_release_digest(asset: dict) -> str:
    digest = str(asset.get("digest", "")).strip()
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    if digest:
        return digest.lower()
    return ""


def _release_fingerprint(release_payload: dict, asset: dict) -> str:
    remote_digest = _normalize_release_digest(asset)
    if remote_digest:
        return remote_digest

    for value in (
        asset.get("updated_at"),
        release_payload.get("published_at"),
        release_payload.get("tag_name"),
        asset.get("id"),
        release_payload.get("id"),
    ):
        if value:
            return str(value).strip()
    return ""


def _stamp() -> None:
    cache_stamps.put(STAMPS, checked=timestamps.utc_now_iso())


def ensure_latest_roms_json() -> dict:
    ROMS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)

    tracked_sha = cache_stamps.get(STAMPS, "release").lower()

    checked = timestamps.iso_to_epoch(cache_stamps.get(STAMPS, "checked"))
    fresh = checked is not None and time.time() - checked < CHECK_EVERY_SECONDS
    if fresh and ROMS_JSON_PATH.exists():
        logger.debug("roms.json was checked at %s; not asking again yet",
                     timestamps.epoch_to_iso(checked))
        return {"status": "checked_recently", "path": ROMS_JSON_PATH,
                "tracked_sha": tracked_sha}

    release_payload = _request_json(LATEST_RELEASE_URL)
    asset = _find_release_asset(release_payload)
    download_url = asset.get("browser_download_url")
    if not download_url:
        raise ValueError(
            "Latest pinmame-score-parser release asset is missing browser_download_url")

    fingerprint = _release_fingerprint(release_payload, asset).lower()
    needs_download = not ROMS_JSON_PATH.exists()
    if not needs_download and fingerprint:
        needs_download = tracked_sha != fingerprint

    if not needs_download:
        logger.info("roms.json is already up to date at %s", ROMS_JSON_PATH)
        _stamp()
        return {
            "status": "up_to_date",
            "path": ROMS_JSON_PATH,
            "tracked_sha": tracked_sha,
        }

    ROMS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with staged_for(ROMS_JSON_PATH) as temp_path:
        logger.info("Downloading latest roms.json from %s", download_url)
        _download_file(download_url, temp_path)
        downloaded_sha = _sha256_file(temp_path)
        expected_sha = _normalize_release_digest(asset)
        if expected_sha and downloaded_sha.lower() != expected_sha:
            raise ValueError(
                "Downloaded roms.json SHA256 does not match the release digest: "
                f"expected {expected_sha}, got {downloaded_sha.lower()}"
            )

    cache_stamps.put(STAMPS, release=fingerprint or downloaded_sha.lower(),
                     checked=timestamps.utc_now_iso())
    logger.info("Updated roms.json at %s", ROMS_JSON_PATH)
    return {
        "status": "downloaded",
        "path": ROMS_JSON_PATH,
        "tracked_sha": fingerprint or downloaded_sha.lower(),
        "file_sha": downloaded_sha.lower(),
    }

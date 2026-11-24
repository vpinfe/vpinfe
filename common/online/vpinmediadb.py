"""VPinMediaDB, the catalog of pictures and videos, as a source.

One JSON manifest for everything it publishes, held whole so every size it carries can be
offered rather than the one the display happens to be set to. The URL never comes from a
caller: the source makes its own, which is what stops this being a way to make an install
fetch whatever somebody asks it to.
"""

from __future__ import annotations

import logging

from common.online import picture_changes, vpsdb_media
from common.online.sources import Offer, Source, may_fetch

logger = logging.getLogger("vpinfe.common.online.vpinmediadb")

ID = "vpinmediadb"

_MANIFEST: dict | None = None


def _manifest() -> dict:
    """The index, fetched once per process and kept.

    One file for the whole catalog, so re-fetching it per request would be a round
    trip to learn nothing. A failed fetch caches nothing and the next caller retries.
    Inside `sources.cached_only` it never fetches: an index not yet held reads as empty.
    """
    global _MANIFEST
    if _MANIFEST is None and may_fetch():
        from common.online.vpsdb_cache import VPinMediaDatabase
        _MANIFEST = VPinMediaDatabase(vpsdb_media.MANIFEST_URL).load()
    return _MANIFEST or {}


def refresh_manifest(fetched: dict | None = None) -> None:
    """Read the index again, or take `fetched`, a copy the caller has just read. A failed
    read keeps the copy already held."""
    global _MANIFEST
    from common.online.vpsdb_cache import VPinMediaDatabase

    fresh = fetched or VPinMediaDatabase(vpsdb_media.MANIFEST_URL).load()
    if fresh:
        _MANIFEST = fresh


def warm(config: object) -> bool:
    """Load the index if this process holds none, for the sources watch to call. Pages read
    the index as held and never fetch it, so this is what puts it there. An offline or failed
    read holds nothing, raises nothing, and the next wake asks again. True when it loaded."""
    if _MANIFEST:
        return False
    try:
        refresh_manifest()
    except Exception:
        logger.debug("The VPinMediaDB index could not be read; will try again", exc_info=True)
        return False
    return bool(_MANIFEST)


def _offers(who: dict, kind: str) -> list[Offer]:
    found = vpsdb_media.offered(_manifest(), str(who.get("vps_id") or "")).get(kind) or []
    return [Offer(source=ID, name=str(item["url"]).rsplit("/", 1)[-1], url=item["url"],
                  kind=kind, size=item.get("size", ""), md5=item.get("md5", ""))
            for item in found]


SOURCE = Source(
    id=ID, name="VPinMediaDB", url="https://github.com/superhac/vpinmediadb",
    kinds=frozenset(vpsdb_media.MANIFEST_KINDS), refresh=warm, offers=_offers,
    upgrades=picture_changes.upgrades,
    automatic=(("updates", "get_media_for_new_games"),
               ("updates", "update_downloaded_media")),
    reachable=lambda: bool(_manifest()))

"""The Virtual Pinball Spreadsheet as the first source in the watch.

Every shelf starts from it, so it has no switch; its schedule is the `download_spreadsheet`
setting, and `never` there keeps VPinFE off it. It lists the releases `vps_releases` indexes
and names a version for each.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from common.config.config_store import ConfigStore
from common.online import vps_releases, vpsdb_sync
from common.online.sources import Listed, Source

logger = logging.getLogger("vpinfe.common.online.vps_source")

ID = "vps"


def refresh(config: ConfigStore) -> bool:
    """Ask the catalog if it is due, and say whether it changed."""
    result = vpsdb_sync.sync(config)
    if result.get("changed"):
        logger.info("VPSdb updated to %s", result.get("version") or "?")
    return bool(result.get("changed"))


def listing() -> Iterator[Listed]:
    """Every release the catalog lists, with the version and the date VPS added it."""
    for vps_id, listed in vps_releases.index()["entries"].items():
        for kind, releases in listed.items():
            for release in releases:
                if release["id"]:
                    yield Listed(release["id"], vps_id, kind, release["version"],
                                 release["added"])


SOURCE = Source(id=ID, name="Virtual Pinball Spreadsheet", versions=True, always_on=True,
                refresh=refresh, listing=listing)

"""One block at the start of every run, so a log sent alone says what it ran on.

Nothing here names the device or the person: no host, no user, no folder. The block goes
through the logger as one record, and its continuation lines belong to that record.
"""

from __future__ import annotations

import logging
import platform

from common import i18n
from common.host import about
from common.support import session
from common.vpinfe_version import get_version

logger = logging.getLogger("vpinfe.common.support.banner")


def lines() -> list[str]:
    facts = [
        ("Build", about.build_flavor()),
        ("Release target", about.release_target()),
        ("System", f"{about.operating_system()} {platform.machine()}"),
        ("Python", platform.python_version()),
        ("Windowing system", about.windowing_system()),
        *session.read(),
        ("Language", i18n.language()),
    ]
    return [f"  {label}: {value}" for label, value in facts]


def write() -> None:
    """Never raises: a failed banner is a missing paragraph, not a start that fails."""
    try:
        with i18n.in_source_language():
            body = "\n".join(lines())
    except Exception:  # noqa: BLE001 - the run goes on without it
        logger.warning("The startup banner could not be written", exc_info=True)
        return
    logger.info("Starting VPinFE %s\n%s", get_version(), body)

"""Finding VPX's own log file, and optionally clearing it before a session."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path


def _wanted(value: object) -> bool:
    """A launcher's stored value, which a hand-edited file may hold as a string."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


logger = logging.getLogger("vpinfe.common.host.vpx_log")

VPINBALL_LOG_FILENAME = "vpinball.log"

# `2026-10-02 15:20:32.448 INFO  [3414553] [VPApp::InitInstance@326] message`: the time, the
# level padded to a column, the thread, the function and line that wrote it.
RECORD = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+ (\w+)\s+\[\d+\] \[([^\]]*)\] (.*)$")


def resolve_vpinball_log_path(vpx_ini_path: str) -> Path | None:
    vpx_ini_path = (vpx_ini_path or "").strip()
    if not vpx_ini_path:
        return None
    return Path(os.path.expanduser(vpx_ini_path)).parent / VPINBALL_LOG_FILENAME


def delete_vpinball_log_on_start_if_configured(delete_on_start: object,
                                               ini_path: str) -> Path | None:
    """Clear Visual Pinball's log before a launch, if the launcher asks for it.

    Takes the two values rather than a settings object: which launcher is about to run
    decides both of them now, and a launcher is not a config section.
    """
    if not _wanted(delete_on_start):
        return None

    log_path = resolve_vpinball_log_path(ini_path)
    if log_path is None:
        logger.warning("Skipping VPinball log delete: this launcher names no ini file")
        return None

    try:
        log_path.unlink()
    except FileNotFoundError:
        logger.info("VPinball log already missing before launch: %s", log_path)
    except Exception:
        logger.exception("Failed to delete VPinball log before launch: %s", log_path)
    else:
        logger.info("Deleted VPinball log before launch: %s", log_path)

    return log_path


def current_vpinball_log() -> Path | None:
    """Where the Visual Pinball this install launches writes, or None when no launcher
    names an ini file. Never raises: the Logs page asks on every read, and a launcher
    store that cannot be read must not take the VPinFE log down with it."""
    try:
        from common.games import launchers

        return resolve_vpinball_log_path(launchers.default_value("ini_path"))
    except Exception:
        logger.debug("Could not find Visual Pinball's log", exc_info=True)
        return None

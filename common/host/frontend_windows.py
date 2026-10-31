"""Where each of the frontend's windows put itself, as the window said.

A window reports through `record` once it has drawn; `current` is what a support summary
reads. A report is what a window last said, never a promise it is still there, so the
reader checks `frontend_state` for whether the frontend is up.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from common.atomic_write import write_atomic
from common.config import paths
from common.timestamps import utc_now_iso

logger = logging.getLogger("vpinfe.common.host.frontend_windows")

# The class the server accepts a window name in, and how many windows are kept. A new name
# past that takes the place of the one that reported longest ago, so a page cannot lock the
# real windows out and a theme that changed its names does not leave the old ones behind.
NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
MOST = 12
# A ceiling on every number the page sends, so a page that lies cannot fill the file.
PIXELS = 100_000

_lock = threading.Lock()


def _whole(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if -PIXELS <= value <= PIXELS else None


def _scale(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 2) if 0 < value <= 16 else None


def _oldest(held: dict[str, Any], keep: str) -> str:
    return min((one for one in held if one != keep),
               key=lambda one: str(held[one].get("reported_at") or "")
               if isinstance(held[one], dict) else "")


def read() -> dict[str, dict[str, Any]]:
    """Every window's last report, by name, or none where there is no file."""
    try:
        held = json.loads(paths.FRONTEND_WINDOWS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Could not read %s", paths.FRONTEND_WINDOWS_PATH, exc_info=True)
        return {}
    return held if isinstance(held, dict) else {}


def record(raw: Any) -> dict[str, Any]:
    """Keep what a window reported about itself. Untrusted: the name must be one a window
    can have, and every number is bounded."""
    raw = raw if isinstance(raw, dict) else {}
    name = raw.get("window")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return {}
    report = {"x": _whole(raw.get("x")), "y": _whole(raw.get("y")),
              "width": _whole(raw.get("width")), "height": _whole(raw.get("height")),
              "scale": _scale(raw.get("scale")), "reported_at": utc_now_iso()}
    with _lock:
        held = read()
        held[name] = report
        while len(held) > MOST:
            held.pop(_oldest(held, keep=name))
        try:
            paths.FRONTEND_WINDOWS_PATH.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(paths.FRONTEND_WINDOWS_PATH,
                         lambda handle: json.dump(held, handle, indent=2, sort_keys=True))
        except OSError:
            logger.warning("Could not keep the window report in %s",
                           paths.FRONTEND_WINDOWS_PATH, exc_info=True)
    return report

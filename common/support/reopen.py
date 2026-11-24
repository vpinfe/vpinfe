"""The note left for the Support Package to open again after VPinFE restarts.

One file in the state folder. `take` reads and removes it, so the package opens once, and a
note older than the window is ignored, so a restart that never came back does not open a
package days later.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from common.config import paths

logger = logging.getLogger("vpinfe.common.support.reopen")

WINDOW_S = 600.0
NOTE = paths.STATE_DIR / "support-package-reopen"


def ask() -> None:
    """Leave the note."""
    try:
        NOTE.parent.mkdir(parents=True, exist_ok=True)
        NOTE.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        logger.warning("Could not leave the note to reopen the Support Package", exc_info=True)


def take() -> bool:
    """True once for a note left within the window, and the note is gone either way."""
    claimed = NOTE.with_name(f"{NOTE.name}.{os.getpid()}.{threading.get_ident()}")
    try:
        os.replace(NOTE, claimed)
    except OSError:
        return False
    try:
        left = float(claimed.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        left = -1.0
    try:
        claimed.unlink()
    except OSError:
        logger.warning("Could not remove the Support Package reopen note", exc_info=True)
    return 0 <= time.time() - left <= WINDOW_S

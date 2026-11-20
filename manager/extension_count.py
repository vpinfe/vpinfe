"""The number on the Extensions entry in the menu: updates the list offers, extensions
whose files changed, and extensions the list dropped.

Counted from the same read as Overview's rows, so the two agree, and shown whether or not
Overview is on. Asked when the page opens and again after anything on the Extensions page
that can move it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from common.failures import why
from common.i18n import t
from manager import rail_marks
from manager.kit import offload
from manager.kit.api import ApiClient
from manager.kit.data import extension_list_held

logger = logging.getLogger("vpinfe.manager.extension_count")

KEY = "extensions"


def held(client: ApiClient | None = None) -> dict[str, Any]:
    """What the kept list and the installed extensions say needs doing. Blocking."""
    client = client or ApiClient()
    return extension_list_held(client.extension_list(), client.extensions())


def mark_of(found: dict[str, Any]) -> rail_marks.Mark | None:
    """The count and a tooltip that names each one, or nothing when none needs doing."""
    parts = []
    count = 0
    for kind, key in (("updates", "manager.page.extension_updates"),
                      ("changed", "manager.page.extension_changed"),
                      ("left", "manager.page.extension_left")):
        items = found.get(kind) or []
        if items:
            count += len(items)
            parts.append(t(key, names=", ".join(str(one["display_name"]) for one in items)))
    return rail_marks.Mark(count, "; ".join(parts)) if count else None


class Count:
    """What the entry shows. `warm` is the page's marks by entry, `repaint` draws them."""

    def __init__(self, warm: dict[str, rail_marks.Mark], repaint: Callable[[], None],
                 read: Callable[[], dict[str, Any]] = held) -> None:
        self.warm = warm
        self.repaint = repaint
        self.read = read

    def show(self, found: dict[str, Any]) -> None:
        mark = mark_of(found)
        if self.warm.get(KEY) == mark:
            return
        if mark is None:
            self.warm.pop(KEY, None)
        else:
            self.warm[KEY] = mark
        self.repaint()

    async def recount(self) -> None:
        """Read it again: when the page opens, and after anything that can move it."""
        try:
            found = await offload.io(self.read)
        except Exception as exc:  # noqa: BLE001 - the rail goes without the count
            logger.warning("Could not read what the extension list needs done: %s", why(exc))
            return
        self.show(found)

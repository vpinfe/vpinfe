"""The number on Discovery's entry in the menu: how many games have an update.

Counted from the same place Home's shelf is, so the two agree. Discovery's Home hands over
the count it already read; every other change that can move it - a mark, a tie, an import,
the setting that turns it off - asks again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from common.failures import why
from common.i18n import t
from manager import rail_marks
from manager.kit import offload
from manager.kit.api import ApiClient, updates_shown

logger = logging.getLogger("vpinfe.manager.update_count")

KEY = "discovery"


def shown(values: dict[str, Any]) -> bool:
    """Whether the person has Discovery's entry count its games with an update. On unless
    the setting says otherwise."""
    return updates_shown(values)


def waiting(client: ApiClient | None = None) -> tuple[bool, int]:
    """Whether the entry counts, and how many games have an update: 0 without asking while
    the setting has it off. A pair, because `offload.io` takes `None` for a shutdown.
    Blocking."""
    client = client or ApiClient()
    if not shown(client.config_values()):
        return False, 0
    return True, int(client.discovery_updates().get("count") or 0)


class Count:
    """What the entry shows. `warm` is the page's marks by entry, `repaint` draws them."""

    def __init__(self, warm: dict[str, rail_marks.Mark], repaint: Callable[[], None],
                 read: Callable[[], tuple[bool, int]] = waiting) -> None:
        self.warm = warm
        self.repaint = repaint
        self.read = read
        self.on = True

    def show(self, count: int) -> None:
        """The games with an update on the entry, nothing drawn for none."""
        mark = rail_marks.Mark(count, t("manager.page.games_updated", count=count)) \
            if count else None
        if self.warm.get(KEY) == mark:
            return
        if mark is None:
            self.warm.pop(KEY, None)
        else:
            self.warm[KEY] = mark
        self.repaint()

    def from_home(self, count: int) -> None:
        """The count Home read, unless the setting has the entry go without one."""
        if self.on:
            self.show(count)

    async def recount(self) -> None:
        """Read it again: when the page opens, and after anything that can move it."""
        try:
            self.on, count = await offload.io(self.read)
        except Exception as exc:  # noqa: BLE001 - the rail goes without the count
            logger.warning("Could not read how many games have an update: %s", why(exc))
            return
        self.show(count)

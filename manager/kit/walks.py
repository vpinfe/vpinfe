"""Walking a selection: a verb that asks something about each row, one dialog per row.

`each` is the loop. The dialog says where it is in the run and offers Skip and
Stop. **Skip leaves a row exactly as it was** - nothing is rewritten for being in the
selection, which is why this walks rather than runs.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.kit.walks")

# What `ask` answers to leave one row as it was, or to end the run. The stop is a NUL string
# so no answer a dialog can give is mistaken for it.
SKIPPED = None
STOPPED = "\x00stopped"


@on_page
async def each(rows: list[dict[str, Any]],
               ask: Callable[[dict[str, Any], str], Awaitable[Any]],
               write: Callable[[dict[str, Any], Any], Awaitable[Any]],
               failed: str) -> int:
    """Ask about each row in turn, write what was answered, and answer how many changed.

    `ask(row, place)` shows the dialog; `place` is the position in the run. It answers `SKIPPED`,
    `STOPPED`, or what to write. A write that raises says why on screen under `failed` and
    the walk goes on to the next row.
    """
    total = len(rows)
    changed = 0
    for index, row in enumerate(rows, 1):
        answer = await ask(row, t("manager.walk.position", index=index, total=total))
        if answer == STOPPED:
            break
        if answer is SKIPPED:
            continue
        try:
            await write(row, answer)
        except Exception as exc:  # noqa: BLE001 - one bad write does not end the walk
            logger.exception("Could not write %s", row.get("id"))
            ui.notify(failed, caption=why(exc), type="negative")
            continue
        changed += 1
    return changed

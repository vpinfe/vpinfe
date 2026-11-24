"""Setting an Overview row aside and bringing it back, each saying what it did with Undo.

A dismissal is the ids a row counted, kept per install; a stop is a check switched off, kept
where `needs.DEVICE_ROWS` says. Both are written off the loop and both end in a redraw of
the page, which draws from what the library holds after the write.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.kit import undo
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.overview import needs

logger = logging.getLogger("vpinfe.manager.overview.set_aside")


class Acts:
    """The things done to a row from the page, bound to the library they write to and
    to what redraws Overview afterwards."""

    def __init__(self, library: Library, redraw: Callable[[], None]) -> None:
        self.library = library
        self.redraw = redraw

    async def _record(self, key: str, ids: list[str]) -> None:
        await run.io_bound(self.library.set_dismissed, key, ids)
        self.redraw()

    async def _checking(self, key: str, on: bool) -> None:
        await run.io_bound(self.library.set_checking, key, on,
                           device=needs.is_device(key))
        self.redraw()

    @on_page
    async def dismiss(self, need: needs.Need) -> None:
        """Set `need` aside over everything it counts now."""
        before = list(self.library.dismissed().get(need.key, ()))
        try:
            await self._record(need.key, list(need.covered))
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: a dismissal was not saved", exc_info=True)
            ui.notify(t("manager.needs.could_not"), caption=why(exc), type="negative")
            return
        said = t("manager.needs.said_skipped") if need.key == needs.UPDATE \
            else t("manager.needs.said_dismissed", label=need.label)
        undo.offer(said, lambda: self._record(need.key, before),
                   caption=needs.comes_back(need.key, need))

    @on_page
    async def stop(self, key: str, label: str = "") -> None:
        """Stop checking `key`. `label` names it where `needs.name_of` cannot: a row an
        extension raised."""
        try:
            await self._checking(key, False)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: a check was not stopped", exc_info=True)
            ui.notify(t("manager.needs.could_not"), caption=why(exc), type="negative")
            return
        undo.offer(t("manager.needs.said_stopped", label=label or needs.name_of(key)),
                   lambda: self._checking(key, True),
                   caption=t("manager.needs.said_stopped.caption"))

    @on_page
    async def show_again(self, key: str, label: str = "") -> None:
        """Bring back a dismissed row."""
        before = list(self.library.dismissed().get(key, ()))
        try:
            await self._record(key, [])
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: a dismissal was not undone", exc_info=True)
            ui.notify(t("manager.needs.could_not"), caption=why(exc), type="negative")
            return
        undo.offer(t("manager.needs.said_showing", label=label or needs.name_of(key)),
                   lambda: self._record(key, before))

    @on_page
    async def check_again(self, key: str, label: str = "") -> None:
        """Turn a stopped check back on."""
        try:
            await self._checking(key, True)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: a check was not turned on", exc_info=True)
            ui.notify(t("manager.needs.could_not"), caption=why(exc), type="negative")
            return
        undo.offer(t("manager.needs.said_checking", label=label or needs.name_of(key)),
                   lambda: self._checking(key, False))

    @on_page
    async def answer(self, need: needs.Need, approve: bool) -> None:
        """Approve or decline the program's ask `need` is a row of."""
        try:
            await run.io_bound(self.library.answer_ask, need.answer, approve)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: an ask was not answered", exc_info=True)
            ui.notify(t("manager.needs.answer_failed"), caption=why(exc), type="negative")
            self.library.forget_watched()
            self.redraw()
            return
        ui.notify(t("manager.needs.said_approved" if approve else "manager.needs.said_declined"),
                  type="positive" if approve else "info")
        self.redraw()

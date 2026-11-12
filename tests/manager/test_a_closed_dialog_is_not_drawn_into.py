"""A media source dialog closed while a tab was reading draws nothing when the read ends."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import core, ui

from manager.kit import busy
from manager.library import mediasource

_PAGE = ui.element()


class _Probe(mediasource._Sources):
    uploads, online, games, keyed = False, False, False, False

    def __init__(self) -> None:
        library = Mock()
        library.discovery.return_value = {"display_name": "Cab"}
        super().__init__(library, "Wheel", Mock())

    def title(self) -> str:
        return "Probe"

    def verb_label(self) -> str:
        return "Use"

    def take_button(self) -> None:
        ui.button("Choose a File")

    def starts(self) -> list[dict[str, Any]]:
        return []

    def suffixes(self) -> tuple[str, ...]:
        return (".png",)


class AHostTabThatReadsAfterTheDialogClosed(unittest.IsolatedAsyncioTestCase):
    async def _drawn(self, *, closes: bool) -> Mock:
        self.enterContext(patch.object(core, "loop", asyncio.get_running_loop()))
        self.enterContext(patch.object(busy.ui, "timer"))
        probe = _Probe()
        with _PAGE:
            probe.open()
            body = ui.column()

        async def io(work: Callable[..., Any], *args: Any) -> Any:
            if closes:
                probe.dialog.close()
            return work(*args)

        self.enterContext(patch.object(mediasource.offload, "io", io))
        self.enterContext(patch.object(mediasource.folder_picker, "read_start",
                                       AsyncMock(return_value=[])))
        draw = self.enterContext(patch.object(mediasource.folder_picker, "draw_browser"))
        await probe.host_tab(body)
        return draw

    async def test_draws_the_browser_while_the_dialog_is_up(self) -> None:
        draw = await self._drawn(closes=False)

        draw.assert_called_once()

    async def test_draws_nothing_once_the_dialog_has_closed(self) -> None:
        draw = await self._drawn(closes=True)

        draw.assert_not_called()


if __name__ == "__main__":
    unittest.main()

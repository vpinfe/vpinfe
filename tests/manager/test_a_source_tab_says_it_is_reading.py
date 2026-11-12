"""A media source tab opened by a click is busy while it reads, and not after."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock, patch

from nicegui import core, ui

from manager.kit import busy
from manager.library import mediasource

_PAGE = ui.element()


def _busy(region: ui.element) -> bool:
    return region.props.get("aria-busy") == "true"


class _Probe(mediasource._Sources):
    uploads, online, games, keyed = False, True, True, True

    def __init__(self) -> None:
        library = Mock()
        library.discovery.return_value = {"display_name": "Cab"}
        super().__init__(library, "Wheel", Mock())
        self.gate = asyncio.Event()
        self.seen: dict[str, bool] = {}

    def title(self) -> str:
        return "Probe"

    def verb_label(self) -> str:
        return "Use"

    def take_button(self) -> None:
        ui.button("Choose a File")

    async def _read(self, tab: str, body: ui.column) -> None:
        self.seen[tab] = _busy(body)
        await self.gate.wait()
        with body:
            ui.label(tab)

    async def host_tab(self, body: ui.column) -> None:
        await self._read("host", body)

    async def online_tab(self, body: ui.column) -> None:
        await self._read("online", body)

    async def games_tab(self, body: ui.column) -> None:
        await self._read("games", body)

    async def keyed_tab(self, body: ui.column) -> None:
        await self._read("keyed", body)


class ATabOpenedByAClick(unittest.IsolatedAsyncioTestCase):
    async def test_busy_while_it_reads_and_idle_after(self) -> None:
        self.enterContext(patch.object(core, "loop", asyncio.get_running_loop()))
        self.enterContext(patch.object(busy.ui, "timer"))
        probe = _Probe()
        with _PAGE:
            probe.open()
        drawn = list(probe.dialog.descendants())
        tabs = next(one for one in drawn if isinstance(one, ui.tabs))
        bodies = {panel.props["name"]: next(one for one in panel.descendants()
                                             if "manager-source-fill" in one.classes)
                  for panel in drawn if isinstance(panel, ui.tab_panel)}

        for tab in ("online", "games", "keyed"):
            with self.subTest(tab):
                tabs.value = tab
                for _turn in range(50):
                    if tab in probe.seen:
                        break
                    await asyncio.sleep(0)

                self.assertEqual({tab: True}, {tab: probe.seen.get(tab, False)})
                self.assertTrue(_busy(bodies[tab]))

        probe.gate.set()
        for _turn in range(50):
            await asyncio.sleep(0)
        self.assertEqual({}, {tab: True for tab in ("online", "games", "keyed")
                              if _busy(bodies[tab])})


if __name__ == "__main__":
    unittest.main()

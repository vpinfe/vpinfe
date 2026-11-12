"""The ways into Setup that are not Setup itself: the line over an unfinished install's
pages, the bare address that opens it, and the row under System > This Device."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from nicegui import core, ui

from common.config.config_store import ConfigStore
from common.devices import install_setup
from manager import page
from manager.kit import panel
from manager.settings import settings
from manager.setup import setup as setup_page
from tests.support.clicks import said
from tests.support.library import TempTree

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _ran(run: Callable[[], Any]) -> Any:
    """`run`, with NiceGUI's loop set so a handler's own task is started at all."""
    async def inside() -> Any:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        try:
            return await run()
        finally:
            core.loop = was

    return asyncio.run(inside())


def _buttons(root: ui.element) -> list[ui.button]:
    return [one for one in root.descendants() if isinstance(one, ui.button)]


def _drawn(draw: Callable[[], None]) -> ui.element:
    """What `draw` adds to the page, under a container of its own."""
    with _PAGE:
        holder = ui.element("div")
        with holder:
            draw()
    return holder


class SetupLineTests(unittest.TestCase):
    def test_the_line_says_setup_is_unfinished_and_offers_continue(self) -> None:
        holder = _drawn(lambda: page.setup_line(window=False))

        words = [one.text for one in holder.descendants() if isinstance(one, ui.label)]
        self.assertIn("Setup isn't finished", words)
        (button,) = _buttons(holder)
        self.assertEqual(button.text, "Continue")

    def test_continue_is_a_button_so_the_keyboard_reaches_it(self) -> None:
        """A label with a click handler is a mouse-only control."""
        holder = _drawn(lambda: page.setup_line(window=False))

        (button,) = _buttons(holder)
        self.assertTrue(button._props.get("icon"))

    def test_pressing_continue_reopens_setup(self) -> None:
        opened = mock.AsyncMock()
        holder = _drawn(lambda: page.setup_line(window=False))

        async def run() -> None:
            with mock.patch.object(page.setup_page, "open_setup", opened):
                said(_buttons(holder)[0], "click")
                for _ in range(8):
                    await asyncio.sleep(0)

        _ran(run)
        opened.assert_called_once_with(window=False)

    def test_in_the_frontends_window_continue_says_so(self) -> None:
        opened = mock.AsyncMock()
        holder = _drawn(lambda: page.setup_line(window=True))

        async def run() -> None:
            with mock.patch.object(page.setup_page, "open_setup", opened):
                said(_buttons(holder)[0], "click")
                for _ in range(8):
                    await asyncio.sleep(0)

        _ran(run)
        opened.assert_called_once_with(window=True)


class ArrivalTests(unittest.TestCase):
    def test_a_bare_address_opens_setup_while_it_is_unfinished(self) -> None:
        self.assertTrue(page.arrives_in_setup(True, ""))
        self.assertTrue(page.arrives_in_setup(True, "   "))

    def test_a_link_naming_a_place_shows_that_place(self) -> None:
        self.assertFalse(page.arrives_in_setup(True, "games"))

    def test_a_finished_install_opens_nothing(self) -> None:
        self.assertFalse(page.arrives_in_setup(False, ""))


class ThisDeviceRowTests(TempTree):
    def test_the_row_opens_setup_titled_setup_with_close_for_the_way_out(self) -> None:
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        install_setup.finish(store)
        host = mock.AsyncMock(return_value=False)
        holder = _drawn(lambda: panel.facts(ui, settings._setup_entries()))

        async def run() -> None:
            with mock.patch.object(setup_page, "_config", return_value=store), \
                 mock.patch.object(setup_page.setup_vpx, "gather", setup_page.setup_vpx.Vpx), \
                 mock.patch.object(setup_page.setup_tables, "gather",
                                   lambda program="": setup_page.setup_tables.Tables()), \
                 mock.patch.object(setup_page.wizard, "open_dialog", host):
                (button,) = _buttons(holder)
                said(button, "click")
                for _ in range(200):
                    if host.called:
                        break
                    await asyncio.sleep(0.01)

        _ran(run)
        host.assert_called_once()
        self.assertEqual(host.call_args.kwargs["label"], "Setup")
        self.assertEqual(host.call_args.kwargs["aside"], "Close")


if __name__ == "__main__":
    unittest.main()

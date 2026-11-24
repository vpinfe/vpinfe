"""What opens a panel somebody shut, and the way into the panel for ticked rows, in a real
browser: a press on a row reopens it and the arrow keys do not, and a narrow window gets an
Edit button once two games are ticked.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

WIDE, NARROW = 1400, 700
ROW = (".ag-center-cols-container .ag-row[row-index=\"%d\"] "
       ".ag-cell:not([col-id=\"ag-Grid-SelectionColumn\"])")
TICK = ".ag-row[row-index=\"%d\"] .ag-selection-checkbox input"
TOGGLE = ".manager-pane-box .manager-panel-toggle"
PANEL_WIDTH = ("document.querySelector('.manager-pane-box > .q-splitter__after')"
               ".getBoundingClientRect().width")
FOCUSED = ("(() => { const el = document.querySelector('.nicegui-aggrid');"
           " const api = getElement(Number(el.id.slice(1))).api;"
           " const node = api.getRowNode(api.getGridOption('context').focusId);"
           " return node.rowIndex; })()")
EDIT = ("(() => { const el = document.querySelector('.manager-edit-selected');"
        " if (!el) return null; const at = el.getBoundingClientRect();"
        " const shown = at.width > 0 && getComputedStyle(el).display !== 'none';"
        " return shown ? el.querySelector('.block').textContent.trim() : ''; })()")
FOCUS_ROW_2 = ("(() => { const el = document.querySelector('.nicegui-aggrid');"
               " const api = getElement(Number(el.id.slice(1))).api;"
               " api.setFocusedCell(2, api.getGridOption('context').focusCol); })()")
ROWS = "document.querySelectorAll('.ag-center-cols-container .ag-row').length > 0"
RAIL_MAX = 100
# A focus move reopens within a frame or two of the round trip to the app; the wait is
# several times that.
NOTHING_REOPENS_S = 1.0
# The edit button's visibility is CSS and needs only the ticks to have settled.
TICKS_SETTLE_S = 0.5
PANEL_MIN = 250


class PaneReopenDrive(unittest.TestCase):
    seen: dict[str, Any] = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            for title in ("Alpha", "Bravo", "Charlie", "Delta"):
                write_game(Path(tmp), title,
                           info=game_info(title, vps_id="", game_id=title.lower()),
                           files={f"{title}.ini": b"[Player]"})
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        instance.wait_for_api()
        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def window(width: int) -> None:
                await browser.send("Emulation.setDeviceMetricsOverride",
                                   {"width": width, "height": 800, "deviceScaleFactor": 1,
                                    "mobile": False})

            await window(WIDE)
            await walk.visit("/manager?view=games")
            await browser.wait_for(ROWS)
            await browser.click(ROW % 1)
            await browser.wait_for(f"{PANEL_WIDTH} > {PANEL_MIN}")
            await browser.click(TOGGLE)
            await browser.wait_for(f"{PANEL_WIDTH} < {RAIL_MAX}")
            seen["shut"] = await browser.evaluate(PANEL_WIDTH)

            await browser.evaluate(FOCUS_ROW_2)
            await browser.wait_for(f"{FOCUSED} === 2")
            await asyncio.sleep(NOTHING_REOPENS_S)
            seen["after arrow"] = await browser.evaluate(PANEL_WIDTH)

            await browser.click(ROW % 3)
            await browser.wait_for(f"{PANEL_WIDTH} > {PANEL_MIN}")
            seen["after press"] = await browser.evaluate(PANEL_WIDTH)

            await browser.click(TICK % 0)
            await browser.click(TICK % 1)
            await asyncio.sleep(TICKS_SETTLE_S)
            seen["edit wide"] = await browser.evaluate(EDIT)

            await window(NARROW)
            await walk.visit("/manager?view=games")
            await browser.wait_for(ROWS)
            seen["edit before"] = await browser.evaluate(EDIT)
            await browser.click(TICK % 0)
            seen["edit one"] = await browser.evaluate(EDIT)
            await browser.click(TICK % 1)
            # The button is in the page from the start with its words already on it, so
            # only what the user sees says that the second tick has been taken.
            await browser.wait_for(f"{EDIT} === 'Edit 2 games'")
            seen["edit two"] = await browser.evaluate(EDIT)
            seen["opened before"] = await browser.evaluate(
                "!!document.querySelector('.manager-pane-box.manager-opened')")
            await browser.click(".manager-edit-selected")
            await browser.wait_for(
                "!!document.querySelector('.manager-pane-box.manager-opened')")
            seen["opened"] = await browser.evaluate(
                "({back: !!document.querySelector('.manager-opened .manager-pane-back'),"
                " text: document.querySelector('.manager-pane-box > .q-splitter__after')"
                ".innerText})")
        return seen

    def test_an_arrow_key_does_not_reopen_a_shut_panel(self) -> None:
        self.assertLess(self.seen["shut"], RAIL_MAX)
        self.assertLess(self.seen["after arrow"], RAIL_MAX)

    def test_a_press_on_a_row_reopens_it(self) -> None:
        self.assertGreater(self.seen["after press"], PANEL_MIN)

    def test_a_wide_window_has_no_edit_button(self) -> None:
        self.assertFalse(self.seen["edit wide"])

    def test_the_edit_button_waits_for_two_ticked_games(self) -> None:
        self.assertFalse(self.seen["edit before"])
        self.assertFalse(self.seen["edit one"])
        self.assertEqual(self.seen["edit two"], "Edit 2 games")

    def test_ticking_a_second_game_does_not_open_the_panel_by_itself(self) -> None:
        self.assertFalse(self.seen["opened before"])

    def test_the_edit_button_opens_the_selection_with_a_back_arrow(self) -> None:
        opened = self.seen["opened"]
        self.assertTrue(opened["back"])
        self.assertTrue(opened["text"].strip())


if __name__ == "__main__":
    unittest.main()

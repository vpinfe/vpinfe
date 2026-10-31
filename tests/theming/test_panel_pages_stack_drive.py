"""A panel page below 760 px, in a real browser: the list takes the width, a picked row opens
its panel full width with a Back arrow, and a wide window shows both side by side.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

WIDE, NARROW = 1400, 700
ROW = (".ag-center-cols-container .ag-row[row-index=\"%d\"] "
       ".ag-cell:not([col-id=\"ag-Grid-SelectionColumn\"])")
NEXT = ".manager-pane-next"
FOCUSED = ("(() => { const el = document.querySelector('.nicegui-aggrid');"
           " const api = getElement(Number(el.id.slice(1))).api;"
           " const node = api.getRowNode(api.getGridOption('context').focusId);"
           " return node.rowIndex; })()")
SHOWN = ("(() => { const box = (selector) => { const el = document.querySelector(selector);"
         " if (!el) return null; const at = el.getBoundingClientRect();"
         " return at.width > 0 && getComputedStyle(el).display !== 'none' ? at.width : 0; };"
         " return {list: box('.manager-pane-box > .q-splitter__before'),"
         " panel: box('.manager-pane-box > .q-splitter__after'),"
         " back: box('.manager-pane-back'),"
         " box: box('.manager-pane-box')}; })()")
COLLECTIONS_ROW = 'a.manager-nav-row[href="/manager?view=collections"]'
ROWS = "document.querySelectorAll('.ag-center-cols-container .ag-row').length > 0"
PLACE = ("(() => ({search: location.search, length: history.length,"
         " address: location.pathname + location.search}))()")


class PanelPagesStackDrive(unittest.TestCase):
    seen: dict[str, dict] = {}

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
    async def _drive(cls, instance: LiveInstance) -> dict[str, dict]:
        seen: dict[str, dict] = {}
        instance.wait_for_api()
        instance.post("/api/v1/collections", {"name": "Picked"})
        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def window(width: int) -> None:
                await browser.send("Emulation.setDeviceMetricsOverride",
                                   {"width": width, "height": 800, "deviceScaleFactor": 1,
                                    "mobile": False})

            async def opened(want: bool) -> None:
                await browser.wait_for(
                    "!!document.querySelector('.manager-pane-box.manager-opened')"
                    f" === {str(want).lower()}")

            await window(NARROW)
            await walk.visit("/manager?view=games")
            await browser.wait_for(ROWS)
            seen["narrow list"] = await browser.evaluate(SHOWN)
            await browser.click(ROW % 1)
            await browser.wait_for(
                "!!document.querySelector('.manager-pane-box.manager-opened')")
            seen["narrow panel"] = await browser.evaluate(SHOWN)
            seen["stepped from"] = {"row": await browser.evaluate(FOCUSED)}
            await browser.click(NEXT)
            await browser.wait_for(f"{FOCUSED} !== {seen['stepped from']['row']}")
            seen["stepped to"] = {"row": await browser.evaluate(FOCUSED)}
            await browser.click(".manager-pane-back")
            await browser.wait_for(
                "!document.querySelector('.manager-pane-box.manager-opened')")
            seen["narrow back"] = await browser.evaluate(SHOWN)
            seen["arrow back"] = await browser.evaluate(PLACE)
            await browser.evaluate("history.forward()")
            await opened(True)
            seen["forward after the arrow"] = await browser.evaluate(PLACE)
            await browser.evaluate("history.back()")
            await opened(False)

            await browser.click(ROW % 2)
            await opened(True)
            seen["picked again"] = await browser.evaluate(PLACE)
            await browser.evaluate("history.back()")
            await opened(False)
            seen["browser back"] = {**await browser.evaluate(PLACE),
                                    "shown": await browser.evaluate(SHOWN)}
            await browser.evaluate("history.forward()")
            await opened(True)
            await browser.wait_for("location.search.includes('pane=open')")
            seen["browser forward"] = {**await browser.evaluate(PLACE),
                                       "shown": await browser.evaluate(SHOWN)}

            await walk.visit(seen["browser forward"]["address"])
            await opened(True)
            seen["reload on the panel"] = await browser.evaluate(PLACE)
            await walk.visit(seen["browser back"]["address"])
            await browser.wait_for(ROWS)
            await opened(False)
            seen["reload on the list"] = {**await browser.evaluate(PLACE),
                                          "shown": await browser.evaluate(SHOWN)}

            await walk.visit("/manager?view=collections")
            await browser.wait_for(ROWS)
            await browser.click(ROW % 1)
            await opened(True)
            await browser.evaluate("history.back()")
            await opened(False)
            seen["collection list"] = await browser.evaluate(PLACE)
            await walk.visit(seen["collection list"]["address"])
            await browser.wait_for(ROWS)
            await opened(False)
            seen["collection reload"] = {**await browser.evaluate(PLACE),
                                         "shown": await browser.evaluate(SHOWN)}

            await walk.visit(seen["browser forward"]["address"])
            await opened(True)
            await browser.evaluate(f"document.querySelector({COLLECTIONS_ROW!r}).click()")
            await browser.wait_for("location.search.includes('view=collections')")
            await browser.evaluate("history.back()")
            await opened(True)
            await browser.click(".manager-pane-back")
            await opened(False)
            seen["arrow after nav and back"] = await browser.evaluate(PLACE)

            await window(WIDE)
            await browser.wait_for(
                f"innerWidth === {WIDE} && document.querySelector("
                "'.manager-pane-box > .q-splitter__after').getBoundingClientRect().width > 0")
            seen["wide"] = await browser.evaluate(SHOWN)
        return seen

    def test_a_narrow_window_shows_the_list_alone(self) -> None:
        shown = self.seen["narrow list"]
        self.assertEqual(shown["list"], shown["box"])
        self.assertFalse(shown["panel"])
        self.assertFalse(shown["back"])

    def test_a_picked_row_opens_its_panel_full_width_with_a_back_arrow(self) -> None:
        shown = self.seen["narrow panel"]
        self.assertEqual(shown["panel"], shown["box"])
        self.assertFalse(shown["list"])
        self.assertTrue(shown["back"])

    def test_next_in_the_panel_steps_from_the_row_that_was_picked(self) -> None:
        self.assertEqual(self.seen["stepped from"]["row"], 1)
        self.assertEqual(self.seen["stepped to"]["row"], 2)

    def test_back_returns_to_the_list(self) -> None:
        shown = self.seen["narrow back"]
        self.assertEqual(shown["list"], shown["box"])
        self.assertFalse(shown["panel"])

    def test_opening_a_panel_adds_an_entry_and_the_arrow_walks_it_back(self) -> None:
        self.assertIn("pane=open", self.seen["picked again"]["search"])
        self.assertIn("pane=list", self.seen["arrow back"]["search"])
        self.assertIn("pane=open", self.seen["forward after the arrow"]["search"])

    def test_the_browsers_back_closes_the_panel_and_forward_reopens_it(self) -> None:
        shown = self.seen["browser back"]["shown"]
        self.assertEqual(shown["list"], shown["box"])
        self.assertFalse(shown["panel"])
        self.assertIn("pane=list", self.seen["browser back"]["search"])
        shown = self.seen["browser forward"]["shown"]
        self.assertEqual(shown["panel"], shown["box"])
        self.assertIn("pane=open", self.seen["browser forward"]["search"])

    def test_a_reload_lands_on_the_half_the_address_names(self) -> None:
        self.assertIn("pane=open", self.seen["reload on the panel"]["search"])
        shown = self.seen["reload on the list"]["shown"]
        self.assertEqual(shown["list"], shown["box"])
        self.assertFalse(shown["panel"])

    def test_a_collections_list_entry_reloads_on_the_list(self) -> None:
        self.assertIn("pane=list", self.seen["collection list"]["search"])
        self.assertIn("collection=", self.seen["collection list"]["search"])
        shown = self.seen["collection reload"]["shown"]
        self.assertEqual(shown["list"], shown["box"])
        self.assertFalse(shown["panel"])

    def test_the_arrow_closes_a_panel_the_page_started_on_after_a_place_and_back(self) -> None:
        place = self.seen["arrow after nav and back"]
        self.assertIn("view=games", place["search"])
        self.assertIn("pane=list", place["search"])

    def test_a_wide_window_shows_both_with_no_back_arrow(self) -> None:
        shown = self.seen["wide"]
        self.assertGreater(shown["list"], 0)
        self.assertGreater(shown["panel"], 0)
        self.assertFalse(shown["back"])


if __name__ == "__main__":
    unittest.main()

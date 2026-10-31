"""The Remote's Games tab on a phone: rows with a frame, the Games | Tables switch,
collection chips, a search with a count, and Tiles that the phone remembers.

The test install has no frontend running, so a tap on a row has no wheel to move and
opens the game's sheet, the same place the chevron goes.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import OVERFLOWING, BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

ROWS = "document.querySelectorAll('.remote-row').length"
TILES = "document.querySelectorAll('.remote-tile').length"
NAMES = ("[...document.querySelectorAll('.remote-row-name')]"
         ".map(el => el.innerText.trim())")
SHEET_TEXT = "document.querySelector('.remote-sheet')?.innerText || ''"
SEGMENTS = ("[...document.querySelectorAll('.manager-segmented')]"
            ".map(box => [...box.querySelectorAll('.q-btn')].map(el => el.innerText.trim()))")
CHIPS = ("[...document.querySelectorAll('.remote-chip')]"
         ".map(el => el.innerText.trim())")
LIT_CHIPS = ("[...document.querySelectorAll('.remote-chip--on')]"
             ".map(el => el.innerText.trim())")
PAGE_WIDE = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
SEARCH = "document.querySelector('.remote-body input')"
LONG = ("Beta Intentionally Very Long Remake Edition With Extra Words And A Version "
        "Number v2.3.1-final-final-REALLY")


class RemoteGamesDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            write_game(root, "Alpha", info=game_info("Alpha", vps_id="", game_id="alpha-id"))
            write_game(root, "Beta", info=game_info(LONG, vps_id="", game_id="beta-id"))
            with LiveInstance(root) as instance:
                instance.wait_for_api()
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        async with BrowserSession(chromium_path() or "") as browser:
            await browser.as_phone()
            walk = ManagerWalk(browser, instance)

            async def opened() -> None:
                await walk.visit("/remote?screen=games")
                await browser.wait_for(f"{ROWS} + {TILES} > 0")
                await walk.drawn()

            await opened()
            seen["rows"] = await browser.evaluate(ROWS)
            seen["names"] = await browser.evaluate(NAMES)
            seen["segments"] = await browser.evaluate(SEGMENTS)
            seen["chips"] = await browser.evaluate(CHIPS)
            seen["lit chips"] = await browser.evaluate(LIT_CHIPS)
            seen["wide"] = await browser.evaluate(PAGE_WIDE)
            seen["leaks in the list"] = await browser.evaluate(OVERFLOWING)
            seen["field text"] = await browser.evaluate(
                "[...document.querySelectorAll('input')].map(el => "
                "parseFloat(getComputedStyle(el).fontSize))")
            seen["placeholder"] = await browser.evaluate(
                f"{SEARCH}?.placeholder || ''")
            seen["shell box"] = await browser.evaluate(
                "(() => { const r = document.querySelector('.remote-viewport')"
                ".getBoundingClientRect(); return [r.left, r.top, r.width, r.height, "
                "innerWidth, innerHeight]; })()")
            seen["viewport meta"] = await browser.evaluate(
                "[...document.querySelectorAll('meta[name=viewport]')].pop().content")

            await browser.evaluate(
                "window.rowClicks = 0;"
                "document.querySelector('.remote-row')"
                ".addEventListener('click', () => { window.rowClicks += 1; })")
            await browser.tap(".remote-row-open")
            await browser.wait_for(f"{SHEET_TEXT}.length > 0")
            seen["chevron sheet"] = await browser.evaluate(SHEET_TEXT)
            seen["row clicks from the chevron"] = await browser.evaluate("window.rowClicks")
            await browser.escape()
            await browser.wait_for(f"{SHEET_TEXT}.length === 0")

            await browser.tap(".remote-row")
            await browser.wait_for(f"{SHEET_TEXT}.length > 0")
            seen["row sheet"] = await browser.evaluate(SHEET_TEXT)
            seen["leaks in the sheet"] = await browser.evaluate(OVERFLOWING)
            await browser.escape()
            await browser.wait_for(f"{SHEET_TEXT}.length === 0")

            await browser.evaluate(
                "document.querySelectorAll('.manager-segmented')[0]"
                ".querySelectorAll('.q-btn')[1].click()")
            await browser.wait_for(
                "document.querySelector('.remote-body input')?.placeholder"
                f" === {json.dumps(t('manager.remote.search_tables', count=2))}")
            seen["tables rows"] = await browser.evaluate(ROWS)

            await browser.evaluate(
                "document.querySelectorAll('.manager-segmented')[0]"
                ".querySelectorAll('.q-btn')[0].click()")
            await browser.wait_for(
                "document.querySelector('.remote-body input')?.placeholder"
                f" === {json.dumps(t('manager.remote.search_games', count=2))}")

            await browser.evaluate(
                "document.querySelectorAll('.manager-segmented')[1]"
                ".querySelectorAll('.q-btn')[1].click()")
            await browser.wait_for(f"{TILES} === 2")
            seen["tiles"] = await browser.evaluate(TILES)
            seen["rows in tiles"] = await browser.evaluate(ROWS)
            seen["wide in tiles"] = await browser.evaluate(PAGE_WIDE)
            seen["leaks in tiles"] = await browser.evaluate(OVERFLOWING)

            await opened()
            seen["tiles after reload"] = await browser.evaluate(TILES)

            await browser.evaluate(
                "document.querySelectorAll('.manager-segmented')[1]"
                ".querySelectorAll('.q-btn')[0].click()")
            await browser.wait_for(f"{ROWS} === 2")
            await opened()
            seen["rows after reload"] = await browser.evaluate(ROWS)
        return seen

    def test_every_game_is_a_row_in_name_order(self) -> None:
        self.assertEqual(self.seen["rows"], 2, json.dumps(self.seen))
        self.assertEqual(self.seen["names"], ["Alpha", LONG])

    def test_a_long_name_is_cut_off_where_it_is_and_nothing_scrolls_sideways(self) -> None:
        for where in ("leaks in the list", "leaks in the sheet", "leaks in tiles"):
            with self.subTest(where=where):
                self.assertEqual(self.seen[where], [])

    def test_the_shell_is_pinned_to_every_edge_of_the_screen(self) -> None:
        left, top, width, height, window_width, window_height = self.seen["shell box"]
        self.assertEqual((left, top, width, height), (0, 0, window_width, window_height))

    def test_the_page_asks_not_to_be_zoomed(self) -> None:
        for asked in ("maximum-scale=1", "user-scalable=no"):
            self.assertIn(asked, self.seen["viewport meta"])

    def test_the_search_counts_what_it_searches(self) -> None:
        self.assertEqual(self.seen["placeholder"], t("manager.remote.search_games", count=2))

    def test_a_field_is_not_so_small_that_a_phone_zooms_the_page_on_tapping_it(self) -> None:
        self.assertTrue(self.seen["field text"], "no text field on the page")
        self.assertGreaterEqual(min(self.seen["field text"]), 16, self.seen["field text"])

    def test_two_switches_one_for_the_lens_and_one_for_the_view(self) -> None:
        self.assertEqual(self.seen["segments"],
                         [[t("manager.section.games"), t("manager.section.tables")],
                          [t("manager.remote.view_list"), t("manager.remote.view_tiles")]])

    def test_all_games_leads_the_chips_and_is_the_one_lit(self) -> None:
        self.assertEqual(self.seen["chips"][0], t("manager.remote.all_games"))
        self.assertEqual(self.seen["lit chips"], [t("manager.remote.all_games")])

    def test_the_chevron_and_a_tap_with_no_wheel_both_open_the_sheet(self) -> None:
        self.assertIn("Alpha", self.seen["chevron sheet"])
        self.assertIn("Alpha", self.seen["row sheet"])

    def test_the_chevrons_tap_does_not_reach_the_row_behind_it(self) -> None:
        self.assertEqual(self.seen["row clicks from the chevron"], 0)

    def test_the_tables_lens_has_a_row_per_file(self) -> None:
        self.assertEqual(self.seen["tables rows"], 2)

    def test_tiles_replace_the_rows_and_stay_on_a_phone_screen(self) -> None:
        self.assertEqual(self.seen["tiles"], 2)
        self.assertEqual(self.seen["rows in tiles"], 0)
        self.assertLessEqual(self.seen["wide in tiles"], 0)
        self.assertLessEqual(self.seen["wide"], 0)

    def test_the_phone_remembers_tiles_and_remembers_the_list_again(self) -> None:
        self.assertEqual(self.seen["tiles after reload"], 2)
        self.assertEqual(self.seen["rows after reload"], 2)


if __name__ == "__main__":
    unittest.main()

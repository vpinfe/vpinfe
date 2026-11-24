"""The Remote's shell on a phone: three tabs, a lit pill on the one you are on, a dots
button in the header, and a page that never scrolls as a document.

The test install has no frontend running, which is the case that opens on Games.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

TAB_LABELS = ("[...document.querySelectorAll('.remote-tab-label')]"
              ".map(el => el.innerText.trim())")
LIT_TAB = ("[...document.querySelectorAll('.remote-tab--here .remote-tab-label')]"
           ".map(el => el.innerText.trim())")
PILL = ("[...document.querySelectorAll('.remote-tab-icon')]"
        ".map(el => getComputedStyle(el).backgroundColor)")
HEADER_GLYPHS = ("[...document.querySelectorAll('.remote-header .q-icon')]"
                 ".map(el => el.innerText.trim())")
SHEET_TEXT = "document.querySelector('.remote-sheet')?.innerText || ''"
PAGE_OVERFLOW = ("[document.documentElement, document.body]"
                 ".map(el => getComputedStyle(el).overflow)")
NOTICES = "[...document.querySelectorAll('.q-notification')].map(el => el.innerText.trim())"
TAB_BAR = ("(() => { const bar = document.querySelector('.remote-tabs');"
           " return [bar.getBoundingClientRect().bottom, innerHeight,"
           " getComputedStyle(bar).paddingBottom]; })()")


class RemoteShellDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            write_game(root, "Solo", info=game_info("Solo", vps_id="", game_id="solo-id"))
            with LiveInstance(root) as instance:
                instance.wait_for_api()
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        async with BrowserSession(chromium_path() or "") as browser:
            await browser.as_phone()
            walk = ManagerWalk(browser, instance)

            async def opened(address: str) -> None:
                await walk.visit(address)
                await browser.wait_for("document.querySelectorAll('.remote-tab').length === 3")
                await walk.drawn()

            await opened("/remote")
            seen["labels"] = await browser.evaluate(TAB_LABELS)
            seen["lit when not said"] = await browser.evaluate(LIT_TAB)
            seen["pill"] = await browser.evaluate(PILL)
            seen["header glyphs"] = await browser.evaluate(HEADER_GLYPHS)
            seen["overflow"] = await browser.evaluate(PAGE_OVERFLOW)
            seen["dots"] = await browser.evaluate(
                "document.querySelectorAll('.remote-header .remote-dots').length")
            seen["tab bar"] = await browser.evaluate(TAB_BAR)

            await browser.tap(".remote-dots")
            await browser.wait_for(f"{SHEET_TEXT}.length > 0")
            seen["dots sheet"] = await browser.evaluate(SHEET_TEXT)

            # Before the wheel's offer is pressed, which changes what the instance is.
            for old in ("control", "play"):
                await opened(f"/remote?screen={old}")
                seen[f"lit on {old}"] = await browser.evaluate(LIT_TAB)

            for name in ("games", "now", "wheel"):
                await opened(f"/remote?screen={name}")
                seen[f"lit on {name}"] = await browser.evaluate(LIT_TAB)
                if name == "wheel":
                    seen["wheel text"] = await browser.evaluate(
                        "document.querySelector('.remote-viewport')?.innerText || ''")
                    await browser.tap(".remote-viewport .remote-action", nth=0)
                    await browser.wait_for(f"{NOTICES}.length > 0")
                    seen["after opening"] = await browser.evaluate(NOTICES)
        return seen

    def test_the_tabs_are_the_wheel_the_games_and_now(self) -> None:
        self.assertEqual(self.seen["labels"],
                         [t("manager.remote.tab_wheel"), t("manager.remote.tab_games"),
                          t("word.now")])

    def test_a_link_to_each_tab_opens_on_it(self) -> None:
        self.assertEqual(self.seen["lit on wheel"], [t("manager.remote.tab_wheel")])
        self.assertEqual(self.seen["lit on games"], [t("manager.remote.tab_games")])
        self.assertEqual(self.seen["lit on now"], [t("word.now")])

    def test_a_closed_frontend_opens_on_the_games(self) -> None:
        self.assertEqual(self.seen["lit when not said"], [t("manager.remote.tab_games")])

    def test_a_retired_screen_name_opens_where_nothing_was_said(self) -> None:
        self.assertEqual(self.seen["lit on control"], [t("manager.remote.tab_games")])
        self.assertEqual(self.seen["lit on play"], [t("manager.remote.tab_games")])

    def test_only_the_tab_you_are_on_has_the_lit_pill(self) -> None:
        pill = self.seen["pill"]
        lit = [i for i, color in enumerate(pill) if color != "rgba(0, 0, 0, 0)"]
        self.assertEqual(lit, [1], pill)

    def test_the_header_has_no_controller_and_one_dots_button(self) -> None:
        self.assertNotIn("sports_esports", self.seen["header glyphs"])
        self.assertEqual(self.seen["dots"], 1)

    def test_the_dots_sheet_says_why_it_has_no_tutorial_while_the_frontend_is_closed(self) -> None:
        sheet = self.seen["dots sheet"]
        self.assertIn(t("manager.remote.frontend_closed"), sheet)
        self.assertNotIn(t("word.tutorial"), sheet)

    def test_a_closed_frontend_offers_to_open_it_in_the_dots_sheet_and_on_the_wheel(self) -> None:
        offer = t("action.frontend.start")
        self.assertIn(offer, self.seen["dots sheet"])
        self.assertIn(offer, self.seen["wheel text"])

    def test_pressing_the_offer_opens_the_frontend_and_says_so(self) -> None:
        self.assertTrue(any(t("manager.devices.windows_opened") in said
                            for said in self.seen["after opening"]), self.seen["after opening"])

    def test_the_dots_sheet_holds_who_and_the_lifecycle_actions(self) -> None:
        sheet = self.seen["dots sheet"]
        for words in (t("manager.remote.who_playing"), t("action.vpinfe.restart"),
                      t("action.vpinfe.stop"), t("manager.remote.the_device").upper(),
                      t("action.system.restart"), t("action.system.stop")):
            self.assertIn(words, sheet)

    def test_a_screen_with_no_inset_leaves_no_room_under_the_tab_bar(self) -> None:
        bottom, window_height, padding = self.seen["tab bar"]
        self.assertEqual(bottom, window_height)
        self.assertEqual(padding, "0px")

    def test_the_page_does_not_scroll_as_a_document(self) -> None:
        self.assertEqual(self.seen["overflow"], ["hidden", "hidden"], json.dumps(self.seen))


if __name__ == "__main__":
    unittest.main()

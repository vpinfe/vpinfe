"""The Folders page in Settings, in a real browser: the rows, the menu on a row, and a
folder removed and put back.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

ADDRESS = "/manager?view=settings&page=library.folders"
BODY = "document.querySelector('.manager-settings-body').innerText"
ROWS = ".manager-settings-body .manager-member-row"
ROW_TEXTS = f"[...document.querySelectorAll({json.dumps(ROWS)})].map(el => el.innerText)"
MENU_ITEMS = ".q-menu .manager-menu-item"
MENU_TEXTS = (f"[...document.querySelectorAll({json.dumps(MENU_ITEMS)})]"
              ".map(el => el.innerText.replace(/^[a-z_]+\\s*/, '').trim())")
NOTICES = "[...document.querySelectorAll('.q-notification')].map(n => n.innerText)"
CHIPS = f"[...document.querySelectorAll({json.dumps(ROWS)})].map(el => el.innerText.includes(%s))"


def choose(word: str) -> str:
    return (f"[...document.querySelectorAll({json.dumps(MENU_ITEMS)})]"
            f".find(el => el.innerText.includes({json.dumps(word)})).click()")


class FoldersPageDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp, TemporaryDirectory() as second:
            for root in (Path(tmp), Path(second)):
                write_game(root, "Bravo", info=game_info(
                    "Bravo", vps_id="", game_id="bravo",
                    Info={"Manufacturer": "Williams", "Year": "1995"}))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance, second))

    @classmethod
    async def _drive(cls, instance: LiveInstance, second: str) -> dict:
        instance.wait_for_api()
        urllib.request.urlopen(urllib.request.Request(
            instance.manager_url("/api/v1/config/locations/second"),
            data=json.dumps({"path": second, "kind": "root"}).encode(), method="PUT",
            headers={"Content-Type": "application/json"}), timeout=10).close()
        seen: dict = {"second": second}
        async with BrowserSession(chromium_path() or "") as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1700, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)
            await walk.visit(ADDRESS)
            await browser.wait_for(f"document.querySelectorAll({json.dumps(ROWS)}).length === 2")
            seen["body"] = await browser.evaluate(BODY)
            seen["rows"] = await browser.evaluate(ROW_TEXTS)

            await walk.act(lambda: browser.click(f"{ROWS} > .q-btn", nth=0),
                           until=f"document.querySelectorAll({json.dumps(MENU_ITEMS)}).length > 0")
            seen["menu_first"] = await browser.evaluate(MENU_TEXTS)
            await browser.press("Escape", "Escape")
            seen["after_escape"] = await browser.evaluate(
                "Promise.race([new Promise(done => requestAnimationFrame(() => done("
                "document.visibilityState))),"
                " new Promise(done => setTimeout(done, 2000, 'stalled'))])")

            await walk.visit(ADDRESS)
            await browser.wait_for(f"document.querySelectorAll({json.dumps(ROWS)}).length === 2")
            await walk.act(lambda: browser.click(f"{ROWS} > .q-btn", nth=1),
                           until=f"document.querySelectorAll({json.dumps(MENU_ITEMS)}).length > 0")
            await browser.evaluate(choose(t("word.remove")))
            await browser.wait_for(f"document.querySelectorAll({json.dumps(ROWS)}).length === 1")
            seen["after_remove"] = await browser.evaluate(ROW_TEXTS)
            seen["notices"] = await browser.evaluate(NOTICES)

            await browser.evaluate(
                "[...document.querySelectorAll('.q-notification button')]"
                ".find(b => b.innerText.includes('Undo')).click()")
            await browser.wait_for(f"document.querySelectorAll({json.dumps(ROWS)}).length === 2")
            seen["after_undo"] = await browser.evaluate(ROW_TEXTS)

            await walk.act(lambda: browser.click(f"{ROWS} > .q-btn", nth=0),
                           until=f"document.querySelectorAll({json.dumps(MENU_ITEMS)}).length > 0")
            await browser.evaluate(choose(t("manager.theme_sources.move_down")))
            await browser.wait_for(
                f"{ROW_TEXTS}[0].includes({json.dumps(second)})")
            seen["after_move"] = await browser.evaluate(ROW_TEXTS)
            seen["moved_on_server"] = [
                one["path"] for one in json.load(urllib.request.urlopen(
                    instance.manager_url("/api/v1/locations"), timeout=10))["locations"]]

            await walk.act(lambda: browser.click(f"{ROWS} > .q-btn", nth=1),
                           until=f"document.querySelectorAll({json.dumps(MENU_ITEMS)}).length > 0")
            await browser.evaluate(choose(t("manager.folders.put_here")))
            chip = json.dumps(t("manager.folders.new_games"))
            await browser.wait_for(f"{CHIPS % chip}.some(Boolean)")
            seen["chips"] = await browser.evaluate(CHIPS % chip)
        return seen

    def test_both_folders_are_listed_in_order_with_what_they_hold(self) -> None:
        first, last = self.seen["rows"]

        self.assertIn(self.seen["second"], last)
        self.assertNotIn(self.seen["second"], first)
        self.assertTrue(all("1 game" in row for row in self.seen["rows"]), self.seen["rows"])

    def test_the_copy_in_the_lower_folder_is_reported_as_a_duplicate(self) -> None:
        self.assertIn("1 duplicate", self.seen["rows"][1])
        self.assertNotIn("duplicate", self.seen["rows"][0])

    def test_the_page_says_which_folder_wins_and_offers_the_choice(self) -> None:
        for key in ("manager.folders.priority", "manager.folders.add_folder"):
            self.assertIn(t(key), self.seen["body"])

    def test_the_menu_on_the_first_row_moves_down_and_never_up(self) -> None:
        menu = self.seen["menu_first"]

        self.assertIn(t("manager.theme_sources.move_down"), menu)
        self.assertNotIn(t("manager.theme_sources.move_up"), menu)
        self.assertEqual(menu[-1], t("word.remove"))

    def test_animation_frames_keep_running_after_an_escape(self) -> None:
        self.assertEqual(self.seen["after_escape"], "visible")

    def test_undo_puts_the_removed_folder_back_in_its_place(self) -> None:
        self.assertEqual(self.seen["after_undo"], self.seen["rows"])

    def test_move_down_changes_the_order_on_screen_and_on_the_server(self) -> None:
        self.assertIn(self.seen["second"], self.seen["after_move"][0])
        self.assertEqual(self.seen["moved_on_server"][0], self.seen["second"])

    def test_putting_new_games_here_marks_one_folder_only(self) -> None:
        self.assertEqual(sum(self.seen["chips"]), 1, self.seen["chips"])

    def test_removing_a_folder_leaves_one_and_says_nothing_on_disk_was_touched(self) -> None:
        self.assertEqual(len(self.seen["after_remove"]), 1)
        self.assertTrue(any("nothing on disk was touched" in one
                            for one in self.seen["notices"]), self.seen["notices"])


if __name__ == "__main__":
    unittest.main()

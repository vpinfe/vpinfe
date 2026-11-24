"""The Home shelves list in Settings > Library > Discovery, in a real browser: the rows,
a shelf switched off and moved, and Home drawn from what was saved.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from common.i18n import t
from manager.library import discovery_shelves as shelves
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

SETTINGS = "/manager?view=settings&page=library.discovery"
HOME = "/manager?view=discovery"
ROWS = ".manager-settings-body .manager-member-row"
ROW_COUNT = f"document.querySelectorAll({json.dumps(ROWS)}).length"
ROW_TEXTS = f"[...document.querySelectorAll({json.dumps(ROWS)})].map(el => el.textContent)"
SWITCHES = (f"[...document.querySelectorAll({json.dumps(ROWS + ' .q-toggle')})]"
            ".map(el => el.getAttribute('aria-checked') === 'true')")
MENU_ITEMS = ".q-menu .manager-menu-item"
# textContent, because innerText carries the stylesheet's capitals.
HEADINGS = ("[...document.querySelectorAll('.manager-fact-heading')]"
            ".map(el => el.textContent.trim())")

NEW = t("manager.discovery.new_shelf")
VIEWS = t("manager.discovery.views")


def choose(word: str) -> str:
    return (f"[...document.querySelectorAll({json.dumps(MENU_ITEMS)})]"
            f".find(el => el.innerText.includes({json.dumps(word)})).click()")


def stored(instance: LiveInstance) -> dict[str, Any]:
    return instance.api("/api/v1/config/settings").get(shelves.SECTION) or {}


async def saved(instance: LiveInstance, check: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        found = stored(instance)
        if check(found):
            return found
        await asyncio.sleep(0.2)
    raise TimeoutError(f"the settings never read as expected: {stored(instance)}")


class DiscoveryShelvesDrive(unittest.TestCase):
    seen: dict[str, Any] = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            write_game(Path(tmp), "Bravo", info=game_info(
                "Bravo", vps_id="", game_id="bravo",
                Info={"Manufacturer": "Williams", "Year": "1995"}))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @staticmethod
    async def _home(browser: BrowserSession, walk: ManagerWalk) -> list[str]:
        """The shelf headings Home draws, once it has drawn the shelves."""
        await walk.visit(HOME)
        await browser.wait_for(f"{HEADINGS}.includes({json.dumps(VIEWS)})"
                               f" || {HEADINGS}.includes({json.dumps(NEW)})")
        return [one for one in await browser.evaluate(HEADINGS) if one in (NEW, VIEWS)]

    @staticmethod
    async def _up(browser: BrowserSession, instance: LiveInstance, at: int) -> None:
        """Move Up on row `at`, once the order is saved and the list shows it."""
        before = stored(instance).get(shelves.ORDER_KEY)
        await browser.click(f"{ROWS} .q-btn", nth=at)
        await browser.wait_for(f"document.querySelectorAll({json.dumps(MENU_ITEMS)}).length > 0")
        moving = shelves.order(before)[at]
        await browser.evaluate(choose(t("manager.theme_sources.move_up")))
        await saved(instance, lambda found: found.get(shelves.ORDER_KEY) != before)
        await browser.wait_for(
            f"{ROW_TEXTS}[{at - 1}].includes({json.dumps(t(shelves.BY_ID[moving].name))})")

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict[str, Any]:
        instance.wait_for_api()
        seen: dict[str, Any] = {"fresh": dict(stored(instance))}
        async with BrowserSession(chromium_path() or "") as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1700, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)
            count = len(shelves.SHELVES)

            await walk.visit(SETTINGS)
            await browser.wait_for(f"{ROW_COUNT} === {count}")
            seen["rows"] = await browser.evaluate(ROW_TEXTS)
            seen["switches"] = await browser.evaluate(SWITCHES)
            seen["home_default"] = await cls._home(browser, walk)

            await walk.visit(SETTINGS)
            await browser.wait_for(f"{ROW_COUNT} === {count}")
            last = count - 1
            await cls._up(browser, instance, last)
            seen["after_one_move"] = await browser.evaluate(ROW_TEXTS)
            seen["order_after_one_move"] = stored(instance).get(shelves.ORDER_KEY)
            for at in range(last - 1, 0, -1):
                await cls._up(browser, instance, at)
            seen["order_views_first"] = stored(instance).get(shelves.ORDER_KEY)
            seen["rows_views_first"] = await browser.evaluate(ROW_TEXTS)
            seen["home_views_first"] = await cls._home(browser, walk)

            await walk.visit(SETTINGS)
            await browser.wait_for(f"{ROW_COUNT} === {count}")
            seen["rows_after_reload"] = await browser.evaluate(ROW_TEXTS)
            new_at = shelves.order(seen["order_views_first"]).index(shelves.NEW)
            await browser.click(f"{ROWS} .q-toggle", nth=new_at)
            await saved(instance, lambda found: found.get(shelves.HIDDEN_KEY) == [shelves.NEW])
            seen["switches_new_off"] = await browser.evaluate(SWITCHES)
            seen["home_new_off"] = await cls._home(browser, walk)

            await walk.visit(SETTINGS)
            await browser.wait_for(f"{ROW_COUNT} === {count}")
            await browser.evaluate(
                "[...document.querySelectorAll('.manager-settings-body .q-btn')]"
                f".find(b => b.innerText.includes({json.dumps(t('word.reset'))})).click()")
            await saved(instance, lambda found: not found.get(shelves.HIDDEN_KEY)
                        and not found.get(shelves.ORDER_KEY))
            await browser.wait_for(f"{SWITCHES}.every(Boolean)")
            seen["rows_after_reset"] = await browser.evaluate(ROW_TEXTS)
            seen["home_after_reset"] = await cls._home(browser, walk)
        return seen

    def test_a_fresh_install_stores_no_arrangement(self) -> None:
        self.assertFalse(self.seen["fresh"].get(shelves.ORDER_KEY))
        self.assertFalse(self.seen["fresh"].get(shelves.HIDDEN_KEY))

    def test_every_shelf_is_a_row_with_its_name_and_description_in_the_built_order(self) -> None:
        self.assertEqual(len(shelves.SHELVES), len(self.seen["rows"]))
        for row, shelf in zip(self.seen["rows"], shelves.SHELVES, strict=True):
            with self.subTest(shelf=shelf.id):
                self.assertIn(t(shelf.name), row)
                self.assertIn(t(shelf.description), row)

    def test_every_switch_starts_on(self) -> None:
        self.assertEqual([True] * len(shelves.SHELVES), self.seen["switches"])

    def test_home_starts_with_the_new_shelf_above_your_views(self) -> None:
        self.assertEqual([NEW, VIEWS], self.seen["home_default"])

    def test_one_move_up_changes_the_row_and_the_stored_order(self) -> None:
        order = self.seen["order_after_one_move"]

        self.assertEqual(shelves.VIEWS, order[-2])
        self.assertIn(t("manager.discovery.views"), self.seen["after_one_move"][-2])

    def test_the_rows_and_the_saved_order_agree_with_the_move(self) -> None:
        self.assertEqual(shelves.VIEWS, self.seen["order_views_first"][0])
        self.assertIn(VIEWS, self.seen["rows_views_first"][0])
        self.assertEqual(self.seen["rows_views_first"], self.seen["rows_after_reload"])

    def test_home_follows_the_order_without_a_restart(self) -> None:
        self.assertEqual([VIEWS, NEW], self.seen["home_views_first"])

    def test_a_shelf_switched_off_is_off_in_the_list_and_gone_from_home(self) -> None:
        self.assertEqual(1, self.seen["switches_new_off"].count(False))
        self.assertEqual([VIEWS], self.seen["home_new_off"])

    def test_reset_returns_the_list_and_home_to_the_built_arrangement(self) -> None:
        for row, shelf in zip(self.seen["rows_after_reset"], shelves.SHELVES, strict=True):
            self.assertIn(t(shelf.name), row)
        self.assertEqual([NEW, VIEWS], self.seen["home_after_reset"])


if __name__ == "__main__":
    unittest.main()

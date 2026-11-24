"""A feature switch on Settings > Identity is applied where the Manager stands, in a real browser.

Slow: boots a real instance and a real browser. The rail and the Settings index follow
the switch; the page is not taken again and the switch that was thrown is not replaced.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

GAMES = 'a.manager-nav-row[href="/manager?view=games"]'
INDEX_ROWS = ".manager-section-row"
SWITCH = ".manager-fact-switch"
STAY = "window.__stay === true"
KEPT = f"document.querySelectorAll('{SWITCH}[data-kept=\"1\"]').length"
MARK = ("(() => { window.__stay = true; const all = document.querySelectorAll("
        f"'{SWITCH}'); all.forEach(el => {{ el.dataset.kept = '1'; }}); return all.length; }})()")
SETTLE_S = 20.0


def _count(selector: str) -> str:
    return f"document.querySelectorAll({selector!r}).length"


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class FeatureSwitchAppliesLiveDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        assert binary is not None
        with TemporaryDirectory() as tmp, LiveInstance(Path(tmp)) as instance:
            write_game(Path(tmp), "Alpha")
            cls.seen = asyncio.run(cls._drive(instance, binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance, binary: str) -> dict:
        instance.wait_for_api()
        seen: dict = {}
        async with BrowserSession(binary) as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1700, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)
            await walk.visit("/manager?view=settings")
            await browser.wait_for(f"{_count(SWITCH)} > 0")
            seen["switches"] = await browser.evaluate(MARK)
            seen["rail before"] = await browser.evaluate(_count(GAMES))
            seen["index before"] = await browser.evaluate(_count(INDEX_ROWS))

            await browser.click(SWITCH)
            await browser.wait_for(f"{_count(GAMES)} === 0", timeout=SETTLE_S)
            await walk.drawn()
            seen["rail off"] = await browser.evaluate(_count(GAMES))
            seen["index off"] = await browser.evaluate(_count(INDEX_ROWS))
            seen["stayed off"] = await browser.evaluate(STAY)
            seen["kept off"] = await browser.evaluate(KEPT)

            await browser.click(SWITCH)
            await browser.wait_for(f"{_count(GAMES)} === 1", timeout=SETTLE_S)
            await walk.drawn()
            seen["rail on"] = await browser.evaluate(_count(GAMES))
            seen["index on"] = await browser.evaluate(_count(INDEX_ROWS))
            seen["stayed on"] = await browser.evaluate(STAY)
            seen["kept on"] = await browser.evaluate(KEPT)
        return seen

    def test_the_rail_follows_the_switch_without_the_page_being_taken_again(self) -> None:
        self.assertEqual((self.seen["rail before"], self.seen["rail off"],
                          self.seen["rail on"]), (1, 0, 1))
        self.assertTrue(self.seen["stayed off"] and self.seen["stayed on"])

    def test_the_settings_index_follows_the_switch(self) -> None:
        self.assertLess(self.seen["index off"], self.seen["index before"])
        self.assertEqual(self.seen["index on"], self.seen["index before"])

    def test_the_switch_that_was_thrown_is_not_replaced(self) -> None:
        self.assertGreater(self.seen["switches"], 0)
        self.assertEqual((self.seen["kept off"], self.seen["kept on"]),
                         (self.seen["switches"], self.seen["switches"]))


if __name__ == "__main__":
    unittest.main()

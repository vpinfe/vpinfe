"""The primary nav in a real browser: a row that lights and puts its address in the bar also
shows its page, from the page you were on.

Settings and Devices read before they draw, which runs the draw from a background task;
a draw that needs the page's slot raises there, and the nav then lights a row over the page
it was meant to replace.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

ROW = 'a.manager-nav-row[href="/manager?view=%s"]'
PAGE = "(document.querySelector('.q-page') || document.body).innerText.replace(/\\s+/g, ' ')"
# Metrics redraws its figures every second, so only the start of the page - its title and
# the line under it - says which page this is.
HEAD = 40
DESTINATIONS = ("settings", "devices", "overview", "games")


class PrimaryNavDrive(unittest.TestCase):
    seen: dict[str, tuple[str, str]] = {}
    logged = ""

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            write_game(Path(tmp), "Alpha",
                       info=game_info("Alpha", vps_id="", game_id="alpha"),
                       files={"Alpha.ini": b"[Player]"})
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))
                cls.logged = instance.output(tail=None)

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict[str, tuple[str, str]]:
        seen: dict[str, tuple[str, str]] = {}
        instance.wait_for_api()
        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)
            for key in DESTINATIONS:
                await walk.visit("/manager?view=metrics")
                before = await browser.evaluate(PAGE)
                await browser.evaluate(f"document.querySelector({ROW % key!r}).click()")
                await browser.wait_for(f"location.search.includes('view={key}')")
                with_changed = f"{PAGE}.slice(0, {HEAD}) !== {json.dumps(before[:HEAD])}"
                try:
                    await browser.wait_for(with_changed, timeout=10)
                except Exception:   # noqa: BLE001
                    pass
                seen[key] = (before, await browser.evaluate(PAGE))
        return seen

    def test_each_row_shows_its_own_page(self) -> None:
        for key in DESTINATIONS:
            with self.subTest(view=key):
                before, after = self.seen[key]
                self.assertNotEqual(before[:HEAD], after[:HEAD],
                                    f"{key}: the page it left is still showing")

    def test_a_draw_raised_nothing(self) -> None:
        at = self.logged.find('manager/page.py", line')
        self.assertEqual(at, -1, self.logged[max(0, at - 600):at + 1800])


if __name__ == "__main__":
    unittest.main()

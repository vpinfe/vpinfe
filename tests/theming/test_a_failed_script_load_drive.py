"""A Manager page whose script fails to load, in a real browser: the first failure reloads
the page, a second right after it puts the message and its Reload button over the page,
and a failed `/_nicegui/` script element counts the same as a rejected import.

Slow: boots a real instance and a real browser. The guard's own cases are in
`tests/manager/test_a_failed_script_load_reloads_once.py`; this is what the browser reports.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from manager.kit import script_failed
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import DRAWN

MISSING_MODULE = "setTimeout(() => import('/_nicegui/0.0.0/components/missing'), 0)"
MISSING_SCRIPT = ("(() => { const el = document.createElement('script');"
                  " el.src = '/_nicegui/0.0.0/static/missing.js';"
                  " document.head.appendChild(el); })()")
FORGET_NOTE = f"sessionStorage.removeItem({script_failed.NOTE_KEY!r})"
MESSAGE = "(el => el ? [...el.children].map(part => part.textContent) : null)" \
          "(document.querySelector('.manager-script-failed'))"
SETTLED_S = 1.5


class FailedScriptLoadDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            write_game(Path(tmp), "Alpha", info=game_info("Alpha", vps_id="", game_id="alpha"))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        instance.wait_for_api()
        seen: dict = {}

        async def opened(page: BrowserSession) -> None:
            await page.navigate(instance.manager_url("/manager"))
            await page.wait_for(DRAWN)
            await page.evaluate("window.__before_reload = true")

        async def reloaded(page: BrowserSession) -> None:
            await page.wait_for("window.__before_reload === undefined", timeout=15.0)
            await page.wait_for(DRAWN)

        async with BrowserSession(chromium_path() or "") as page:
            await opened(page)
            await page.evaluate(FORGET_NOTE)
            await page.evaluate(MISSING_MODULE)
            await reloaded(page)
            seen["message_after_first"] = await page.evaluate(MESSAGE)
            seen["noted"] = await page.evaluate(
                f"sessionStorage.getItem({script_failed.NOTE_KEY!r}) !== null")

            await page.evaluate("window.__before_reload = true")
            await page.evaluate(MISSING_MODULE)
            await page.wait_for(f"{MESSAGE} !== null", timeout=15.0)
            await asyncio.sleep(SETTLED_S)
            seen["still_the_same_page"] = await page.evaluate("window.__before_reload === true")
            seen["message_after_second"] = await page.evaluate(MESSAGE)

            await page.evaluate("document.querySelector('.manager-script-failed__reload')"
                                ".click()")
            await reloaded(page)
            seen["message_after_reload_button"] = await page.evaluate(MESSAGE)

            await page.evaluate(FORGET_NOTE)
            await page.evaluate("window.__before_reload = true")
            await page.evaluate(MISSING_SCRIPT)
            try:
                await reloaded(page)
                seen["script_element_reloaded"] = True
            except TimeoutError:
                seen["script_element_reloaded"] = False
        return seen

    def test_the_first_failure_reloads_and_leaves_the_page_clear(self) -> None:
        self.assertIsNone(self.seen["message_after_first"], self.seen)
        self.assertTrue(self.seen["noted"], self.seen)

    def test_a_second_failure_right_after_stops_and_says_so(self) -> None:
        self.assertTrue(self.seen["still_the_same_page"], self.seen)
        title, detail, reload = self.seen["message_after_second"]
        self.assertEqual(t("manager.page.script_failed"), title)
        self.assertIn("/_nicegui/0.0.0/components/missing", detail)
        self.assertEqual(t("manager.page.reload"), reload)

    def test_a_failed_script_element_reloads_the_page(self) -> None:
        self.assertTrue(self.seen["script_element_reloaded"], self.seen)

    def test_the_reload_button_reloads_the_page_clear(self) -> None:
        self.assertIsNone(self.seen["message_after_reload_button"], self.seen)


if __name__ == "__main__":
    unittest.main()

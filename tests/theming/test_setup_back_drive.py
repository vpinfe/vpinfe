"""The browser's Back inside a step-by-step dialog, in a real browser: on a later step it is
the dialog's own Back and the dialog stays open, and on the first step it closes the dialog
the way Escape does. Setup and the support package stand for the dialogs that share the host.

Slow: boots a real instance and a real browser, with Setup unfinished.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk, answer_how_you_play

CARD = ".manager-wizard-card"
TITLE = f"{CARD} .manager-wizard-title"
FORWARD = f"{CARD} .manager-wizard-forward"
CONTINUE = ".manager-attention .manager-action"
CONTINUE_AT = ("(() => { const i = [...document.querySelectorAll("
               f"{json.dumps(CONTINUE)})].findIndex(el => el.innerText.includes("
               f"{json.dumps(t('word.continue'))})); return i >= 0 ? i + 1 : 0; }})()")
TITLE_NOW = f"((document.querySelector({json.dumps(TITLE)}) || {{}}).innerText || '').trim()"
CARDS = f"document.querySelectorAll({json.dumps(CARD)}).length"
SETTLE_S = 1.5
SUPPORT_LABEL = json.dumps(t("manager.about.support_package"))
CLICK_SUPPORT = ("(() => { const el = [...document.querySelectorAll('button, .q-btn, a')]"
                 f".find(el => el.innerText.includes({SUPPORT_LABEL}));"
                 " if (el) el.click(); return !!el; })()")


class SetupBackDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        if not binary:
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            with LiveInstance(Path(tmp), extra_settings={("device", "setup_pending"): True}
                              ) as instance:
                cls.seen = asyncio.run(cls._drive(instance, binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance, binary: str) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, instance)
            # A page address that names a place, so Setup does not open by itself and
            # Continue is what opens it.
            await walk.visit("/?view=overview")
            await browser.click(CONTINUE, nth=int(await browser.wait_for(CONTINUE_AT)) - 1)
            await cls._walk(browser, seen, "setup")

            await walk.visit("/?view=about")
            await browser.wait_for(CLICK_SUPPORT)
            await cls._walk(browser, seen, "support")
        return seen

    @staticmethod
    async def _walk(browser: BrowserSession, seen: dict, name: str) -> None:
        """Forward once, Back twice: the first Back is a step, the second closes."""
        await browser.wait_for(f"{CARDS} === 1 && {TITLE_NOW} !== ''", timeout=60)
        await asyncio.sleep(SETTLE_S)
        first = await browser.evaluate(TITLE_NOW)

        await answer_how_you_play(browser)
        await browser.click(FORWARD)
        await browser.wait_for(f"{TITLE_NOW} !== {json.dumps(first)}")
        await asyncio.sleep(SETTLE_S)
        seen[f"{name}_moved"] = await browser.evaluate(TITLE_NOW) != first

        await browser.evaluate("history.back()")
        await browser.wait_for(f"{TITLE_NOW} === {json.dumps(first)}")
        await asyncio.sleep(SETTLE_S)
        seen[f"{name}_after_back_cards"] = await browser.evaluate(CARDS)

        await browser.evaluate("history.back()")
        await browser.wait_for(f"{CARDS} === 0")
        seen[f"{name}_first_step_closed"] = True

    def test_back_on_a_later_step_is_one_step_back(self) -> None:
        for name in ("setup", "support"):
            with self.subTest(name):
                self.assertTrue(self.seen[f"{name}_moved"])
                self.assertEqual(self.seen[f"{name}_after_back_cards"], 1)

    def test_back_on_the_first_step_closes_the_dialog(self) -> None:
        for name in ("setup", "support"):
            with self.subTest(name):
                self.assertTrue(self.seen[f"{name}_first_step_closed"])


if __name__ == "__main__":
    unittest.main()

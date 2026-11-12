"""Setup on a first run, driven as a person would: a bare address opens it by itself,
Finish later leaves the line over the page, Continue reopens it, the frontend's own window
names where else to reach it, and Finish closes it onto the Manager's usual home with the
line gone and discovery no longer saying it is pending.

Slow: boots a real instance and a real browser. The instance is kept at its first run,
which the harness otherwise clears.
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
from tests.support.manager_walk import ManagerWalk

CARD = ".manager-wizard-card"
TITLE = f"{CARD} .manager-wizard-title"
ASIDE = f"{CARD} .manager-dialog-aside"
FORWARD = f"{CARD} .manager-wizard-forward"
CONTINUE = ".manager-action--inline"
# The card has finished its entrance: Quasar scales it in, and a click on the way lands
# somewhere else.
CARD_UP = ("[...document.querySelectorAll('.manager-wizard-card')].some(card =>"
           " getComputedStyle(card.closest('.q-dialog__inner')).transform === 'none'"
           " && card.getBoundingClientRect().width > 0)")
CARD_SHOWN = f"!!document.querySelector({json.dumps(CARD)})"
CARD_GONE = f"!document.querySelector({json.dumps(CARD)})"
TEXT_OF = "(() => { const el = document.querySelector(%s); return el ? el.innerText : null; })()"
LINE = ("[...document.querySelectorAll('.manager-help')]"
        f".some(el => el.innerText.trim() === {json.dumps(t('manager.setup.not_finished'))})")
CONTINUE_SHOWN = ("[...document.querySelectorAll('.manager-action--inline')]"
                  f".some(el => el.innerText.includes({json.dumps(t('word.continue'))}))")
# Up to the address, which is wherever this machine is reached.
FROM_ELSEWHERE = t("manager.setup.address_note", address="\0", action="").split("\0")[0].strip()


class SetupFirstRunDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        if not binary:
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh"
            fresh.mkdir()
            with LiveInstance(Path(tmp), extra_settings={("device", "setup_pending"): True}
                              ) as instance:
                cls.seen = asyncio.run(cls._drive(instance, binary, fresh))

    @classmethod
    async def _drive(cls, instance: LiveInstance, binary: str, fresh: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()

        def on_disk() -> object:
            saved = json.loads((instance.config_dir / "vpinfe.json").read_text(encoding="utf-8"))
            return saved["settings"]["device"]["setup_pending"]

        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, instance)

            async def click_text(selector: str, text: str) -> None:
                await browser.click(selector, nth=int(await browser.wait_for(
                    "(() => { const i = [...document.querySelectorAll("
                    f"{json.dumps(selector)})].findIndex(el => el.innerText.includes("
                    f"{json.dumps(text)})); return i >= 0 ? i + 1 : 0; }})()")) - 1)

            async def opened() -> None:
                await browser.wait_for(CARD_UP, timeout=60)
                await walk.drawn()

            async def choose_none_yet() -> None:
                radio = f"{CARD} .manager-wizard-pick"
                await click_text(radio, t("manager.setup.tables.none"))
                field = f"{CARD} input.q-field__native"
                shown = (f"[...document.querySelectorAll({json.dumps(field)})]"
                         ".filter(el => el.getBoundingClientRect().width > 0)")
                await browser.wait_for(f"{shown}.length > 0")
                nth = int(await browser.evaluate(
                    f"[...document.querySelectorAll({json.dumps(field)})]"
                    ".indexOf(" + shown + ".at(-1))"))
                await browser.click(field, nth=nth)
                await browser.send("Input.insertText", {"text": str(fresh)})
                await browser.wait_for(
                    f"!document.querySelector({json.dumps(FORWARD)}).disabled"
                    f" && !document.querySelector({json.dumps(FORWARD)}).classList"
                    ".contains('disabled')", timeout=30)

            async def choose_not_installed() -> None:
                # Whether the program is found depends on the machine; Next waits for an
                # answer where it is not, so the walk answers the same way everywhere.
                await click_text(f"{CARD} .manager-wizard-pick", t("manager.setup.vpx.none"))
                await browser.wait_for(
                    f"!document.querySelector({json.dumps(FORWARD)}).disabled")

            async def left_for_later() -> None:
                await walk.act(lambda: browser.click(ASIDE), until=CARD_GONE)

            await walk.visit("/")
            await opened()
            seen["arrival_title"] = (await browser.evaluate(TEXT_OF % json.dumps(TITLE))).strip()
            seen["arrival_card"] = await browser.evaluate(TEXT_OF % json.dumps(CARD))
            seen["arrival_line"] = await browser.evaluate(LINE)
            seen["pending_said"] = instance.api("/api/v1").get("setup_pending")

            await browser.evaluate("window.keptThroughLater = true")
            await left_for_later()
            seen["later_kept_page"] = await browser.evaluate("window.keptThroughLater === true")
            seen["later_line"] = await browser.evaluate(LINE)
            seen["later_continue"] = await browser.evaluate(CONTINUE_SHOWN)
            seen["later_pending"] = on_disk()

            await walk.act(lambda: click_text(CONTINUE, t("word.continue")), until=CARD_UP)
            await walk.drawn()
            seen["continue_title"] = (
                await browser.evaluate(TEXT_OF % json.dumps(TITLE))).strip()
            await left_for_later()

            await walk.visit("/?setup=window")
            await opened()
            seen["window_card"] = await browser.evaluate(TEXT_OF % json.dumps(CARD))
            await left_for_later()

            await walk.visit("/")
            await opened()

            async def forward_says() -> str:
                return (await browser.evaluate(
                    f"document.querySelector({json.dumps(FORWARD + ' .block')}).innerText")
                ).strip()

            # Each page but the last says Next, and the one it opens has another title.
            for _ in range(10):
                title = (await browser.evaluate(TEXT_OF % json.dumps(TITLE))).strip()
                if title == t("manager.setup.tables.title"):
                    await choose_none_yet()
                if title in (t("manager.setup.vpx.title_one"), t("manager.setup.vpx.title_many")):
                    await choose_not_installed()
                if await forward_says() == t("word.finish"):
                    break
                await walk.act(
                    lambda: browser.click(FORWARD),
                    mark=f"window.fromTitle = {json.dumps(title)}",
                    until=(f"(document.querySelector({json.dumps(TITLE)}) || {{innerText: ''}})"
                           ".innerText.trim() !== window.fromTitle"))
                await walk.drawn()
            seen["finish_says"] = await forward_says()
            # Finishing reloads the page onto the Manager's usual home, and the marker
            # goes with the old document.
            await walk.act(lambda: browser.click(FORWARD),
                           mark="window.beforeFinish = true",
                           until="window.beforeFinish !== true")
            seen["done_card"] = await browser.evaluate(CARD_SHOWN)
            seen["done_line"] = await browser.evaluate(LINE)
            seen["done_location"] = await browser.evaluate("window.location.pathname")
            seen["done_pending_said"] = "setup_pending" in instance.api("/api/v1")
            seen["done_pending"] = on_disk()
            seen["log"] = instance.output(tail=None)
        return seen

    def test_a_bare_address_opens_setup_on_its_welcome(self) -> None:
        self.assertEqual(self.seen["arrival_title"], t("manager.setup.welcome_title"))

    def test_the_plain_page_does_not_name_another_address(self) -> None:
        self.assertNotIn(FROM_ELSEWHERE, self.seen["arrival_card"])

    def test_the_line_is_over_the_page_while_setup_is_pending(self) -> None:
        self.assertTrue(self.seen["arrival_line"])

    def test_discovery_says_setup_is_pending(self) -> None:
        self.assertIs(self.seen["pending_said"], True)

    def test_finish_later_closes_setup_and_leaves_the_page_and_the_line(self) -> None:
        self.assertTrue(self.seen["later_kept_page"], "the page was reloaded")
        self.assertTrue(self.seen["later_line"])
        self.assertTrue(self.seen["later_continue"])
        self.assertIs(self.seen["later_pending"], True)

    def test_continue_reopens_setup(self) -> None:
        self.assertEqual(self.seen["continue_title"], t("manager.setup.welcome_title"))

    def test_the_frontends_window_names_where_else_setup_can_be_reached(self) -> None:
        self.assertIn(FROM_ELSEWHERE, self.seen["window_card"])

    def test_the_last_question_ends_on_finish(self) -> None:
        self.assertEqual(self.seen["finish_says"], t("word.finish"))

    def test_finish_closes_setup_onto_the_managers_home(self) -> None:
        self.assertFalse(self.seen["done_card"])
        self.assertFalse(self.seen["done_line"])
        self.assertEqual(self.seen["done_location"], "/manager")

    def test_finish_clears_the_mark_and_discovery_stops_saying_it(self) -> None:
        self.assertFalse(self.seen["done_pending_said"])
        self.assertIs(self.seen["done_pending"], False)

    def test_nothing_waiting_for_finish_failed_to_start(self) -> None:
        self.assertNotIn("did not start", self.seen["log"])


if __name__ == "__main__":
    unittest.main()

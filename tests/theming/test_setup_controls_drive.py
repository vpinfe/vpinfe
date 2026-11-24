"""Setup's Controls step, driven as a person would: the table sits in Setup's own dialog and
still hears what is pressed, a controller's buttons stay out of it until a controller is
heard, a clicked row takes the next press and that press is saved as it is made.

Slow: boots a real instance and a real browser. Chrome has no controller, so one is stood in
for on `navigator.getGamepads`.
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
CARD_UP = ("[...document.querySelectorAll('.manager-wizard-card')].some(card =>"
           " getComputedStyle(card.closest('.q-dialog__inner')).transform === 'none'"
           " && card.getBoundingClientRect().width > 0)")
TITLE_IS = ("((document.querySelector(%s) || {innerText: ''}).innerText.trim() === %s)")
ROW = ("[...document.querySelectorAll('.manager-binding-row')].find(row =>"
       " row.querySelector('.manager-binding-label').textContent.trim() === %s)")
LABELS = ("[...document.querySelectorAll('.manager-binding-row .manager-binding-label')]"
          ".map(el => el.textContent.trim())")
CHIPS = ("(row => [...row.querySelectorAll('.manager-binding-field .manager-member-chip')]"
         ".map(el => el.textContent.trim()))(" + ROW + ")")
LINE = ("(el => el ? el.textContent.trim().replace(/\\s+/g, ' ') : null)"
        "(document.querySelector('.manager-binding-live'))")
PAD = """(() => {
  window.__pad = {index: 0, mapping: 'standard', id: 'Stand-in',
                  buttons: Array.from({length: 17}, () => ({pressed: false}))};
  Object.defineProperty(navigator, 'getGamepads',
                        {configurable: true, value: () => [window.__pad]});
})()"""
SCROLL = "document.querySelector('.manager-wizard-scroll')"
MORE_HIDDEN = "document.querySelector('.manager-more').hidden"
# The page reads a controller every 40ms, so a button pressed has been seen by this.
PAD_READ_S = 0.3


def _held(button: int, down: bool) -> str:
    return f"window.__pad.buttons[{button}] = {{pressed: {json.dumps(down)}}}"


class SetupControlsDrive(unittest.TestCase):
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

        def stored(key: str) -> list[str]:
            value = instance.api("/api/v1/config/settings")["input"][key]
            return value.split(",") if isinstance(value, str) else list(value)

        async with BrowserSession(binary) as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1600, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)
            controls = t("manager.setup.controls.title")

            await walk.visit("/")
            await browser.wait_for(CARD_UP, timeout=60)
            await walk.drawn()
            await browser.evaluate(PAD)
            await answer_how_you_play(browser)
            for _ in range(6):
                title = (await browser.evaluate(
                    f"document.querySelector({json.dumps(TITLE)}).innerText")).strip()
                if title == controls:
                    break
                await walk.act(
                    lambda: browser.click(FORWARD),
                    mark=f"window.fromTitle = {json.dumps(title)}",
                    until=(f"(document.querySelector({json.dumps(TITLE)}) || {{innerText: ''}})"
                           ".innerText.trim() !== window.fromTitle"))
                await walk.drawn()
            await browser.wait_for(f"{LABELS}.length > 5")
            seen["title"] = title
            seen["labels"] = await browser.evaluate(LABELS)
            seen["select_before_pad"] = await browser.evaluate(CHIPS % json.dumps("Select"))
            seen["idle"] = await browser.evaluate(LINE)
            seen["more_at_top"] = await browser.evaluate(MORE_HIDDEN)
            seen["sideways"] = await browser.evaluate(
                f"({SCROLL}).scrollWidth - ({SCROLL}).clientWidth")
            await browser.evaluate(f"({SCROLL}).scrollTop = 1e6")
            await browser.wait_for(MORE_HIDDEN)
            await browser.evaluate(f"({SCROLL}).scrollTop = 0")
            await browser.wait_for(f"!{MORE_HIDDEN}")

            await browser.key_down("ArrowLeft", "ArrowLeft")
            await browser.wait_for(f"{LINE}.includes('Previous')")
            seen["heard_key"] = await browser.evaluate(LINE)
            seen["select_after_key"] = await browser.evaluate(CHIPS % json.dumps("Select"))
            await browser.key_up("ArrowLeft", "ArrowLeft")

            await browser.evaluate(_held(0, True))
            await browser.wait_for(f"{CHIPS % json.dumps('Select')}.length > 1")
            await browser.evaluate(_held(0, False))
            await asyncio.sleep(PAD_READ_S)
            seen["select_after_pad"] = await browser.evaluate(CHIPS % json.dumps("Select"))

            nth = await browser.evaluate(f"{LABELS}.indexOf('Tutorial')")
            await browser.click(".manager-binding-row .manager-binding-label", nth=nth)
            await browser.wait_for(
                "(window.__bindingListen || {}).row === 'tutorial'")
            await browser.press("z", "KeyZ")
            await browser.wait_for(f"{CHIPS % json.dumps('Tutorial')}.includes('Z')")
            seen["tutorial_chips"] = await browser.evaluate(CHIPS % json.dumps("Tutorial"))
            seen["tutorial_saved"] = stored("tutorial")

            await browser.evaluate(f"({ROW % json.dumps('Tutorial')}"
                                   ".querySelector('.manager-binding-label').focus())")
            await browser.press("Enter", "Enter")
            await browser.wait_for("(window.__bindingListen || {}).row === 'tutorial'")
            await browser.press("x", "KeyX")
            await browser.wait_for(f"{CHIPS % json.dumps('Tutorial')}.includes('X')")
            await browser.wait_for(
                f"document.activeElement === ({ROW % json.dumps('Tutorial')}"
                ".querySelector('.manager-binding-label'))")
            seen["tutorial_by_key"] = stored("tutorial")

            await browser.press("Enter", "Enter")
            await browser.wait_for("(window.__bindingListen || {}).row === 'tutorial'")
            await browser.press("Enter", "Enter")
            await browser.wait_for("!!document.querySelector('.q-dialog .manager-dialog-footer')")
            await asyncio.sleep(PAD_READ_S)
            seen["title_after_enter_bound"] = (await browser.evaluate(
                f"document.querySelector({json.dumps(TITLE)}).innerText")).strip()
        return seen

    def test_the_step_is_the_one_asked_for(self) -> None:
        self.assertEqual(t("manager.setup.controls.title"), self.seen["title"])

    def test_what_is_only_heard_during_play_is_not_asked(self) -> None:
        self.assertIn("Previous", self.seen["labels"])
        self.assertNotIn("Take Picture", self.seen["labels"])

    def test_a_key_pressed_in_the_dialog_names_itself(self) -> None:
        self.assertIn("Previous", self.seen["heard_key"])
        self.assertNotEqual(self.seen["idle"], self.seen["heard_key"])

    def test_no_controller_button_is_shown_until_a_controller_is_heard(self) -> None:
        self.assertEqual(["Enter"], self.seen["select_before_pad"])
        self.assertEqual(["Enter"], self.seen["select_after_key"])

    def test_the_first_controller_press_brings_the_buttons_in(self) -> None:
        self.assertGreater(len(self.seen["select_after_pad"]), 1)
        self.assertEqual("Enter", self.seen["select_after_pad"][0])

    def test_a_clicked_row_takes_the_next_press_and_it_is_saved_as_made(self) -> None:
        self.assertIn("Z", self.seen["tutorial_chips"])
        self.assertIn("key:KeyZ", self.seen["tutorial_saved"])

    def test_a_step_taller_than_the_dialog_says_there_is_more_below(self) -> None:
        self.assertFalse(self.seen["more_at_top"])
        self.assertEqual(0, self.seen["sideways"])

    def test_enter_on_a_label_asks_for_a_press_and_focus_stays_on_it(self) -> None:
        self.assertIn("key:KeyX", self.seen["tutorial_by_key"])

    def test_enter_taken_as_the_press_does_not_press_next(self) -> None:
        self.assertEqual(t("manager.setup.controls.title"), self.seen["title_after_enter_bound"])


if __name__ == "__main__":
    unittest.main()

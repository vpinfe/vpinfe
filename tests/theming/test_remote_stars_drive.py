"""A star tapped on the phone lights up to it at once, and the rating follows.

The phone is aimed at a second install, so the write is one the test can hold back: B is
stopped while the tap is made, which is a write with no answer yet, and then taken down,
which is a write that is refused. The stars are read while each is true.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk
from tests.support.saved import saved_record

TARGET = "Install B"
PLAYED = {"LastRun": "2026-09-01T10:00:00Z", "StartCount": 1}
STARS = ".manager-stars .manager-star"

# Which of the five carry the lit class, and what each is painted with.
LIT_NOW = (f"[...document.querySelectorAll({json.dumps(STARS)})]"
           ".map(el => el.classList.contains('manager-star--on'))")
OPACITY_NOW = (f"[...document.querySelectorAll({json.dumps(STARS)})]"
               ".map(el => getComputedStyle(el).opacity)")
SAID_NOW = "[...document.querySelectorAll('.q-notification')].map(n => n.innerText).join(' | ')"
SAVED_SHOWN = ("[...document.querySelectorAll('.manager-stars-saved')]"
               ".some(el => el.offsetParent !== null)")


def _call(instance: LiveInstance, method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        instance.manager_url(path), method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as handle:
        raw = handle.read()
    return json.loads(raw) if raw else None


def _rating(root: Path) -> int:
    info = saved_record(root / "Solo" / "Solo.info")
    return int((info.get("User") or {}).get("Rating") or 0)


class RemoteStarsDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as a_dir, TemporaryDirectory() as b_dir:
            a_root, b_root = Path(a_dir), Path(b_dir)
            write_game(a_root, "Other", info=game_info("Other", vps_id="", game_id="other-id"))
            write_game(b_root, "Solo",
                       info=game_info("Solo", vps_id="", game_id="solo-id", User=PLAYED))
            with LiveInstance(a_root) as a, LiveInstance(b_root) as b:
                a.wait_for_api()
                b.wait_for_api()
                cls.seen = asyncio.run(cls._drive(a, b, b_root))

    @classmethod
    async def _drive(cls, a: LiveInstance, b: LiveInstance, b_root: Path) -> dict:
        b_id = _call(b, "GET", "/api/v1")["install_id"]
        _call(a, "PUT", "/api/v1/devices",
              {"device_id": b_id, "kind": "vpinfe", "display_name": TARGET,
               "features": ["frontend"], "port": b.ports["manager"]})
        seen: dict = {}
        assert b.proc is not None

        async with BrowserSession(chromium_path() or "") as browser:
            await browser.as_phone()
            walk = ManagerWalk(browser, a)

            async def click_text(selector: str, text: str) -> None:
                at = await browser.wait_for(
                    f"(() => {{ const i = [...document.querySelectorAll({json.dumps(selector)})]"
                    f".findIndex(el => el.innerText.split('\\n').map(l => l.trim())"
                    f".includes({json.dumps(text)})); return i >= 0 ? i + 1 : 0; }})()")
                await browser.tap(selector, nth=int(at) - 1)

            await walk.visit("/remote?screen=games")
            seen["hover media"] = await browser.evaluate(
                "matchMedia('(hover: hover)').matches")
            await browser.tap(".remote-target")
            await click_text(".q-menu .q-item", TARGET)
            await browser.wait_for("document.body.innerText.includes('Solo')")
            await click_text(".remote-tab", t("word.now"))
            await browser.wait_for(f"document.querySelectorAll({json.dumps(STARS)}).length === 5")
            await walk.drawn()

            # No answer yet: B holds still, so the write cannot have been taken.
            os.kill(b.proc.pid, signal.SIGSTOP)
            try:
                await browser.tap(STARS, nth=1)
                await browser.wait_for(f"{LIT_NOW}.filter(Boolean).length === 2", timeout=5)
                seen["lit while waiting"] = await browser.evaluate(LIT_NOW)
                # The paint eases in over a moment, so read it once it has settled.
                await browser.wait_for(f"{OPACITY_NOW}.slice(0, 2).every(o => o === '1')")
                seen["opacity while waiting"] = await browser.evaluate(OPACITY_NOW)
                seen["saved while waiting"] = await browser.evaluate(SAVED_SHOWN)
            finally:
                os.kill(b.proc.pid, signal.SIGCONT)

            await browser.wait_for(SAVED_SHOWN)
            seen["saved after"] = True
            seen["rating after"] = _rating(b_root)
            seen["lit after"] = await browser.evaluate(LIT_NOW)

            # Two taps before the first is answered: the writes go in tap order, so the
            # install holds the last tap and the stars show it.
            os.kill(b.proc.pid, signal.SIGSTOP)
            try:
                await browser.tap(STARS, nth=4)
                await browser.tap(STARS, nth=2)
            finally:
                os.kill(b.proc.pid, signal.SIGCONT)
            await browser.wait_for(SAVED_SHOWN)
            seen["rating after two taps"] = _rating(b_root)
            seen["lit after two taps"] = await browser.evaluate(LIT_NOW)

            # Drawn again: the rating is what the phone now holds, not the old one.
            await click_text(".remote-tab", t("manager.remote.tab_games"))
            await click_text(".remote-tab", t("word.now"))
            await browser.wait_for(f"document.querySelectorAll({json.dumps(STARS)}).length === 5")
            seen["lit after redraw"] = await browser.evaluate(LIT_NOW)

            # Refused: B is gone, so the write fails.
            b.proc.kill()
            b.proc.wait(timeout=30)
            await browser.tap(STARS, nth=3)
            failed = json.dumps(t("manager.stars.save_failed"))
            await browser.wait_for(f"{SAID_NOW}.includes({failed})")
            seen["notified"] = True
            seen["lit after refusal"] = await browser.evaluate(LIT_NOW)
            seen["saved after refusal"] = await browser.evaluate(SAVED_SHOWN)
        return seen

    def test_the_tap_does_not_hover_on_a_phone(self) -> None:
        self.assertFalse(self.seen["hover media"])

    def test_stars_one_and_two_are_lit_before_the_answer_arrives(self) -> None:
        self.assertEqual(self.seen["lit while waiting"], [True, True, False, False, False])

    def test_a_star_that_is_not_lit_does_not_look_lit(self) -> None:
        opacity = [float(one) for one in self.seen["opacity while waiting"]]
        self.assertEqual(opacity[:2], [1.0, 1.0])
        self.assertTrue(all(one < 1.0 for one in opacity[2:]), opacity)

    def test_saved_waits_for_the_answer(self) -> None:
        self.assertFalse(self.seen["saved while waiting"])
        self.assertTrue(self.seen["saved after"])
        self.assertEqual(self.seen["rating after"], 2)
        self.assertEqual(self.seen["lit after"], [True, True, False, False, False])

    def test_two_quick_taps_leave_the_last_one_saved_and_lit(self) -> None:
        self.assertEqual(self.seen["rating after two taps"], 3)
        self.assertEqual(self.seen["lit after two taps"], [True, True, True, False, False])

    def test_stars_drawn_again_hold_the_saved_rating(self) -> None:
        self.assertEqual(self.seen["lit after redraw"], [True, True, True, False, False])

    def test_a_refused_write_puts_the_stars_back_and_says_so(self) -> None:
        self.assertTrue(self.seen["notified"])
        self.assertEqual(self.seen["lit after refusal"], [True, True, True, False, False])
        self.assertFalse(self.seen["saved after refusal"])


if __name__ == "__main__":
    unittest.main()

"""Attract mode in a real page, against a real backend.

The timer logic runs on a fake clock in tests/js/attract.test.js. What is driven here is
what only a page shows: core moving the wheel and tagging the move, a key press handing it
back, the theme's own file reaching core over the bridge, and a setting saved in the
Manager applying to a page that is already open.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100fd5c9a6b0000000049454e44ae426082")

GAMES = ("Alpha Table", "Beta Table", "Gamma Table", "Delta Table")

# Written to the ini directly, below the range the Manager offers, so a test waits seconds
# and not a minute.
FAST = {("behavior", "attract_idle"): 1, ("behavior", "attract_step_time"): 1,
        ("behavior", "attract_order"): "in_order"}

# A check that the wheel stayed still waits as long as attract could have started and
# stepped twice: FAST's idle time plus two steps, with a second to spare.
QUIET_WINDOW_S = 4
# A press that was not swallowed acts on the very next frame.
PRESS_ACTS_WITHIN_S = 0.5

# Wraps moveTo before attract can call it, so the page keeps a record of who moved it.
RECORD = """
(() => {
  window.__moves = [];
  const was = vpin.moveTo.bind(vpin);
  vpin.moveTo = (index, options = {}) => {
    window.__moves.push({index, source: options.source || "user", reason: options.reason || ""});
    return was(index, options);
  };
})()
"""

SELECTED = "Number(document.body.dataset.selected)"
ATTRACTING = "window.vpin.attractActive"


def _info(name: str) -> dict:
    return {"Info": {"Name": name}, "VPinFE": {}, "User": {}}


def _write_library(root: Path) -> None:
    for name in GAMES:
        write_game(root, name, info=_info(name), medias={"wheel.png": PNG, "table.png": PNG})


def _save(instance: LiveInstance, **changes) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{instance.ports['manager']}/api/v1/config",
        data=json.dumps({"settings": {"behavior": changes}}).encode(), method="PUT",
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=10).close()


async def _open(instance: LiveInstance, browser: BrowserSession) -> None:
    await browser.navigate(instance.theme_url("playfield"))
    await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
    await browser.evaluate(RECORD)


def _theme_dir(instance: LiveInstance) -> Path:
    return instance.config_dir / "frontend_themes" / instance.theme


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class AttractDriveTests(unittest.TestCase):
    """The theme's config.json is read when a page loads, and each test loads its own, so
    removing it before each test is all the isolation it needs."""

    shared: LiveInstance

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        _write_library(Path(tmp.name))
        cls.shared = cls.enterClassContext(LiveInstance(Path(tmp.name), extra_settings=FAST))

    def setUp(self) -> None:
        super().setUp()
        (_theme_dir(self.shared) / "config.json").unlink(missing_ok=True)

    def _drive(self, scenario, *, prepare=None):
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await _open(instance, browser)
                return await scenario(browser)

        if prepare:
            prepare(_theme_dir(self.shared))
        return asyncio.run(run(self.shared))

    def test_an_idle_wheel_moves_and_says_it_was_attract(self) -> None:
        async def scenario(browser):
            await browser.wait_for(ATTRACTING, timeout=30.0)
            await browser.wait_for("window.__moves.length >= 2", timeout=30.0)
            return await browser.evaluate("window.__moves")

        moves = self._drive(scenario)
        self.assertTrue(all(move["source"] == "attract" for move in moves), moves)

    def test_the_next_press_is_an_ordinary_one(self) -> None:
        async def scenario(browser):
            await browser.wait_for("window.__moves.length >= 2", timeout=30.0)
            await browser.press("ArrowRight", "ArrowRight")
            await browser.wait_for(f"!{ATTRACTING} && {SELECTED} === 0", timeout=10.0)
            await browser.press("ArrowRight", "ArrowRight")
            return await browser.wait_for(
                "window.__moves.filter(m => m.source === 'user' && m.reason !== 'restore')"
                ".length === 1 && 'moved'", timeout=10.0)

        self.assertEqual(self._drive(scenario), "moved")

    def test_a_theme_can_turn_it_off_for_itself(self) -> None:
        def opt_out(theme):
            (theme / "config.json").write_text(json.dumps({"attract": {"enabled": False}}))

        async def scenario(browser):
            await asyncio.sleep(QUIET_WINDOW_S)
            return await browser.evaluate("window.__moves.length")

        self.assertEqual(self._drive(scenario, prepare=opt_out), 0)


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class AttractChangedWhileOpenTests(TempTree):
    BOOTS_PER_TEST = ("It raises the step time through the Manager's API, which has no way "
                      "to lower it again for the next test.")

    def test_a_change_saved_while_the_page_is_open_applies_without_reopening_it(self) -> None:
        _write_library(self.root)

        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await _open(instance, browser)
                await asyncio.sleep(QUIET_WINDOW_S)
                still = await browser.evaluate("window.__moves.length")
                _save(instance, attract_enabled=True)
                await browser.wait_for(ATTRACTING, timeout=30.0)
                _save(instance, attract_step_time=2)
                await asyncio.sleep(PRESS_ACTS_WITHIN_S)
                kept = await browser.evaluate(ATTRACTING)
                _save(instance, attract_enabled=False)
                await browser.wait_for(f"!{ATTRACTING}", timeout=30.0)
                await browser.wait_for(f"{SELECTED} === 0", timeout=10.0)
                return still, kept

        with LiveInstance(self.root, extra_settings={
                **FAST, ("behavior", "attract_enabled"): False}) as instance:
            instance.wait_for_api()
            still, kept = asyncio.run(run(instance))
        self.assertEqual(still, 0)
        self.assertTrue(kept, "a new step time does not end the run in progress")


if __name__ == "__main__":
    unittest.main()

"""A window that opens after the wheel has moved comes up on the game the wheel is on.

Driven with two real browsers against one backend, because the thing under test is the
conversation between the controller window and a window that was not there when the
wheel moved.
"""

from __future__ import annotations

import asyncio
import unittest

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100fd5c9a6b0000000049454e44ae426082")

GAMES = ("Alpha Table", "Beta Table", "Gamma Table")

# How long the playfield is watched after the backglass has drawn: the controller's answer
# is one websocket hop, so a stray copy of it would have arrived well inside a second.
ANSWER_WITHIN_S = 1.0

DRAWN = "(document.querySelector('[data-vpin-media] img')?.alt || '')"


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class LateWindowDriveTests(TempTree):
    BOOTS_PER_TEST = "Each test opens the second window at a different moment."

    def setUp(self) -> None:
        super().setUp()
        for name in GAMES:
            write_game(self.root, name,
                       info={"Info": {"Name": name}, "VPinFE": {}, "User": {}},
                       medias={"wheel.png": PNG, "table.png": PNG, "bg.png": PNG})

    @staticmethod
    def _backglass_url(instance: LiveInstance) -> str:
        return instance.url(f"/core/index_backglass.html?window=backglass"
                            f"&wsPort={instance.ports['ws']}"
                            f"&themeAssetsPort={instance.ports['manager']}")

    def test_a_backglass_opened_after_the_wheel_moved_shows_that_game(self) -> None:
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as playfield:
                await playfield.navigate(instance.theme_url("playfield"))
                await playfield.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                await playfield.press("ArrowRight", "ArrowRight")
                await playfield.press("ArrowRight", "ArrowRight")
                await playfield.wait_for("Number(document.body.dataset.selected) === 2")
                async with BrowserSession(chromium_path() or "") as backglass:
                    await backglass.navigate(self._backglass_url(instance))
                    return await backglass.wait_for(f"{DRAWN} === 'Gamma Table' && 'drawn'",
                                                    timeout=30.0)

        with LiveInstance(self.root) as instance:
            self.assertEqual(asyncio.run(run(instance)), "drawn")

    def test_a_window_that_was_already_open_is_not_told_the_move_twice(self) -> None:
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as playfield:
                await playfield.navigate(instance.theme_url("playfield"))
                await playfield.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                await playfield.evaluate("""
                    (() => {
                      window.__arrivals = [];
                      vpin.registerEventHandler("TableIndexUpdate",
                        (message) => window.__arrivals.push(message.reason));
                    })()""")
                async with BrowserSession(chromium_path() or "") as backglass:
                    await backglass.navigate(self._backglass_url(instance))
                    await backglass.wait_for(f"{DRAWN} === 'Alpha Table' && 'drawn'",
                                             timeout=30.0)
                    await asyncio.sleep(ANSWER_WITHIN_S)
                return await playfield.evaluate("window.__arrivals")

        with LiveInstance(self.root) as instance:
            self.assertEqual(asyncio.run(run(instance)), [])


if __name__ == "__main__":
    unittest.main()

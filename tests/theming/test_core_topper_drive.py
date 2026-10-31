"""The topper window core draws when the theme ships no page for it.

Opened in a real browser against a real backend, because what matters is the wiring: the
page core serves, the media the selected game carries, and the window knowing it is the
topper.
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

TOPPER = "document.querySelector('[data-vpin-media] img')?.src || ''"


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class CoreTopperDriveTests(TempTree):
    BOOTS_PER_TEST = "Each test reads what the one window drew."

    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, "Alpha Table",
                   info={"Info": {"Name": "Alpha Table"}, "VPinFE": {}, "User": {}},
                   medias={"wheel.png": PNG, "table.png": PNG, "topper.png": PNG})

    @staticmethod
    def _url(instance: LiveInstance) -> str:
        return instance.url(f"/core/index_topper.html?window=topper"
                            f"&wsPort={instance.ports['ws']}"
                            f"&themeAssetsPort={instance.ports['manager']}")

    @staticmethod
    async def _open_controller(playfield: BrowserSession, instance: LiveInstance) -> None:
        await playfield.navigate(instance.theme_url("playfield"))
        await playfield.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)

    def test_the_selected_game_s_topper_is_drawn(self) -> None:
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as playfield:
                await self._open_controller(playfield, instance)
                async with BrowserSession(chromium_path() or "") as browser:
                    await browser.navigate(self._url(instance))
                    src = await browser.wait_for(TOPPER, timeout=90.0)
                    return src, list(browser.failed_requests)

        with LiveInstance(self.root, windows=("playfield", "topper")) as instance:
            src, failed = asyncio.run(run(instance))
        self.assertIn("topper", src)
        self.assertEqual(failed, [])

    def test_the_window_knows_it_is_the_topper(self) -> None:
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as playfield:
                await self._open_controller(playfield, instance)
                async with BrowserSession(chromium_path() or "") as browser:
                    await browser.navigate(self._url(instance))
                    await browser.wait_for(TOPPER, timeout=90.0)
                    return await browser.evaluate("window.vpin.windowMediaKind")

        with LiveInstance(self.root, windows=("playfield", "topper")) as instance:
            self.assertEqual(asyncio.run(run(instance)), "topper")


if __name__ == "__main__":
    unittest.main()

"""Protects: (d) a launch shows the table's own picture while it starts, and is never left covered.

Driven in a real browser: the playfield page has to take the screen from the launch message
the backend sends, show the table's picture, and give the screen back when the table runs.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import write_game
from tests.support.live_instance import LiveInstance

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100fd5c9a6b0000000049454e44ae426082")

FIRST_TABLE = "vpin.tableData[0] && vpin._reader.identity(vpin.tableData[0])"
SCREEN = "document.querySelectorAll('.vpinfe-loading-screen')"
PLAYFIELD = {"from": "table", "media": "playfield", "kind": "image"}


def _info(name: str) -> dict:
    return {"Info": {"Name": name}, "VPinFE": {}, "User": {}}


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class LoadingScreenDriveTests(unittest.TestCase):
    instance: LiveInstance

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        write_game(Path(tmp.name), "Alpha Table", info=_info("Alpha Table"),
                   medias={"wheel.png": PNG, "table.png": PNG,
                           "bg.png": PNG, "dmd.png": PNG})
        cls.instance = cls.enterClassContext(LiveInstance(Path(tmp.name)))

    def _counts(self, settings: dict | None) -> tuple[int, int, str]:
        """Screens shown after a launch message, after the table runs, and what it shows."""
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.navigate(instance.theme_url("playfield"))
                await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                table = await browser.evaluate(f"JSON.stringify({FIRST_TABLE})")
                message = {"type": "TableLaunching", "table_id": json.loads(table)}
                if settings is not None:
                    message["loading_screen"] = settings
                await browser.evaluate(f"vpin.handleEvent({json.dumps(message)})")
                shown = await browser.evaluate(f"{SCREEN}.length")
                what = await browser.evaluate(
                    f"{SCREEN}[0]?.firstElementChild?.tagName || ''")
                await browser.evaluate("vpin.handleEvent({type: 'TableRunning'})")
                after = await browser.evaluate(f"{SCREEN}.length")
                return shown, after, what

        return asyncio.run(run(self.instance))

    def test_a_table_with_a_picture_shows_it_and_it_comes_down_when_the_table_runs(self) -> None:
        shown, after, what = self._counts({"mode": "image", "repeat": False, "audio": [],
                                           "rungs": [PLAYFIELD]})

        self.assertEqual((shown, after, what), (1, 0, "IMG"))

    def test_off_shows_no_screen(self) -> None:
        shown, _, _ = self._counts({"mode": "off", "repeat": False, "audio": [], "rungs": []})

        self.assertEqual(shown, 0)

    def test_a_launch_message_without_the_choice_shows_no_screen(self) -> None:
        shown, _, _ = self._counts(None)

        self.assertEqual(shown, 0)


if __name__ == "__main__":
    unittest.main()

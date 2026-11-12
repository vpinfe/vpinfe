"""Protects: (a) a picture already in a game folder is not copied into a game's media by a
click in the picker, only by the verb at its foot.

A click on a file in This Device, which opens in the game folder, shows it as New, arms the
verb and writes nothing. Cancel leaves the media as it was. Choosing again and pressing the
verb copies the file in and closes the dialog.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from common.games import media_placement
from tests.support import drops
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

GAME = "Medieval Madness"
LOOSE = "party.png"
CARD = ".manager-sources-card"
ROWS = f"{CARD} .manager-source-row--pick"
FOOTER = f"{CARD} .manager-dialog-footer .q-btn"
NEW = f"{CARD} .manager-pair-art"
VERB = f"[...document.querySelectorAll('{FOOTER}')].pop()"
OPEN = f"!!document.querySelector('{CARD}')"
SHAPE = (200, 100)
NOTHING_WRITTEN_WITHIN = 1.0
ASK = ("[...document.querySelectorAll('.q-btn')]"
       ".find(b => ['Add…', 'Replace…'].includes(b.innerText.split('\\n').pop().trim()))")


class SourcePickerDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            folder = write_game(library, GAME, info=game_info(GAME, vps_id="", game_id="mm"))
            (folder / LOOSE).write_bytes(drops.png_bytes(*SHAPE))
            with LiveInstance(library) as instance:
                cls.seen = asyncio.run(cls._drive(instance, folder))

    @classmethod
    async def _drive(cls, instance: LiveInstance, folder: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        written = folder / "medias" / media_placement.target_name("wheel", GAME, ".png")

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def open_picker() -> None:
                await walk.visit("/manager?view=games&game=mm&section=media&slot=wheel")
                await browser.wait_for(f"!!({ASK})")
                await browser.evaluate(f"({ASK}).click()")
                await browser.wait_for(OPEN)

            async def pick_loose() -> None:
                await browser.wait_for(
                    f"[...document.querySelectorAll('{ROWS}')]"
                    f".some(r => r.innerText.trim() === {json.dumps(LOOSE)})")
                await browser.evaluate(
                    f"[...document.querySelectorAll('{ROWS}')]"
                    f".find(r => r.innerText.trim() === {json.dumps(LOOSE)}).click()")
                await browser.wait_for(
                    f"(b => !!b && !b.disabled && !b.classList.contains('disabled'))({VERB})")

            await open_picker()
            seen["verb_before"] = await browser.evaluate(f"({VERB}).className")
            await pick_loose()
            seen["new"] = await browser.evaluate(
                f"[...document.querySelectorAll('{NEW}')].pop().innerText")
            await asyncio.sleep(NOTHING_WRITTEN_WITHIN)
            seen["written_on_click"] = written.exists()

            await browser.evaluate(f"[...document.querySelectorAll('{FOOTER}')][0].click()")
            await browser.wait_for(f"!{OPEN}")
            await asyncio.sleep(NOTHING_WRITTEN_WITHIN)
            seen["written_on_cancel"] = written.exists()

            await open_picker()
            await pick_loose()
            await browser.evaluate(f"{VERB}.click()")
            await browser.wait_for(f"!{OPEN}")
            deadline = time.monotonic() + 15
            while not written.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            seen["written"] = written.exists()
            if written.exists():
                with Image.open(io.BytesIO(written.read_bytes())) as image:
                    seen["size"] = image.size
        return seen

    def test_a_click_arms_the_verb_and_writes_nothing(self) -> None:
        self.assertIn("disabled", self.seen["verb_before"])
        self.assertIn(media_placement.target_name("wheel", GAME, ".png"), self.seen["new"])
        self.assertFalse(self.seen["written_on_click"])

    def test_cancel_leaves_the_media_as_it_was(self) -> None:
        self.assertFalse(self.seen["written_on_cancel"])

    def test_the_verb_copies_the_file_in(self) -> None:
        self.assertTrue(self.seen["written"])
        self.assertEqual(self.seen["size"], SHAPE)


if __name__ == "__main__":
    unittest.main()

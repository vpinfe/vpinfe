"""An unnamed picture dropped on a game is placed by its shape, with a Suggested chip beside
its place: a see-through picture reads as a wheel and is ticked, a square one is asked about,
choosing another place takes the chip away, and the place chosen last is the one written.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from common.games import media_placement
from common.i18n import t
from tests.support import drops
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

GAME = "Medieval Madness"
API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")
CELL = ("(() => { const c = document.querySelector("
        "'.ag-row[row-id=%s] .ag-cell[col-id=\"%s\"]'); if (!c) return null;"
        " const r = c.getBoundingClientRect();"
        " return r.height ? [r.left + r.width / 2, r.top + r.height / 2] : null; })()")
CARD = ".manager-import-card"
SHEET = f"{CARD} .manager-import-rows"
OPEN = f"!!document.querySelector('{CARD}')"
SELECTS = f"[...document.querySelectorAll('{SHEET} .q-select')]"
CHIPS = (f"[...document.querySelectorAll('{SHEET} .manager-member-chip')]"
         f".filter(e => e.innerText.trim() === {json.dumps(t('manager.drop_sheet.shape_guess'))})"
         ".length")
GO = (f"(() => {{ const b = [...document.querySelectorAll('{CARD} .q-btn')].pop();"
      " return {text: b.innerText.trim(),"
      " off: b.disabled || b.classList.contains('disabled')}; })()")
READS = f"{SELECTS}.map(e => e.innerText.split('\\n')[0].trim())"


class ShapeSuggestionDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            folder = write_game(library, GAME, info=game_info(GAME, vps_id="",
                                                              game_id="mm"))
            pictures = Path(tmp) / "pictures.zip"
            with zipfile.ZipFile(pictures, "w") as archive:
                archive.writestr("IMG_0001.png", drops.png_bytes(700, 331, alpha=True))
                archive.writestr("IMG_0002.png", drops.png_bytes(800, 800))
            with LiveInstance(library) as instance:
                cls.seen = asyncio.run(cls._drive(instance, folder, pictures))

    @classmethod
    async def _drive(cls, instance: LiveInstance, folder: Path, pictures: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        written = folder / "medias" / media_placement.target_name("wheel", GAME, ".png")

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)
            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            x, y = await browser.wait_for(CELL % (json.dumps("mm"), "name"))
            data = {"items": [], "files": [str(pictures)], "dragOperationsMask": 1}
            for kind in ("dragEnter", "dragOver", "drop"):
                await browser.send("Input.dispatchDragEvent",
                                   {"type": kind, "x": x, "y": y, "data": data})
                await asyncio.sleep(0.15)
            await browser.wait_for(OPEN)
            await browser.wait_for(f"{CHIPS} === 1")
            await browser.wait_for(f"({GO}).off === false")
            seen["reads"] = await browser.evaluate(READS)
            seen["chips"] = await browser.evaluate(CHIPS)
            seen["go"] = await browser.evaluate(GO)

            await browser.click(f"{SHEET} .q-select", nth=0)
            await browser.wait_for("!!document.querySelector('.q-menu .q-item')")
            await browser.evaluate(
                "[...document.querySelectorAll('.q-menu .q-item')]"
                ".find(i => i.innerText.trim() === 'Topper').click()")
            await browser.wait_for(f"{CHIPS} === 0")
            seen["reads_after"] = await browser.evaluate(READS)
            seen["chips_after"] = await browser.evaluate(CHIPS)

            await browser.click(f"{SHEET} .q-select", nth=0)
            await browser.wait_for("!!document.querySelector('.q-menu .q-item')")
            await browser.evaluate(
                "[...document.querySelectorAll('.q-menu .q-item')]"
                ".find(i => i.innerText.trim() === 'Wheel').click()")
            await walk.listen()
            await browser.wait_for(f"({GO}).off === false")
            await browser.evaluate(
                "[...document.querySelectorAll('" + CARD + " .q-btn')].pop().click()")
            deadline = time.monotonic() + 15
            while not written.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            seen["written"] = written.exists()
            seen["tree"] = sorted(one.relative_to(folder).as_posix() for one in folder.rglob("*"))
        return seen

    def test_the_see_through_picture_is_placed_as_a_wheel_with_a_chip_beside_it(self) -> None:
        self.assertEqual(1, self.seen["chips"])
        self.assertEqual("Wheel", self.seen["reads"][0])

    def test_the_square_picture_is_asked_about_and_the_sheet_is_ready_to_import(self) -> None:
        self.assertEqual(t("manager.drop_sheet.unplaced"), self.seen["reads"][1])
        self.assertFalse(self.seen["go"]["off"], self.seen["go"])

    def test_choosing_another_place_takes_the_chip_away(self) -> None:
        self.assertEqual(0, self.seen["chips_after"])
        self.assertEqual("Topper", self.seen["reads_after"][0])

    def test_the_place_chosen_last_is_the_one_written(self) -> None:
        self.assertTrue(self.seen["written"], self.seen["tree"])


if __name__ == "__main__":
    unittest.main()

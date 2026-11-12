"""The sheet a drop opens: a zip of wheel variants dropped on a wheel cell shows each one with
its picture and a radio, holds the button until one is picked and imports the picked file,
and a mixed zip dropped on a game asks where an unnamed picture goes and offers a text file to
keep in the game folder as it is. A table chosen from the Add dialog on Games opens the sheet
for a new game, the one way to make a game from a mobile device.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

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
RADIOS = f"document.querySelectorAll('{SHEET} .q-radio').length"
PICTURES = f"document.querySelectorAll('{SHEET} img').length"
BUTTONS = f"[...document.querySelectorAll('{CARD} .q-btn')]"
GO = (f"(() => {{ const b = {BUTTONS}.pop(); return {{text: b.innerText.trim(),"
      " off: b.disabled || b.classList.contains('disabled')}; })()")
CARD_TEXT = f"document.querySelector('{CARD}').innerText"
KEPT = t("manager.drop_sheet.other_saved")
SOURCES = ".manager-sources-card"
ADDED = "Brand New Table"


class DropSheetDrive(unittest.TestCase):
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
            wheels = drops.wheel_zip(Path(tmp) / "wheels", 3)
            mixed = Path(tmp) / "mixed.zip"
            with zipfile.ZipFile(mixed, "w") as archive:
                archive.writestr("(Wheel) Medieval Madness.png", drops.png_bytes(400, 200))
                archive.writestr("IMG_0001.png", drops.png_bytes(800, 800))
                archive.writestr("notes.txt", b"x" * 16)
            added = Path(tmp) / f"{ADDED}.vpx"
            added.write_bytes(b"not really a vpx")
            with LiveInstance(library) as instance:
                cls.seen = asyncio.run(cls._drive(instance, folder, wheels, mixed, added))

    @classmethod
    async def _drive(cls, instance: LiveInstance, folder: Path, wheels: Path,
                     mixed: Path, added: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        written = folder / "medias" / media_placement.target_name("wheel", GAME, ".png")

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def let_go(source: Path, column: str) -> None:
                x, y = await browser.wait_for(CELL % (json.dumps("mm"), column))
                data = {"items": [], "files": [str(source)], "dragOperationsMask": 1}
                for kind in ("dragEnter", "dragOver", "drop"):
                    await browser.send("Input.dispatchDragEvent",
                                       {"type": kind, "x": x, "y": y, "data": data})
                    await asyncio.sleep(0.15)
                await browser.wait_for(OPEN)

            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            await browser.evaluate(API + ".setColumnsVisible(['media_wheel'], true)")
            await browser.evaluate(API + ".ensureColumnVisible('media_wheel')")

            await let_go(wheels, "media_wheel")
            await browser.wait_for(f"{RADIOS} >= 4")
            seen["radios"] = await browser.evaluate(RADIOS)
            seen["pictures"] = await browser.evaluate(PICTURES)
            seen["card"] = await browser.evaluate(CARD_TEXT)
            seen["go_before"] = await browser.evaluate(GO)
            await browser.click(f"{SHEET} .manager-import-row:nth-of-type(2)")
            await browser.wait_for(f"({GO}).off === false")
            seen["go_after"] = await browser.evaluate(GO)
            seen["card_after"] = await browser.evaluate(CARD_TEXT)
            await browser.evaluate(f"{BUTTONS}.pop().click()")
            deadline = time.monotonic() + 15
            while not written.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            seen["written"] = written.exists()
            if written.exists():
                with Image.open(io.BytesIO(written.read_bytes())) as image:
                    seen["written_size"] = image.size

            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            await let_go(mixed, "name")
            await browser.wait_for(f"{CARD_TEXT}.includes({json.dumps(KEPT)})")
            seen["mixed"] = await browser.evaluate(CARD_TEXT)
            seen["mixed_go"] = await browser.evaluate(GO)

            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            await browser.evaluate(
                "(() => { const click = HTMLInputElement.prototype.click;"
                " HTMLInputElement.prototype.click = function () {"
                " if (this.type === 'file') { window.__asked = this; return; }"
                " return click.call(this); }; })()")
            await browser.evaluate(
                "[...document.querySelectorAll('.manager-grid-bar .q-btn')]"
                ".find(b => b.innerText.trim() === 'add').click()")
            await browser.wait_for(f"!!document.querySelector('{SOURCES} .manager-split .q-btn')")
            await browser.evaluate(f"document.querySelector('{SOURCES} .manager-split .q-btn')"
                                   ".click()")
            await browser.wait_for("!!window.__asked")
            document = await browser.send("Runtime.evaluate", {"expression": "window.__asked"})
            await browser.send("DOM.setFileInputFiles", {
                "objectId": document["result"]["objectId"], "files": [str(added)]})
            verb = f"[...document.querySelectorAll('{SOURCES} .q-btn')].pop()"
            await browser.wait_for(
                f"(b => !!b && !b.disabled && !b.classList.contains('disabled'))({verb})")
            await browser.evaluate(f"{verb}.click()")
            await browser.wait_for(OPEN)
            seen["added_card"] = await browser.evaluate(CARD_TEXT)

        return seen

    def test_each_variant_has_a_radio_and_a_picture_with_none_of_these(self) -> None:
        self.assertEqual(self.seen["radios"], 4)
        self.assertGreaterEqual(self.seen["pictures"], 3)
        self.assertIn(t("manager.drop_sheet.pick_none"), self.seen["card"])

    def test_the_button_names_what_is_unanswered_until_a_variant_is_picked(self) -> None:
        self.assertTrue(self.seen["go_before"]["off"])
        self.assertTrue(self.seen["go_before"]["text"].lower().endswith("wheel"),
                        self.seen["go_before"])
        self.assertFalse(self.seen["go_after"]["off"])
        self.assertIn(t("manager.import_dialog.import").lower(),
                      self.seen["go_after"]["text"].lower())

    def test_the_picked_variant_is_the_one_written(self) -> None:
        self.assertTrue(self.seen["written"])
        self.assertEqual(self.seen["written_size"][0] / self.seen["written_size"][1], 2)
        self.assertEqual(self.seen["written_size"][0], 200)

    def test_an_unplaced_file_is_asked_about_and_a_text_file_is_offered_to_keep(self) -> None:
        text = self.seen["mixed"]
        self.assertIn(t("manager.drop_sheet.unplaced"), text)
        self.assertIn(KEPT, text)

    def test_the_add_on_games_turns_a_chosen_table_into_a_new_game_sheet(self) -> None:
        self.assertIn(ADDED, self.seen["added_card"])


if __name__ == "__main__":
    unittest.main()

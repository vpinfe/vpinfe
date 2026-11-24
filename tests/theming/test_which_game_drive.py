"""A drop on the library that brought only pictures asks which game they are for: the game the
file names is already chosen and goes on to the sheet, one that names no game waits for a pick,
and Cancel lets the pictures go with nothing written.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
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

GAMES = ("Medieval Madness", "Taxi")
# How long a sheet that was not going to open is given to open anyway after Cancel.
NO_SHEET_WINDOW_S = 1.0
ASK = ".manager-dialog"
TITLE = f"{ASK} .manager-dialog-title"
FIELD = f"{ASK} input"
BUTTONS = f"[...document.querySelectorAll('{ASK} .q-btn')]"
GO = (f"(() => {{ const b = {BUTTONS}.pop(); return {{text: b.innerText.trim(),"
      " off: b.disabled || b.classList.contains('disabled')}; })()")
ASKING = f"!!document.querySelector('{TITLE}')"
SHEET = "!!document.querySelector('.manager-import-card')"
FIELD_VALUE = f"document.querySelector('{FIELD}').value"
OPTIONS = "[...document.querySelectorAll('.q-menu .q-item')].map(e => e.innerText.trim())"


class WhichGameDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            folders = {title: write_game(library, title,
                                         info=game_info(title, vps_id="", game_id=title.lower()))
                       for title in GAMES}
            named = Path(tmp) / "named.zip"
            unnamed = Path(tmp) / "unnamed.zip"
            for archive, name in ((named, "(Wheel) Medieval Madness.png"),
                                  (unnamed, "(Wheel) Mystery.png")):
                with zipfile.ZipFile(archive, "w") as made:
                    made.writestr(name, drops.png_bytes(200, 100))
            with LiveInstance(library) as instance:
                cls.seen = asyncio.run(cls._drive(instance, folders, named, unnamed))

    @classmethod
    async def _drive(cls, instance: LiveInstance, folders: dict[str, Path], named: Path,
                     unnamed: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        written = folders["Medieval Madness"] / "medias" / media_placement.target_name(
            "wheel", "Medieval Madness", ".png")
        taxi = folders["Taxi"] / "medias"

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def let_go(source: Path) -> None:
                data = {"items": [], "files": [str(source)], "dragOperationsMask": 1}
                for kind in ("dragEnter", "dragOver", "drop"):
                    await browser.send("Input.dispatchDragEvent",
                                       {"type": kind, "x": 600, "y": 400, "data": data})
                    await asyncio.sleep(0.15)
                await browser.wait_for(ASKING)

            await walk.visit("/manager?view=overview")
            await let_go(unnamed)
            seen["title"] = await browser.evaluate(f"document.querySelector('{TITLE}').innerText")
            seen["unnamed_go"] = await browser.evaluate(GO)
            await browser.click(FIELD)
            await browser.wait_for(f"{OPTIONS}.length === 2")
            seen["order"] = await browser.evaluate(OPTIONS)
            await browser.send("Input.insertText", {"text": "Tax"})
            await browser.wait_for(f"{OPTIONS}.length === 1")
            await browser.click(".q-menu .q-item")
            await browser.wait_for(f"({GO}).off === false")
            seen["picked"] = await browser.evaluate(FIELD_VALUE)
            await browser.evaluate(f"{BUTTONS}[0].click()")
            await browser.wait_for(f"!{ASKING}")
            await asyncio.sleep(NO_SHEET_WINDOW_S)
            seen["sheet_after_cancel"] = await browser.evaluate(SHEET)
            seen["taxi_files"] = sorted(one.name for one in taxi.glob("*")) if taxi.exists() else []

            await walk.visit("/manager?view=overview")
            await let_go(named)
            await browser.wait_for(f"{FIELD_VALUE} !== ''")
            seen["named_field"] = await browser.evaluate(FIELD_VALUE)
            seen["named_go"] = await browser.evaluate(GO)
            await browser.evaluate(f"{BUTTONS}.pop().click()")
            await browser.wait_for(SHEET)
            seen["sheet_card"] = await browser.evaluate(
                "document.querySelector('.manager-import-card').innerText")
            await browser.evaluate(
                "[...document.querySelectorAll('.manager-import-card .q-btn')].pop().click()")
            deadline = time.monotonic() + 15
            while not written.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            seen["written"] = written.exists()

        return seen

    def test_the_question_is_asked_with_the_continue_held_until_a_game_is_chosen(self) -> None:
        self.assertEqual(self.seen["title"], t("manager.which_game.title"))
        self.assertTrue(self.seen["unnamed_go"]["off"])
        self.assertTrue(self.seen["unnamed_go"]["text"].endswith(t("word.continue")))

    def test_the_games_are_offered_in_the_libraries_order_where_the_file_names_none(self) -> None:
        self.assertEqual([one.split("\n")[0] for one in self.seen["order"]], list(GAMES))
        self.assertEqual(self.seen["picked"], "Taxi")

    def test_cancelling_opens_no_sheet_and_writes_nothing(self) -> None:
        self.assertFalse(self.seen["sheet_after_cancel"])
        self.assertEqual(self.seen["taxi_files"], [])

    def test_the_game_the_file_names_is_chosen_and_goes_on_to_the_sheet_and_the_import(
            self) -> None:
        self.assertEqual(self.seen["named_field"], "Medieval Madness")
        self.assertFalse(self.seen["named_go"]["off"])
        self.assertTrue(self.seen["sheet_card"])
        self.assertTrue(self.seen["written"])


if __name__ == "__main__":
    unittest.main()

"""What a file dragged over the Manager shows before it is let go: the place that would take
it lights, a place that cannot take it shows the no-drop cursor, and a dialog's own drop
zone keeps its drop to itself.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk
from tests.support.pictures import png

API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")
CELL = ("(() => { const c = document.querySelector("
        "'.ag-row[row-id=%s] .ag-cell[col-id=\"%s\"]'); if (!c) return null;"
        " const r = c.getBoundingClientRect();"
        " return r.height ? [r.left + r.width / 2, r.top + r.height / 2] : null; })()")
ROW = ("(() => { const c = document.querySelector('.ag-row[row-id=%s] .ag-cell[col-id=\"name\"]');"
       " if (!c) return null; const r = c.getBoundingClientRect();"
       " return [r.left + r.width / 2, r.top + r.height / 2]; })()")
# What the page's own dragover handler decided, read after it has run.
EFFECT_LISTENER = ("(() => { window.__effects = [];"
                   " window.addEventListener('dragover', e => window.__effects.push("
                   "e.dataTransfer && e.dataTransfer.dropEffect), false); })()")
LIT = "document.querySelectorAll('.manager-drop-lit').length"
LAST_EFFECT = "window.__effects[window.__effects.length - 1]"
GAMES = ("Alpha", "Bravo")


class DropTargetsDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            for title in GAMES:
                write_game(library, title, info=game_info(title, vps_id="",
                                                          game_id=title.lower()))
            picture = Path(tmp) / "IMG_0001.png"
            picture.write_bytes(png(800, 800))
            with LiveInstance(library) as instance:
                cls.seen = asyncio.run(cls._drive(instance, picture))

    @classmethod
    async def _drive(cls, instance: LiveInstance, picture: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        data = {"items": [], "files": [str(picture)], "dragOperationsMask": 1}

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def over(x: float, y: float) -> None:
                for kind in ("dragEnter", "dragOver", "dragOver"):
                    await browser.send("Input.dispatchDragEvent",
                                       {"type": kind, "x": x, "y": y, "data": data})
                    await asyncio.sleep(0.15)

            async def leave() -> None:
                await browser.send("Input.dispatchDragEvent",
                                   {"type": "dragCancel", "x": 0, "y": 0, "data": data})
                await asyncio.sleep(0.15)

            async def hovered(x: float, y: float) -> dict:
                await over(x, y)
                found = {"lit": await browser.evaluate(LIT),
                         "effect": await browser.evaluate(LAST_EFFECT)}
                await leave()
                return found

            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            await browser.evaluate(EFFECT_LISTENER)

            x, y = await browser.wait_for(ROW % json.dumps("alpha"))
            seen["name_cell"] = await hovered(x, y)

            await browser.evaluate(API + ".setColumnsVisible(['media_wheel'], true)")
            await browser.evaluate(API + ".ensureColumnVisible('media_wheel')")
            x, y = await browser.wait_for(CELL % (json.dumps("alpha"), "media_wheel"))
            seen["media_cell"] = await hovered(x, y)

            for view in ("settings", "devices"):
                await walk.visit(f"/manager?view={view}")
                await browser.evaluate(EFFECT_LISTENER)
                seen[view] = await hovered(600, 400)

        return seen

    def test_a_media_cell_lights_and_takes_the_drop(self) -> None:
        self.assertEqual(self.seen["media_cell"]["lit"], 1)
        self.assertNotEqual(self.seen["media_cell"]["effect"], "none")

    def test_a_name_cell_lights_its_row(self) -> None:
        self.assertEqual(self.seen["name_cell"]["lit"], 1)

    def test_pages_that_take_no_files_show_the_no_drop_cursor(self) -> None:
        for view in ("settings", "devices"):
            with self.subTest(view=view):
                self.assertEqual(self.seen[view]["effect"], "none")
                self.assertEqual(self.seen[view]["lit"], 0)


if __name__ == "__main__":
    unittest.main()

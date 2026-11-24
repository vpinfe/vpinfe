"""The line under a name, in the places a panel lists games or tables, read against the
grid cell it copies. And a game's header with no manufacturer.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

EMPTY = "Empty List"
HELD = "Held List"
# Two builds of one game told apart only at the end, and too long for any line here.
LONG = "Delta (Williams 1993) " + "Community Remaster " * 6 + "%s.vpx"
# Whether a filename's end is on screen and its front is what was cut.
CUT = ("(root => { const box = root && root.querySelector('.manager-file-name');"
       " if (!box) return null; const all = box.getBoundingClientRect();"
       " const text = box.firstElementChild.getBoundingClientRect();"
       " return [text.right <= all.right + 1, text.left < all.left - 1,"
       " box.firstElementChild.innerText]; })(%s)")
DELTA_ROW = ("[...document.querySelectorAll('.manager-section-work .manager-member-row')]"
             ".find(r => r.innerText.startsWith('Delta'))")
DELTA_TABLE = ("[...document.querySelectorAll('.manager-section-work .manager-member-row')]"
               ".find(r => r.innerText.includes('lw'))")
# Words, color and size of each part of a line: the name's selector, then the root.
PARTS = ("(root => { if (!root) return null;"
         " const look = sel => { const el = root.querySelector(sel); if (!el) return null;"
         " const style = getComputedStyle(el);"
         " return [el.innerText.trim(), style.color, style.fontSize]; };"
         " return [%s, '.manager-cell-made', '.manager-cell-join', '.manager-cell-built']"
         ".map(look); })(%s)")
# A cell is drawn before its row's text arrives, so a line is read once it says something.
SAID = "(line => line && line[1] ? line : null)(%s)"
GAME_CELL = ".ag-row[row-id=\"alpha\"] .manager-cell-identifier"
TABLE_CELL = ".ag-row[row-id=\"t-a1\"] .manager-cell-identifier"
MEMBER = ("[...document.querySelectorAll('.manager-section-work .manager-member-row')]"
          ".find(r => r.innerText.startsWith('Alpha'))")
HEADER = ("(() => { const title = document.querySelector('.manager-workbench-title');"
          " const el = document.querySelector('.manager-workbench-label');"
          " return title && title.innerText === 'Charlie' && el ? el.innerText : null; })()")
GAMES_ROW = ("(() => { const rows = [...document.querySelectorAll('.manager-section-row')];"
             " const at = rows.findIndex(r => r.innerText.startsWith('Games'));"
             " return at < 0 ? 0 : rows[at].classList.contains('manager-section-on')"
             " ? -1 : at + 1; })()")
ADD_BOX = ("[...document.querySelectorAll('.manager-section-work .q-select')]"
           ".findIndex(e => e.innerText.includes('Add Games'))")
OPTION = ("(() => { const item = [...document.querySelectorAll('.q-menu .q-item')]"
          ".find(e => e.innerText.startsWith('Alpha')); if (!item) return null;"
          " const labels = [...item.querySelectorAll('.q-item__label')];"
          " const side = item.querySelector('.q-item__section--side');"
          " return [labels.map(l => [l.innerText.trim(), getComputedStyle(l).color]),"
          " side ? side.innerText.trim() : null]; })()")


def _cell(selector: str) -> str:
    return f"document.querySelector({json.dumps(selector)})"


class RecordLinesDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            tables = {"t-a1": {"id": "t-a1", "filename": "Alpha 1.vpx", "version": "1.2",
                               "authors": ["Someone"], "user": {"tags": ["VR"]}},
                      "t-a2": {"id": "t-a2", "filename": "Alpha 2.vpx", "version": "2"}}
            info = game_info("Alpha", vps_id="", game_id="alpha", tables=tables,
                             Info={"Manufacturer": "Bally", "Year": "1992"},
                             User={"Tags": ["Night"]})
            write_game(Path(tmp), "Alpha", info=info, vpx=False,
                       files={"Alpha 1.vpx": b"x", "Alpha 2.vpx": b"x"})
            delta = {f"t-d{end}": {"id": f"t-d{end}", "filename": LONG % end,
                                   **({"user": {"tags": ["VR"]}} if end == "lw" else {})}
                     for end in ("12", "lw")}
            held = game_info("Delta", vps_id="", game_id="delta", tables=delta,
                             Info={"Manufacturer": "Williams", "Year": "1993"})
            held["vpinfe"]["default_table"] = "t-dlw"
            write_game(Path(tmp), "Delta", vpx=False, info=held,
                       files={LONG % end: b"x" for end in ("12", "lw")})
            write_game(Path(tmp), "Charlie", info=game_info(
                "Charlie", vps_id="", game_id="charlie", Info={"Year": "1993"}))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        instance.post("/api/v1/collections", {"name": EMPTY})
        instance.post("/api/v1/collections",
                      {"name": HELD, "games": ["delta"]})

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def parts(name: str, root: str, key: str) -> None:
                seen[key] = await browser.wait_for(SAID % (PARTS % (json.dumps(name), root)),
                                                   timeout=60.0)

            async def open_tag(tag: str) -> None:
                row = f".ag-row[row-id={json.dumps(tag)}] .ag-cell"
                await browser.wait_for(f"!!document.querySelector({json.dumps(row)})",
                                       timeout=60.0)
                await walk.open_pane(lambda: browser.click(row))
                at = await browser.wait_for(GAMES_ROW, timeout=30.0)
                if at > 0:
                    await walk.act(
                        lambda: browser.click(".manager-section-row", nth=int(at) - 1),
                        mark="window.__games = document.querySelectorAll("
                             f"'.manager-section-row')[{int(at) - 1}]",
                        until="!window.__games.isConnected")
                await browser.wait_for(f"!!({MEMBER})", timeout=30.0)

            await walk.visit("/manager?view=games")
            await parts(".manager-cell-named", _cell(GAME_CELL), "grid_game")
            await walk.visit("/manager?view=tables")
            await parts(".manager-cell-named", _cell(TABLE_CELL), "grid_table")
            seen["cut_grid"] = await browser.wait_for(
                CUT % _cell(".ag-row[row-id=\"t-dlw\"] .manager-cell-identifier"))

            await walk.visit("/manager?view=tags")
            await open_tag("Night")
            await parts(".manager-link", MEMBER, "tag_game")
            await open_tag("VR")
            await parts(".manager-link", MEMBER, "tag_table")
            seen["cut_tag"] = await browser.wait_for(CUT % DELTA_ROW, timeout=30.0)

            await walk.visit("/manager?view=games&game=charlie")
            seen["header"] = await browser.wait_for(HEADER, timeout=60.0)
            await walk.visit("/manager?view=games&game=delta")
            seen["cut_game"] = await browser.wait_for(CUT % DELTA_TABLE, timeout=60.0)
            await walk.visit(f"/manager?view=collections&collection={quote(HELD)}")
            seen["cut_collection"] = await browser.wait_for(CUT % DELTA_ROW, timeout=60.0)

            await walk.visit(f"/manager?view=collections&collection={quote(EMPTY)}")
            await browser.click(".manager-section-work .q-select input",
                                nth=await browser.evaluate(ADD_BOX))
            seen["option"] = await browser.wait_for(OPTION, timeout=30.0)
        return seen

    def test_a_tag_s_game_reads_as_the_games_grid_does(self) -> None:
        name, *line = self.seen["tag_game"]
        self.assertEqual("Alpha", name[0])
        self.assertEqual(self.seen["grid_game"][1:3], line[:2])
        self.assertIsNone(line[2])

    def test_a_tag_s_table_reads_as_the_tables_grid_does(self) -> None:
        name, *line = self.seen["tag_table"]
        self.assertEqual("Alpha", name[0])
        self.assertEqual(self.seen["grid_table"][1:], line)
        self.assertEqual(["Bally 1992", "·", "1.2 · Someone"],
                         [part[0] for part in line])

    def test_the_picker_says_maker_and_year_under_the_name_in_the_grid_s_color(self) -> None:
        labels, side = self.seen["option"]
        self.assertEqual(["Alpha", "Bally 1992"], [one[0] for one in labels])
        self.assertEqual(self.seen["grid_game"][1][1], labels[1][1])
        self.assertEqual("", side)

    def test_a_long_filename_keeps_its_end_and_loses_its_front(self) -> None:
        for place in ("cut_grid", "cut_tag", "cut_game", "cut_collection"):
            with self.subTest(place=place):
                self.assertEqual([True, True, LONG % "lw"], self.seen[place])

    def test_a_game_with_no_maker_shows_its_year_alone(self) -> None:
        self.assertEqual("1993", self.seen["header"])


if __name__ == "__main__":
    unittest.main()

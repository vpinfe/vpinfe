"""The Games grid's list columns in a real grid, and Show in Games from a collection.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

TAGGED = {"Alpha": ["Shortlist", "Night Owl"], "Bravo": ["Late Night"], "Charlie": [],
          "Delta": ["Shortlist"]}

API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")

SHOWN = ("(() => { const out = []; " + API + ".forEachNodeAfterFilterAndSort("
         "n => out.push(n.data.name)); return out; })()")


UPDATED = ("(async () => { const api = " + API + ";"
           " await new Promise(done => { const heard = () => {"
           " api.removeEventListener('modelUpdated', heard); done(); };"
           " api.addEventListener('modelUpdated', heard); %s; }); })()")
UPDATE_FLAG = ("(() => { window.__updated = false;"
               " " + API + ".addEventListener('modelUpdated', () => { window.__updated = true; });"
               " })()")


def _filtered(model: dict | None) -> str:
    return ("(async () => { await "
            + UPDATED % (API + ".setFilterModel(" + json.dumps(model) + ")")
            + "; const shown = " + SHOWN + "; await "
            + UPDATED % (API + ".setFilterModel(null)") + "; return shown; })()")


def _sorted(direction: str) -> str:
    return ("(async () => { await " + UPDATED % (
        API + ".applyColumnState({state: [{colId: 'tags', sort: " + json.dumps(direction)
        + "}], defaultState: {sort: null}})") + "; return " + SHOWN + "; })()")


CHIPS_OF_ALPHA = ("(() => { let id = ''; " + API + ".forEachNode(n => { if (n.data.name"
                  " === 'Alpha') id = n.id; }); const cell = document.querySelector("
                  "'.ag-row[row-id=\"' + id + '\"] .ag-cell[col-id=\"tags\"]');"
                  " return cell ? [...cell.querySelectorAll('.manager-tag')]"
                  ".map(c => c.innerText) : null; })()")

FILTER_TEXT = ("(() => { const f = document.querySelector('.manager-filter');"
               " return f ? f.innerText.split('\\n').map(s => s.trim()).filter(Boolean)"
               " : null; })()")

ROW_INDEX = ("(() => [...document.querySelectorAll('.manager-filter-row')]"
             ".findIndex(r => r.innerText.startsWith(%s)))()")

SHOW_IN_GAMES = "a.manager-link[href*='view=games']"

COUNT = ("(() => [...document.querySelectorAll('.manager-label')]"
         ".map(l => l.innerText).find(s => s.includes('games')))()")

HELD = ("(() => { const out = {}; " + API + ".forEachNode(n => {"
        " out[n.data.name] = [...n.data.collections]; }); return out; })()")


class ListColumnDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            for name, tags in TAGGED.items():
                write_game(Path(tmp), name,
                           info=game_info(name, vps_id="", User={"Tags": tags}))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        ids = {one["name"]: one["id"] for one in instance.api("/api/v1/games")["games"]}
        instance.post("/api/v1/collections",
                      {"name": "Friday Night", "games": [ids["Alpha"], ids["Delta"]]})
        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)
            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() === 4", timeout=90.0)
            for label, model in (
                    ("any", {"tags": {"values": ["Night Owl", "Shortlist"]}}),
                    ("all", {"tags": {"values": ["Night Owl", "Shortlist"], "all": True}}),
                    ("none", {"tags": {"values": [""]}}),
                    ("none_or", {"tags": {"values": ["", "Late Night"]}}),
                    ("none_and", {"tags": {"values": ["", "Late Night"], "all": True}})):
                seen[label] = await browser.evaluate(_filtered(model))
            seen["asc"] = await browser.evaluate(_sorted("asc"))
            seen["desc"] = await browser.evaluate(_sorted("desc"))
            await browser.evaluate(API + ".ensureColumnVisible('tags')")
            seen["chips"] = await browser.wait_for(CHIPS_OF_ALPHA)
            await browser.click('.ag-header-cell[col-id="tags"] .ag-header-cell-filter-button')
            await browser.wait_for(FILTER_TEXT)
            seen["offered"] = await browser.evaluate(FILTER_TEXT)
            shortlist = await browser.evaluate(ROW_INDEX % json.dumps("Shortlist"))
            await walk.act(lambda: browser.click(".manager-filter-row input", nth=int(shortlist)),
                           mark=UPDATE_FLAG, until="window.__updated")
            seen["ticked"] = await browser.evaluate(SHOWN)
            seen["model"] = await browser.evaluate(API + ".getFilterModel()")
            seen["held"] = await browser.evaluate(HELD)

            await walk.visit("/manager?view=collections&collection=Friday%20Night")
            await walk.act(lambda: browser.click(SHOW_IN_GAMES),
                           until="location.search.includes('view=games')")
            await browser.wait_for(API + ".getDisplayedRowCount() === 2", timeout=60.0)
            seen["address"] = await browser.evaluate("location.search")
            seen["narrowed"] = await browser.evaluate(SHOWN)
            with suppress(TimeoutError):
                await browser.wait_for(f"({COUNT} || '').includes(' of ')", timeout=30.0)
            seen["count"] = await browser.evaluate(COUNT)
        return seen

    def test_any_of_the_picked_values(self) -> None:
        self.assertEqual({"Alpha", "Delta"}, set(self.seen["any"]))

    def test_all_of_them_with_the_switch(self) -> None:
        self.assertEqual(["Alpha"], self.seen["all"])

    def test_none_is_the_row_holding_nothing(self) -> None:
        self.assertEqual(["Charlie"], self.seen["none"])
        self.assertEqual({"Bravo", "Charlie"}, set(self.seen["none_or"]))
        self.assertEqual([], self.seen["none_and"])

    def test_the_sort_goes_by_the_first_chip_with_empty_rows_last_both_ways(self) -> None:
        self.assertEqual(["Bravo", "Alpha", "Delta", "Charlie"], self.seen["asc"])
        self.assertEqual(["Delta", "Alpha", "Bravo", "Charlie"], self.seen["desc"])

    def test_the_chips_are_drawn_in_the_order_the_sort_reads(self) -> None:
        self.assertEqual(["Night Owl", "Shortlist"], self.seen["chips"])

    def test_the_filter_offers_every_value_with_its_count_and_none(self) -> None:
        self.assertEqual(["Any of", "All of", "Late Night", "1", "Night Owl", "1",
                          "Shortlist", "2", "None", "1"], self.seen["offered"])

    def test_ticking_a_value_filters_and_the_model_is_the_values(self) -> None:
        self.assertEqual({"Alpha", "Delta"}, set(self.seen["ticked"]))
        self.assertEqual({"tags": {"values": ["Shortlist"]}}, self.seen["model"])


    def test_a_game_s_collections_are_its_cell(self) -> None:
        self.assertEqual({"Alpha": ["Friday Night"], "Bravo": [], "Charlie": [],
                          "Delta": ["Friday Night"]}, self.seen["held"])

    def test_show_in_games_narrows_the_grid_to_the_collection(self) -> None:
        self.assertEqual("?view=games&collection=Friday+Night", self.seen["address"])
        self.assertEqual({"Alpha", "Delta"}, set(self.seen["narrowed"]))
        self.assertEqual("2 of 4 games", self.seen["count"])


if __name__ == "__main__":
    unittest.main()

"""A Games view saved against a column that has since gone, opened in a real grid.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

GAMES = ("Alpha", "Bravo", "Charlie")

SCOPE = "manager.games.columns.views"

API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")

COLUMNS = "(() => " + API + ".getAllDisplayedColumns().map(c => c.getColId()))()"
SHOWN = ("(() => { const out = []; " + API + ".forEachNodeAfterFilterAndSort("
         "n => out.push(n.data.name)); return out; })()")


def _save(instance: LiveInstance, view: dict) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{instance.ports['manager']}/api/v1/preferences/{SCOPE}",
        data=json.dumps({"views": [view], "active": view["id"]}).encode(), method="PUT",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10):
        pass


class StaleViewDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            for name in GAMES:
                write_game(Path(tmp), name, info=game_info(name, vps_id=""))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            _save(instance, {"id": "one", "name": "Old", "columns": ["name", "script_fix", "year"],
                             "filters": {"script_fix": {"values": ["Has a Fix"]}}})
            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() === 3", timeout=90.0)
            seen["columns"] = await browser.evaluate(COLUMNS)
            seen["shown"] = await browser.evaluate(SHOWN)
            seen["model"] = await browser.evaluate(API + ".getFilterModel()")

            _save(instance, {"id": "two", "name": "Older", "columns": ["script_fix"]})
            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() === 3", timeout=90.0)
            seen["only_gone"] = await browser.evaluate(COLUMNS)
        return seen

    def test_the_columns_that_survive_are_what_shows(self) -> None:
        self.assertEqual(["name", "year"],
                         [c for c in self.seen["columns"] if c != "ag-Grid-SelectionColumn"])

    def test_a_filter_on_the_gone_column_hides_nothing(self) -> None:
        self.assertEqual(set(GAMES), set(self.seen["shown"]))
        self.assertNotIn("script_fix", self.seen["model"] or {})

    def test_a_view_naming_only_gone_columns_shows_every_column(self) -> None:
        self.assertGreater(len(self.seen["only_gone"]), 10)
        self.assertNotIn("script_fix", self.seen["only_gone"])


if __name__ == "__main__":
    unittest.main()

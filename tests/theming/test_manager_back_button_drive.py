"""Back and Forward across the Manager's places, in a real browser: a rail click is a history
entry, a row pick rewrites the current one, and Back lands on the page before with what it had
open.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

WIDE = 1400
SETTLE_S = 1.0
IMPORT_LABEL = json.loads(
    (Path(__file__).resolve().parents[2] / "extensions" / "library_importer" / "i18n"
     / "en.json").read_text(encoding="utf-8"))["action.import.label"]
ACTIONS = ".manager-settings-body .manager-action"
CARD = ".manager-wizard-card"
ROW = (".ag-center-cols-container .ag-row[row-index=\"%d\"] "
       ".ag-cell:not([col-id=\"ag-Grid-SelectionColumn\"])")
NAV = 'a.manager-nav-row[href="/manager?view=%s"]'
ROWS = "document.querySelectorAll('.ag-center-cols-container .ag-row').length > 0"
ACTIVE_TAB = ("((el) => el && el.innerText.trim())"
              "(document.querySelector('.manager-tabs-row .q-tab--active'))")
PLACE = ("(() => ({search: location.search, title: document.title,"
         " length: history.length}))()")


def _view(place: dict) -> str:
    return parse_qs(place["search"].lstrip("?")).get("view", [""])[0]


def _game(place: dict) -> str:
    return parse_qs(place["search"].lstrip("?")).get("game", [""])[0]


class ManagerBackButtonDrive(unittest.TestCase):
    seen: dict[str, dict] = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            for title in ("Alpha", "Bravo", "Charlie"):
                write_game(Path(tmp), title,
                           info=game_info(title, vps_id="", game_id=title.lower()),
                           files={f"{title}.ini": b"[Player]"})
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict[str, dict]:
        seen: dict[str, dict] = {}
        instance.wait_for_api()
        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def place(name: str) -> None:
                seen[name] = await browser.evaluate(PLACE)

            async def on(view: str) -> None:
                await browser.wait_for(
                    f"new URLSearchParams(location.search).get('view') === {view!r}")
                await browser.wait_for(
                    f"!!document.querySelector('{NAV % view}.manager-nav-active')")
                await walk.drawn()

            async def back() -> None:
                await browser.evaluate("history.back()")

            async def forward() -> None:
                await browser.evaluate("history.forward()")

            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": WIDE, "height": 800, "deviceScaleFactor": 1,
                                "mobile": False})
            await walk.visit("/manager?view=games")
            await browser.wait_for(ROWS)
            await place("start")

            await browser.click(ROW % 1)
            await browser.wait_for("new URLSearchParams(location.search).has('game')")
            await place("picked")

            await browser.click(NAV % "collections")
            await on("collections")
            await place("collections")

            await browser.click(NAV % "tables")
            await on("tables")
            await place("tables")

            await back()
            await on("collections")
            await place("back to collections")

            await back()
            await on("games")
            await browser.wait_for(ROWS)
            await place("back to games")
            await browser.wait_for(
                "document.querySelector('.manager-panel-header').innerText.toLowerCase()"
                f".includes({_game(seen['picked'])!r})")

            await forward()
            await on("collections")
            await place("forward to collections")

            await browser.click(NAV % "tables")
            await on("tables")
            await place("tables again")
            await back()
            await on("collections")
            await forward()
            await on("tables")
            await place("forward to tables")

            async def tab(name: str) -> None:
                await browser.evaluate(
                    "[...document.querySelectorAll('.manager-tabs-row .q-tab')]"
                    f".find(el => el.innerText.trim() === {name!r}).click()")

            await browser.click(NAV % "discovery")
            await on("discovery")
            await browser.wait_for(
                "document.querySelectorAll('.manager-tabs-row .q-tab').length === 3")
            await tab("All Games")
            await browser.wait_for("new URLSearchParams(location.search).get('tab') === 'browse'")
            await tab("Releases")
            await browser.wait_for(
                "new URLSearchParams(location.search).get('tab') === 'releases'")
            await place("releases tab")
            await back()
            await browser.wait_for("new URLSearchParams(location.search).get('tab') === 'browse'")
            await browser.wait_for(f"{ACTIVE_TAB} === 'All Games'")
            await place("back to browse tab")

            await walk.visit("/manager?view=discovery&tab=releases")
            await browser.wait_for(f"{ACTIVE_TAB} === 'Releases'")
            await place("reloaded on a tab")

            await walk.visit("/manager?view=settings&page=library.folders")
            at = await browser.wait_for(
                f"(() => {{ const i = [...document.querySelectorAll({ACTIONS!r})]"
                f".findIndex(el => el.innerText.includes({IMPORT_LABEL!r}));"
                " return i >= 0 ? i + 1 : 0; })()")
            await browser.evaluate("window.keptThroughTheDialog = true")
            await place("before the dialog")
            await walk.act(
                lambda: browser.click(ACTIONS, nth=int(at) - 1),
                until=f"!!document.querySelector({CARD!r})")
            await place("dialog open")
            await back()
            await browser.wait_for(f"!document.querySelector({CARD!r})")
            await asyncio.sleep(SETTLE_S)
            await place("dialog closed by back")
            seen["kept"] = {"value": await browser.evaluate("window.keptThroughTheDialog")}
        return seen

    def test_a_row_pick_does_not_add_an_entry(self) -> None:
        self.assertEqual(self.seen["start"]["length"], self.seen["picked"]["length"])
        self.assertTrue(_game(self.seen["picked"]))

    def test_each_place_adds_one_entry(self) -> None:
        self.assertEqual(self.seen["collections"]["length"],
                         self.seen["picked"]["length"] + 1)
        self.assertEqual(self.seen["tables"]["length"],
                         self.seen["collections"]["length"] + 1)

    def test_back_goes_to_the_page_before(self) -> None:
        self.assertEqual("collections", _view(self.seen["back to collections"]))

    def test_back_again_lands_on_the_game_that_was_open(self) -> None:
        self.assertEqual("games", _view(self.seen["back to games"]))
        self.assertEqual(_game(self.seen["picked"]), _game(self.seen["back to games"]))

    def test_forward_goes_where_back_came_from(self) -> None:
        self.assertEqual("collections", _view(self.seen["forward to collections"]))
        self.assertEqual("tables", _view(self.seen["forward to tables"]))

    def test_a_place_after_back_replaces_the_forward_entry(self) -> None:
        self.assertEqual(self.seen["tables again"]["length"], self.seen["tables"]["length"])

    def test_back_returns_to_the_discovery_tab_before(self) -> None:
        self.assertIn("tab=browse", self.seen["back to browse tab"]["search"])

    def test_a_reload_lands_on_the_discovery_tab(self) -> None:
        self.assertIn("tab=releases", self.seen["reloaded on a tab"]["search"])

    def test_back_closes_a_dialog_and_leaves_the_page_where_it_was(self) -> None:
        self.assertEqual(self.seen["before the dialog"]["search"],
                         self.seen["dialog closed by back"]["search"])
        self.assertEqual(self.seen["before the dialog"]["title"],
                         self.seen["dialog closed by back"]["title"])
        self.assertTrue(self.seen["kept"]["value"])

    def test_the_title_names_the_place(self) -> None:
        self.assertIn("VPinFE Manager", self.seen["tables"]["title"])
        self.assertNotEqual(self.seen["collections"]["title"], self.seen["tables"]["title"])
        self.assertEqual(self.seen["back to collections"]["title"],
                         self.seen["collections"]["title"])


if __name__ == "__main__":
    unittest.main()

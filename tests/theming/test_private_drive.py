"""Private in Library › Games, driven as a person would: one game marked Private from its
panel, its chip, the grid filtered to it, then a selection marked Private and another Not
Private from the selection's menu, each read back from the game's `.info`.

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
from tests.support.manager_walk import MARK, ManagerWalk, notice

GAMES = ("Alpha", "Bravo", "Charlie", "Delta")

API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")
SHOWN = ("(() => { const out = []; " + API + ".forEachNodeAfterFilterAndSort("
         "n => out.push(n.data.id)); return out; })()")
WORK = ".manager-section-work"
# The switch in the fact row labelled Private, and the line under it.
SWITCH = ("[...document.querySelectorAll('.manager-section-work .manager-fact-label')]"
          ".find(el => el.innerText.trim() === 'Private').nextElementSibling")
SWITCH_ON = f"({SWITCH}).getAttribute('aria-checked')"
UNDER_SWITCH = f"({SWITCH}).nextElementSibling.innerText.trim()"
CHIPS = ("Object.fromEntries([...document.querySelectorAll("
         "'.ag-center-cols-container .ag-row, .ag-pinned-left-cols-container .ag-row')]"
         ".filter(row => row.querySelector('.ag-cell[col-id=private]'))"
         ".map(row => { const chip = row.querySelector("
         "'.ag-cell[col-id=private] .manager-state');"
         " return [row.getAttribute('row-id'), chip ? [chip.innerText.trim(),"
         " chip.className, chip.title] : null]; }))")
MENU = ("[...document.querySelectorAll('.q-menu .manager-menu-item')]"
        ".map(e => [e.innerText.replace(/\\n+/g, ' / '),"
        " e.classList.contains('manager-menu-blocked')])")
FILTER_ROWS = ("[...document.querySelectorAll('.manager-filter .manager-filter-row')]"
               ".map(r => r.innerText.replace(/\\s+/g, ' ').trim())")
CHECKBOX = ".ag-row[row-id=%s] .ag-selection-checkbox input"


class PrivateDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        if not binary:
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            for title in GAMES:
                write_game(Path(tmp), title,
                           info=game_info(title, vps_id="", game_id=title.lower()))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance, Path(tmp), binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance, root: Path, binary: str) -> dict:
        seen: dict = {}
        instance.wait_for_api()

        def on_disk() -> dict[str, object]:
            return {title.lower(): json.loads(
                (root / title / f"{title}.info").read_text(encoding="utf-8"))["vpinfe"]
                .get("private") for title in GAMES}

        async with BrowserSession(binary) as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1600, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)

            async def show_private() -> None:
                """Private is a column of the Frontend view, not of the one Games opens on."""
                await browser.evaluate(API + ".setColumnsVisible(['private'], true)")
                await browser.evaluate(API + ".ensureColumnVisible('private')")

            async def chips(until: str) -> dict:
                await show_private()
                return await browser.wait_for(f"(c => ({until}) ? c : null)({CHIPS})")

            async def pick(*ids: str) -> None:
                await browser.evaluate(API + ".deselectAll()")
                for one in ids:
                    await browser.click(CHECKBOX % json.dumps(one))
                await browser.wait_for(f"{API}.getSelectedNodes().length === {len(ids)}")

            async def selection_menu() -> list:
                await browser.evaluate(
                    "[...document.querySelectorAll('.manager-grid-bar .q-btn')]"
                    ".find(b => b.textContent.includes('more_vert'))"
                    ".setAttribute('data-drive', 'actions')")
                await walk.act(lambda: browser.click("[data-drive=actions]"),
                               until="!!document.querySelector('.q-menu .manager-menu-item')")
                return await browser.evaluate(MENU)

            async def choose(label: str, said: str) -> list:
                at = await browser.evaluate(
                    "[...document.querySelectorAll('.q-menu .manager-menu-item')]"
                    f".findIndex(e => e.innerText.split('\\n')[0] === {json.dumps(label)})")
                await walk.act(lambda: browser.click(".q-menu .manager-menu-item", nth=at),
                               mark=MARK, until=notice(said))
                return await browser.evaluate(notice(said))

            seen["before"] = on_disk()

            # One game, from its panel.
            await walk.visit("/manager?view=games&game=alpha&section=game_details")
            await browser.wait_for(API + ".getDisplayedRowCount() === 4")
            await browser.wait_for(f"({SWITCH_ON}) === 'false'")
            seen["line"] = await browser.evaluate(UNDER_SWITCH)
            await browser.evaluate(f"({SWITCH}).setAttribute('data-drive', 'private')")
            await browser.click("[data-drive=private]")
            await browser.wait_for(f"({SWITCH_ON}) === 'true'")
            seen["chips_one"] = await chips("c.alpha")
            seen["panel_one"] = on_disk()
            seen["header"] = await browser.evaluate(
                "document.querySelector('.ag-header-cell[col-id=private]"
                " .ag-header-cell-text').textContent.trim()")

            # The grid filtered to it, through the column's funnel.
            await show_private()
            await browser.click(".ag-header-cell[col-id=private] .ag-header-cell-filter-button")
            await browser.wait_for("!!document.querySelector('.manager-filter-row')")
            seen["funnel"] = await browser.wait_for(
                f"(r => r.length && /\\d/.test(r[0]) ? r : null)({FILTER_ROWS})")
            await browser.click(".manager-filter-row input", nth=0)
            await browser.wait_for(API + ".getDisplayedRowCount() === 1")
            seen["filtered"] = await browser.evaluate(SHOWN)
            await browser.click(".manager-filter-row input", nth=0)
            await browser.wait_for(API + ".getDisplayedRowCount() === 4")
            await browser.press("Escape", "Escape")
            await walk.drawn()

            # Two games marked Private from a selection.
            await pick("bravo", "charlie")
            seen["menu_two"] = await selection_menu()
            seen["said_two"] = await choose("Mark Private", "marked Private")
            seen["chips_two"] = await chips("c.bravo && c.charlie")
            seen["bulk_two"] = on_disk()

            # One marked Not Private, with its panel open.
            await pick("alpha")
            seen["menu_one"] = await selection_menu()
            seen["said_one"] = await choose("Mark Not Private", "marked Not Private")
            seen["chips_off"] = await chips("!c.alpha")
            seen["bulk_one"] = on_disk()
            seen["panel_after"] = await browser.wait_for(
                f"(s => s === 'false' ? s : null)({SWITCH_ON})")
            seen["rows"] = {one["id"]: one["private"]
                            for one in instance.api("/api/v1/games")["games"]}
            seen["page_log"] = [line for line in browser.page_log if "error" in line.lower()]
        return seen

    def test_nothing_is_private_until_somebody_marks_it(self) -> None:
        self.assertEqual(set(self.seen["before"].values()), {None})

    def test_the_panel_says_what_private_means_under_the_switch(self) -> None:
        self.assertEqual(self.seen["line"], "Never sent to VPinPlay or any community service")

    def test_the_panel_switch_writes_the_game_s_info(self) -> None:
        self.assertEqual(self.seen["panel_one"],
                         {"alpha": True, "bravo": None, "charlie": None, "delta": None})

    def test_only_a_private_row_draws_the_word(self) -> None:
        chips = self.seen["chips_one"]
        self.assertEqual({key for key, chip in chips.items() if chip}, {"alpha"})
        label, classes, tip = chips["alpha"]
        self.assertEqual(label, "Private")
        self.assertIn("manager-state--quiet", classes)
        self.assertEqual(tip, "Private - Never sent to VPinPlay or any community service")
        self.assertEqual(self.seen["header"], "Community")

    def test_the_funnel_filters_to_the_private_games(self) -> None:
        self.assertEqual(self.seen["funnel"], ["Private 1", "Not Private 3"])
        self.assertEqual(self.seen["filtered"], ["alpha"])

    def test_the_selection_menu_offers_only_what_would_change(self) -> None:
        names = {key: [label for label, _inert in self.seen[key]]
                 for key in ("menu_two", "menu_one")}
        self.assertIn(["Mark Private", False], self.seen["menu_two"])
        self.assertNotIn("Mark Not Private", names["menu_two"])
        self.assertIn(["Mark Not Private", False], self.seen["menu_one"])
        self.assertNotIn("Mark Private", names["menu_one"])

    def test_two_marked_private_from_a_selection(self) -> None:
        text, _classes = self.seen["said_two"]
        self.assertIn("2 games marked Private", text)
        self.assertIn("Undo", text)
        self.assertEqual({key for key, chip in self.seen["chips_two"].items() if chip},
                         {"alpha", "bravo", "charlie"})
        self.assertEqual(self.seen["bulk_two"],
                         {"alpha": True, "bravo": True, "charlie": True, "delta": None})

    def test_one_marked_not_private_from_a_selection(self) -> None:
        text, _classes = self.seen["said_one"]
        self.assertIn("1 game marked Not Private", text)
        self.assertEqual({key for key, chip in self.seen["chips_off"].items() if chip},
                         {"bravo", "charlie"})
        self.assertEqual(self.seen["bulk_one"],
                         {"alpha": False, "bravo": True, "charlie": True, "delta": None})
        self.assertEqual(self.seen["rows"],
                         {"alpha": False, "bravo": True, "charlie": True, "delta": False})

    def test_an_open_panel_follows_a_selection_s_mark(self) -> None:
        self.assertEqual(self.seen["panel_after"], "false")

    def test_nothing_failed_in_the_browser(self) -> None:
        self.assertEqual(self.seen["page_log"], [])


if __name__ == "__main__":
    unittest.main()

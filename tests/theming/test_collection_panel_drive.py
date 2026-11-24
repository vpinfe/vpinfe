"""A collection's panel in a real browser: rules as a draft, Locked, the limit's line,
and a row drawn as the Tables grid draws its table.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import MARK, ManagerWalk, newer, notice

NAME = "Smart Bally"
OTHER = "Hand Picked"
GAMES = {"Alpha": ("Bally", "1992"), "Bravo": ("Bally", "1995"),
         "Charlie": ("Williams", "1993")}

INDEX_OF = ("(() => [...document.querySelectorAll(%s)]"
            ".findIndex(el => el.innerText.includes(%s)))()")
TEXT_OF = "(() => { const el = document.querySelector(%s); return el ? el.innerText : null; })()"
SHOWS = "!!document.querySelector(%s)"
CHECKED = ("[...document.querySelectorAll('.q-dialog .q-radio[aria-checked=\"true\"]')]"
           ".some(el => el.innerText.includes(%s))")
GRID_NAME = (".ag-row[row-id=" + json.dumps(NAME) + "] .ag-cell[col-id=\"name\"]")
OTHER_NAME = (".ag-row[row-id=" + json.dumps(OTHER) + "] .ag-cell[col-id=\"name\"]")
UNSAVED_IN_GRID = ("(() => { const el = document.querySelector('.nicegui-aggrid');"
                   " const row = getElement(el.id.slice(1)).api.getRowNode(%s);"
                   " return row ? row.data.unsaved : null; })()")
# Words, color and size of each part of a row: the name's selector, then the row.
PARTS = ("(root => { if (!root) return null;"
         " const look = sel => { const el = root.querySelector(sel); if (!el) return null;"
         " const style = getComputedStyle(el);"
         " return [el.innerText.trim(), style.color, style.fontSize]; };"
         " return [%s, '.manager-cell-made', '.manager-cell-join', '.manager-cell-built']"
         ".map(look); })(%s)")
ALPHA_ROW = ("[...document.querySelectorAll('.manager-member-row')]"
             ".find(r => r.querySelector('.manager-member-name').innerText === 'Alpha')")
ALPHA_CELL = ".ag-row[row-id=\"t-a1\"] .manager-cell-identifier"


class CollectionPanelDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            for title, (maker, year) in GAMES.items():
                tables = ({"t-a1": {"id": "t-a1", "filename": "Alpha 1.vpx", "version": "1"},
                           "t-a2": {"id": "t-a2", "filename": "Alpha 2.vpx", "version": "2"}}
                          if title == "Alpha" else None)
                info = game_info(title, vps_id="", game_id=title.lower(), tables=tables,
                                 Info={"Manufacturer": maker, "Year": year})
                if tables:
                    info["vpinfe"]["default_table"] = "t-a2"
                write_game(Path(tmp), title, info=info, vpx=not tables,
                           files={"Alpha 1.vpx": b"x", "Alpha 2.vpx": b"x"} if tables
                           else None)
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        instance.post("/api/v1/collections", {"name": NAME})
        instance.post("/api/v1/collections", {"name": OTHER, "games": ["bravo", "charlie"]})
        stored = f"/api/v1/collections/{quote(NAME)}"

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            def click_text(selector: str, text: str) -> Callable[[], Awaitable[None]]:
                async def click() -> None:
                    at = await browser.wait_for(
                        f"(() => {{ const i = "
                        f"{INDEX_OF % (json.dumps(selector), json.dumps(text))};"
                        " return i >= 0 ? i + 1 : 0; })()")
                    await browser.click(selector, nth=int(at) - 1)
                return click

            await walk.visit(f"/manager?view=collections&collection={quote(NAME)}")
            await browser.wait_for("document.body.innerText.includes('Add a Rule')")
            seen["empty"] = await browser.evaluate(
                TEXT_OF % json.dumps(".manager-section-work"))

            await walk.act(click_text(".manager-section-work button", "Add a Rule"),
                           until=SHOWS % json.dumps(".q-menu .manager-menu-item"))
            await walk.act(click_text(".q-menu .manager-menu-item", "Manufacturer"),
                           mark=MARK, until=newer(".manager-condition-value"))
            await walk.act(lambda: browser.click(".manager-condition-value .q-field"),
                           until=SHOWS % json.dumps(".q-menu .q-item"))
            seen["offered"] = await browser.evaluate(
                "[...document.querySelectorAll('.q-menu .q-item')].map(i => i.innerText)")
            await walk.answered(click_text(".q-menu .q-item", "Bally"))
            seen["still_open"] = await browser.evaluate(
                "!!document.querySelector('.q-menu')")
            await walk.act(lambda: browser.click(".manager-workbench-title"),
                           mark=MARK, until=newer(".manager-draft-bar"))
            seen["bar"] = await browser.evaluate(TEXT_OF % json.dumps(".manager-draft-bar"))
            seen["marked"] = await browser.evaluate(TEXT_OF % json.dumps(GRID_NAME))
            seen["before_save"] = instance.api(stored)["filters"]

            await walk.open_pane(lambda: browser.click(OTHER_NAME))
            await browser.wait_for(TEXT_OF % json.dumps(".manager-workbench-title")
                                   + " === " + json.dumps(OTHER))
            seen["marked_elsewhere"] = await browser.evaluate(UNSAVED_IN_GRID
                                                              % json.dumps(NAME))
            await walk.open_pane(lambda: browser.click(GRID_NAME))
            await browser.wait_for("!!document.querySelector('.manager-draft-bar')")

            await walk.act(click_text(".manager-draft-bar button", "Save Rules"),
                           until="!document.querySelector('.manager-draft-bar')")
            seen["saved"] = instance.api(stored)["filters"]["manufacturer"]
            seen["unmarked"] = await browser.evaluate(TEXT_OF % json.dumps(GRID_NAME))

            await walk.act(click_text(".manager-member-row .manager-member-table-line", "2"),
                           until=SHOWS % json.dumps(".q-menu .manager-menu-item"))
            await walk.act(click_text(".q-menu .manager-menu-item", "Lock to 1"),
                           mark=MARK, until=newer(".manager-member-table-line"))
            members = instance.api(f"{stored}/members")["members"]
            seen["locked"] = [(one["game"], one["origin"], one["ref_table"])
                              for one in members if one["game"] == "alpha"]
            seen["lock_line"] = await browser.evaluate(
                TEXT_OF % json.dumps(".manager-member-row .manager-member-table-line"))
            seen["row_parts"] = await browser.evaluate(
                PARTS % (json.dumps(".manager-member-name"), ALPHA_ROW))

            async def limit_to_one() -> None:
                await browser.click(".manager-order-bar input[type=number]")
                await browser.send("Input.insertText", {"text": "1"})
                await browser.press("Enter", "Enter")

            await walk.act(limit_to_one, mark=MARK, until=newer(".manager-limit-line"))
            seen["cut"] = await browser.evaluate(
                "[...document.querySelectorAll('.manager-member-row.manager-past-limit')]"
                ".map(r => r.querySelector('.manager-member-name').innerText)")
            seen["cut_line"] = await browser.evaluate(
                TEXT_OF % json.dumps(".manager-limit-line"))

            await walk.act(
                lambda: browser.click(".manager-condition .manager-condition-remove"),
                until="!document.querySelector('.manager-condition')")
            await walk.act(click_text(".manager-draft-bar button", "Save Rules"),
                           until=SHOWS % json.dumps(".q-dialog"))
            seen["question"] = await browser.evaluate(TEXT_OF % json.dumps(".q-dialog"))
            await walk.act(click_text(".q-dialog .q-radio", "you added"),
                           until=CHECKED % json.dumps("you added"))
            await walk.act(click_text(".q-dialog button", "Remove Rules"), mark=MARK,
                           until=notice(t("manager.workbench.converted", name=NAME))
                           + " && " + newer(".manager-workbench-title"))
            after = instance.api(stored)
            seen["kept"] = (after["type"], [one["game"] for one in instance.api(
                f"{stored}/members")["members"]])

            await walk.act(lambda: browser.send(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1024, "height": 720, "deviceScaleFactor": 1, "mobile": False}),
                until="window.innerWidth === 1024")
            seen["order_bar"] = await browser.evaluate(
                "(() => { const bar = document.querySelector('.manager-order-bar');"
                " return bar ? [bar.scrollWidth, bar.clientWidth] : null; })()")

            await walk.visit("/manager?view=tables")
            seen["cell_parts"] = await browser.wait_for(
                "(parts => parts && parts[0] ? parts : null)("
                + PARTS % (json.dumps(".manager-cell-named"),
                           "document.querySelector(" + json.dumps(ALPHA_CELL) + ")") + ")",
                timeout=90.0)

            await walk.visit(f"/manager?view=collections&collection={quote(OTHER)}")
            seen["no_limit"] = await browser.wait_for(
                "(() => { const el = document.querySelector("
                "'.manager-section-work input[type=number]'); return el && el.placeholder; })()",
                timeout=60.0)
        return seen

    def test_an_empty_collection_offers_both_ways_in(self) -> None:
        self.assertIn("Nothing in it yet", self.seen["empty"])
        self.assertIn("Add Games", self.seen["empty"])

    def test_no_limit_reads_any(self) -> None:
        self.assertEqual("Any", self.seen["no_limit"])

    def test_values_say_how_many_games_hold_them_and_stay_open_while_picked(self) -> None:
        self.assertIn("Bally\n2", self.seen["offered"])
        self.assertTrue(self.seen["still_open"])

    def test_a_rule_waits_as_a_draft_and_the_grid_says_so(self) -> None:
        self.assertTrue(self.seen["bar"].startswith("2 games match · 2 join"), self.seen["bar"])
        self.assertIn("Not saved", self.seen["marked"])
        self.assertIsNone(self.seen["before_save"])

    def test_the_mark_stays_while_another_collection_is_open(self) -> None:
        self.assertIs(True, self.seen["marked_elsewhere"])

    def test_saving_writes_the_rule_and_clears_the_mark(self) -> None:
        self.assertEqual(["Bally"], self.seen["saved"])
        self.assertNotIn("Not saved", self.seen["unmarked"])

    def test_a_row_the_rule_found_can_be_locked_to_one_table(self) -> None:
        self.assertEqual([("alpha", "named", "t-a1")], self.seen["locked"])
        self.assertTrue(self.seen["lock_line"].startswith("lock\nLocked"),
                        self.seen["lock_line"])

    def test_a_row_is_drawn_as_the_tables_grid_draws_its_table(self) -> None:
        words = [part and part[0] for part in self.seen["row_parts"] or []]
        self.assertEqual(["Alpha", "Bally 1992", "·", "1"], words)
        self.assertEqual(self.seen["cell_parts"], self.seen["row_parts"])

    def test_a_limit_draws_the_rows_it_cuts_under_a_line(self) -> None:
        self.assertEqual(["Bravo"], self.seen["cut"])
        self.assertIn("limit 1", self.seen["cut_line"])

    def test_the_order_bar_fits_a_panel_at_a_1024px_window(self) -> None:
        needs, has = self.seen["order_bar"]
        self.assertLessEqual(needs, has)

    def test_taking_the_last_rule_away_asks_what_to_keep(self) -> None:
        self.assertIn("Keep the 1 game it found", self.seen["question"])
        self.assertIn("Keep only the 1 you added", self.seen["question"])
        self.assertEqual(("manual", ["alpha"]), self.seen["kept"])


if __name__ == "__main__":
    unittest.main()

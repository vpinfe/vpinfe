"""Adding from the Games and Tables grids in a real browser: the menus, the dialog behind
More Collections..., the message each add or removal leaves and its Undo.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import MARK, ManagerWalk, newer, notice

HAND = "Hand Picked"
SMART = "Smart Bally"
GAMES = {"Alpha": ("Bally", "1992"), "Bravo": ("Bally", "1995"),
         "Charlie": ("Williams", "1993"), "Delta": ("Williams", "1980")}

API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")
SHOWN = ("(() => { const out = []; " + API + ".forEachNodeAfterFilterAndSort("
         "n => out.push(n.data.id)); return out; })()")
MENU = ("[...document.querySelectorAll('.q-menu .manager-menu-item')]"
        ".map(e => [e.innerText.replace(/\\n+/g, ' / '),"
        " e.classList.contains('manager-menu-blocked')])")
MENU_OPEN = "!!document.querySelector('.q-menu')"
HAS = "[...document.querySelectorAll(%s)].some(el => el.innerText.includes(%s))"
# The middle of a row's cell, once the grid shows it whole.
CELL_AT = ("(() => { const c = document.querySelector("
           "'.ag-row[row-id=%s] .ag-cell[col-id=\"%s\"]');"
           " const body = document.querySelector('.ag-body-viewport');"
           " if (!c || !body) return null; const r = c.getBoundingClientRect();"
           " const b = body.getBoundingClientRect();"
           " return r.height && r.top >= b.top && r.bottom <= b.bottom"
           " ? [r.left + r.width / 2, r.top + r.height / 2] : null; })()")
# The add box holding what was typed, with an option picked out for Enter: the box has
# filtered for it.
PICKING = ("(typed => { const box = document.activeElement;"
           " const lit = document.querySelector('.q-menu .q-item.q-manual-focusable--focused');"
           " return !!box && box.value === typed && !!lit"
           " && lit.innerText.toLowerCase().includes(typed); })(%s)")
ADD_BOX = ("[...document.querySelectorAll('.manager-section-work .q-select')]"
           ".findIndex(e => e.innerText.includes('Add Games'))")
OPTIONS = ("[...document.querySelectorAll('.q-menu .q-item')].map(e => ["
           "e.innerText.replace(/\\n+/g, ' / '), e.classList.contains('disabled')])")
BOX_AFTER = ("(() => { const a = document.activeElement;"
             " const box = a && a.closest && a.closest('.q-select');"
             " return [!!box && box.innerText.includes('Add Games'),"
             " !!document.querySelector('.q-menu.manager-picker-popup')]; })()")
PANEL_ROWS = ("[...document.querySelectorAll('.manager-section-work .manager-member-row')]"
              ".map(r => [r.innerText.split('\\n').slice(0, 2).join(' / '),"
              " !!r.querySelector('button')])")
INDEX_OF = ("(() => [...document.querySelectorAll(%s)]"
            ".findIndex(el => el.innerText.includes(%s)))()")
ROW_X = ("[...document.querySelectorAll('.manager-section-work .manager-member-row button')]"
         ".findIndex(b => b.closest('.manager-member-row').innerText.includes(%s)"
         " && b.innerText.trim() === 'remove')")
UNDO_AT = ("(() => { const n = [...document.querySelectorAll('.q-notification')]"
           ".find(n => n.innerText.includes(%s)); const b = n && [...n.querySelectorAll("
           "'button')].find(b => b.innerText.includes('Undo')); if (!b) return null;"
           " const r = b.getBoundingClientRect(); return [r.left + r.width / 2,"
           " r.top + r.height / 2]; })()")


class CollectionAddsDrive(unittest.TestCase):
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

        def send(method: str, path: str, body: dict) -> None:
            urllib.request.urlopen(urllib.request.Request(
                instance.manager_url(path), data=json.dumps(body).encode(), method=method,
                headers={"Content-Type": "application/json"}), timeout=10).close()

        instance.post("/api/v1/collections", {"name": HAND, "games": ["bravo", "charlie"]})
        send("PATCH", f"/api/v1/collections/{quote(HAND)}", {"order_by": "manual"})
        instance.post("/api/v1/collections", {"name": SMART,
                                              "filters": {"manufacturer": ["Bally"]}})

        def refs(name: str) -> list:
            return [(one["game"], one["ref_table"], one["origin"]) for one in instance.api(
                f"/api/v1/collections/{quote(name)}/members")["members"]]

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def mouse(kind: str, x: float, y: float, button: str = "left") -> None:
                await browser.send("Input.dispatchMouseEvent",
                                   {"type": kind, "x": x, "y": y, "button": button,
                                    "buttons": 2 if button == "right" else 1,
                                    "clickCount": 1})

            async def right_click(row_id: str, column: str) -> None:
                """The row's menu, once the server has filled it for this row: until
                then it shows what it held last."""
                await browser.evaluate(API + ".ensureNodeVisible(" + API + ".getRowNode("
                                       + json.dumps(row_id) + "), 'middle')")
                x, y = await browser.wait_for(CELL_AT % (json.dumps(row_id), column))

                async def press() -> None:
                    await mouse("mousePressed", x, y, "right")
                    await mouse("mouseReleased", x, y, "right")

                await walk.act(press, mark=MARK, until=newer(".q-menu .manager-menu-item"))

            def click_text(selector: str, text: str) -> Callable[[], Awaitable[None]]:
                async def click() -> None:
                    at = await browser.wait_for(
                        f"(() => {{ const i = "
                        f"{INDEX_OF % (json.dumps(selector), json.dumps(text))};"
                        " return i >= 0 ? i + 1 : 0; })()")
                    await browser.click(selector, nth=int(at) - 1)
                return click

            async def outside() -> None:
                async def press() -> None:
                    await mouse("mousePressed", 1200, 700)
                    await mouse("mouseReleased", 1200, 700)

                if await browser.evaluate(MENU_OPEN):
                    await walk.act(press, until=f"!{MENU_OPEN}")
                else:
                    await press()

            async def said(text: str, action: Callable[[], Awaitable[None]], *,
                           drawn: str = "") -> list:
                """`action`, and the notice it gives holding `text` - and, where the
                server draws something after saying so, `drawn` too."""
                await walk.act(action, mark=MARK,
                               until=notice(text) + (f" && {drawn}" if drawn else ""))
                return await browser.evaluate(notice(text))

            async def undo(text: str) -> None:
                last = None
                for _ in range(30):
                    where = await browser.evaluate(UNDO_AT % json.dumps(text))
                    if where and where == last:
                        break
                    last = where
                    await asyncio.sleep(0.1)

                async def press() -> None:
                    await mouse("mousePressed", where[0], where[1])
                    await mouse("mouseReleased", where[0], where[1])

                await said(t("manager.undo.undone"), press)

            async def pick_from_dialog(name: str, text: str) -> list:
                await walk.act(click_text(".q-menu .manager-menu-item", "Add to Collection"),
                               until=HAS % (json.dumps(".q-menu .manager-menu-item"),
                                            json.dumps("More Collections")))
                await walk.act(click_text(".q-menu .manager-menu-item", "More Collections"),
                               until="!!document.querySelector('.q-dialog"
                                     " .manager-collection-pick')")
                seen.setdefault("dialogs", []).append(await browser.evaluate(
                    "document.querySelector('.q-dialog').innerText"))
                await walk.act(click_text(".q-dialog .manager-source-row", name),
                               until=HAS % (json.dumps(".q-dialog .manager-source-row--chosen"),
                                            json.dumps(name)))
                return await said(text, click_text(".q-dialog button", "Add"))

            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")

            await right_click("delta", "name")
            seen["first_menu"] = await browser.evaluate(MENU)
            seen["added"] = await pick_from_dialog(HAND, "Added to")
            seen["added_refs"] = refs(HAND)

            await right_click("delta", "name")
            seen["again"] = await browser.evaluate(MENU)
            await outside()
            await undo("Added to")
            seen["undone"] = refs(HAND)

            await right_click("charlie", "name")
            seen["exception"] = await pick_from_dialog(SMART, "exception")
            seen["exception_refs"] = refs(SMART)

            await walk.visit("/manager?view=tables")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            await right_click("t-a1", "game")
            seen["table_menu"] = await browser.evaluate(MENU)
            await pick_from_dialog(HAND, "Added to")
            seen["table_refs"] = refs(HAND)

            await walk.visit(f"/manager?view=games&collection={quote(HAND)}")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            seen["narrowed"] = await browser.evaluate(SHOWN)
            await right_click("bravo", "name")
            seen["narrowed_menu"] = await browser.evaluate(MENU)
            seen["removed"] = await said("Removed from", click_text(
                ".q-menu .manager-menu-item", f"Remove from “{HAND}”"))
            seen["removed_refs"] = refs(HAND)
            seen["removed_shown"] = await browser.evaluate(SHOWN)
            await undo("Removed from")
            seen["put_back_refs"] = refs(HAND)
            seen["put_back_shown"] = await browser.evaluate(SHOWN)

            async def key(name: str, code: int, text: str) -> None:
                for kind in ("keyDown", "keyUp"):
                    await browser.send("Input.dispatchKeyEvent", {
                        "type": kind, "key": name, "code": name,
                        "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code,
                        **({"text": text} if kind == "keyDown" and text else {})})
                    await asyncio.sleep(0.05)

            async def typed(letters: str, *, first: str = "") -> None:
                if first:
                    await key(first, 8, "")
                for letter in letters:
                    await key(letter, ord(letter.upper()), letter)

            await walk.visit(f"/manager?view=collections&collection={quote(HAND)}")
            await browser.wait_for("document.body.innerText.includes('Add Games')")
            box = await browser.evaluate(ADD_BOX)

            async def type_a() -> None:
                await browser.click(".manager-section-work .q-select input", nth=box)
                await typed("a")

            await walk.act(type_a, until=PICKING % json.dumps("a"))
            seen["options"] = await browser.evaluate(OPTIONS)
            await walk.act(lambda: typed("delta", first="Backspace"),
                           until=PICKING % json.dumps("delta"))
            await said("Added to", lambda: key("Enter", 13, "\r"),
                       drawn=newer(".manager-section-work .manager-member-row"))
            seen["box_after"] = await browser.evaluate(BOX_AFTER)

            await outside()
            seen["before_x"] = refs(HAND)
            alpha_x = await browser.evaluate(ROW_X % json.dumps("Alpha"))
            seen["x_said"] = await said("Removed from", lambda: browser.click(
                ".manager-section-work .manager-member-row button", nth=alpha_x))
            seen["x_refs"] = refs(HAND)
            await undo("Removed from")
            seen["x_undone"] = refs(HAND)

            send("PUT", f"/api/v1/collections/{quote(SMART)}/excluded/bravo", {"table": ""})
            await walk.visit("/manager?view=games&game=bravo&section=collections")
            await browser.wait_for("document.body.innerText.includes('Add to Collection')")
            seen["game_panel"] = await browser.evaluate(PANEL_ROWS)
            await walk.act(click_text(".manager-section-work button", "Add to Collection"),
                           until="!!document.querySelector('.q-menu .manager-menu-item')")
            seen["game_menu"] = await browser.evaluate(MENU)
            await outside()
            await said(t("manager.workbench.restore_into", name=SMART), lambda: browser.click(
                ".manager-section-work .manager-member-row button",
                nth=[row for row, _b in seen["game_panel"]].index(f"{SMART} / Manually removed")),
                drawn=newer(".manager-section-work .manager-member-row"))
            seen["put_back"] = refs(SMART)

            seen["before_panel_x"] = refs(HAND)
            rows = [row for row, _b in await browser.evaluate(PANEL_ROWS)]
            seen["panel_x_said"] = await said("Removed from", lambda: browser.click(
                ".manager-section-work .manager-member-row button",
                nth=rows.index(f"{HAND} / Manually added")))
            seen["panel_x_refs"] = refs(HAND)
            await undo("Removed from")
            seen["panel_x_undone"] = refs(HAND)
        return seen

    def test_a_first_menu_offers_the_rest_through_a_dialog(self) -> None:
        labels = [label for label, _inert in self.seen["first_menu"]]
        self.assertEqual("Launch", labels[0])
        self.assertEqual("Add to Collection / chevron_right", labels[-2])
        self.assertEqual("Delete from Disk", labels[-1])
        self.assertLess(labels.index("Back Up Now…"), len(labels) - 2)
        self.assertIn("Hand-picked · 2 games", self.seen["dialogs"][0])
        self.assertIn("Smart · 2 games", self.seen["dialogs"][0])

    def test_an_add_into_a_hand_picked_one_is_green_with_undo(self) -> None:
        text, classes = self.seen["added"]
        self.assertIn(f"Added to “{HAND}”", text)
        self.assertIn("Undo", text)
        self.assertIn("bg-positive", classes)
        self.assertIn(("delta", "", "named"), self.seen["added_refs"])

    def test_the_collection_used_last_leads_and_says_when_it_holds_the_row(self) -> None:
        self.assertIn((f"Add to “{HAND}” / In It", True),
                      [tuple(entry) for entry in self.seen["again"]])

    def test_undo_takes_the_add_back(self) -> None:
        self.assertNotIn("delta", [game for game, _t, _o in self.seen["undone"]])

    def test_an_add_into_a_smart_one_is_amber_and_an_exception(self) -> None:
        text, classes = self.seen["exception"]
        self.assertIn(f"Added to “{SMART}” as an exception to its rules", text)
        self.assertIn("settings_suggest", text)
        self.assertIn("bg-warning", classes)
        self.assertIn(("charlie", "", "named"), self.seen["exception_refs"])

    def test_a_table_goes_in_held_to_that_table(self) -> None:
        self.assertIn(("alpha", "t-a1", "named"), self.seen["table_refs"])

    def test_a_grid_narrowed_to_a_collection_offers_to_remove_from_it(self) -> None:
        self.assertIn(f"Remove from “{HAND}”",
                      [label for label, _inert in self.seen["narrowed_menu"]])
        self.assertNotIn("bravo", [game for game, _t, _o in self.seen["removed_refs"]])
        self.assertNotIn("bravo", self.seen["removed_shown"])

    def test_the_add_box_says_maker_and_year_and_ticks_what_is_in_it(self) -> None:
        options = dict((label, held) for label, held in self.seen["options"])
        self.assertIs(False, options.get("Delta / Williams 1980"))
        self.assertIs(True, options.get("Charlie / Williams 1993 / check"))
        # Held to one of its tables is held all the same.
        self.assertIs(True, options.get("Alpha / Bally 1992 / check"))

    def test_the_add_box_stays_open_and_focused_for_the_next(self) -> None:
        self.assertEqual([True, True], self.seen["box_after"])

    def test_a_game_s_panel_shows_where_it_was_removed_and_puts_it_back(self) -> None:
        self.assertIn([f"{SMART} / Manually removed", True], self.seen["game_panel"])
        self.assertIn(("bravo", "", "filter"), self.seen["put_back"])

    def test_a_game_s_panel_offers_every_collection_and_a_new_one(self) -> None:
        self.assertEqual([[f"{HAND} / In It", True],
                          ["settings_suggest / Last Played", False],
                          [f"settings_suggest / {SMART}", False],
                          ["New Collection…", False]], self.seen["game_menu"])

    def test_undoing_a_removal_puts_it_back_in_its_place(self) -> None:
        order = [game for game, _t, origin in self.seen["put_back_refs"] if origin == "named"]
        self.assertEqual("bravo", order[0])
        self.assertIn("bravo", self.seen["put_back_shown"])

    def test_the_x_on_a_row_of_a_list_offers_undo_which_puts_that_row_back(self) -> None:
        text, classes = self.seen["x_said"]
        self.assertIn(f"Removed from “{HAND}”", text)
        self.assertIn("Undo", text)
        self.assertIn("bg-positive", classes)
        self.assertNotIn(("alpha", "t-a1", "named"), self.seen["x_refs"])
        self.assertEqual(self.seen["before_x"], self.seen["x_undone"])

    def test_the_x_in_a_game_s_panel_offers_undo_too(self) -> None:
        text, _classes = self.seen["panel_x_said"]
        self.assertIn(f"Removed from “{HAND}”", text)
        self.assertIn("Undo", text)
        self.assertNotIn("bravo", [game for game, _t, _o in self.seen["panel_x_refs"]])
        self.assertEqual(self.seen["before_panel_x"], self.seen["panel_x_undone"])


if __name__ == "__main__":
    unittest.main()

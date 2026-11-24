"""Rows dragged from the Games and Tables grids onto a collection, in a real browser: what
a drag carries and shows, the grip that starts it, a drop at a place in a collection's list
or anywhere else in its panel, the rail's collections while rows are dragged, and drops
refused - outside a collection, or from another install.

The drag is started for real and intercepted, and its data dropped where the test says,
which is how a drop from another window arrives. Slow: boots a real instance and a real
browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, quote, urlparse

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import MARK, ManagerWalk, newer, notice

HAND = "Hand Picked"
SMART = "Smart Bally"
GAMES = {"Alpha": ("Bally", "1992"), "Bravo": ("Bally", "1995"),
         "Charlie": ("Williams", "1993"), "Delta": ("Williams", "1980"),
         "Echo": ("Williams", "2005"), "Foxtrot": ("Stern", "1999")}
# Rows in the drop that passes a megabyte on the socket; the last one is the only
# one not already in the collection.
MANY = 100_000

API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")
BOX = ("(() => { const el = document.querySelector(%s); if (!el) return null;"
       " const r = el.getBoundingClientRect();"
       " return [r.left + r.width / 2, r.top + r.height / 2, r.top, r.height]; })()")
RAIL = ("[...document.querySelectorAll('.manager-rail-drops .manager-drop-target')]"
        ".map(e => [e.innerText.replace(/\\n+/g, ' / '), e.getBoundingClientRect().height > 0])")
LINE = ("(() => { const line = document.querySelector('.manager-drop-line');"
        " return line ? [line.getBoundingClientRect().width,"
        " line.parentElement.getBoundingClientRect().width] : null; })()")
ELSEWHERE_IN_ROW = ("(() => { const id = %s;"
                    " const home = document.querySelector(`.ag-row[row-id=\"${id}\"]"
                    " .ag-drag-handle`).closest('.ag-row');"
                    " const other = [...document.querySelectorAll(`.ag-row[row-id=\"${id}\"]`)]"
                    ".find(row => row !== home);"
                    " const box = [...other.querySelectorAll('.ag-cell')]"
                    ".map(cell => cell.getBoundingClientRect())"
                    ".reduce((a, b) => (b.left < a.left ? b : a));"
                    " return [box.left + box.width / 2, box.top + box.height / 2]; })()")
HOVERED = "!!document.querySelector('.ag-row-hover[row-id=%s]')"
DRAGGING = "document.body.classList.contains('manager-dragging-rows')"
PINNED_CELLS = "document.querySelectorAll('.ag-pinned-left-cols-container .ag-cell').length"
# How many drops the page has sent the server.
TURN = "window.__hubDropTurn || 0"
RING = ("(() => { const panel = document.querySelector('.manager-workbench');"
        " return [panel.classList.contains('manager-drop-hot'),"
        " getComputedStyle(panel).outlineStyle]; })()")
TOPS = ("[...document.querySelectorAll('[data-drop-list] .manager-member-row')]"
        ".map(row => Math.round(row.getBoundingClientRect().top))")
LINE_AT = ("(() => { const line = document.querySelector('.manager-drop-line');"
           " const rows = [...document.querySelectorAll('[data-drop-list] .manager-member-row')];"
           " return line ? [Math.round(line.getBoundingClientRect().top),"
           " Math.round(rows[rows.length - 1].getBoundingClientRect().bottom)] : null; })()")


class RowDragDrive(unittest.TestCase):
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
        instance.post("/api/v1/collections", {"name": HAND, "games": ["bravo", "charlie"]})
        urllib.request.urlopen(urllib.request.Request(
            instance.manager_url(f"/api/v1/collections/{quote(HAND)}"),
            data=json.dumps({"order_by": "manual"}).encode(), method="PATCH",
            headers={"Content-Type": "application/json"}), timeout=10).close()
        instance.post("/api/v1/collections", {"name": SMART,
                                              "filters": {"manufacturer": ["Bally"]}})

        def refs(name: str) -> list:
            return [(one["game"], one["ref_table"], one["origin"]) for one in instance.api(
                f"/api/v1/collections/{quote(name)}/members")["members"]]

        async with BrowserSession(chromium_path() or "") as browser:
            # The drop below the last row needs room under the list.
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1280, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            caught: list[dict] = []
            heard = browser._on_event

            def hearing(method: str, params: dict) -> None:
                if method == "Input.dragIntercepted":
                    caught.append(params["data"])
                heard(method, params)

            browser._on_event = hearing  # type: ignore[method-assign]
            await browser.send("Input.setInterceptDrags", {"enabled": True})
            walk = ManagerWalk(browser, instance)

            async def mouse(kind: str, x: float, y: float) -> None:
                await browser.send("Input.dispatchMouseEvent",
                                   {"type": kind, "x": x, "y": y, "button": "left",
                                    "buttons": 0 if kind == "mouseReleased" else 1,
                                    "clickCount": 1})

            async def grab(row_id: str, column: str) -> dict:
                """Drag this row by its grip, and answer what the drag carries."""
                await browser.evaluate(API + ".ensureNodeVisible(" + API + ".getRowNode("
                                       + json.dumps(row_id) + "), 'middle')")
                cell = f'.ag-row[row-id="{row_id}"] .ag-cell[col-id="{column}"]'
                x, y, _top, _height = await browser.wait_for(BOX % json.dumps(cell))
                await browser.send("Input.dispatchMouseEvent",
                                   {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
                await browser.wait_for(HOVERED % json.dumps(row_id))
                gx, gy, _top, _height = await browser.evaluate(
                    BOX % json.dumps(cell + " .ag-drag-handle"))
                before = len(caught)
                await mouse("mousePressed", gx, gy)
                for step in range(1, 8):
                    await browser.send("Input.dispatchMouseEvent",
                                       {"type": "mouseMoved", "x": gx + step * 12,
                                        "y": gy + step * 6, "button": "left",
                                        "buttons": 1})
                    await asyncio.sleep(0.04)
                for _ in range(50):
                    if len(caught) > before:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError(f"dragging {row_id} started no drag")
                await mouse("mouseReleased", gx + 84, gy + 42)
                return caught[-1]

            async def let_go(data: dict) -> None:
                """End an intercepted drag nowhere, as dropping outside the page does."""
                await walk.act(lambda: browser.send(
                    "Input.dispatchDragEvent",
                    {"type": "dragCancel", "x": 0, "y": 0, "data": data}),
                    until=f"!{DRAGGING}")

            async def hover(data: dict, x: float, y: float) -> None:
                for kind in ("dragEnter", "dragOver", "dragOver"):
                    await browser.send("Input.dispatchDragEvent",
                                       {"type": kind, "x": x, "y": y, "data": data})
                    await asyncio.sleep(0.15)

            def let_fall(data: dict, x: float, y: float) -> Callable[[], Awaitable[dict]]:
                return lambda: browser.send("Input.dispatchDragEvent",
                                            {"type": "drop", "x": x, "y": y, "data": data})

            async def drop(data: dict, x: float, y: float, text: str, *,
                           drawn: str = "") -> list:
                """`data` let go here, and the notice holding `text` that it gives - and,
                where the panel is drawn again after, `drawn` too."""
                await hover(data, x, y)
                seen.setdefault("lines", []).append(await browser.evaluate(LINE))
                await walk.act(let_fall(data, x, y), mark=MARK,
                               until=notice(text) + (f" && {drawn}" if drawn else ""))
                return await browser.evaluate(notice(text))

            await walk.visit("/manager?view=games")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")

            await browser.evaluate(
                "(() => { const real = DataTransfer.prototype.setDragImage;"
                " DataTransfer.prototype.setDragImage = function (el, x, y) {"
                " window.__dragImage = el.innerText; return real.call(this, el, x, y); };"
                " })()")
            pinned = ".applyColumnState({state: [{colId: 'ag-Grid-SelectionColumn', pinned: %s}]})"
            await walk.act(lambda: browser.evaluate(API + pinned % "'left'"),
                           until=f"{PINNED_CELLS} > 0")
            x, y = await browser.evaluate(ELSEWHERE_IN_ROW % json.dumps("echo"))
            await browser.send("Input.dispatchMouseEvent",
                               {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
            await browser.wait_for(HOVERED % json.dumps("echo"))
            seen["grip_from_elsewhere_in_row"] = await browser.evaluate(
                "getComputedStyle(document.querySelector("
                "'.ag-row[row-id=\"echo\"] .ag-drag-handle')).opacity")
            await walk.act(lambda: browser.evaluate(API + pinned % "null"),
                           until=f"{PINNED_CELLS} === 0")
            lone = await grab("echo", "name")
            seen["image"] = await browser.evaluate("window.__dragImage")
            await let_go(lone)
            spare = await grab("foxtrot", "name")
            await let_go(spare)

            for game in ("alpha", "delta"):
                await browser.evaluate(API + f".getRowNode('{game}').setSelected(true)")
            both = await grab("delta", "name")
            seen["payload"] = {item["mimeType"]: item["data"] for item in both["items"]}
            await let_go(both)
            await browser.evaluate(API + ".deselectAll()")

            await browser.evaluate(MARK)
            one = await grab("delta", "name")
            await browser.wait_for(newer(".manager-rail-drops .manager-drop-target"))
            await walk.drawn()
            seen["rail"] = await browser.evaluate(RAIL)
            target = f'.manager-rail-drops [data-drop-collection="{SMART}"]'
            x, y, _top, _height = await browser.evaluate(BOX % json.dumps(target))
            seen["rail_note"] = await drop(one, x, y, "exception")
            seen["rail_refs"] = refs(SMART)
            seen["rail_after"] = await browser.evaluate(
                "getComputedStyle(document.querySelector('.manager-rail-drops')).display")

            await walk.visit("/manager?view=tables")
            await browser.wait_for(API + ".getDisplayedRowCount() > 0")
            table = await grab("t-a1", "game")
            await let_go(table)

            redrawn = newer("[data-drop-list] .manager-member-row")
            await walk.visit(f"/manager?view=collections&collection={quote(HAND)}")
            await browser.wait_for("document.body.innerText.includes('Add Games')")
            rows = "[data-drop-list] .manager-member-row"
            x, _y, top, _height = await browser.evaluate(
                BOX.replace("querySelector(%s)", "querySelectorAll(%s)[1]")
                % json.dumps(rows))
            seen["placed_note"] = await drop(both, x, top + 4, "Added 2 games", drawn=redrawn)
            seen["placed"] = refs(HAND)

            zone = "[data-drop-collection]"
            x, y, _top, _height = await browser.evaluate(BOX % json.dumps(zone))
            await drop(table, x, y, "Added to", drawn=redrawn)
            seen["table_refs"] = refs(HAND)

            await browser.evaluate(
                "document.addEventListener('dragover', event => { window.__over ="
                " [event.defaultPrevented, event.dataTransfer.dropEffect]; }); true")
            x, y, _top, _height = await browser.evaluate(
                BOX % json.dumps(".manager-section-hit"))
            await hover(lone, x, y)
            seen["ringed_from_rail"] = await browser.evaluate(RING)
            x, _y, top, height = await browser.evaluate(BOX % json.dumps("[data-drop-list]"))
            still = await browser.evaluate(TOPS)
            await hover(lone, x, top + height + 40)
            seen["line_at_end"] = await browser.evaluate(LINE_AT)
            seen["rows_moved"] = still != await browser.evaluate(TOPS)
            await walk.act(let_fall(lone, x, top + height + 40), mark=MARK,
                           until=notice(f"“{HAND}”") + f" && {redrawn}")
            seen["appended"] = refs(HAND)

            x, y, _top, _height = await browser.evaluate(
                BOX % json.dumps("a.manager-nav-row"))
            await hover(spare, x, y)
            seen["outside"] = await browser.evaluate("window.__over")
            seen["ring_outside"] = await browser.evaluate(RING)
            await browser.evaluate(MARK)
            turn = await browser.evaluate(TURN)
            await let_fall(spare, x, y)()
            if await browser.evaluate(TURN) != turn:
                # Sent after all: read the collection once the add it asked for is said.
                await browser.wait_for(notice(f"“{HAND}”"))
            seen["after_outside_drop"] = refs(HAND)
            zone = "[data-drop-collection]"
            x, y, _top, _height = await browser.evaluate(BOX % json.dumps(zone))

            elsewhere = {"items": [{"mimeType": "text/uri-list",
                                    "data": "http://elsewhere.example:8001/manager?view=games"
                                            "&game=bravo"}], "dragOperationsMask": 1}
            seen["refused"] = await drop(elsewhere, x, y, "another VPinFE")

            many = {"items": [{"mimeType": "text/uri-list", "data": "\r\n".join(
                [instance.manager_url("/manager?view=games&game=delta")] * MANY
                + [instance.manager_url("/manager?view=games&game=foxtrot")])}],
                "dragOperationsMask": 1}
            await drop(many, x, y, f"“{HAND}”")
            seen["many"] = refs(HAND)
        return seen

    def test_a_drag_carries_each_row_s_manager_address(self) -> None:
        lines = self.seen["payload"]["text/uri-list"].split("\r\n")
        found = sorted(parse_qs(urlparse(line).query)["game"][0] for line in lines)
        self.assertEqual(["alpha", "delta"], found)
        self.assertTrue(all("view=games" in line for line in lines))
        self.assertEqual(2, len(json.loads(self.seen["payload"]["application/json"])["rows"]))

    def test_the_rail_opens_into_the_collections_while_rows_are_dragged(self) -> None:
        self.assertIn([f"settings_suggest / {SMART}", True], self.seen["rail"])
        self.assertIn([HAND, True], self.seen["rail"])
        self.assertEqual("none", self.seen["rail_after"])

    def test_a_drop_on_the_rail_adds_through_the_one_add(self) -> None:
        text, classes = self.seen["rail_note"]
        self.assertIn(f"Added to “{SMART}” as an exception to its rules", text)
        self.assertIn("bg-warning", classes)
        self.assertIn(("delta", "", "named"), self.seen["rail_refs"])

    def test_the_line_showing_where_a_drop_goes_spans_the_list(self) -> None:
        drawn, across = self.seen["lines"][1]
        self.assertGreater(drawn, 0)
        self.assertGreaterEqual(drawn, across - 8)

    def test_the_grip_shows_whichever_part_of_the_row_is_hovered(self) -> None:
        self.assertEqual("1", self.seen["grip_from_elsewhere_in_row"])

    def test_a_single_row_drags_as_its_name(self) -> None:
        self.assertEqual("Echo", self.seen["image"])

    def test_the_whole_panel_is_ringed_while_a_drag_is_over_it(self) -> None:
        self.assertEqual([True, "solid"], self.seen["ringed_from_rail"])

    def test_a_drop_beside_the_list_lands_at_its_end_and_nothing_moves_meanwhile(self) -> None:
        line_top, last_bottom = self.seen["line_at_end"]
        self.assertLessEqual(abs(line_top - last_bottom), 3)
        self.assertFalse(self.seen["rows_moved"])
        order = [game for game, _t, origin in self.seen["appended"] if origin == "named"]
        self.assertEqual("echo", order[-1])

    def test_a_drag_outside_any_collection_is_refused_rather_than_left_to_the_browser(
            self) -> None:
        self.assertEqual([True, "none"], self.seen["outside"])
        self.assertEqual([False, "none"], self.seen["ring_outside"])
        self.assertNotIn("foxtrot", [game for game, _t, _o in self.seen["after_outside_drop"]])

    def test_a_drop_in_a_list_kept_in_its_order_lands_where_it_was_let_go(self) -> None:
        order = [game for game, _t, origin in self.seen["placed"] if origin == "named"]
        self.assertEqual("bravo", order[0])
        self.assertEqual({"alpha", "delta"}, set(order[1:3]))
        self.assertEqual("charlie", order[3])

    def test_a_dragged_table_goes_in_held_to_that_table(self) -> None:
        self.assertIn(("alpha", "t-a1", "named"), self.seen["table_refs"])

    def test_rows_from_another_install_are_refused(self) -> None:
        text, classes = self.seen["refused"]
        self.assertIn("nothing was added", text)
        self.assertIn("bg-warning", classes)

    def test_a_drop_past_a_megabyte_arrives_whole(self) -> None:
        self.assertGreater(len(json.dumps([["delta", ""]] * MANY)), 1_000_000)
        self.assertIn("foxtrot", [game for game, _t, _o in self.seen["many"]])


if __name__ == "__main__":
    unittest.main()

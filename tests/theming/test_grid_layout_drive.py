"""What a grid writes to `ui-preferences.json`, in a real browser: nothing on a load,
a view switch or a column turned on, and the person's own layout when they change it.
And how wide the name is drawn with art beside it, at a wide window and a narrow one,
and that a column changing place is never drawn over another on the way.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from manager.kit import grid, thumbnails
from manager.library import collections, games
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import MARK, ManagerWalk, newer

NOTHING_WRITTEN_S = grid.SAVE_THROTTLE_S + 0.5
WRITE_LIMIT_S = 30.0

COLLECTION = "Probe"
BY_LINK = (f"/manager?view=collections&collection={quote(COLLECTION)}"
           "&section=collection_details")
SELECTION = "ag-Grid-SelectionColumn"
WIDE, NARROW = (1600, 900), (1024, 768)
API = ("(() => { const el = document.querySelector('.nicegui-aggrid');"
       " return getElement(Number(el.id.slice(1))).api; })()")
PINNED_ON_SCREEN = ("Object.fromEntries(" + API + ".getColumnState()"
                    ".map(s => [s.colId, s.pinned]))")
BODY_RIGHT = "document.querySelector('.ag-body-viewport').getBoundingClientRect().right"
# A point on the handle the grid shows, or null. The last pinned column's handle hangs
# past the pinned area, which clips the half that does.
EDGE_OF = ("(() => { const el = document.querySelector("
           "'.ag-header-cell[col-id=\"%s\"] .ag-header-cell-resize');"
           " if (!el) return null; const box = el.getBoundingClientRect();"
           " return box.left + 2 < " + BODY_RIGHT + ""
           " ? [box.left + 2, box.top + box.height / 2] : null; })()")
# The middle of the part of a header the grid shows, or null.
HEADER_OF = ("(() => { const el = document.querySelector("
             "'.ag-header-cell[col-id=\"%s\"]'); if (!el) return null;"
             " const box = el.getBoundingClientRect();"
             " const right = Math.min(box.right, " + BODY_RIGHT + ");"
             " return box.left < right"
             " ? [(box.left + right) / 2, box.top + box.height / 2] : null; })()")
NAME_DRAWN = ("(() => { const handle = document.querySelector("
              "'.ag-header-cell[col-id=\"name\"] .ag-header-cell-resize');"
              " return {width: " + API + ".getColumn('name').getActualWidth(),"
              " handle: handle && handle.getBoundingClientRect().right,"
              " body: " + BODY_RIGHT + "}; })()")
TABLE_NAME_DRAWN = NAME_DRAWN.replace('col-id="name"', 'col-id="game"').replace(
    "getColumn('name')", "getColumn('game')")
PICK = ("(() => { const i = [...document.querySelectorAll(%s)]"
        ".findIndex(el => el.innerText.trim() === %s); return i >= 0 ? i + 1 : 0; })()")
PICKER = "document.querySelector('.manager-view-picker').innerText"
RESIZED_FLAG = ("(() => { window.__resized = false; " + API + ".addEventListener("
                "'gridSizeChanged', () => { window.__resized = true; }); })()")
# Unpins every column pinned left, as the grid does itself when it has no room for them,
# and watches the frames after it for two header cells drawn over each other. Each cell
# is clipped to the part of the header it shows through.
UNPINNED = ("(async () => { const api = " + API + ";"
            " const pinned = api.getColumnState().filter(s => s.pinned === 'left')"
            ".map(s => s.colId);"
            " const crossed = []; let frames = 0;"
            " const drawn = () => [...document.querySelectorAll('.ag-header-cell')]"
            ".map(el => { const box = el.getBoundingClientRect();"
            " const seen = el.closest('.ag-pinned-left-header, .ag-pinned-right-header,"
            " .ag-header-viewport').getBoundingClientRect();"
            " return {id: el.getAttribute('col-id'), left: Math.max(box.left, seen.left),"
            " right: Math.min(box.right, seen.right)}; })"
            ".filter(one => one.right - one.left > 1).sort((a, b) => a.left - b.left);"
            " const until = performance.now() + 600;"
            " const watched = new Promise(done => { const look = () => { frames += 1;"
            " const shown = drawn();"
            " shown.slice(1).forEach((one, i) => { if (one.left < shown[i].right - 1)"
            " crossed.push(`${shown[i].id} under ${one.id}`); });"
            " if (performance.now() < until) requestAnimationFrame(look); else done(); };"
            " requestAnimationFrame(look); });"
            " api.setColumnsPinned(pinned, null); await watched;"
            " return {pinned, frames, crossed}; })()")


class GridLayoutDrive(unittest.TestCase):
    seen: dict = {}

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
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        instance.post("/api/v1/collections", {"name": COLLECTION, "games": ["alpha"]})
        stored = instance.config_dir / "ui-preferences.json"

        def stamp() -> tuple:
            if not stored.exists():
                return (None, None)
            return (stored.stat().st_mtime_ns, stored.read_bytes())

        def scopes() -> dict:
            return json.loads(stored.read_text())["scopes"] if stored.exists() else {}

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def load(path: str) -> None:
                await walk.visit(path)
                await browser.wait_for(API + ".getDisplayedRowCount() > 0")
                await walk.listen()

            async def unchanged_by(step) -> bool:
                before = stamp()
                await step()
                await asyncio.sleep(NOTHING_WRITTEN_S)
                return stamp() == before

            async def written_by(step) -> None:
                before = stamp()
                await step()
                deadline = time.monotonic() + WRITE_LIMIT_S
                while stamp() == before:
                    if time.monotonic() > deadline:
                        raise AssertionError("nothing was written")
                    await asyncio.sleep(0.05)
                last, still = stamp(), time.monotonic()
                while time.monotonic() - still < NOTHING_WRITTEN_S:
                    if time.monotonic() > deadline:
                        raise AssertionError("the file never stopped changing")
                    await asyncio.sleep(0.05)
                    if stamp() != last:
                        last, still = stamp(), time.monotonic()

            async def window(size: tuple[int, int]) -> None:
                await walk.act(lambda: browser.send(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": size[0], "height": size[1], "deviceScaleFactor": 1,
                     "mobile": False}),
                    mark=RESIZED_FLAG, until=f"window.__resized && innerWidth === {size[0]}")

            def click_text(selector: str, text: str) -> Callable[[], Awaitable[None]]:
                async def click() -> None:
                    at = await browser.wait_for(
                        PICK % (json.dumps(selector), json.dumps(text)))
                    await browser.click(selector, nth=int(at) - 1)
                return click

            async def mouse(kind: str, x: float, y: float, button: str = "left",
                            held: bool = False) -> None:
                await browser.send("Input.dispatchMouseEvent",
                                   {"type": kind, "x": x, "y": y, "button": button,
                                    "buttons": 1 if held else 0, "clickCount": 1})

            async def header_menu(col_id: str) -> bool:
                """The column's menu, once the server has filled it for this column:
                until then it shows what it held last."""
                at = await browser.evaluate(HEADER_OF % col_id)
                if not at:
                    return False

                async def press() -> None:
                    for kind in ("mousePressed", "mouseReleased"):
                        await mouse(kind, *at, button="right")  # type: ignore[misc]

                await walk.act(press, mark=MARK, until=newer(".q-menu .manager-menu-item"))
                return True

            seen["first_load"] = await unchanged_by(
                lambda: load("/manager?view=collections"))
            seen["by_link"] = await unchanged_by(lambda: load(BY_LINK))
            seen["assets"] = await unchanged_by(lambda: load("/manager?view=assets"))

            async def turn_a_column_on() -> None:
                await load("/manager?view=tables")
                await walk.act(lambda: browser.click(".manager-view-menu"),
                               until="!!document.querySelector('.q-menu .q-checkbox')")
                at = await browser.evaluate(
                    "[...document.querySelectorAll('.q-menu .q-checkbox')]"
                    ".findIndex(c => c.getAttribute('aria-checked') === 'false')")
                await walk.act(lambda: browser.click(".q-menu .q-checkbox", nth=int(at)),
                               mark=f"window.__picker = {PICKER}",
                               until=f"{PICKER} !== window.__picker")

            seen["column_on"] = await unchanged_by(turn_a_column_on)
            seen["column_on_picker"] = await browser.evaluate(PICKER)

            await window(WIDE)
            await load("/manager?view=games")
            seen["games_wide"] = await browser.evaluate(NAME_DRAWN)
            if await header_menu("name"):
                await written_by(lambda: browser.click(".q-menu .manager-menu-item"))
            seen["games_pinned"] = scopes()
            await window(NARROW)
            seen["games_narrowed"] = await browser.evaluate(NAME_DRAWN)
            await window(WIDE)
            seen["games_widened"] = await browser.evaluate(NAME_DRAWN)

            await load(BY_LINK)
            seen["wide"] = await browser.evaluate(NAME_DRAWN)

            await window(NARROW)
            seen["narrow"] = await unchanged_by(lambda: load(BY_LINK))
            seen["narrow_on_screen"] = await browser.evaluate(PINNED_ON_SCREEN)
            seen["narrow_drawn"] = await browser.evaluate(NAME_DRAWN)

            before = scopes()
            at = await browser.evaluate(EDGE_OF % "name")
            if at:
                x, y = at

                async def drag() -> None:
                    await mouse("mouseMoved", x, y)
                    await mouse("mousePressed", x, y, held=True)
                    for step in range(1, 9):
                        await mouse("mouseMoved", x - 5 * step, y, held=True)
                        await asyncio.sleep(0.05)
                    await mouse("mouseReleased", x - 40, y)

                await written_by(drag)
            seen["resized"] = (before, scopes())
            seen["resized_on_screen"] = await browser.evaluate(PINNED_ON_SCREEN)

            await window(WIDE)
            if await header_menu("kind"):
                await written_by(click_text(".q-menu .manager-menu-item", "Pin Left"))
            seen["pinned"] = scopes()
            seen["resized_widened"] = await browser.evaluate(NAME_DRAWN)
            seen["reloaded"] = await unchanged_by(lambda: load(BY_LINK))
            seen["resized_reloaded"] = await browser.evaluate(NAME_DRAWN)
            seen["unpinned"] = await browser.evaluate(UNPINNED)

            await window(NARROW)
            await load("/manager?view=tables")
            seen["tables_narrow"] = await browser.evaluate(TABLE_NAME_DRAWN)
        return seen

    def _layout(self, held: dict, scope: str = "manager.collections::") -> dict:
        found = [value for key, value in held.items() if key.startswith(scope)]
        self.assertEqual(1, len(found), sorted(held))
        return found[0]

    def test_a_load_writes_nothing(self) -> None:
        self.assertTrue(self.seen["first_load"])

    def test_opening_a_collection_by_link_writes_nothing(self) -> None:
        self.assertTrue(self.seen["by_link"])

    def test_the_assets_page_writes_no_widths_on_a_load(self) -> None:
        self.assertTrue(self.seen["assets"])

    def test_a_column_turned_on_marks_the_view_modified_and_writes_nothing(self) -> None:
        self.assertTrue(self.seen["column_on"])
        self.assertIn("modified", self.seen["column_on_picker"])

    def test_a_narrow_window_unpins_on_screen_and_writes_nothing(self) -> None:
        self.assertIsNone(self.seen["narrow_on_screen"][SELECTION])
        self.assertTrue(self.seen["narrow"])

    def test_a_wide_window_gives_the_name_the_art_s_whole_room(self) -> None:
        self.assertEqual(self.seen["games_wide"]["width"], _declared(games, True))
        self.assertEqual(self.seen["wide"]["width"], _declared(collections, True))

    def test_a_narrow_window_shows_the_name_s_edge_and_its_handle(self) -> None:
        drawn = self.seen["narrow_drawn"]
        self.assertLessEqual(drawn["handle"], drawn["body"])
        self.assertGreater(drawn["width"], _declared(collections, False))

    def test_the_tables_name_shows_its_edge_and_its_handle_at_a_laptop_width(self) -> None:
        drawn = self.seen["tables_narrow"]
        self.assertLessEqual(drawn["handle"], drawn["body"])

    def test_the_name_never_gives_up_more_than_the_art_s_room(self) -> None:
        self.assertEqual(self.seen["games_narrowed"]["width"], _declared(games, False))

    def test_the_name_takes_the_room_back_when_the_window_widens(self) -> None:
        self.assertEqual(self.seen["games_widened"]["width"], _declared(games, True))

    def test_a_fitted_width_is_not_saved_with_a_pin(self) -> None:
        layout = self._layout(self.seen["games_pinned"], "manager.games.columns::")
        self.assertEqual(["pins"], sorted(layout))

    def test_a_resize_saves_that_width_and_not_the_unpins_a_narrow_window_made(self) -> None:
        before, after = self.seen["resized"]
        self.assertEqual({}, {k: v for k, v in before.items()
                              if k.startswith("manager.collections::")})
        layout = self._layout(after)
        self.assertEqual(["widths"], sorted(layout))
        self.assertEqual(["name"], sorted(layout["widths"]))
        self.assertLess(layout["widths"]["name"], _declared(collections, True))
        self.assertIsNone(self.seen["resized_on_screen"][SELECTION])

    def test_pinning_from_the_header_menu_is_saved(self) -> None:
        layout = self._layout(self.seen["pinned"])
        self.assertEqual({"kind": "left"}, layout["pins"])

    def test_a_dragged_width_stays_through_a_resize_and_a_reload(self) -> None:
        dragged = self._layout(self.seen["resized"][1])["widths"]["name"]
        self.assertEqual(self.seen["resized_widened"]["width"], dragged)
        self.assertEqual(self.seen["resized_reloaded"]["width"], dragged)
        self.assertTrue(self.seen["reloaded"])

    def test_a_column_changing_place_is_never_drawn_over_another(self) -> None:
        unpinned = self.seen["unpinned"]
        self.assertIn("name", unpinned["pinned"])
        self.assertGreater(unpinned["frames"], 5)
        self.assertEqual([], unpinned["crossed"])


def _declared(owner, art: bool) -> int:
    """The width `owner`'s grid declares for the name, with art beside it in the look a
    new install draws, or without."""
    look = thumbnails.Look(thumbnails.SQUARE) if art else None
    return next(one["width"] for one in grid.with_art(owner.COLUMNS, look)
                if one["field"] == "name")


if __name__ == "__main__":
    unittest.main()

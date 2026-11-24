"""A Media fill, in a real browser: the search, the view and the selection are all still
there when it has placed something.

Slow: serves a bare page holding the Media grid over a stand-in library, and drives it
in a real browser. The fill itself is stood in for, so nothing is fetched.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import unittest
from collections.abc import Callable
from typing import Any

from common.i18n import t
from tests.support.browser_session import BrowserSession
from tests.support.manager_walk import ManagerWalk
from tests.theming.test_grid_selection_drive import (
    API,
    HEADER_BOX,
    _heard,
    drive_page,
    exit_with_parent,
)

SEARCH = "Alpha"
FILLED = "alpha1:wheel:"
COUNT_SAYS = t("manager.media.selected", picked="{picked}", shown="{shown}")
# The bar's count is the grid's own: the rows selected and the rows on screen.
COUNTED = ("[...document.querySelectorAll('.manager-grid-bar .manager-label')]"
           ".some(el => el.textContent.trim() === %s"
           f".replace('{{picked}}', {API}.getSelectedNodes().length)"
           f".replace('{{shown}}', {API}.getDisplayedRowCount()))")


class _Page:
    def __init__(self, port: int) -> None:
        self.port = port

    def manager_url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


def _gaps() -> list[dict]:
    """Ten games, each missing a wheel and a backglass: half of them match `SEARCH`."""
    return [{"id": f"{name.lower()}{n}:{kind}:", "game_id": f"{name.lower()}{n}",
             "game": f"{name} {n}", "kind": kind, "label": kind, "present": False}
            for name in (SEARCH, "Beta") for n in range(1, 6)
            for kind in ("wheel", "backglass")]


class _Library:
    def __init__(self) -> None:
        self.found = _gaps()
        self.games = [{"id": row["game_id"], "name": row["game"]} for row in self.found]

    def preferences(self, key: str) -> dict:
        """The grid opens on Have; these rows are all gaps, so the drive opens it on Missing."""
        from manager.kit import views
        from manager.library import media
        if key == media.SCOPE + views.VIEWS_SUFFIX:
            return {"active": views.builtin_id("manager.view.missing")}
        return {}

    def put_preferences(self, _key: str, _value: dict) -> None:
        pass

    def discovery(self) -> dict:
        return {"capabilities": []}

    def forget_media(self, _game_id: str) -> None:
        pass

    def thumbnail(self) -> str:
        return ""

    def thumbnail_look(self) -> None:
        return None

    def kept_kinds(self) -> dict[str, set[str]]:
        from common.media.media_specs import media_label_map
        return {"media": set(media_label_map()), "asset": set()}

    def load_media_rows(self) -> list[dict]:
        return self.found

    def media_rows(self) -> list[dict]:
        return list(self.found)


def serve(port: int) -> None:
    """The Media page, drawn as the Manager draws it."""
    from nicegui import app, ui

    from manager.kit import api, grid
    from manager.library import media, media_fill

    api.local_base_url = lambda: "http://127.0.0.1:9"
    library = _Library()
    heard: dict = {"asked": [], "placed": False}

    async def fill(game_ids: list[str] | None, state: dict[str, Any],
                   then: Callable[[], Any], name: str = "") -> None:
        heard["asked"] = list(game_ids or [])
        library.found = [{**row, "id": "alpha1:wheel:wheel.png", "present": True,
                          "path": "wheel.png"} if row["id"] == FILLED else row
                         for row in library.found]
        answer = then()
        if inspect.isawaitable(answer):
            await answer
        heard["placed"] = True

    async def fill_slots(rows: list[dict[str, Any]], state: dict[str, Any],
                         then: Callable[[], Any]) -> None:
        await fill(list(dict.fromkeys(str(row["game_id"]) for row in rows)), state, then)

    media_fill.ask = fill
    media_fill.confirm_slots = fill_slots

    @ui.page("/")
    async def page() -> None:
        grid.install_filters()
        box = ui.element("div").classes("w-full h-[700px] flex flex-col")

        # After the browser is there, as the Manager draws it: a view put on a grid
        # before then is lost.
        await ui.context.client.connected()
        with box:
            media.build(library.media_rows(), library, lambda _row: None, {})

    @app.get("/heard")
    def said() -> dict:
        return heard

    ui.run(port=port, show=False, reload=False, title="Media fill drive")


class MediaFillDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.seen = drive_page("tests.theming.test_media_fill_drive", cls._drive)

    @classmethod
    async def _drive(cls, binary: str, port: int) -> dict:
        grid_id = ("document.querySelector('.ag-root-wrapper')"
                   ".closest('.nicegui-aggrid').id")
        search = f"input[placeholder={json.dumps(t('manager.media.search_media'))}]"

        async def looking() -> dict:
            return await browser.evaluate(
                "({grid: " + grid_id + ","
                " search: document.querySelector(" + json.dumps(search) + ").value,"
                " quick: " + API + ".getGridOption('quickFilterText') || '',"
                " shown: " + API + ".getDisplayedRowCount(),"
                " filters: " + API + ".getFilterModel(),"
                " selected: " + API + ".getSelectedNodes().map(n => n.id).sort(),"
                " count: [...document.querySelectorAll('.manager-grid-bar .manager-label')]"
                ".map(el => el.textContent.trim())})")

        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, _Page(port))
            await walk.visit("/")
            await browser.wait_for(API + ".getDisplayedRowCount() === 20", timeout=60.0)
            await browser.click(search)
            await browser.send("Input.insertText", {"text": SEARCH})
            await browser.wait_for(API + ".getDisplayedRowCount() === 10")
            await browser.click(HEADER_BOX)
            await browser.wait_for(API + ".getSelectedNodes().length === 10")
            await browser.wait_for(
                "[...document.querySelectorAll('.manager-grid-bar .manager-label')]"
                ".some(el => el.textContent.trim() === "
                f"{json.dumps(t('manager.media.selected', picked=10, shown=10))})")
            before = await looking()
            await browser.evaluate(
                "[...document.querySelectorAll('.manager-grid-bar .q-btn')]"
                ".find(b => b.textContent.includes('more_vert'))"
                ".setAttribute('data-drive', 'actions')")
            await browser.click("[data-drive=actions]")
            await browser.wait_for("!!document.querySelector('.manager-menu-item')")
            await browser.evaluate(
                "[...document.querySelectorAll('.manager-menu-item')].find(el => "
                f"el.textContent.includes({json.dumps(t('manager.media_fill.get_missing'))}))"
                ".setAttribute('data-drive', 'fill')")
            await walk.act(lambda: browser.click("[data-drive=fill]"),
                           until=f"{API}.getDisplayedRowCount() !== {before['shown']}"
                                 f" && {COUNTED % json.dumps(COUNT_SAYS)}")
            return {"before": before, "after": await looking(),
                    "heard": await asyncio.to_thread(_heard, port)}

    def test_the_fill_ran_on_the_games_selected(self) -> None:
        self.assertTrue(self.seen["heard"]["placed"])
        self.assertEqual(self.seen["heard"]["asked"], [f"alpha{n}" for n in range(1, 6)])

    def test_the_grid_is_not_built_again(self) -> None:
        self.assertEqual(self.seen["after"]["grid"], self.seen["before"]["grid"])

    def test_the_search_is_still_applied(self) -> None:
        self.assertEqual(self.seen["after"]["search"], SEARCH)
        self.assertEqual(self.seen["after"]["quick"], SEARCH)

    def test_the_view_still_filters(self) -> None:
        self.assertTrue(self.seen["before"]["filters"])
        self.assertEqual(self.seen["after"]["filters"], self.seen["before"]["filters"])

    def test_the_filled_row_leaves_the_missing_view(self) -> None:
        self.assertEqual(self.seen["after"]["shown"], 9)

    def test_every_row_still_there_is_still_selected(self) -> None:
        self.assertEqual(self.seen["after"]["selected"],
                         [one for one in self.seen["before"]["selected"] if one != FILLED])

    def test_the_count_says_what_is_selected(self) -> None:
        self.assertIn(t("manager.media.selected", picked=9, shown=9),
                      self.seen["after"]["count"])


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        exit_with_parent()
        serve(int(sys.argv[2]))
    else:
        unittest.main()

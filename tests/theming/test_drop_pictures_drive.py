"""A picture dropped on a collection's row, on a collection's panel and on a player's panel
sets that picture and nothing else, and anything that is not one picture is turned away with
the reason.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk
from tests.support.pictures import png

NAME = "Favorites"
OTHER = "Hand Picked"
ROW = ("(() => { const c = document.querySelector('.ag-row[row-id=%s] .ag-cell[col-id=\"name\"]');"
       " if (!c) return null; const r = c.getBoundingClientRect();"
       " return [r.left + r.width / 2, r.top + r.height / 2]; })()")
PANEL = ("(() => { const el = document.querySelector('.manager-workbench');"
         " const r = el.getBoundingClientRect();"
         " return [r.left + r.width / 2, r.top + r.height * 0.75]; })()")
SAVED = t("manager.mediasource.label_saved", label=t("manager.workbench.wheel"))
ALL_GAMES_PANEL = ("(() => { const el = document.querySelector('.manager-workbench');"
                   f" return !!el && !el.innerText.includes({json.dumps(OTHER)}); }})()")
EFFECT_LISTENER = ("(() => { window.__effects = [];"
                   " window.addEventListener('dragover', e => window.__effects.push("
                   "e.dataTransfer && e.dataTransfer.dropEffect), false); })()")
LAST_EFFECT = "window.__effects[window.__effects.length - 1]"
NOTICES = ("[...document.querySelectorAll('.q-notification')].map(n => n.innerText)")
GAMES = ("Alpha", "Bravo")


class DropPicturesDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            for title in GAMES:
                write_game(library, title, info=game_info(title, vps_id="",
                                                          game_id=title.lower()))
            pictures = []
            for number in (1, 2):
                one = Path(tmp) / f"IMG_000{number}.png"
                one.write_bytes(png(800, 800))
                pictures.append(one)
            table = Path(tmp) / "Some Table.vpx"
            table.write_bytes(b"x")
            with LiveInstance(library) as instance:
                cls.seen = asyncio.run(cls._drive(instance, pictures, table))

    @classmethod
    async def _drive(cls, instance: LiveInstance, pictures: list[Path], table: Path) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        instance.post("/api/v1/collections", {"name": NAME})
        instance.post("/api/v1/collections", {"name": OTHER})
        owner = instance.api("/api/v1/players")["players"][0]["id"]

        def held(name: str) -> Any:
            return instance.api(f"/api/v1/collections/{quote(name)}").get("image")

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, instance)

            async def let_go(files: list[Path], x: float, y: float) -> None:
                data = {"items": [], "files": [str(one) for one in files],
                        "dragOperationsMask": 1}
                for kind in ("dragEnter", "dragOver", "drop"):
                    await browser.send("Input.dispatchDragEvent",
                                       {"type": kind, "x": x, "y": y, "data": data})
                    await asyncio.sleep(0.15)

            async def said(text: str) -> bool:
                try:
                    await browser.wait_for(
                        f"{NOTICES}.some(n => n.includes({json.dumps(text)}))")
                except TimeoutError:
                    return False
                return True

            await walk.visit("/manager?view=collections")
            await browser.wait_for(ROW % json.dumps(NAME))

            x, y = await browser.wait_for(ROW % json.dumps(NAME))
            await let_go(pictures, x, y)
            seen["two_said"] = await said(t("error.uploads.picture_file"))
            seen["two_set"] = held(NAME)

            x, y = await browser.wait_for(ROW % json.dumps(NAME))
            await let_go([table], x, y)
            seen["table_said"] = await said(t("error.uploads.not_picture", name=table.name))
            seen["table_set"] = held(NAME)

            x, y = await browser.wait_for(ROW % json.dumps(NAME))
            await let_go(pictures[:1], x, y)
            await said(SAVED)
            seen["row_set"] = held(NAME)
            seen["row_other"] = held(OTHER)

            await walk.visit(f"/manager?view=collections&collection={quote(OTHER)}")
            await browser.wait_for("!!document.querySelector('.manager-workbench')")
            x, y = await browser.wait_for(PANEL)
            await let_go(pictures[1:], x, y)
            await said(SAVED)
            seen["panel_set"] = held(OTHER)

            # The selection moves to All games, which declares nothing, so what the
            # collection's panel declared must be gone: a picture dropped now names no one.
            await browser.click(".ag-row[row-id=\"builtin:all\"] .ag-cell[col-id=\"name\"]")
            await browser.wait_for(ALL_GAMES_PANEL)
            x, y = await browser.wait_for(PANEL)
            await let_go(pictures[:1], x, y)
            seen["all_panel_said"] = await said(t("error.uploads.nothing_recognized"))
            seen["all_panel_moved"] = held(OTHER) != seen["panel_set"]

            await browser.evaluate(EFFECT_LISTENER)
            x, y = await browser.wait_for(ROW % json.dumps("builtin:all"))
            await let_go(pictures[:1], x, y)
            seen["all_row_effect"] = await browser.evaluate(LAST_EFFECT)
            seen["all_row_moved"] = held(OTHER) != seen["panel_set"]

            await walk.visit(f"/manager?view=players&player={owner}")
            await browser.wait_for("!!document.querySelector('.manager-workbench')")
            x, y = await browser.wait_for(PANEL)
            await let_go(pictures[:1], x, y)
            await said(t("manager.players.picture_added"))
            seen["player_set"] = instance.api(f"/api/v1/players/{owner}").get(
                "picture_version")

        return seen

    def test_two_pictures_are_turned_away_with_the_reason(self) -> None:
        self.assertTrue(self.seen["two_said"])
        self.assertFalse(self.seen["two_set"])

    def test_a_table_is_turned_away_with_the_reason(self) -> None:
        self.assertTrue(self.seen["table_said"])
        self.assertFalse(self.seen["table_set"])

    def test_one_picture_on_a_row_sets_only_that_collections_picture(self) -> None:
        self.assertTrue(self.seen["row_set"])
        self.assertFalse(self.seen["row_other"])

    def test_one_picture_on_the_collection_panel_sets_its_picture(self) -> None:
        self.assertTrue(self.seen["panel_set"])

    def test_the_all_games_panel_does_not_take_the_last_collections_declaration(self) -> None:
        self.assertTrue(self.seen["all_panel_said"])
        self.assertFalse(self.seen["all_panel_moved"])

    def test_the_all_games_row_shows_the_no_drop_cursor_and_sets_nothing(self) -> None:
        self.assertEqual(self.seen["all_row_effect"], "none")
        self.assertFalse(self.seen["all_row_moved"])

    def test_one_picture_on_the_player_panel_sets_their_picture(self) -> None:
        self.assertTrue(self.seen["player_set"])


if __name__ == "__main__":
    unittest.main()

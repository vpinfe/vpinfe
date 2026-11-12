"""The Remote aimed at another install writes to that install.

Two live installs, each with its own library. A serves the page and aims it at B, then
rates, favorites and adds to a collection from a game's sheet, and rates the last played
game on Now. Only B's files change. A game both hold under one id is the case to watch: a
write that goes to the serving install lands there without a word.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk
from tests.support.saved import saved_record

TARGET = "Install B"
PLAYED = {"LastRun": "2026-09-01T10:00:00Z", "StartCount": 1}

# The index of the first element under `selector` with a line of text equal to `text`.
# By line, because a Quasar button's text begins with its icon's ligature.
INDEX_OF = ("[...document.querySelectorAll(%s)].findIndex(el => el.innerText.split('\\n')"
            ".map(line => line.trim()).includes(%s))")
SHEET_OPEN = "!!document.querySelector('.remote-sheet')"
# Where the header's pieces sit: the picker's box, the dots' glyph and the window's width.
HEADER = ("(() => { const box = el => el.getBoundingClientRect();"
          " const dots = box(document.querySelector('.remote-dots i, .remote-dots .q-icon'));"
          " const pick = box(document.querySelector('.remote-target'));"
          " return {width: innerWidth, pickLeft: pick.left, pickRight: pick.right,"
          " dotsRight: dots.right}; })()")
MENU = ("(() => { const menu = document.querySelector('.q-menu').getBoundingClientRect();"
        " const pick = document.querySelector('.remote-target').getBoundingClientRect();"
        " return {menuTop: menu.top, pickBottom: pick.bottom}; })()")


def _call(instance: LiveInstance, method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        instance.manager_url(path), method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as handle:
        raw = handle.read()
    return json.loads(raw) if raw else None


def _user(root: Path, name: str) -> dict:
    info = saved_record(root / name / f"{name}.info")
    return info.get("User") or {}


def _stars(root: Path, name: str) -> int:
    """The saved rating in whole stars; the file holds half stars, 0-10."""
    return int(_user(root, name).get("Rating") or 0) // 2


class RemoteWritesToItsTargetDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as a_dir, TemporaryDirectory() as b_dir:
            a_root, b_root = Path(a_dir), Path(b_dir)
            for root in (a_root, b_root):
                write_game(root, "Shared",
                           info=game_info("Shared", vps_id="", game_id="shared-id"))
            write_game(b_root, "Solo",
                       info=game_info("Solo", vps_id="", game_id="solo-id", User=PLAYED))
            with LiveInstance(a_root) as a, LiveInstance(b_root) as b:
                a.wait_for_api()
                b.wait_for_api()
                cls.seen = asyncio.run(cls._drive(a, b, a_root, b_root))

    @classmethod
    async def _drive(cls, a: LiveInstance, b: LiveInstance,
                     a_root: Path, b_root: Path) -> dict:
        b_id = _call(b, "GET", "/api/v1")["install_id"]
        _call(a, "PUT", "/api/v1/devices",
              {"device_id": b_id, "kind": "vpinfe", "display_name": TARGET,
               "features": ["frontend"], "port": b.ports["manager"]})
        _call(a, "POST", "/api/v1/collections", {"name": "Hand A", "games": []})
        _call(b, "POST", "/api/v1/collections", {"name": "Hand B", "games": []})
        seen: dict = {}

        async with BrowserSession(chromium_path() or "") as browser:
            walk = ManagerWalk(browser, a)

            async def click_text(selector: str, text: str) -> None:
                at = await browser.wait_for(
                    f"(() => {{ const i = {INDEX_OF % (json.dumps(selector), json.dumps(text))};"
                    " return i >= 0 ? i + 1 : 0; })()")
                await browser.click(selector, nth=int(at) - 1)

            async def open_sheet(name: str) -> None:
                # Where the target's frontend is up, the first tap moves its wheel and the
                # next one opens the sheet.
                for _attempt in range(3):
                    await click_text(".remote-row", name)
                    try:
                        await browser.wait_for(SHEET_OPEN, timeout=4)
                    except TimeoutError:
                        continue
                    await walk.drawn()
                    return
                raise AssertionError(f"no sheet opened for {name}")

            async def rated(root: Path, name: str, want: int) -> None:
                """The write has landed, and the sheet it was made on is still open."""
                for _attempt in range(100):
                    if _stars(root, name) == want:
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError(f"{name} was not rated {want}: {_user(root, name)}")
                await browser.wait_for(SHEET_OPEN)

            async def sheet_closed() -> None:
                try:
                    await browser.wait_for("!document.querySelector('.remote-sheet')")
                except TimeoutError:
                    said = await browser.evaluate(
                        "[...document.querySelectorAll('.q-notification')]"
                        ".map(n => n.innerText).join(' | ')")
                    raise AssertionError(f"the sheet stayed open; notified: {said!r}; "
                                         f"B's Solo: {_user(b_root, 'Solo')}") from None
                await walk.drawn()

            await walk.visit("/remote?screen=games")
            seen["header"] = await browser.evaluate(HEADER)
            await browser.click(".remote-target")
            await browser.wait_for("!!document.querySelector('.q-menu .q-item')")
            seen["menu"] = await browser.evaluate(MENU)
            await click_text(".q-menu .q-item", TARGET)
            await browser.wait_for("document.body.innerText.includes('Solo')")

            await open_sheet("Solo")
            await browser.click(".remote-sheet .manager-star", nth=3)
            await rated(b_root, "Solo", 4)
            seen["solo rated"] = _stars(b_root, "Solo")

            # A rating leaves the sheet where it is.
            await click_text(".remote-sheet button", t("word.favorite"))
            await sheet_closed()
            seen["solo favorite"] = _user(b_root, "Solo").get("Favorite")

            await open_sheet("Solo")
            await click_text(".remote-sheet button", t("manager.remote.add_collection"))
            await click_text(".q-menu .manager-menu-item", "Hand B")
            await sheet_closed()
            seen["hand b"] = [one.get("id") or one.get("game_id") for one in
                              _call(b, "GET", "/api/v1/collections/Hand%20B/games")
                              .get("games", [])]

            await open_sheet("Shared")
            await browser.click(".remote-sheet .manager-star", nth=1)
            await rated(b_root, "Shared", 2)
            await browser.escape()
            await sheet_closed()
            seen["shared on b"] = _stars(b_root, "Shared")
            seen["shared on a"] = _stars(a_root, "Shared")

            # The tab, not a reload: a fresh page is aimed at the install serving it again.
            await click_text(".remote-tab", t("word.now"))
            await browser.wait_for("!!document.querySelector('.manager-stars')")
            await walk.drawn()
            await browser.click(".manager-stars .manager-star", nth=4)
            await walk.drawn()
            seen["solo from now"] = _stars(b_root, "Solo")
        seen["hand a"] = _call(a, "GET", "/api/v1/collections/Hand%20A/games").get("games")
        return seen

    def test_a_rating_on_the_sheet_lands_on_the_target(self) -> None:
        self.assertEqual(self.seen["solo rated"], 4)

    def test_the_picker_fills_the_header_and_the_dots_sit_in_its_corner(self) -> None:
        header = self.seen["header"]
        self.assertGreater(header["pickRight"] - header["pickLeft"], header["width"] * 0.5,
                           header)
        self.assertGreater(header["dotsRight"], header["width"] - 24, header)

    def test_the_picker_opens_under_its_box(self) -> None:
        menu = self.seen["menu"]
        self.assertLess(abs(menu["menuTop"] - menu["pickBottom"]), 12, menu)

    def test_a_favorite_lands_on_the_target(self) -> None:
        self.assertTrue(self.seen["solo favorite"])

    def test_an_add_goes_to_the_targets_collection(self) -> None:
        self.assertIn("solo-id", self.seen["hand b"])
        self.assertEqual(self.seen["hand a"], [])

    def test_a_game_both_hold_is_rated_only_where_the_phone_is_aimed(self) -> None:
        self.assertEqual(self.seen["shared on b"], 2)
        self.assertFalse(self.seen["shared on a"])

    def test_the_last_played_rating_on_now_lands_on_the_target(self) -> None:
        self.assertEqual(self.seen["solo from now"], 5)


if __name__ == "__main__":
    unittest.main()

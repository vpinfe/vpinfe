"""Protects: (a) a setting changed in a table's Add or Change Settings reaches the table's own file
and shows in its list, a Point of View view mode is offered there, and the revert icon takes it out.

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

INI = """[Player]
; AAFactor: Supersampling [Default: 1.0]
AAFactor = 1.0
; Anaglyph1sRGB: Anaglyph sRGB [Default: 0]
Anaglyph1sRGB = 0

[TableOverride]
; ViewDTMode: View Mode
ViewDTMode = 1
; ViewDTFOV: Field of View
ViewDTFOV = 0
"""
WRITE_WAIT_S = 20.0
KEY = "Player.Anaglyph1sRGB"
VIEW = "TableOverride.ViewDTMode"
CLICK = ("[...document.querySelectorAll('.q-btn')]"
         ".filter(b => b.innerText.replace(/^\\w+\\n/, '').trim() === '%s').pop().click()")


ROW = ("[...document.querySelectorAll('.q-dialog .manager-section-row')]"
       ".find(row => row.innerText.trim() === '%s').click()")
PICK = ("[...document.querySelectorAll('.q-menu .q-item')]"
        ".find(i => i.innerText.trim().startsWith('%s')).click()")


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class TableAllSettingsDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        assert binary is not None
        with TemporaryDirectory() as tmp, TemporaryDirectory() as program:
            tables = {"t1": {"id": "t1", "filename": "Alpha.vpx"}}
            folder = write_game(Path(tmp), "Alpha", info=game_info(
                "Alpha", vps_id="", game_id="alpha", tables=tables))
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance, folder, Path(program), binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance, folder: Path, program: Path,
                     binary: str) -> dict:
        instance.wait_for_api()
        ini = program / "VPinballX.ini"
        ini.write_text(INI)
        bin_path = program / "VPinballX_BGFX"
        bin_path.write_text("#!/bin/sh\nexit 0\n")
        bin_path.chmod(0o755)
        listing = json.load(urllib.request.urlopen(
            instance.manager_url("/api/v1/config/launchers"), timeout=10))
        launcher = next(one["id"] for one in listing if one["app"] == "vpx")
        body = {"id": launcher, "app": "vpx", "enabled": True, "name": "Drive",
                "settings": {"bin_path": str(bin_path), "ini_path": str(ini)}}
        urllib.request.urlopen(urllib.request.Request(
            instance.manager_url(f"/api/v1/config/launchers/{launcher}"),
            data=json.dumps(body).encode(), method="PUT",
            headers={"Content-Type": "application/json"}), timeout=10).close()
        seen: dict = {}
        async with BrowserSession(binary) as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1700, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)
            await walk.visit("/manager?view=tables&game=alpha&table=t1&section=table_settings")
            await walk.drawn()

            async def text() -> str:
                return await browser.evaluate("document.body.innerText")

            async def table_file() -> str:
                files = sorted(folder.glob("*.ini"))
                return files[0].read_text() if files else ""

            await browser.wait_for(
                "document.body.innerText.includes('Add or Change Settings')", timeout=90.0)
            await browser.evaluate(CLICK % "Add or Change Settings")
            await browser.wait_for(f"!!document.querySelector('[data-setting=\"{KEY}\"]')")
            await browser.click(f'[data-setting="{KEY}"] .q-toggle')
            waited = 0.0
            while "Anaglyph1sRGB" not in await table_file() and waited < WRITE_WAIT_S:
                await asyncio.sleep(0.05)
                waited += 0.05
            seen["file"] = await table_file()
            await browser.wait_for(
                f"!!document.querySelector('.q-dialog [data-setting=\"{VIEW}\"]')")
            seen["dialog_text"] = await browser.evaluate(
                "document.querySelector('.q-dialog .manager-section-work').innerText")
            seen["rail"] = await browser.evaluate(
                "[...document.querySelectorAll('.q-dialog .manager-section-row')]"
                ".map(row => row.innerText.trim())")
            await browser.evaluate(ROW % "Point of View")
            await browser.wait_for(
                "!document.querySelector('.q-dialog .q-checkbox')"
                f" && !!document.querySelector('.q-dialog [data-setting=\"{VIEW}\"]')")
            seen["view_page"] = await browser.evaluate(
                "document.querySelector('.q-dialog .manager-section-work').innerText")
            await browser.click(f'.q-dialog [data-setting="{VIEW}"] .q-select')
            await browser.wait_for("!!document.querySelector('.q-menu .q-item')")
            await browser.evaluate(PICK % "Window")
            waited = 0.0
            while "ViewDTMode" not in await table_file() and waited < WRITE_WAIT_S:
                await asyncio.sleep(0.05)
                waited += 0.05
            seen["view_file"] = await table_file()
            await browser.evaluate(CLICK % "Done")
            await browser.wait_for("!document.querySelector('.q-dialog')")
            await browser.wait_for(f"!!document.querySelector('[data-setting=\"{KEY}\"]')")
            await walk.drawn()
            seen["listed"] = await text()
            seen["launcher_file"] = ini.read_text()
            seen["view_listed"] = await browser.evaluate(
                f"!!document.querySelector('[data-setting=\"{VIEW}\"] [aria-label=\"Reset\"]')")
            await browser.click(f'[data-setting="{VIEW}"] [aria-label="Reset"]')
            waited = 0.0
            while "ViewDTMode" in await table_file() and waited < WRITE_WAIT_S:
                await asyncio.sleep(0.05)
                waited += 0.05
            seen["view_after_reset"] = await table_file()
            await walk.drawn()
            await browser.click(f'[data-setting="{KEY}"] [aria-label="Reset"]')
            waited = 0.0
            while "Anaglyph1sRGB" in await table_file() and waited < WRITE_WAIT_S:
                await asyncio.sleep(0.05)
                waited += 0.05
            await walk.drawn()
            seen["after_reset"] = await table_file()
        return seen

    def test_the_change_reached_the_tables_own_file_only(self) -> None:
        self.assertIn("Anaglyph1sRGB = 1", self.seen["file"])
        self.assertEqual(self.seen["launcher_file"], INI)

    def test_the_dialog_offers_the_launchers_areas_then_all_settings(self) -> None:
        self.assertEqual(self.seen["rail"][-2:], ["Point of View", "All Settings"])
        self.assertGreater(len(self.seen["rail"]), 2)

    def test_the_point_of_view_area_holds_the_view_modes(self) -> None:
        self.assertIn("Desktop View Mode", self.seen["view_page"])

    def test_the_dialog_names_a_view_mode_as_the_panel_does(self) -> None:
        self.assertIn("Desktop View Mode", self.seen["dialog_text"])
        self.assertIn("Desktop View Mode", self.seen["listed"])

    def test_the_dialog_heads_a_view_mode_with_the_panels_heading(self) -> None:
        said = self.seen["dialog_text"].lower()

        self.assertEqual(said.splitlines().count("point of view"), 1)
        self.assertNotIn("table override", said)

    def test_a_view_mode_is_set_in_the_dialog_and_the_revert_icon_clears_it(self) -> None:
        self.assertIn("ViewDTMode = 2", self.seen["view_file"])
        self.assertTrue(self.seen["view_listed"])
        self.assertNotIn("ViewDTMode", self.seen["view_after_reset"])

    def test_the_list_shows_what_the_table_holds_and_reset_takes_it_out(self) -> None:
        self.assertIn("Anaglyph sRGB", self.seen["listed"])
        self.assertNotIn("Anaglyph1sRGB", self.seen["after_reset"])


if __name__ == "__main__":
    unittest.main()

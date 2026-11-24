"""A write on Visual Pinball X's All Settings view leaves the page as it was, in a real browser.

Slow: boots a real instance and a real browser. Every row is marked before a write and
counted after it, so a page drawn again shows as rows that lost the mark.
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

VPX = "vpx-drive"
INI = """[Player]
; PlayfieldDisplay: PlayfieldDisplay [Default: '']
PlayfieldDisplay =
; Anaglyph1sRGB: Anaglyph1sRGB [Default: 0]
Anaglyph1sRGB = 0
; AlphaRampAccuracy: AlphaRampAccuracy [Default: 0]
AlphaRampAccuracy = 0
; AAFactor: AAFactor [Default: 1.0]
AAFactor = 1.0
; Anaglyph1Filter: Anaglyph1Filter [Default: 0, 0='One', 1='Two']
Anaglyph1Filter = 0
[Backglass]
; BackglassDisplay: BackglassDisplay [Default: '']
BackglassDisplay =
[Plugin.B2S]
; Enable: Enable [Default: 1]
Enable = 1
[Plugin.B2SLegacy]
; Enable: Enable [Default: 0]
Enable = 0
"""
LOG = 'Using display "Built-in Retina Display".\n'

REDRAW_WINDOW_S = 1.5  # a rebuild draws within a second of the write that asks for it
WRITE_WAIT_S = 20.0
ROWS = ".manager-field-line"
MARK = (f"(() => {{ const rows = document.querySelectorAll('{ROWS}'); "
        f"rows.forEach(el => {{ el.dataset.sentinel = '1'; }}); return rows.length; }})()")
KEPT = f"document.querySelectorAll('{ROWS}[data-sentinel=\"1\"]').length"
CONFLICT = "document.body.innerText.toLowerCase().includes('conflict')"


def _row(key: str, control: str) -> str:
    return f'[data-setting="{key}"] {control}'


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class VpxSettingsInPlaceDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        assert binary is not None
        with TemporaryDirectory() as tmp, TemporaryDirectory() as program, \
                LiveInstance(Path(tmp)) as instance:
            write_game(Path(tmp), "Alpha", info=game_info("Alpha", vps_id="", game_id="alpha"))
            cls.seen = asyncio.run(cls._drive(instance, Path(program), binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance, program: Path, binary: str) -> dict:
        instance.wait_for_api()
        ini = program / "VPinballX.ini"
        ini.write_text(INI)
        bin_path = program / "VPinballX_BGFX"
        bin_path.write_text("#!/bin/sh\nexit 0\n")
        bin_path.chmod(0o755)
        log = instance.home / "Library" / "Application Support" / "VPinballX" / "vpinball.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(LOG)
        body = {"id": VPX, "app": "vpx", "enabled": True, "name": "Drive",
                "settings": {"bin_path": str(bin_path), "ini_path": str(ini)}}
        urllib.request.urlopen(urllib.request.Request(
            instance.manager_url(f"/api/v1/config/launchers/{VPX}"),
            data=json.dumps(body).encode(), method="PUT",
            headers={"Content-Type": "application/json"}), timeout=10).close()
        seen: dict = {"kept": {}, "conflict": {}}
        async with BrowserSession(binary) as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1700, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)
            await walk.visit(f"/manager?view=launchers&launcher={VPX}")
            async for name in walk.sections():
                if name == "All Settings":
                    break
            await walk.drawn()

            async def write(label: str, do) -> None:
                marked = await browser.evaluate(MARK)
                before = ini.read_text()
                await do()
                waited = 0.0
                while ini.read_text() == before and waited < WRITE_WAIT_S:
                    await asyncio.sleep(0.05)
                    waited += 0.05
                await asyncio.sleep(REDRAW_WINDOW_S)
                await walk.drawn()
                seen["kept"][label] = (await browser.evaluate(KEPT), marked)
                seen["conflict"][label] = await browser.evaluate(CONFLICT)

            async def switch(key: str) -> None:
                await browser.click(_row(key, ".q-toggle"))

            async def pick(key: str, which: str = "") -> None:
                await browser.click(_row(key, ".q-select"))
                await browser.wait_for("!!document.querySelector('.q-menu .q-item')")
                await browser.click(f".q-menu .q-item{which}")

            async def type_into(key: str, text: str) -> None:
                await browser.click(_row(key, "input"))
                await browser.press("a", "KeyA")
                await browser.send("Input.insertText", {"text": text})
                await browser.press("Tab", "Tab")

            await write("switch", lambda: switch("Player.Anaglyph1sRGB"))
            await write("display", lambda: pick("Player.PlayfieldDisplay"))
            await write("backglass display", lambda: pick("Backglass.BackglassDisplay"))
            await write("choice", lambda: pick("Player.Anaglyph1Filter", ":nth-child(2)"))
            await write("int", lambda: type_into("Player.AlphaRampAccuracy", "7"))
            await write("number", lambda: type_into("Player.AAFactor", "2"))
            await write("rival on", lambda: switch("Plugin.B2SLegacy.Enable"))
            await write("rival off", lambda: switch("Plugin.B2SLegacy.Enable"))
        seen["ini"] = ini.read_text()
        return seen

    def test_every_write_leaves_every_row_drawn(self) -> None:
        for label, (kept, marked) in self.seen["kept"].items():
            with self.subTest(label):
                self.assertGreater(marked, 0)
                self.assertEqual(kept, marked)

    def test_the_writes_reached_the_file(self) -> None:
        for line in ("PlayfieldDisplay = Built-in Retina Display",
                     "BackglassDisplay = Built-in Retina Display",
                     "Anaglyph1sRGB = 1", "Anaglyph1Filter = 1", "AlphaRampAccuracy = 7"):
            with self.subTest(line):
                self.assertIn(line, self.seen["ini"])

    def test_a_rival_switched_on_says_so_and_switched_off_stops(self) -> None:
        self.assertTrue(self.seen["conflict"]["rival on"])
        self.assertFalse(self.seen["conflict"]["rival off"])


if __name__ == "__main__":
    unittest.main()

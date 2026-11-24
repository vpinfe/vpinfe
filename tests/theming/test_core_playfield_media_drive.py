"""Playfield art that core draws into a `data-vpin-media` element, turned to fill the surface.

Opened in a real browser because the answer is geometry: the box the art ends up in after
the quarter turn, against the screen it has to cover, in each of the three cabinet setups.
"""

from __future__ import annotations

import asyncio
import json
import struct
import unittest
import zlib

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + b"\x80\x40\xc0" * width
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height))
            + chunk(b"IEND", b""))


LANDSCAPE = _png(160, 90)
PORTRAIT = _png(90, 160)

# The page a theme would write: the outermost element is the one that turns in setup B.
STAGE = """
(() => {
  const style = document.createElement('style');
  style.textContent = 'body{margin:0} .vpinfe-upright{width:100vw;height:100vh;overflow:hidden}'
    + ' .stage{position:relative;width:100%;height:100%}';
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/core/common/vpinfe-style.css';
  document.head.append(link, style);
  document.body.insertAdjacentHTML('beforeend',
    '<div class="vpinfe-upright"><div class="stage" data-vpin-media="playfield"></div></div>');
  return new Promise((done) => link.addEventListener('load', () => done(true)));
})()
"""

SHOW = ("document.querySelector('[data-vpin-media]').replaceChildren(),"
        " vpin.handleEvent({type: 'TableIndexUpdate', index: %d}), true")

# The art's box on screen once the quarter turn is applied.
MEASURE = """
(() => {
  const node = document.querySelector('[data-vpin-media] .vpinfe-playfield-media');
  if (!node || node.dataset.vpinfeTurned === undefined) return null;
  const box = node.getBoundingClientRect();
  return {turned: node.dataset.vpinfeTurned, width: box.width, height: box.height,
          left: box.left, top: box.top,
          screen: [innerWidth, innerHeight]};
})()
"""

SETUPS = {
    # The OS turned a portrait monitor, so the window is already tall.
    "os_rotated_portrait": dict(viewport=(600, 1000), orientation="portrait", rotation="0"),
    # VPinFE turns the page: the window is wide and the player sees it a quarter turn round.
    "app_rotated_portrait": dict(viewport=(1000, 600), orientation="portrait", rotation="90"),
    "landscape": dict(viewport=(1000, 600), orientation="landscape", rotation="0"),
}


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class CorePlayfieldMediaDriveTests(TempTree):
    BOOTS_PER_TEST = "One instance per setup, driving both pictures through it."

    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Name": "x"}, "VPinFE": {}, "User": {}}
        write_game(self.root, "Alpha Landscape", info={**info, "Info": {"Name": "Alpha Landscape"}},
                   medias={"table.png": LANDSCAPE, "wheel.png": LANDSCAPE})
        write_game(self.root, "Beta Portrait", info={**info, "Info": {"Name": "Beta Portrait"}},
                   medias={"table.png": PORTRAIT, "wheel.png": LANDSCAPE})

    def _measure(self, setup: dict) -> list[dict]:
        async def run(instance: LiveInstance):
            theme = instance.config_dir / "frontend_themes" / instance.theme
            (theme / "config.json").write_text(json.dumps({"layout": {"enabled": True}}))
            width, height = setup["viewport"]
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.send("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
                await browser.navigate(instance.theme_url("playfield"))
                await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                await browser.evaluate(STAGE)
                found = []
                for index in (0, 1):
                    await browser.evaluate(SHOW % index)
                    found.append(await browser.wait_for(MEASURE, timeout=30.0))
                return found

        settings = {("windows.playfield", "orientation"): setup["orientation"],
                    ("windows.playfield", "rotation"): setup["rotation"]}
        with LiveInstance(self.root, extra_settings=settings) as instance:
            return asyncio.run(run(instance))

    def test_the_art_covers_the_screen_in_every_setup(self) -> None:
        for name, setup in SETUPS.items():
            with self.subTest(setup=name):
                for art, found in zip(("landscape", "portrait"), self._measure(setup), strict=True):
                    screen_w, screen_h = found["screen"]
                    self.assertAlmostEqual(found["width"], screen_w, delta=2,
                                           msg=f"{art} art, {found}")
                    self.assertAlmostEqual(found["height"], screen_h, delta=2,
                                           msg=f"{art} art, {found}")


if __name__ == "__main__":
    unittest.main()

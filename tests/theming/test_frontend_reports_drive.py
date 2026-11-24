"""What a real page tells the host about its browser and its window, end to end.

The host and JS halves each have a test of their own. What only a browser shows is that the
page sends the reports at all, in the shape the host accepts, and that they are written to the
files a support package reads.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.live_instance import LiveInstance

from .test_render_smoke import _needs_a_browser, _Smoke, _write_library

WAIT_S = 30.0
POLL_S = 0.2


async def _held(instance: LiveInstance, name: str, ready: Any) -> Any:
    """What the instance kept in `name` once `ready` accepts it, or whatever it held last."""
    path = instance.config_dir / "cache" / name
    deadline = asyncio.get_event_loop().time() + WAIT_S
    held: Any = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            held = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = None
        if held is not None and ready(held):
            break
        await asyncio.sleep(POLL_S)
    return held


@_needs_a_browser
class FrontendReportsDriveTests(_Smoke):
    shared: LiveInstance

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        _write_library(Path(tmp.name))
        cls.shared = cls.enterClassContext(LiveInstance(Path(tmp.name)))

    def _instance(self) -> LiveInstance:
        return self.shared

    def test_the_controller_window_reports_what_its_browser_can_do(self) -> None:
        async def run(instance: LiveInstance) -> Any:
            async with BrowserSession(chromium_path() or "") as browser:
                await self._open(browser, instance, "playfield")
                return await _held(instance, "frontend_browser.json",
                                   lambda held: bool(held.get("features")))

        held = asyncio.run(run(self.shared))
        self.assertIsNotNone(held, "the page never reported its browser")
        features = held["features"]
        self.assertIn(features["webgl"], (True, False, None))
        self.assertIn("h264", features["decodes"])
        self.assertIsInstance(features["took_ms"], int)

    def test_a_window_reports_where_it_opened(self) -> None:
        async def run(instance: LiveInstance) -> Any:
            async with BrowserSession(chromium_path() or "") as browser:
                await self._open(browser, instance, "playfield")
                await _held(instance, "frontend_windows.json", lambda held: "playfield" in held)
                await self._open(browser, instance, "backglass")
                return await _held(instance, "frontend_windows.json",
                                   lambda held: "backglass" in held)

        held = asyncio.run(run(self.shared))
        self.assertIsNotNone(held, "no window reported")
        self.assertEqual(sorted(held), ["backglass", "playfield"])
        for name in held:
            self.assertGreater(held[name]["width"], 0, name)
            self.assertGreater(held[name]["height"], 0, name)


if __name__ == "__main__":
    unittest.main()

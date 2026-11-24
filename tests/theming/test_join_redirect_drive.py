"""2.x's own page keys - the ones a bookmark or a cabinet's manager-ui-state.json may
still carry - open the Remote's Join screen now, not the Manager on whatever page was
last open.

Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.live_instance import LiveInstance

LOCATION = "window.location.pathname + window.location.search"


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class JoinRedirectDrive(unittest.TestCase):
    landed: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        with TemporaryDirectory() as tmp:
            with LiveInstance(Path(tmp)) as instance:
                instance.wait_for_api()
                cls.landed = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict[str, str]:
        landed: dict[str, str] = {}
        async with BrowserSession(chromium_path() or "") as browser:
            for key in ("vpinplay_account", "vpinplay_player"):
                await browser.navigate(instance.manager_url(f"/?page={key}"))
                landed[key] = await browser.wait_for(
                    f"(({LOCATION}).startsWith('/remote') ? ({LOCATION}) : false)")
        return landed

    def test_the_2x_account_page_key_opens_join(self) -> None:
        self.assertEqual(self.landed["vpinplay_account"], "/remote?screen=join")

    def test_the_2x_player_page_key_opens_join(self) -> None:
        self.assertEqual(self.landed["vpinplay_player"], "/remote?screen=join")

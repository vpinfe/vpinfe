"""The open device's panel says what the probe found, on a fresh load of Devices.

The panel is already drawn when the first answer lands, so it has to be redrawn from the
probe's own task. Slow: boots a real instance and a real browser.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

# The panel's State by its word, and the grid's cell by the tick it draws, which carries the
# word in its tooltip.
PANEL = "[...document.querySelectorAll('.manager-tier')].map(el => el.innerText.trim())"
GRID = ("[...document.querySelectorAll('.ag-cell[col-id=state] .manager-state')]"
        ".map(el => el.title.split(' - ')[0])")
PANEL_ANSWERS = f"{GRID}.includes('Answering') && {PANEL}.includes('Answering')"
CHIPS = "[...document.querySelectorAll('.manager-chips .manager-tier')]"
FEATURES = f"{CHIPS}.map(el => el.innerText.trim())"
LIT = f"{CHIPS}.filter(el => el.classList.contains('manager-tier--on')).length"
FACTS = "document.querySelector('.manager-workbench-body').innerText"


class DevicePanelReachDrive(unittest.TestCase):
    answered = False
    features: list[str] = []
    facts = ""
    lit = 0

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp, LiveInstance(Path(tmp)) as instance:
            asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> None:
        instance.wait_for_api()
        async with BrowserSession(chromium_path() or "") as browser:
            await ManagerWalk(browser, instance).visit("/manager?view=devices")
            await browser.wait_for(PANEL_ANSWERS)
            cls.answered = True
            cls.features = await browser.evaluate(FEATURES)
            cls.facts = await browser.evaluate(FACTS)
            cls.lit = await browser.evaluate(LIT)

    def test_the_open_panel_says_answering_once_the_device_answers(self) -> None:
        self.assertTrue(self.answered)

    def test_every_feature_is_a_chip_in_the_panel(self) -> None:
        self.assertEqual(self.features,
                         ["Library", "Frontend", "Device Management", "Overview"])

    def test_every_feature_the_device_has_is_lit(self) -> None:
        self.assertEqual(self.lit, 4)

    def test_the_chips_say_where_features_are_turned_on(self) -> None:
        self.assertIn("Turned on in this device's Settings", self.facts)

    def test_the_details_tab_has_an_address_and_none_of_the_removed_rows(self) -> None:
        self.assertIn("Address", self.facts)
        for gone in ("Announced", "This entry", "Kind"):
            self.assertNotIn(gone, self.facts)


if __name__ == "__main__":
    unittest.main()

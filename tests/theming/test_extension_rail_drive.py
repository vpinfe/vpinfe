"""Protects: (c) a rail entry on an extension's panel that is clicked and changes nothing.

Slow: boots a real instance and a real browser. Each extension's panel is opened, and each
entry of its rail is pressed in turn. Every entry must show its own content.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

EXTENSIONS = ("library_importer", "vpinplay")
WORK = ("(() => { const area = [...document.querySelectorAll('.manager-section-work')]"
        ".find(el => el.getClientRects().length > 0); return area ? area.innerText : ''; })()")


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class AnExtensionsRailEntriesEachOpenTheirOwnSection(unittest.TestCase):
    shown: dict[str, list[tuple[str, str]]] = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        assert binary is not None
        with TemporaryDirectory() as tmp:
            write_game(Path(tmp), "Alpha", info=game_info("Alpha", vps_id="", game_id="alpha"),
                       files={"Alpha.ini": b"[Player]"})
            with LiveInstance(Path(tmp)) as instance:
                cls.shown = asyncio.run(cls._drive(instance, binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance,
                     binary: str) -> dict[str, list[tuple[str, str]]]:
        instance.wait_for_api()
        shown: dict[str, list[tuple[str, str]]] = {}
        async with BrowserSession(binary) as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1800, "height": 1100, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)
            for name in EXTENSIONS:
                await walk.visit(f"/manager?view=extensions&extension={name}")
                shown[name] = [(section, str(await browser.evaluate(WORK)))
                               async for section in walk.sections()]
        return shown

    def test_each_extension_has_a_rail_of_more_than_one_entry(self) -> None:
        for name in EXTENSIONS:
            self.assertGreater(len(self.shown[name]), 1, f"{name}: {self.shown[name]}")

    def test_no_two_entries_of_one_rail_show_the_same_content(self) -> None:
        for name in EXTENSIONS:
            texts = [text for _section, text in self.shown[name]]
            self.assertEqual(len(texts), len(set(texts)),
                             f"{name}: an entry was pressed and the panel did not change: "
                             f"{[section for section, _text in self.shown[name]]}")


if __name__ == "__main__":
    unittest.main()

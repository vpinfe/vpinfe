"""A theme's `locales/` words reach `vpin.t`, in the page, beside core's.

Driven in a real browser: the page asks for the catalog by the folder its own address
names, and `ready` has to wait for the answer.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance

READ = ("JSON.stringify({"
        " own: vpin.t('theme.authors', 'English in the call'),"
        " one: vpin.t('theme.tables', '', { count: 1 }),"
        " many: vpin.t('theme.tables', '', { count: 12 }),"
        " missing: vpin.t('theme.nothing', 'Fallback'),"
        " core: vpin.t('frontend.theme.launching', 'English in the call') })")


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class ThemeLocalesDriveTests(TempTree):
    BOOTS_PER_TEST = "One page load answers every question."

    def test_a_theme_reads_its_own_words_and_core_keeps_its_own(self) -> None:
        write_game(self.root, "Alpha Table", info={"Info": {"Name": "Alpha Table"},
                                                   "VPinFE": {}, "User": {}})

        async def run(instance: LiveInstance) -> dict:
            locales = instance.config_dir / "frontend_themes" / instance.theme / "locales"
            locales.mkdir()
            (locales / "en.json").write_text(json.dumps({
                "authors": "Authors from the file",
                "tables": {"one": "{count} table", "other": "{count} tables"},
                "frontend.theme.launching": "Not core's"}))
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.navigate(instance.theme_url("playfield"))
                await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                return json.loads(await browser.evaluate(READ))

        with LiveInstance(self.root) as instance:
            said = asyncio.run(run(instance))

        self.assertEqual(said["own"], "Authors from the file")
        self.assertEqual((said["one"], said["many"]), ("1 table", "12 tables"))
        self.assertEqual(said["missing"], "Fallback")
        self.assertEqual(said["core"], "Launching…")


if __name__ == "__main__":
    unittest.main()

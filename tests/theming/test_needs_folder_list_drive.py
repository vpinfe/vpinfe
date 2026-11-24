"""A Needs You folder row opens the folder list, driven in a real browser.

Slow: boots a real instance and a real browser. A library holding one ordinary game and one
whose `.info` cannot be read; the Unreadable Folder row on Overview opens a dialog naming
that folder and why, with Close in its footer and nothing to upgrade.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

ORDINARY = "Sample Game (Original 2024)"
BROKEN = "Malformed Info (Original 2024)"

ROW = ".manager-need"
DIALOG = ".manager-dialog"
ROW_AT = """(label => [...document.querySelectorAll(%s)].findIndex(
  row => row.querySelector('.manager-setting')?.innerText === label))(%s)"""
READ = """(() => {
  const card = document.querySelector(%s);
  if (!card) return null;
  const why = el => el.nextElementSibling?.matches('.manager-help')
    ? el.nextElementSibling.innerText : '';
  return {
    title: card.querySelector('.manager-dialog-title')?.innerText ?? null,
    folders: [...card.querySelectorAll('.manager-setting')]
      .map(el => [el.innerText, why(el)]),
    buttons: [...card.querySelectorAll('.manager-dialog-footer button')]
      .map(el => el.innerText.split('\\n').pop()),
  };
})()"""


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class NeedsFolderListDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        assert binary is not None
        with TemporaryDirectory() as tmp:
            write_game(Path(tmp), ORDINARY, info=game_info(ORDINARY, game_id="ordinary"))
            (write_game(Path(tmp), BROKEN) / f"{BROKEN}.info").write_text(
                '{"Info": {"Title": "broken",,,}', encoding="utf-8")
            with LiveInstance(Path(tmp)) as instance:
                cls.seen = asyncio.run(cls._drive(instance, binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance, binary: str) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, instance)
            await walk.visit("/manager?view=overview")
            seen["row"] = await browser.evaluate(
                ROW_AT % (json.dumps(ROW), json.dumps(t("manager.needs.unreadable"))))
            if seen["row"] >= 0:
                await browser.click(ROW, nth=seen["row"])
                await browser.wait_for(f"!!document.querySelector({json.dumps(DIALOG)})",
                                       timeout=30)
                seen["dialog"] = await browser.evaluate(READ % json.dumps(DIALOG))
        return seen

    def test_the_unreadable_folder_is_a_row_on_overview(self) -> None:
        self.assertGreaterEqual(self.seen["row"], 0)

    def test_the_row_opens_a_dialog_titled_for_the_unreadable_folders(self) -> None:
        self.assertEqual(t("manager.needs.unreadable_title"), self.seen["dialog"]["title"])

    def test_it_names_the_folder_with_the_bad_file_and_not_the_other(self) -> None:
        self.assertEqual([BROKEN], [name for name, _why in self.seen["dialog"]["folders"]])

    def test_it_says_why_under_the_name(self) -> None:
        self.assertEqual([t("error.games.info_bad_line", line=1)],
                         [why for _name, why in self.seen["dialog"]["folders"]])

    def test_the_footer_has_close_and_no_upgrade(self) -> None:
        self.assertEqual([t("word.close")], self.seen["dialog"]["buttons"])


if __name__ == "__main__":
    unittest.main()

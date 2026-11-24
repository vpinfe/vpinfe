"""A select on a settings line never runs over the Reset beside it.

Its floor is measured against the line, so where the column is narrower than the floor
plus the Reset the select gives way, and where there is room it keeps the whole floor.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.stylesheet import SHEET

PAGE = """<!doctype html><html><head><link rel="stylesheet" href="{sheet}">
<style>:root {{ --select-min: 200px; --target-inline: 22px; --label-max: 60px; }}
body {{ margin: 0; }}
.manager-field-line, .manager-field-row {{ display: flex; flex-wrap: nowrap; gap: 4px; }}
.manager-fact-edit {{ display: flex; }}</style></head><body>
<div class="manager-facts" id="facts">
<span>Display</span>
<div class="manager-field-line">
  <div class="manager-field-row"><div class="manager-fact-edit">
    <label class="q-field manager-edit-select"><input style="width: 40px"></label></div></div>
  <div class="manager-row-action"><button class="q-btn">R</button></div>
</div></div></body></html>"""

MEASURE = """(() => {{
  document.getElementById('facts').style.width = '{width}px';
  const select = document.querySelector('.manager-edit-select').getBoundingClientRect();
  const action = document.querySelector('.manager-row-action').getBoundingClientRect();
  return JSON.stringify({{select: select.width, gap: action.left - select.right}});
}})()"""


class ASelectLeavesRoomForReset(unittest.TestCase):
    async def _measure(self, binary: str, widths: list[int]) -> list[dict[str, float]]:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text(PAGE.format(sheet=SHEET.as_uri()), encoding="utf-8")
            async with BrowserSession(binary) as browser:
                await browser.navigate(page.as_uri())
                await browser.wait_for("!!document.querySelector('.manager-row-action')")
                return [json.loads(await browser.evaluate(MEASURE.format(width=width)))
                        for width in widths]

    def test_the_select_gives_way_to_a_narrow_column_and_keeps_its_floor_when_there_is_room(
            self) -> None:
        binary = chromium_path()
        if not binary:
            self.skipTest("no Chromium on this machine")

        narrow, wide = asyncio.run(self._measure(binary, [150, 700]))

        self.assertGreaterEqual(narrow["gap"], 0)
        self.assertLess(narrow["select"], 200)
        self.assertGreaterEqual(wide["gap"], 0)
        self.assertEqual(200, wide["select"])


if __name__ == "__main__":
    unittest.main()

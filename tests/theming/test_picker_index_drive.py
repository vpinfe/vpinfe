"""Moving in a picker leaves the wheel's table alone, in the page.

Driven in a real browser: the index messages a picker sends travel through the page's own
message handler, and that handler is where the wheel's place used to be overwritten.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance

WATCH = """(() => {
  window.__heard = 0;
  const handle = vpin.handleEvent.bind(vpin);
  vpin.handleEvent = async (message) => {
    await handle(message);
    if (message && message.kind === 'collection') window.__heard += 1;
  };
})()"""

OPEN_PICKER = """(() => {
  vpin.moveTo(2);
  const rows = ['All Games', 'One', 'Two'].map((label) => ({ label }));
  vpin.pushList(vpin.createList(rows, { id: 'collections', kind: 'collection', cursor: 0 }));
  vpin.moveBy(1);
})()"""

READ = "JSON.stringify({ wheel: vpin.getCurrentTableIndex(), heard: window.__heard })"


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class PickerIndexDriveTests(TempTree):
    BOOTS_PER_TEST = "One page load answers every question."

    def test_the_wheel_keeps_its_table_while_a_picker_moves_and_after_back(self) -> None:
        for name in ("Alpha Table", "Bravo Table", "Charlie Table", "Delta Table"):
            write_game(self.root, name, info={"Info": {"Name": name}, "VPinFE": {}, "User": {}})

        async def run(instance: LiveInstance) -> tuple[dict, dict]:
            async with BrowserSession(chromium_path() or "") as browser:
                await browser.navigate(instance.theme_url("playfield"))
                await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)
                await browser.evaluate(WATCH)
                await browser.evaluate(OPEN_PICKER)
                await browser.wait_for("window.__heard >= 2")
                inside = json.loads(await browser.evaluate(READ))
                await browser.evaluate("vpin.popList()")
                await browser.wait_for("window.__heard >= 2 && vpin.atRoot")
                return inside, json.loads(await browser.evaluate(READ))

        with LiveInstance(self.root) as instance:
            inside, back = asyncio.run(run(instance))

        self.assertEqual(inside["wheel"], 2)
        self.assertEqual(back["wheel"], 2)


if __name__ == "__main__":
    unittest.main()

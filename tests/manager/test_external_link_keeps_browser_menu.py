"""A right click on a link that leaves the Manager opens the browser's menu, not the row's.

The row's `ui.context_menu` listens on the element around the link and prevents the default
there. The page script stops the event before it gets that far, so the default is untouched
and everything else in the row still reaches the app's menu.

**What this cannot prove:** Quasar's own menu opening. The page stands in for it with a
listener on the row that prevents the default, which is what Quasar's anchor does. A real
page is driven in the browser.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from manager.kit import panel
from tests.support.browser_session import BrowserSession, chromium_path

PAGE = """<!doctype html><html><body>
<div id="row" style="padding: 20px">
  <span id="name">Game</span>
  <a id="out" class="manager-link manager-link-out" href="https://example.invalid/page"
     target="_blank" rel="noopener"><span id="said">Open</span><i id="mark">open_in_new</i></a>
  <a id="in" class="manager-link" href="/manager?view=games">Games</a>
</div>
<script>
  window.appMenu = 0;
  document.getElementById('row').addEventListener('contextmenu', (event) => {
    window.appMenu += 1;
    event.preventDefault();
  });
</script></body></html>"""

RIGHT_CLICK = """(() => {
  window.appMenu = 0;
  const event = new MouseEvent('contextmenu', {bubbles: true, cancelable: true});
  document.getElementById(%s).dispatchEvent(event);
  return JSON.stringify({prevented: event.defaultPrevented, appMenu: window.appMenu});
})()"""


class AnExternalLinkKeepsTheBrowsersMenu(unittest.TestCase):
    async def _clicks(self, binary: str, ids: list[str]) -> dict[str, dict[str, object]]:
        async with BrowserSession(binary) as browser:
            await browser.navigate("about:blank")
            await browser.send("Page.setDocumentContent", {
                "frameId": (await browser.send("Page.getFrameTree"))["frameTree"]["frame"]["id"],
                "html": PAGE})
            await browser.wait_for("!!document.getElementById('row')")
            await browser.evaluate(panel.NAV_CLICK)
            return {one: json.loads(await browser.evaluate(RIGHT_CLICK % json.dumps(one)))
                    for one in ids}

    def test_the_link_is_left_to_the_browser_and_the_rest_of_the_row_is_not(self) -> None:
        binary = chromium_path()
        if not binary:
            self.skipTest("no Chromium on this machine")

        seen = asyncio.run(self._clicks(binary, ["out", "said", "mark", "name", "in"]))

        for part in ("out", "said", "mark"):
            self.assertEqual({"prevented": False, "appMenu": 0}, seen[part],
                             f"a right click on the external link's {part} met the app's menu")
        for part in ("name", "in"):
            self.assertEqual({"prevented": True, "appMenu": 1}, seen[part],
                             f"a right click on {part} no longer opens the app's menu")


if __name__ == "__main__":
    unittest.main()

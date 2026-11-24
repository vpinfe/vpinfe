"""The Open Links In setting: the way it is read, declared and followed by a link.

A real Chromium follows two clicks on links to another site in each way, because whether a
second click finds the first one's tab is the browser's doing, not something a unit can
stand in for.
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest import mock

from common.config import config_schema
from manager.kit import appearance, link_tab
from manager.settings import settings
from tests.support.browser_session import BrowserSession, chromium_path

PAGE = """<!doctype html><html><body>
<a id="first" href="http://127.0.0.1:%(port)s/one" target="_blank"
   rel="noopener noreferrer">One</a>
<a id="second" href="http://127.0.0.1:%(port)s/two" target="_blank"
   rel="noopener noreferrer">Two</a>
<a id="own" href="http://127.0.0.1:%(port)s/own" target="vpinfe-manager">Own</a>
<a id="same" href="/inside" target="_blank">Same</a>
<a id="mailto" href="mailto:someone@example.invalid" target="_blank">Mail</a>
</body></html>"""

# Each click is dispatched by hand and then cancelled, so nothing opens. What matters is
# whether the page's own handler had already cancelled it before that.
LEFT_ALONE = """(() => {
  const answer = {};
  for (const [id, keys] of [['own', {}], ['same', {}], ['mailto', {}],
                            ['first', {ctrlKey: true}], ['first', {shiftKey: true}],
                            ['first', {metaKey: true}]]) {
    const link = document.getElementById(id);
    const event = new MouseEvent('click', {bubbles: true, cancelable: true, ...keys});
    let seen = null;
    link.addEventListener('click', (e) => { seen = e.defaultPrevented; e.preventDefault(); },
                          {once: true});
    link.dispatchEvent(event);
    answer[id + JSON.stringify(keys)] = seen === false;
  }
  return JSON.stringify(answer);
})()"""

# What a site that was handed a way back to the Manager would do with it. It says it ran,
# so a refusal by the browser cannot pass for a defence.
HOSTILE = """<html><title>other site</title><script>
for (const to of [() => { opener.top.location = '/hijacked'; },
                  () => { opener.parent.location = '/hijacked'; }]) {
  try { to(); } catch (e) { /* refused */ }
}
fetch('/ran');
</script></html>"""


class _Server(ThreadingHTTPServer):
    ran = threading.Event()


class _Quiet(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:  # noqa: N802 - the name http.server asks for
        port = int(self.headers["Host"].rsplit(":", 1)[1])
        if self.path == "/ran":
            self.server.ran.set()
        body = (PAGE % {"port": port} if self.path == "/manager-page" else HOSTILE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


class TheSettingIsReadAndDeclared(unittest.TestCase):
    def test_the_default_is_a_new_tab_each_time(self) -> None:
        option = config_schema.option(link_tab.SECTION, link_tab.KEY)
        assert option is not None
        self.assertEqual(option.default, link_tab.NEW)
        self.assertEqual(tuple(option.choices), link_tab.CHOICES)

    def test_a_value_nobody_offers_reads_as_the_default(self) -> None:
        for said, way in ((None, link_tab.NEW), ("", link_tab.NEW), ("Reuse", link_tab.REUSE),
                          ("one tab", link_tab.NEW), ("reuse", link_tab.REUSE)):
            with mock.patch.object(link_tab, "cfg_get", return_value=said):
                self.assertEqual(link_tab.configured(), way, said)

    def test_the_page_gets_the_way_before_a_click_can_happen(self) -> None:
        self.assertTrue(link_tab.script("reuse").startswith('window.vpinfeLinks = "reuse";'))

    def test_every_page_with_the_shared_look_carries_the_script(self) -> None:
        added: list[str] = []
        with mock.patch.object(appearance.ui, "add_head_html"), \
             mock.patch.object(appearance.ui, "add_body_html", side_effect=added.append), \
             mock.patch.object(link_tab, "cfg_get", return_value="reuse"):
            appearance.apply_flair()

        self.assertTrue(any('window.vpinfeLinks = "reuse";' in one for one in added), added)


class TheSettingsPageDrawsAndSendsIt(unittest.TestCase):
    def _editor(self) -> Any:
        option = config_schema.option(link_tab.SECTION, link_tab.KEY)
        assert option is not None
        self.assertEqual(option.editor, config_schema.EDITOR_OPEN_LINKS)
        return settings.EDITORS[config_schema.EDITOR_OPEN_LINKS]

    @staticmethod
    def _wire(option: Any) -> dict[str, Any]:
        return {"choices": list(option.choices), "choice_labels": option.choice_labels}

    def test_the_option_has_an_editor_the_page_knows(self) -> None:
        self.assertTrue(callable(self._editor()))

    def test_a_saved_choice_is_sent_to_the_page_already_open(self) -> None:
        option = config_schema.option(link_tab.SECTION, link_tab.KEY)
        assert option is not None
        chosen: dict[str, Any] = {}
        saved: list[str] = []

        async def save(value: Any) -> bool:
            saved.append(value)
            return True

        async def change() -> None:
            with mock.patch.object(settings.panel, "select",
                                   side_effect=lambda _o, _v, on, **_k: chosen.update(on=on)), \
                 mock.patch.object(settings.ui, "run_javascript") as run:
                self._editor()(self._wire(option), "new", save)
                await chosen["on"].__wrapped__(mock.Mock(value="reuse"))
            self.assertEqual(saved, ["reuse"])
            run.assert_called_once_with(link_tab.change_js("reuse"))

        asyncio.run(change())

    def test_a_choice_that_did_not_save_is_not_sent(self) -> None:
        option = config_schema.option(link_tab.SECTION, link_tab.KEY)
        assert option is not None
        chosen: dict[str, Any] = {}

        async def save(_value: Any) -> bool:
            return False

        async def change() -> None:
            with mock.patch.object(settings.panel, "select",
                                   side_effect=lambda _o, _v, on, **_k: chosen.update(on=on)), \
                 mock.patch.object(settings.ui, "run_javascript") as run:
                self._editor()(self._wire(option), "new", save)
                await chosen["on"].__wrapped__(mock.Mock(value="reuse"))
            run.assert_not_called()

        asyncio.run(change())


class AClickFollowsTheSetting(unittest.TestCase):
    async def _pages(self, browser: BrowserSession) -> list[dict[str, Any]]:
        targets = (await browser.send("Target.getTargets"))["targetInfos"]
        return [one for one in targets
                if one["type"] == "page" and one["url"].startswith("http://127.0.0.1")]

    async def _until(self, browser: BrowserSession, count: int, last: str) -> list[dict]:
        for _ in range(100):
            pages = await self._pages(browser)
            if len(pages) == count and pages[-1]["url"].endswith(last):
                return pages
            await asyncio.sleep(0.1)
        return await self._pages(browser)

    async def _drive(self, binary: str, way: str, port: int,
                     ran: threading.Event) -> tuple[int, str, dict[str, bool], str, bool]:
        async with BrowserSession(binary) as browser:
            await browser.navigate(f"http://localhost:{port}/manager-page")
            await browser.wait_for("!!document.getElementById('first')")
            await browser.evaluate(link_tab.script(way))
            left = json.loads(await browser.evaluate(LEFT_ALONE))
            wanted = 2 if way == link_tab.NEW else 1
            for count, one in ((1, "first"), (wanted, "second")):
                await browser.send("Page.bringToFront")
                await browser.click(f"#{one}")
                pages = await self._until(browser, count, f"/{'one' if one == 'first' else 'two'}")
            for _ in range(100):
                if ran.is_set():
                    break
                await asyncio.sleep(0.1)
            return (len(pages), (pages[-1]["url"] if pages else ""), left,
                    await browser.evaluate("location.href"), ran.is_set())

    def _run(self, way: str) -> tuple[int, str, dict[str, bool], str, bool]:
        binary = chromium_path()
        if not binary:
            self.skipTest("no Chromium on this machine")
        server = _Server(("127.0.0.1", 0), _Quiet)
        server.ran = threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            return asyncio.run(self._drive(binary, way, int(server.server_address[1]),
                                           server.ran))
        finally:
            server.shutdown()
            server.server_close()

    def test_a_new_tab_each_time_opens_a_tab_for_every_click(self) -> None:
        count, _last, _left, manager, _ran = self._run(link_tab.NEW)

        self.assertEqual(count, 2)
        self.assertTrue(manager.endswith("/manager-page"), manager)

    def test_one_tab_to_keep_using_opens_one_and_sends_the_next_link_there(self) -> None:
        count, last, left, manager, ran = self._run(link_tab.REUSE)

        self.assertTrue(ran, "the other site never ran, so nothing was defended against")
        self.assertEqual(count, 1)
        self.assertTrue(manager.endswith("/manager-page"),
                        f"the site in the tab sent the Manager to {manager}")
        self.assertTrue(last.endswith("/two"), last)
        self.assertTrue(all(left.values()),
                        f"a click the browser should keep was taken by the page: {left}")


if __name__ == "__main__":
    unittest.main()

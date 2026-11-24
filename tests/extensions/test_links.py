"""The links an extension draws to its own UI: what it may declare, what the host answers
once it runs, how the Manager fills in an address and places an entry, and that a page
core serves for an extension runs as no origin."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
import unittest.mock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.testclient import TestClient

from common import i18n
from common.extensions import check, contract, host, store
from httpapi import extensions as httpapi_extensions
from manager.extensions import ext_links, ext_lists
from tests.support.accepted import owner_accepts
from tests.support.browser_session import BrowserSession, chromium_path, free_port
from tests.support.identity import IDENTITY

MODULE = "tests.extensions.test_links"
NAME = "linked"
UI = "http://{host}:9100/ui"

WORDS = {"link.panel.label": "Open the panel", "link.panel.tip": "Opens in a new tab",
         "link.docs.label": "Docs"}

# What `register` declares this time.
DECLARE: list[dict[str, Any]] = []


def register(ctx: Any) -> None:
    for one in DECLARE:
        ctx.ui.link(one["key"], one["url"],
                    **{name: one[name] for name in ("area", "icon") if name in one})


def _installed(root: Path, capabilities: list[str], words: dict | None = None) -> Path:
    directory = root / NAME
    (directory / "i18n").mkdir(parents=True)
    (directory / "extension.json").write_text(json.dumps({
        "name": NAME, "display_name": "Linked", "version": "1.0.0",
        "description": "Links to its own page.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": capabilities}), encoding="utf-8")
    (directory / "i18n" / "en.json").write_text(
        json.dumps(WORDS if words is None else words), encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import register as register\n", encoding="utf-8")
    return directory


class LinkedCase(unittest.TestCase):
    capabilities = ["ui:mount"]

    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        DECLARE.clear()
        self.addCleanup(DECLARE.clear)
        self.registry = host.Registry(store.ExtensionStore(self.root))
        self.addCleanup(self.registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")

    def loaded(self, words: dict | None = None) -> host.Record:
        self.folder = _installed(self.root / "installed", self.capabilities, words)
        return self.registry.load(self.folder)


class WhatAnExtensionMayDeclare(LinkedCase):
    def declare(self, *links: dict[str, Any]) -> host.Record:
        DECLARE.extend(links)
        return self.loaded()

    def refused(self, *links: dict[str, Any]) -> host.Record:
        with self.assertLogs("vpinfe.common.extensions.host", "ERROR"):
            return self.declare(*links)

    def test_a_link_with_an_address_loads(self) -> None:
        record = self.declare({"key": "panel", "url": UI})

        self.assertEqual(host.LOADED, record.state, record.error)
        self.assertEqual([{"key": "panel", "area": "", "icon": ""}], record.declared_links)

    def test_a_link_can_be_placed_in_an_area_of_the_rail(self) -> None:
        record = self.declare({"key": "panel", "url": UI, "area": "library"})

        self.assertEqual("library", record.declared_links[0]["area"])

    def test_a_link_can_name_an_icon_of_the_rail(self) -> None:
        record = self.declare({"key": "panel", "url": UI, "area": "library", "icon": "wifi"})

        self.assertEqual("wifi", record.declared_links[0]["icon"])

    def test_an_icon_the_rail_does_not_have_fails_the_load(self) -> None:
        record = self.refused({"key": "panel", "url": UI, "area": "library", "icon": "nope"})

        self.assertEqual(host.FAILED, record.state)
        self.assertIn("nope", record.error)

    def test_a_function_can_answer_the_address(self) -> None:
        record = self.declare({"key": "panel", "url": lambda: UI})

        self.assertEqual(host.LOADED, record.state, record.error)

    def test_without_the_capability_it_fails_the_load_in_words(self) -> None:
        self.capabilities = []
        record = self.refused({"key": "panel", "url": UI})

        self.assertEqual(host.FAILED, record.state)
        self.assertIn("ui:mount", record.error)

    def test_a_link_with_no_key_fails_the_load(self) -> None:
        record = self.refused({"key": " ", "url": UI})

        self.assertEqual(host.FAILED, record.state)
        self.assertEqual([], record.declared_links)

    def test_the_same_key_twice_fails_the_load(self) -> None:
        record = self.refused({"key": "panel", "url": UI}, {"key": "panel", "url": UI})

        self.assertEqual(host.FAILED, record.state)
        self.assertIn("twice", record.error)

    def test_an_area_the_rail_does_not_have_fails_the_load(self) -> None:
        record = self.refused({"key": "panel", "url": UI, "area": "nowhere"})

        self.assertEqual(host.FAILED, record.state)
        self.assertIn("nowhere", record.error)

    def test_an_address_that_is_not_http_fails_the_load(self) -> None:
        for url in ("javascript:alert(1)", "file:///etc/passwd", "ftp://host/", "panel", "",
                    "//host/ui", 5, "http://[oops", "http://", "http:///x", "http://a:xx/"):
            with self.subTest(url=url):
                self.setUp()
                record = self.refused({"key": "panel", "url": url})

                self.assertEqual(host.FAILED, record.state)
                self.assertIn("http://", record.error)


class WhatTheHostAnswers(LinkedCase):
    def running(self, *links: dict[str, Any], words: dict | None = None) -> host.Record:
        DECLARE.extend(links)
        record = self.loaded(words)
        self.assertEqual(host.LOADED, record.state, record.error)
        return record

    def test_a_link_has_its_address_and_the_words_of_the_catalog(self) -> None:
        record = self.running({"key": "panel", "url": UI})

        self.assertEqual([{"key": "panel", "area": "", "icon": "", "url": UI,
                           "label": "Open the panel", "tip": "Opens in a new tab"}],
                         record.links())

    def test_a_link_with_no_tip_has_none(self) -> None:
        record = self.running({"key": "docs", "url": UI})

        self.assertEqual("", record.links()[0]["tip"])

    def test_the_function_is_asked_each_time(self) -> None:
        answers = iter(["http://one.example/", "http://two.example/"])
        record = self.running({"key": "panel", "url": lambda: next(answers)})

        self.assertEqual("http://one.example/", record.links()[0]["url"])
        self.assertEqual("http://two.example/", record.links()[0]["url"])

    def test_a_function_that_raises_leaves_its_link_out_and_not_the_others(self) -> None:
        def broken() -> str:
            raise RuntimeError("not up yet")

        record = self.running({"key": "panel", "url": broken}, {"key": "docs", "url": UI})

        with self.assertLogs("vpinfe.common.extensions.host", "ERROR"):
            found = record.links()

        self.assertEqual(["docs"], [one["key"] for one in found])

    def test_a_function_that_answers_something_else_leaves_its_link_out(self) -> None:
        for answer in ("javascript:alert(1)", None, 5, "", "http://[oops", "http:///x"):
            with self.subTest(answer=answer):
                self.setUp()
                record = self.running({"key": "panel", "url": lambda answer=answer: answer})

                self.assertEqual([], record.links())

    def test_the_links_are_in_the_extensions_record_while_it_runs(self) -> None:
        record = self.running({"key": "panel", "url": UI})

        self.assertEqual(["panel"], [one["key"] for one in record.as_dict()["links"]])

    def test_a_stopped_extension_draws_none(self) -> None:
        record = self.running({"key": "panel", "url": UI})
        self.registry.switch(NAME, False)

        self.assertEqual([], record.as_dict()["links"])

    def test_a_link_with_no_label_is_named_by_its_key(self) -> None:
        record = self.running({"key": "panel", "url": UI}, words={})

        self.assertEqual("panel", record.links()[0]["label"])


class WhatTheCheckSays(LinkedCase):
    def checked(self, *links: dict[str, Any], words: dict | None = None) -> tuple[list[str], bool]:
        DECLARE.extend(links)
        with unittest.mock.patch.object(check, "_boundary", return_value=[]):
            return check.check(_installed(self.root / "checked", self.capabilities, words))

    def test_it_says_each_link_with_where_it_sits_and_what_it_answers(self) -> None:
        lines, passed = self.checked({"key": "panel", "url": UI, "area": "library"})

        self.assertTrue(passed, lines)
        self.assertIn(f"link       panel in library: {UI}", lines)

    def test_a_link_with_no_area_is_on_the_page_only(self) -> None:
        lines, _ = self.checked({"key": "panel", "url": UI})

        self.assertIn(f"link       panel: {UI}", lines)

    def test_a_function_that_answers_no_web_address_fails_the_check(self) -> None:
        lines, passed = self.checked({"key": "panel", "url": lambda: "nothing"})

        self.assertFalse(passed)
        self.assertTrue(any(one.startswith("fails      link panel") for one in lines), lines)

    def test_a_link_with_no_words_fails_the_check(self) -> None:
        lines, passed = self.checked({"key": "panel", "url": UI}, words={})

        self.assertFalse(passed)
        self.assertTrue(any("link.panel.label" in one for one in lines), lines)


class HowTheManagerFillsAnAddress(unittest.TestCase):
    def test_host_is_the_name_the_manager_was_opened_at(self) -> None:
        for said, expected in (("cab.local:8001", "cab.local"), ("192.168.1.5:8001", "192.168.1.5"),
                               ("localhost", "localhost"), ("[::1]:8001", "[::1]"),
                               ("[fe80::1]:8001", "[fe80::1]")):
            with self.subTest(said=said):
                self.assertEqual(expected, ext_links.host_of(said))

    def test_a_host_header_that_is_not_a_name_falls_back_to_localhost(self) -> None:
        for said in ("", "a b", "ho\"st", "@", ":80", "bad_name", "[abc"):
            with self.subTest(said=said):
                self.assertEqual("localhost", ext_links.host_of(said))

    def test_filled_puts_the_host_in_every_place_it_is_named(self) -> None:
        self.assertEqual("http://cab:9100/ui?from=cab",
                         ext_links.filled("http://{host}:9100/ui?from={host}", "cab"))

    def test_filled_answers_nothing_for_an_address_that_is_not_http(self) -> None:
        for url in ("{host}:9100", "javascript:alert(1)", "file:///x", "http://", "", "http://[oops",
                    "http:///x"):
            with self.subTest(url=url):
                self.assertEqual("", ext_links.filled(url, "cab"))

    def test_a_link_whose_address_fills_to_nothing_is_not_drawn(self) -> None:
        extension = {"links": [{"key": "a", "url": "http://{host}/"},
                               {"key": "b", "url": "javascript:x"}]}

        self.assertEqual(["a"], [one["key"] for one in ext_links.drawn(extension, "cab")])

    def test_find_resolves_a_rail_entry_to_its_link(self) -> None:
        extensions = [{"name": "linked", "links": [{"key": "a", "url": "http://{host}/"}]}]

        found = ext_links.find(extensions, ext_links.view_key("linked", "a"), "cab")

        self.assertEqual("http://cab/", found["url"] if found else None)
        self.assertIsNone(ext_links.find(extensions, ext_links.view_key("linked", "z"), "cab"))
        self.assertTrue(ext_links.is_link(ext_links.view_key("linked", "a")))
        self.assertFalse(ext_links.is_link("community:linked:a"))


class WhereTheRailPutsALink(unittest.TestCase):
    def extension(self, **declared: Any) -> dict[str, Any]:
        return {"name": "linked", "state": "loaded", "lists": [], "links": [], **declared}

    def test_a_link_with_an_area_is_an_entry_of_that_area(self) -> None:
        items = ext_lists.nav_items([self.extension(
            links=[{"key": "panel", "area": "library", "label": "Open the panel"}])])

        (entry,) = items["library"]
        self.assertEqual((ext_links.view_key("linked", "panel"), "Open the panel"), entry[:2])

    def test_a_link_is_drawn_with_its_icon_or_the_extension_icon(self) -> None:
        items = ext_lists.nav_items([self.extension(links=[
            {"key": "a", "area": "library", "label": "A", "icon": "wifi"},
            {"key": "b", "area": "library", "label": "B", "icon": ""}])])

        self.assertEqual(["wifi", ext_lists.ICON], [one[2] for one in items["library"]])

    def test_a_link_with_no_area_is_not_in_the_rail(self) -> None:
        items = ext_lists.nav_items([self.extension(
            links=[{"key": "panel", "area": "", "label": "Open the panel"}])])

        self.assertEqual({}, items)

    def test_a_stopped_extension_adds_nothing(self) -> None:
        items = ext_lists.nav_items([self.extension(
            state="stopped", links=[{"key": "panel", "area": "library", "label": "L"}])])

        self.assertEqual({}, items)

    def test_an_extensions_lists_come_before_its_links(self) -> None:
        items = ext_lists.nav_items([self.extension(
            lists=[{"key": "things", "title": "Things", "area": "library"}],
            links=[{"key": "panel", "area": "library", "label": "Open the panel"}])])

        self.assertEqual(["Things", "Open the panel"], [one[1] for one in items["library"]])


class WhatAnExtensionsPageCanDo(unittest.TestCase):
    """A page core serves under /ext/ runs as no origin: it cannot read the Manager's
    cookie or storage, and what it requests carries no sign-in."""

    PAGE = """<!doctype html><body><script>
      const found = {origin: String(window.origin)};
      for (const [name, read] of Object.entries({
          cookie: () => document.cookie, storage: () => localStorage.length})) {
        try { found[name] = String(read()); } catch (err) { found[name] = "blocked"; }
      }
      fetch("/whoami", {credentials: "include", mode: "no-cors"})
        .finally(() => { document.body.dataset.found = JSON.stringify(found); });
    </script></body>"""

    @staticmethod
    def app(seen: list[str]) -> FastAPI:
        api = FastAPI()
        api.add_middleware(httpapi_extensions.SecretsStayHere)

        @api.get("/ext/linked/page")
        def page() -> HTMLResponse:
            response = HTMLResponse(WhatAnExtensionsPageCanDo.PAGE)
            response.headers["X-Content-Type-Options"] = "sniff"
            return response

        @api.get("/ext/linked/data")
        def data() -> JSONResponse:
            return JSONResponse({"fine": True})

        @api.get("/control")
        def control() -> HTMLResponse:
            response = HTMLResponse(WhatAnExtensionsPageCanDo.PAGE)
            response.set_cookie("session", "signed-in", samesite="lax")
            return response

        @api.get("/whoami")
        def whoami(request: Request) -> JSONResponse:
            seen.append(request.headers.get("cookie", ""))
            return JSONResponse({})

        return api

    def test_every_answer_under_ext_is_sandboxed_and_not_sniffed(self) -> None:
        client = TestClient(self.app([]))

        for path in ("/ext/linked/page", "/ext/linked/data"):
            with self.subTest(path=path):
                response = client.get(path)

                self.assertEqual("sandbox allow-scripts allow-forms",
                                 response.headers["content-security-policy"])
                self.assertEqual("nosniff", response.headers["x-content-type-options"])

    def test_an_answer_outside_ext_is_left_as_it_was(self) -> None:
        response = TestClient(self.app([])).get("/control")

        self.assertNotIn("content-security-policy", response.headers)

    @contextmanager
    def served(self, seen: list[str]) -> Iterator[int]:
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(self.app(seen), host="127.0.0.1", port=port,
                                               log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        try:
            yield port
        finally:
            server.should_exit = True
            thread.join(timeout=10)

    def test_in_a_browser_the_page_runs_as_no_origin(self) -> None:
        if not chromium_path():
            self.skipTest("no Chromium on this machine")
        seen: list[str] = []
        with self.served(seen) as port:
            found = asyncio.run(self._drive(port))
        control, ext = found

        self.assertEqual(f"http://127.0.0.1:{port}", control["origin"])
        self.assertEqual("signed-in", control["cookie"].split("=", 1)[1])
        self.assertEqual({"origin": "null", "cookie": "blocked", "storage": "blocked"}, ext)
        self.assertEqual(["session=signed-in", ""], seen)

    @staticmethod
    async def _drive(port: int) -> tuple[dict, dict]:
        browser_path = chromium_path()
        assert browser_path is not None
        async with BrowserSession(browser_path) as browser:
            found = []
            for path in ("/control", "/ext/linked/page"):
                await browser.navigate(f"http://127.0.0.1:{port}{path}")
                await browser.wait_for("!!(document.body && document.body.dataset.found)")
                found.append(json.loads(await browser.evaluate("document.body.dataset.found")))
            return found[0], found[1]


if __name__ == "__main__":
    unittest.main()

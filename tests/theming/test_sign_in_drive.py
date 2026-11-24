"""The sign-in request on the cabinet, driven in a real browser against a real backend.

The page is an iframe core builds on first use, so what is under test is the whole path:
the machine's request, the message to the window, the overlay opening over the theme, and
the page's buttons answering the machine.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.request
from typing import Any

from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100fd5c9a6b0000000049454e44ae426082")

FRAME = "document.getElementById('signin-frame').contentDocument"
# How long to wait before checking that a held-back overlay stayed shut.
HELD_QUIET_WINDOW_S = 1.0
OPEN = "vpin.overlay === 'signIn' && 'open'"


def _browser() -> BrowserSession:
    path = chromium_path()
    assert path is not None
    return BrowserSession(path)


def _page(expression: str) -> str:
    return f"(() => {{ const d = {FRAME}; return {expression}; }})()"


def _ask(instance: LiveInstance) -> None:
    """A browser on this machine asking for a code. This instance has no window or Manager
    to show Show on, so the digits are revealed at once: the state the page draws after Show."""
    request = urllib.request.Request(instance.manager_url("/api/v1/auth/code"), data=b"{}",
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as answer:
        json.load(answer)


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class SignInDriveTests(TempTree):
    BOOTS_PER_TEST = "Each test starts from a window with nothing waiting."

    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, "Alpha Table",
                   info={"Info": {"Name": "Alpha Table"}, "VPinFE": {}, "User": {}},
                   medias={"wheel.png": PNG, "table.png": PNG})

    async def _ready(self, browser: BrowserSession, instance: LiveInstance) -> None:
        await browser.navigate(instance.theme_url("playfield"))
        await browser.wait_for("document.body.dataset.ready === 'true'", timeout=90.0)

    def _drive(self, run: Any) -> Any:
        async def go(instance: LiveInstance):
            async with _browser() as browser:
                await self._ready(browser, instance)
                return await run(browser, instance)

        with LiveInstance(self.root) as instance:
            return asyncio.run(go(instance))

    def test_a_request_opens_over_the_theme_and_close_ends_it(self) -> None:
        async def run(browser: BrowserSession, instance: LiveInstance):
            _ask(instance)
            await browser.wait_for(OPEN, timeout=30.0)
            await browser.wait_for(_page("!d.getElementById('stage').hidden && 'drawn'"))
            seen = await browser.evaluate(_page(
                "JSON.stringify({heading: d.getElementById('heading').textContent,"
                " digits: d.getElementById('digits').textContent,"
                " from: d.getElementById('from').textContent,"
                " expires: d.getElementById('expires').textContent,"
                " lasts: d.getElementById('lasts').textContent,"
                " visible: [...d.querySelectorAll('button')].filter(b => !b.hidden)"
                ".map(b => b.id)})"))
            await browser.evaluate(_page("d.getElementById('close').click()"))
            await browser.wait_for("vpin.overlay === null && 'closed'", timeout=30.0)
            return json.loads(seen)

        seen = self._drive(run)

        self.assertEqual(seen["heading"], "Sign-In Code")
        self.assertRegex(seen["digits"], r"^\d{6}$")
        self.assertRegex(seen["from"], r"^The browser at \S+ wants to sign in\. "
                                       r"Type this code there to let it in\.$")
        self.assertEqual(seen["expires"], "The code works for 10 minutes")
        self.assertEqual(seen["lasts"], "Once signed in, that browser stays signed in "
                                        "until it goes unused for 90 days.")
        self.assertEqual(seen["visible"], ["close"])

    def test_a_pairing_code_is_drawn_at_once_and_close_leaves_it_good(self) -> None:
        async def run(browser: BrowserSession, instance: LiveInstance):
            request = urllib.request.Request(
                instance.manager_url("/api/v1/auth/pair/code"),
                data=json.dumps({"name": "Game Room"}).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as answer:
                json.load(answer)
            await browser.wait_for(OPEN, timeout=30.0)
            await browser.wait_for(_page("!d.getElementById('stage').hidden && 'drawn'"))
            seen = await browser.evaluate(_page(
                "JSON.stringify({heading: d.getElementById('heading').textContent,"
                " digits: d.getElementById('digits').textContent,"
                " from: d.getElementById('from').textContent,"
                " expires: d.getElementById('expires').textContent,"
                " lasts: d.getElementById('lasts').textContent,"
                " visible: [...d.querySelectorAll('button')].filter(b => !b.hidden)"
                ".map(b => b.id)})"))
            await browser.evaluate(_page("d.getElementById('close').click()"))
            await browser.wait_for("vpin.overlay === null && 'closed'", timeout=30.0)
            return json.loads(seen)

        seen = self._drive(run)

        self.assertEqual(seen["heading"], "Pairing Code")
        self.assertRegex(seen["digits"], r"^\d{6}$")
        self.assertRegex(seen["from"], r"^Another VPinFE device named Game Room at \S+ wants to "
                                       r"pair with this device\. Type this code there to give it "
                                       r"full control of this device\.$")
        self.assertEqual(seen["expires"], "The code works for 10 minutes")
        self.assertEqual(seen["lasts"], "Once paired, it stays paired until you remove it "
                                        "under Security > Tokens.")
        self.assertEqual(seen["visible"], ["close"])

    def test_back_ends_it_too(self) -> None:
        async def run(browser: BrowserSession, instance: LiveInstance):
            _ask(instance)
            await browser.wait_for(OPEN, timeout=30.0)
            await browser.wait_for(_page("!d.getElementById('stage').hidden && 'drawn'"))
            await browser.press("Escape", "Escape")
            return await browser.wait_for("vpin.overlay === null && 'closed'", timeout=30.0)

        self.assertEqual(self._drive(run), "closed")

    def test_nothing_is_drawn_over_a_running_table_and_it_shows_when_that_ends(self) -> None:
        async def run(browser: BrowserSession, instance: LiveInstance):
            await browser.evaluate("vpin.handleEvent({type: 'TableLaunching'})")
            _ask(instance)
            await asyncio.sleep(HELD_QUIET_WINDOW_S)
            during = await browser.evaluate("vpin.overlay")
            await browser.evaluate("vpin.handleEvent({type: 'TableLaunchComplete'})")
            after = await browser.wait_for(OPEN, timeout=30.0)
            return during, after

        self.assertEqual(self._drive(run), (None, "open"))

    def test_a_request_not_yet_shown_asks_before_it_gives_the_digits(self) -> None:
        async def run(browser: BrowserSession, instance: LiveInstance):
            _ask(instance)
            await browser.wait_for(OPEN, timeout=30.0)
            await browser.wait_for(_page("!d.getElementById('stage').hidden && 'drawn'"))
            # The same request as the machine would hold it before Show, pushed to the
            # page the way the machine pushes a change.
            await browser.evaluate("""
                vpin.handleEvent({type: 'SignInRequest', request: {
                  id: 'r1', address: '192.168.1.20', state: 'waiting', code: '',
                  agent: 'Safari on iPad', place: 'ipad.lan', expires_in: 600}})""")
            await browser.wait_for(_page(
                "d.getElementById('heading').textContent.includes('wants to sign in') && 'ok'"))
            return json.loads(await browser.evaluate(_page(
                "JSON.stringify({heading: d.getElementById('heading').textContent,"
                " digits: d.getElementById('digits').hidden,"
                " focused: d.querySelector('button.focus')?.id,"
                " visible: [...d.querySelectorAll('button')].filter(b => !b.hidden)"
                ".map(b => b.id)})")))

        seen = self._drive(run)

        self.assertEqual(seen, {"heading": "Safari on iPad at ipad.lan wants to sign in",
                                "digits": True,
                                "focused": "show", "visible": ["show", "ignore"]})


if __name__ == "__main__":
    unittest.main()

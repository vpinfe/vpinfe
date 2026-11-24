"""A program's ask on a Manager open on this device, driven in a real browser.

Slow: boots a real instance and a real browser. The Manager on the machine itself opens a
dialog the moment a Full Control token asks; Close leaves the ask on the Needs You page.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from common.i18n import t
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk

DIALOG = ".manager-dialog--question"
BUTTON = f"{DIALOG} .manager-dialog-footer button"
DECLINE, CLOSE, APPROVE = 0, 1, 2
ROW = ".manager-need--static"
ROW_BUTTON = f"{ROW} .manager-action"
CHANGE = "/extensions/nothing/accept"
TEXT_OF = "(() => { const el = document.querySelector(%s); return el ? el.innerText : null; })()"


def _call(instance: LiveInstance, method: str, path: str, body: dict | None = None, *,
          secret: str = "", approval: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if secret:
        # A forwarding header is what makes a call from this machine a network caller, so
        # the token is the identity rather than the machine.
        headers["Authorization"] = "Bearer " + secret
        headers["X-Forwarded-For"] = "192.168.1.50"
    if approval:
        headers["X-VPinFE-Approval"] = approval
    request = urllib.request.Request(
        instance.manager_url(path), method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            return answer.status, json.load(answer)
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else {}


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class ManagerAskFirstDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        binary = chromium_path()
        assert binary is not None
        with TemporaryDirectory() as tmp, LiveInstance(Path(tmp)) as instance:
            cls.seen = asyncio.run(cls._drive(instance, binary))

    @classmethod
    async def _drive(cls, instance: LiveInstance, binary: str) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, instance)
            await walk.visit("/")
            made_status, made = _call(instance, "POST", "/api/v1/auth/tokens",
                                      {"name": "Home Assistant", "access": "full",
                                       "can_power": False})
            seen["token_status"] = made_status
            secret = made.get("secret", "")
            seen["quiet_before"] = await browser.evaluate(TEXT_OF % json.dumps(DIALOG))

            # Approve on the dialog, then repeat the request once.
            status, first = _call(instance, "POST", "/api/v1/auth/asks",
                                  {"method": "POST", "path": CHANGE, "body": {"name": "a"}},
                                  secret=secret)
            assert status == 200, (made_status, made, status, first)
            await browser.wait_for(f"!!document.querySelector({json.dumps(DIALOG)})", timeout=30)
            seen["dialog"] = await browser.evaluate(TEXT_OF % json.dumps(DIALOG))
            seen["dialog_buttons"] = await browser.evaluate(
                f"[...document.querySelectorAll({json.dumps(BUTTON)})]"
                ".map(el => el.innerText.split('\\n').pop())")
            await browser.click(BUTTON, nth=APPROVE)
            await browser.wait_for(f"!document.querySelector({json.dumps(DIALOG)})", timeout=30)
            seen["approved"] = _call(instance, "GET", f"/api/v1/auth/asks/{first['id']}",
                                     secret=secret)[1].get("state")
            seen["without_the_id"] = _call(instance, "POST", "/api/v1/" + CHANGE.lstrip("/"),
                                           {"name": "a"}, secret=secret)[0]
            seen["with_the_id"] = _call(instance, "POST", "/api/v1/" + CHANGE.lstrip("/"),
                                        {"name": "a"}, secret=secret, approval=first["id"])[0]
            seen["again"] = _call(instance, "POST", "/api/v1/" + CHANGE.lstrip("/"),
                                  {"name": "a"}, secret=secret, approval=first["id"])[0]

            # Close leaves the ask on Needs You, where Decline answers it.
            _, second = _call(instance, "POST", "/api/v1/auth/asks",
                              {"method": "POST", "path": CHANGE, "body": {"name": "b"}},
                              secret=secret)
            await browser.wait_for(f"!!document.querySelector({json.dumps(DIALOG)})", timeout=30)
            await browser.click(BUTTON, nth=CLOSE)
            await browser.wait_for(f"!document.querySelector({json.dumps(DIALOG)})", timeout=30)
            seen["still_waiting"] = _call(instance, "GET", f"/api/v1/auth/asks/{second['id']}",
                                          secret=secret)[1].get("state")
            await browser.wait_for(f"!!document.querySelector({json.dumps(ROW)})", timeout=60)
            seen["row"] = await browser.evaluate(TEXT_OF % json.dumps(ROW))
            await browser.click(ROW_BUTTON, nth=0)
            await browser.wait_for(f"!document.querySelector({json.dumps(ROW)})", timeout=30)
            seen["declined"] = _call(instance, "GET", f"/api/v1/auth/asks/{second['id']}",
                                     secret=secret)[1].get("state")
        return seen

    def test_a_token_is_made_to_ask_with(self) -> None:
        self.assertEqual(self.seen["token_status"], 200)

    def test_nothing_shows_until_a_program_asks(self) -> None:
        self.assertIsNone(self.seen["quiet_before"])

    def test_the_dialog_names_the_program_and_the_exact_request(self) -> None:
        words = str(self.seen["dialog"])
        self.assertIn("Home Assistant", words)
        self.assertIn(f"POST {CHANGE}", words)
        self.assertIn('{"name":"a"}', words)

    def test_the_dialog_sets_decline_apart_and_ends_on_approve(self) -> None:
        self.assertEqual(self.seen["dialog_buttons"],
                         [t("manager.needs.decline"), t("word.close"),
                          t("manager.needs.approve")])

    def test_approving_lets_that_one_request_through_once(self) -> None:
        self.assertEqual(self.seen["approved"], "approved")
        self.assertEqual(self.seen["without_the_id"], 403)
        self.assertEqual(self.seen["with_the_id"], 404)
        self.assertEqual(self.seen["again"], 403)

    def test_close_leaves_the_ask_waiting_on_needs_you(self) -> None:
        self.assertEqual(self.seen["still_waiting"], "waiting")
        self.assertIn("Home Assistant", str(self.seen["row"]))

    def test_declining_on_the_row_answers_it(self) -> None:
        self.assertEqual(self.seen["declined"], "declined")


if __name__ == "__main__":
    unittest.main()

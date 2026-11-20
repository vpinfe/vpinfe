"""Frontend › Players, a picture copied from a service: the Online source in the picture
dialog offers it only while the service has one, copies it once, and Undo puts the old
picture back.

Slow: boots a real instance and a real browser. The service is the sample fixture
extension, installed into the instance's own extensions folder.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from common.security import accepted, machine_secrets
from tests.support import pictures
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import game_info, write_game
from tests.support.live_instance import REPO_ROOT, LiveInstance
from tests.support.manager_walk import ManagerWalk
from tests.support.players_page import (
    ACTS,
    AT,
    DIALOG,
    DIALOG_TEXT,
    DIALOG_UP,
    NOTICE,
    TITLE,
    _cell,
    _centre,
)

SAMPLE = REPO_ROOT / "tests" / "fixtures" / "extensions" / "sample"


class PictureFromAService(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not chromium_path():
            raise unittest.SkipTest("no Chromium on this machine")
        with TemporaryDirectory() as tmp:
            write_game(Path(tmp), "Alpha", info=game_info(
                "Alpha", vps_id="", game_id="alpha",
                Info={"Manufacturer": "Bally", "Year": "1992"}))
            instance = LiveInstance(Path(tmp))
            shutil.copytree(SAMPLE, instance.config_dir / "extensions" / "sample")
            declared = json.loads((SAMPLE / "extension.json").read_text(encoding="utf-8"))
            machine_secrets.write_private(instance.config_dir / "accepted.json", {
                "themes": {}, "seeded": True, "extensions": {"sample": {
                    "origin": accepted.IN_FOLDER,
                    "folder": str(instance.config_dir / "extensions"),
                    "version": declared["version"], "scopes": sorted(declared["scopes"]),
                    "capabilities": sorted(declared["capabilities"])}}})
            with instance:
                cls.seen = asyncio.run(cls._drive(instance))

    @classmethod
    async def _drive(cls, instance: LiveInstance) -> dict:
        seen: dict = {}
        instance.wait_for_api()
        owner = instance.api("/api/v1/players")["players"][0]["id"]
        account = instance.manager_url(f"/api/v1/players/{owner}/accounts/sample")

        def put(values: dict) -> None:
            urllib.request.urlopen(urllib.request.Request(
                account, method="PUT", data=json.dumps({"values": values}).encode(),
                headers={"Content-Type": "application/json"}), timeout=10).close()

        def stored() -> tuple[int, int, int] | None:
            try:
                with urllib.request.urlopen(instance.manager_url(
                        f"/api/v1/players/{owner}/picture"), timeout=10) as held:
                    return _centre(held.read())
            except urllib.error.HTTPError:
                return None

        async def stored_is(color: tuple[int, int, int]) -> bool:
            for _ in range(100):
                now = stored()
                if now is not None and all(
                        abs(a - b) < 16 for a, b in zip(now, color, strict=True)):
                    return True
                await asyncio.sleep(0.05)
            return False

        async with BrowserSession(chromium_path() or "") as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": 1600, "height": 1000, "deviceScaleFactor": 1,
                                "mobile": False})
            walk = ManagerWalk(browser, instance)

            async def press(selector: str, says: str) -> None:
                found = AT % (json.dumps(selector), json.dumps(says))
                nth = await browser.wait_for(f"(at => at >= 0 ? at + 1 : 0)({found})")
                await browser.click(selector, nth=int(nth) - 1)

            async def open_dialog(which: str) -> None:
                await press(ACTS, which)
                await browser.wait_for(DIALOG_UP)

            async def visit() -> None:
                await walk.visit("/manager?view=players")
                await walk.drawn()
                await browser.click(_cell(owner, ".ag-cell[col-id=initials]"))
                await browser.wait_for(f"{TITLE}.length > 0")

            async def noticed(said: str) -> None:
                await browser.wait_for(
                    f"[...document.querySelectorAll('{NOTICE}')]"
                    f".some(n => n.innerText.includes({json.dumps(said)}))")

            async def copy_from_service() -> str:
                """Open the Online source, press the service's button, and wait for the
                dialog to go. Returns what the dialog said."""
                await press(f"{DIALOG} .q-tab", "Online")
                # The Online panel is drawn after its tab opens: the dialog's text before
                # that point is the tab bar alone, so wait for the button itself.
                said = await browser.wait_for(
                    f"(said => said && said.includes(\"Use Sample's Picture\") ? said : null)"
                    f"({DIALOG_TEXT})")
                # The panel moves the card as it draws: a click on the way lands on the
                # backdrop and dismisses the dialog.
                rect = ("(b => b ? JSON.stringify(b.getBoundingClientRect()) : null)"
                        "([...document.querySelectorAll('.q-dialog .q-btn')]"
                        ".find(b => b.innerText.includes(\"Use Sample's Picture\")))")
                before = None
                for _ in range(100):
                    now = await browser.evaluate(rect)
                    if now is not None and now == before:
                        break
                    before = now
                    await asyncio.sleep(0.1)
                await press(f"{DIALOG} .q-btn", "Use Sample's Picture")
                await browser.wait_for(f"!document.querySelector('{DIALOG}')")
                return said

            # The service has no picture yet: the Online source is not offered.
            await visit()
            await open_dialog("Add")
            seen["tabs_without"] = await browser.evaluate(
                f"[...document.querySelectorAll('{DIALOG} .q-tab')].map(t => t.innerText.trim())")

            # A picture from the service, copied.
            put({"picture": "blue"})
            await visit()
            await open_dialog("Add")
            seen["offer"] = await copy_from_service()
            await noticed("Picture added")
            seen["copied"] = await stored_is(pictures.BLUE)

            # Following the service is not what happened: it changes, the copy does not.
            put({"picture": "red"})
            seen["not_followed"] = await stored_is(pictures.BLUE)

            # A second press replaces, and Undo puts the first back.
            await open_dialog("Replace")
            await copy_from_service()
            await noticed("Picture replaced")
            seen["replaced"] = await stored_is(pictures.RED)
            await browser.evaluate(
                f"[...document.querySelectorAll('{NOTICE}')]"
                f".find(n => n.innerText.includes('Picture replaced'))"
                ".querySelector('.q-btn').click()")
            seen["undone"] = await stored_is(pictures.BLUE)

            seen["page_log"] = [line for line in browser.page_log if "error" in line.lower()]
        return seen

    def test_the_online_source_is_offered_only_when_the_service_has_a_picture(self) -> None:
        self.assertNotIn("Online", self.seen["tabs_without"])
        self.assertIn("Use Sample's Picture", self.seen["offer"])

    def test_the_picture_is_copied_once_and_never_followed(self) -> None:
        self.assertTrue(self.seen["copied"])
        self.assertTrue(self.seen["not_followed"])

    def test_a_copied_picture_is_replaced_and_undo_puts_the_old_one_back(self) -> None:
        self.assertTrue(self.seen["replaced"])
        self.assertTrue(self.seen["undone"])

    def test_nothing_failed_in_the_browser(self) -> None:
        self.assertEqual(self.seen["page_log"], [])


if __name__ == "__main__":
    unittest.main()

"""Create Support Package in a real browser, in Dark and Light at phone width: the three
steps, what Inspect Everything marks, removing a line, and the places it goes.

Slow: serves a bare page whose device read is a fixed one, and drives the dialog.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from typing import Any
from urllib.parse import unquote

from tests.support.browser_session import BrowserSession
from tests.theming.test_grid_selection_drive import drive_page, exit_with_parent

DIALOG = ".q-dialog"
FORWARD = f"{DIALOG} .manager-wizard-forward"
PHONE = 390
HOST = "cabinet-zed"
HOME = "/home/someone"

SUMMARY = """# VPinFE support package

## VPinFE

- **Version:** 3.0.0
- **Build:** Full

## This Device

- **Operating system:** Linux 6.8
- **Windowing system:** Wayland on cabinet-zed
"""
LOG = (f"2026-01-02 03:04:01 INFO [vpinfe.test] opened {HOME}/tables for the first time\n"
       f"2026-01-02 03:04:02 INFO [vpinfe.test] {HOST} is up and listening\n"
       "2026-01-02 03:04:03 ERROR [vpinfe.test] something went wrong here\n")


def serve(port: int) -> None:
    from nicegui import app, ui

    from common.support import package
    from common.support.redact import Identity
    from manager.kit import api, appearance
    from manager.system import support

    api.local_base_url = lambda: "http://127.0.0.1:9"
    app.add_static_files(appearance.BASE_MOUNT, str(appearance.BASE_CSS.parent))
    package.collect = lambda options=None: package.Raw(
        datetime.now().astimezone(), Identity(homes=(HOME,), hosts=(HOST,)), SUMMARY,
        (package.Part(package.LOG, LOG),))

    for mode in ("dark", "light"):
        def page(mode: str = mode) -> None:
            appearance.apply_mode(mode)
            ui.button("Open", on_click=support.open_support_package).props("id=open")

        ui.page(f"/{mode}")(page)

    @app.get("/heard")
    def heard() -> dict:
        return {}

    ui.run(port=port, show=False, reload=False, title="Support drive")


class SupportDialogDrive(unittest.TestCase):
    seen: dict[str, dict] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.seen = drive_page("tests.theming.test_support_dialog_drive", cls._drive)

    @classmethod
    async def _drive(cls, binary: str, port: int) -> dict:
        seen: dict[str, dict] = {}
        async with BrowserSession(binary) as browser:
            await browser.send("Emulation.setDeviceMetricsOverride",
                               {"width": PHONE, "height": 800, "deviceScaleFactor": 1,
                                "mobile": False})

            async def press(label: str) -> None:
                clicked = await browser.evaluate(
                    f"(() => {{ const b = [...document.querySelectorAll('{DIALOG} button')]"
                    f".find(b => b.innerText.toLowerCase().includes({json.dumps(label.lower())})); "
                    f"if (b) b.click(); return !!b; }})()")
                assert clicked, f"no button reads {label}"

            async def reads(text: str) -> None:
                try:
                    await browser.wait_for(
                        f"(document.querySelector('{DIALOG}') || {{}}).innerText"
                        f"?.toLowerCase().includes({json.dumps(text.lower())})")
                except TimeoutError as exc:
                    shown = await browser.evaluate(
                        f"(document.querySelector('{DIALOG}') || {{}}).innerText")
                    raise AssertionError(f"waiting for {text!r}, the dialog says {shown!r}") \
                        from exc

            async def fits() -> bool:
                return bool(await browser.evaluate(
                    f"(() => {{ const c = document.querySelector('{DIALOG} .q-card'); "
                    f"const s = c.querySelector('.manager-wizard-scroll'); "
                    f"return c.scrollWidth <= c.clientWidth + 1 && "
                    f"s.scrollWidth <= s.clientWidth + 1; }})()"))

            for mode in ("dark", "light"):
                found: dict[str, Any] = {}
                await browser.navigate(f"http://127.0.0.1:{port}/{mode}")
                await browser.wait_for("!!document.querySelector('#open')")
                await browser.click("#open")
                await reads("What Goes In")
                found["what_fits"] = await fits()

                await browser.click(FORWARD)
                await reads("Private Details Replaced")
                found["privacy_fits"] = await fits()
                found["privacy"] = await browser.evaluate(
                    f"document.querySelector('{DIALOG}').innerText")

                await press("Inspect Everything")
                await browser.wait_for(f"!!document.querySelector('{DIALOG} .support-lines')")
                await browser.click(f"{DIALOG} .support-inspect .q-select")
                await browser.wait_for("!!document.querySelector('.q-menu .q-item')")
                await browser.evaluate(
                    "[...document.querySelectorAll('.q-menu .q-item')]"
                    ".find(i => i.innerText.trim() === 'vpinfe.log').click()")
                await browser.wait_for(
                    f"!!document.querySelector('{DIALOG} .support-mark')")
                found["marks"] = await browser.evaluate(
                    f"document.querySelectorAll('{DIALOG} .support-mark').length")
                found["mark_ground"] = await browser.evaluate(
                    f"getComputedStyle(document.querySelector('{DIALOG} .support-mark'))"
                    f".backgroundColor")
                found["mark_titles"] = await browser.evaluate(
                    f"[...document.querySelectorAll('{DIALOG} .support-mark')]"
                    f".map(m => m.title)")
                found["inspect_fits"] = await fits()

                await browser.evaluate(
                    f"(() => {{ const s = document.querySelector('{DIALOG} .support-lines'); "
                    f"s.querySelector('[data-line]').click(); }})()")
                await reads("Remove Line")
                await press("Remove Line")
                await browser.wait_for(
                    f"!!document.querySelector('{DIALOG} .support-line--removed')")
                found["removed"] = await browser.evaluate(
                    f"document.querySelectorAll('{DIALOG} .support-line--removed').length")

                await press("Back to Summary")
                await reads("Private Details Replaced")
                await browser.click(FORWARD)
                await reads("Copy Summary")
                found["share_fits"] = await fits()
                found["links"] = await browser.evaluate(
                    f"[...document.querySelectorAll('{DIALOG} a')].map(a => "
                    f"({{href: a.href, target: a.target, rel: a.rel}}))")

                await browser.click(FORWARD)
                await browser.wait_for(f"!document.querySelector('{DIALOG}')")
                seen[mode] = found
        return seen

    def test_every_step_fits_a_phone_in_both_modes(self) -> None:
        for mode, found in self.seen.items():
            for step in ("what", "privacy", "inspect", "share"):
                self.assertTrue(found[f"{step}_fits"], f"{mode} {step}")

    def test_privacy_says_what_was_replaced(self) -> None:
        for mode, found in self.seen.items():
            with self.subTest(mode=mode):
                self.assertIn("private details replaced", found["privacy"].lower())
                self.assertIn("Device Name", found["privacy"])
                self.assertNotIn(HOST, found["privacy"])

    def test_inspect_marks_each_replaced_value_and_shows_the_original_on_hover(self) -> None:
        for mode, found in self.seen.items():
            with self.subTest(mode=mode):
                self.assertGreaterEqual(found["marks"], 2)
                self.assertNotEqual(found["mark_ground"], "rgba(0, 0, 0, 0)")
                self.assertIn(f"Device Name - {HOST}", found["mark_titles"])

    def test_a_picked_line_is_removed(self) -> None:
        for mode, found in self.seen.items():
            with self.subTest(mode=mode):
                self.assertEqual(found["removed"], 1)

    def test_both_places_open_in_a_new_tab_without_an_opener(self) -> None:
        for mode, found in self.seen.items():
            with self.subTest(mode=mode):
                hrefs = {one["href"].split("?")[0]: one for one in found["links"]}
                self.assertIn("https://discord.gg/SFBfA6Te2A", hrefs)
                self.assertIn("https://github.com/vpinfe/vpinfe/issues/new", hrefs)
                for one in found["links"]:
                    self.assertEqual(one["target"], "_blank")
                    self.assertIn("noopener", one["rel"])

    def test_no_link_carries_a_private_value(self) -> None:
        for mode, found in self.seen.items():
            with self.subTest(mode=mode):
                github = [one for one in found["links"] if "github.com" in one["href"]]
                self.assertTrue(github, "no GitHub link was drawn")
                self.assertIn("Wayland", unquote(github[0]["href"]),
                              "the link must carry the windowing fact the host rides on")
                for one in found["links"]:
                    self.assertNotIn(HOST, one["href"])
                    self.assertNotIn(HOME, one["href"])


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        exit_with_parent()
        serve(int(sys.argv[2]))
    else:
        unittest.main()

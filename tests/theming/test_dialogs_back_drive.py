"""Back with a dialog open, in a real browser: it closes the dialog and leaves the page where
it is, a dialog closed by its own button leaves no entry behind, a persistent one stays
open and keeps its entry, and nested dialogs close one at a time.

Slow: serves a bare page that opens each kind of dialog, and drives it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest

from tests.support.browser_session import BrowserSession
from tests.support.manager_walk import ManagerWalk
from tests.theming.test_escape_in_dialogs_drive import _Page
from tests.theming.test_grid_selection_drive import drive_page, exit_with_parent

DIALOG = ".q-dialog"
COUNT = f"document.querySelectorAll('{DIALOG}').length"
TITLE = f"((document.querySelector('{DIALOG} .manager-dialog-title') || {{}}).innerText || '')"
LAST_TITLE = ("(() => { const all = document.querySelectorAll('.q-dialog .manager-dialog-title');"
              " return all.length ? all[all.length - 1].innerText : ''; })()")
PLACE = "(() => ({search: location.search, popped: window.popped, state: history.state}))()"
SETTLE_S = 1.0


def serve(port: int) -> None:
    """A button for a plain dialog, one for a persistent one, and one for a dialog that
    opens another. The page counts the `popstate` events that reach it."""
    from nicegui import app, ui

    from manager.kit import api, appearance, deeplink, walks
    from manager.kit import dialog as frame

    api.local_base_url = lambda: "http://127.0.0.1:9"
    app.add_static_files(appearance.BASE_MOUNT, str(appearance.BASE_CSS.parent))

    @ui.page("/")
    async def page() -> None:
        appearance.apply_mode()

        async def plain() -> None:
            with frame.opened("Plain", ignore_outside_click=True) as box:
                with frame.footer():
                    frame.cancel(lambda: box.submit(walks.SKIPPED))
            await box

        async def required() -> None:
            with frame.opened("Required", persistent=True) as box:
                with frame.footer():
                    frame.answer("Close", lambda: box.submit(True), icon="close")
            await box

        async def inner() -> None:
            with frame.opened("Inner", ignore_outside_click=True) as box:
                with frame.footer():
                    frame.cancel(lambda: box.submit(walks.SKIPPED))
            await box

        async def outer() -> None:
            with frame.opened("Outer", ignore_outside_click=True) as box:
                ui.button("Open inner", on_click=inner).props("id=inner")
                with frame.footer():
                    frame.cancel(lambda: box.submit(walks.SKIPPED))
            await box

        async def leaving() -> None:
            with frame.opened("Leaving", ignore_outside_click=True) as box:
                with frame.footer():
                    frame.answer("Leave", lambda: box.submit(True), icon="close")
            await box
            deeplink.sync({"view": "games"}, push=True)

        ui.button("Leave", on_click=leaving).props("id=leave")
        ui.button("Plain", on_click=plain).props("id=plain")
        ui.button("Required", on_click=required).props("id=required")
        ui.button("Outer", on_click=outer).props("id=outer")
        deeplink.dialogs_follow_back()
        ui.run_javascript("window.popped = 0;"
                          " window.addEventListener('popstate', () => { window.popped++; })")

    @app.get("/heard")
    def said() -> dict:
        return {}

    ui.run(port=port, show=False, reload=False, title="Dialogs and Back")


class DialogsBackDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.seen = drive_page("tests.theming.test_dialogs_back_drive", cls._drive)

    @classmethod
    async def _drive(cls, binary: str, port: int) -> dict:
        seen: dict = {}
        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, _Page(port))

            async def opened(button: str, title: str) -> None:
                await walk.act(lambda: browser.click(f"#{button}"),
                               until=f"{TITLE} === {json.dumps(title)}")

            async def gone() -> None:
                await browser.wait_for(f"{COUNT} === 0")
                await asyncio.sleep(SETTLE_S)

            async def fresh() -> None:
                await walk.visit("/?first=1")
                await browser.wait_for("window.popped === 0")
                await walk.visit("/")
                await browser.wait_for("window.popped === 0")

            # Back closes a dialog and the page stays where it was.
            await fresh()
            await opened("plain", "Plain")
            await browser.evaluate("history.back()")
            await gone()
            seen["back closes"] = await browser.evaluate(PLACE)

            # A dialog closed by its own button gives its entry back, so the next Back
            # leaves the page and does not spend itself on a dead entry.
            await opened("plain", "Plain")
            await browser.click(f"{DIALOG} .manager-dialog-footer button")
            await gone()
            seen["button closes"] = await browser.evaluate(PLACE)
            await browser.evaluate("history.back()")
            await browser.wait_for("location.search === '?first=1'")

            # A persistent dialog stays open on Back, and still holds an entry.
            await fresh()
            await opened("required", "Required")
            await browser.evaluate("history.back()")
            await asyncio.sleep(SETTLE_S)
            seen["persistent after back"] = {**await browser.evaluate(PLACE),
                                             "open": await browser.evaluate(COUNT)}
            await browser.evaluate("history.back()")
            await asyncio.sleep(SETTLE_S)
            seen["persistent after back twice"] = {**await browser.evaluate(PLACE),
                                                   "open": await browser.evaluate(COUNT)}
            await browser.click(f"{DIALOG} .manager-dialog-footer button")
            await gone()
            await browser.evaluate("history.back()")
            await browser.wait_for("location.search === '?first=1'")

            # Nested: Back closes the inner dialog, and then the outer.
            await fresh()
            await opened("outer", "Outer")
            await walk.act(lambda: browser.click(f"{DIALOG} #inner"),
                           until=f"{LAST_TITLE} === 'Inner'")
            await browser.evaluate("history.back()")
            await browser.wait_for(f"{COUNT} === 1")
            await asyncio.sleep(SETTLE_S)
            seen["inner closed"] = {**await browser.evaluate(PLACE),
                                    "title": await browser.evaluate(TITLE)}
            await browser.evaluate("history.back()")
            await gone()
            seen["outer closed"] = await browser.evaluate(PLACE)

            # A place pushed as the dialog goes away takes the dialog's entry, so Back from
            # it reaches the page it left and not a dead entry.
            await fresh()
            await opened("leave", "Leaving")
            await browser.click(f"{DIALOG} .manager-dialog-footer button")
            await browser.wait_for("location.search === '?view=games'")
            await gone()
            seen["left"] = await browser.evaluate(PLACE)
            await browser.evaluate("history.back()")
            await browser.wait_for("location.search === ''")
            await asyncio.sleep(SETTLE_S)
            seen["left, then back"] = await browser.evaluate(PLACE)

            # Forward onto the entry a closed dialog left does nothing.
            await fresh()
            await opened("plain", "Plain")
            await browser.evaluate("history.back()")
            await gone()
            await browser.evaluate("history.forward()")
            await asyncio.sleep(SETTLE_S)
            seen["forward"] = await browser.evaluate(PLACE)
        return seen

    def test_back_closes_the_dialog_and_the_page_stays(self) -> None:
        place = self.seen["back closes"]
        self.assertEqual("", place["search"])
        self.assertEqual(0, place["popped"])

    def test_a_dialog_closed_by_its_own_button_leaves_no_entry_behind(self) -> None:
        place = self.seen["button closes"]
        self.assertEqual("", place["search"])
        self.assertEqual(0, place["popped"])

    def test_a_persistent_dialog_stays_open_on_back_and_keeps_an_entry(self) -> None:
        for name in ("persistent after back", "persistent after back twice"):
            self.assertEqual(1, self.seen[name]["open"])
            self.assertEqual("", self.seen[name]["search"])
            self.assertEqual(0, self.seen[name]["popped"])
            self.assertTrue((self.seen[name]["state"] or {}).get("vpinSentinel"))

    def test_back_closes_nested_dialogs_one_at_a_time(self) -> None:
        self.assertEqual("Outer", self.seen["inner closed"]["title"])
        self.assertEqual(0, self.seen["inner closed"]["popped"])
        self.assertEqual("", self.seen["outer closed"]["search"])
        self.assertEqual(0, self.seen["outer closed"]["popped"])

    def test_a_place_pushed_as_a_dialog_goes_away_leaves_no_entry_under_it(self) -> None:
        self.assertEqual("?view=games", self.seen["left"]["search"])
        self.assertEqual("", self.seen["left, then back"]["search"])
        self.assertEqual(1, self.seen["left, then back"]["popped"])

    def test_forward_onto_the_entry_a_dialog_left_does_nothing(self) -> None:
        self.assertEqual("", self.seen["forward"]["search"])
        self.assertEqual(0, self.seen["forward"]["popped"])


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        exit_with_parent()
        serve(int(sys.argv[2]))
    else:
        unittest.main()

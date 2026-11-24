"""Escape and a click outside a Manager dialog, in a real browser: Escape cancels one with
typed input and a click outside does not, a persistent one answers neither, Escape in a
walk over a selection ends the run, and Escape cannot leave a wizard run in progress but
closes the report it ends on.

Slow: serves a bare page that opens each kind of dialog, and drives it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from collections.abc import Callable
from typing import Any

from tests.support.browser_session import BrowserSession
from tests.support.manager_walk import ManagerWalk
from tests.theming.test_grid_selection_drive import _heard, drive_page, exit_with_parent

DIALOG = ".q-dialog"
BACKDROP = ".q-dialog__backdrop"
GONE = "!document.querySelector(%s)"
TITLE = f"((document.querySelector('{DIALOG} .manager-dialog-title') || {{}}).innerText || '')"
ROWS = ["alpha", "beta", "gamma"]


class _Page:
    def __init__(self, port: int) -> None:
        self.port = port

    def manager_url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


def serve(port: int) -> None:
    """A button for a dialog with typed input, one for a persistent one, one that walks
    three rows, and one for a wizard whose run waits to be released."""
    from nicegui import app, ui

    from manager.kit import api, appearance, walks, wizard
    from manager.kit import dialog as frame

    api.local_base_url = lambda: "http://127.0.0.1:9"
    app.add_static_files(appearance.BASE_MOUNT, str(appearance.BASE_CSS.parent))
    heard: dict = {"plain": "unasked", "plain_open": None, "required": "unasked",
                   "required_open": None, "asked": [], "written": [], "walk": "unasked",
                   "wizard": "unasked", "wizard_run": "unasked", "wizard_answer": None}
    released = asyncio.Event()

    def ping(box: Any, key: str) -> None:
        """Said from inside the dialog, so it reaches the server after anything the browser
        sent about the dialog before it: whether the dialog is still open is then settled."""
        heard[key] = box.value

    @ui.page("/")
    def page() -> None:
        appearance.apply_mode()

        async def plain() -> None:
            heard["plain"] = "open"
            with frame.opened("Plain", ignore_outside_click=True) as box:
                frame.field()
                ui.button("Ping", on_click=lambda: ping(box, "plain_open")).props("id=ping")
                with frame.footer():
                    frame.cancel(lambda: box.submit(walks.SKIPPED))
            heard["plain"] = "answered" if await box else "left"

        async def required() -> None:
            heard["required"] = "open"
            with frame.opened("Required", persistent=True) as box:
                ui.button("Ping", on_click=lambda: ping(box, "required_open")).props("id=ping")
                with frame.footer():
                    frame.answer("Close", lambda: box.submit(True), icon="close")
            heard["required"] = "answered" if await box else "left"

        async def ask(row: dict, place: str) -> object:
            heard["asked"].append(row["id"])
            with frame.opened(f"Row {row['id']}", ignore_outside_click=True) as box:
                ui.label(place)
                with frame.footer():
                    frame.leave(box, True)
                    frame.answer("Save", lambda: box.submit("saved"), icon="check")
            return await box

        async def write(row: dict, answer: object) -> None:
            heard["written"].append(row["id"])

        async def walk() -> None:
            heard["walk"] = "walking"
            await walks.each([{"id": one} for one in ROWS], ask, write, "failed")
            heard["walk"] = "done"

        async def first(_key: str | None) -> dict:
            return {"step": "go", "title": "Wizard", "help": "", "steps": [], "fields": [],
                    "confirm": "Run it"}

        async def never(*_asked: Any) -> dict:
            return {}

        async def run(_values: dict, _step: str) -> dict:
            heard["wizard_run"] = "running"
            await released.wait()
            return {"ok": True, "message": "Finished"}

        async def wizard_flow() -> None:
            heard["wizard"] = "open"
            heard["wizard_answer"] = await wizard.open_dialog(
                label="Wizard", under="drive.wizard",
                calls=wizard.Calls(first, never, never, run, never))
            heard["wizard"] = "closed"

        ui.button("Plain", on_click=plain).props("id=plain")
        ui.button("Required", on_click=required).props("id=required")
        ui.button("Walk", on_click=walk).props("id=walk")
        ui.button("Wizard", on_click=wizard_flow).props("id=wizard")

    @app.get("/heard")
    def said() -> dict:
        return heard

    @app.get("/release")
    async def release() -> dict:
        released.set()
        return {}

    ui.run(port=port, show=False, reload=False, title="Escape drive")


class EscapeInDialogsDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.seen = drive_page("tests.theming.test_escape_in_dialogs_drive", cls._drive)

    @classmethod
    async def _drive(cls, binary: str, port: int) -> dict:
        seen: dict = {}

        async def heard() -> dict:
            return await asyncio.to_thread(_heard, port)

        async def server_says(said: Callable[[dict], bool]) -> dict:
            """Until `said`, over what the server has heard, is true."""
            for _ in range(400):
                now = await heard()
                if said(now):
                    return now
                await asyncio.sleep(0.05)
            raise AssertionError(f"the server never heard what was waited for: {now}")

        async with BrowserSession(binary) as browser:
            walk = ManagerWalk(browser, _Page(port))
            await walk.visit("/")

            async def opened(button: str, title: str) -> None:
                await walk.act(lambda: browser.click(f"#{button}"),
                               until=f"{TITLE} === {json.dumps(title)}")

            async def pinged(key: str) -> dict:
                """The dialog's own button pressed, and the server's word that it did."""
                await browser.evaluate(f"document.querySelector('{DIALOG} #ping').click()")
                return await server_says(lambda h: h[key] is not None)

            # Typed input: a click outside leaves it open, Escape cancels it.
            await opened("plain", "Plain")
            await browser.evaluate(f"document.querySelector('{BACKDROP}').click()")
            seen["plain_after_click"] = await pinged("plain_open")
            await browser.escape()
            await browser.wait_for(GONE % json.dumps(DIALOG))
            seen["plain_escaped"] = await server_says(lambda h: h["plain"] == "left")

            # An answer that cannot be left unsaid: neither one gets out.
            await opened("required", "Required")
            await browser.evaluate(f"document.querySelector('{BACKDROP}').click()")
            await browser.escape()
            seen["required_after_both"] = await pinged("required_open")
            await browser.click(f"{DIALOG} .manager-dialog-footer button")
            await browser.wait_for(GONE % json.dumps(DIALOG))
            await server_says(lambda h: h["required"] == "answered")

            # A walk: Save the first row, then Escape on the second ends the run.
            await opened("walk", "Row alpha")
            await walk.act(lambda: browser.click(f"{DIALOG} .manager-dialog-footer button",
                                                 nth=2),
                           until=f"{TITLE} === 'Row beta'")
            await browser.escape()
            seen["walk"] = await server_says(lambda h: h["walk"] == "done")
            await browser.wait_for(GONE % json.dumps(DIALOG))

            # A wizard run in progress is not left by Escape; the report it ends on is, and
            # that still counts as the run having happened.
            await opened("wizard", "Wizard")
            await browser.click(f"{DIALOG} .manager-wizard-forward")
            await server_says(lambda h: h["wizard_run"] == "running")
            await browser.escape()
            await browser.evaluate("fetch('/release')")
            await browser.wait_for(
                f"(document.querySelector('{DIALOG}') || {{}}).innerText?.includes('Finished')")
            await browser.escape()
            await browser.wait_for(GONE % json.dumps(DIALOG))
            seen["wizard"] = await server_says(lambda h: h["wizard"] == "closed")
        return seen

    def test_a_click_outside_leaves_a_dialog_with_typed_input_open(self) -> None:
        self.assertTrue(self.seen["plain_after_click"]["plain_open"])
        self.assertEqual("open", self.seen["plain_after_click"]["plain"])

    def test_escape_cancels_a_dialog_with_typed_input(self) -> None:
        self.assertEqual("left", self.seen["plain_escaped"]["plain"])

    def test_neither_gets_out_of_a_persistent_dialog(self) -> None:
        self.assertTrue(self.seen["required_after_both"]["required_open"])
        self.assertEqual("open", self.seen["required_after_both"]["required"])

    def test_escape_in_a_walk_ends_the_run_and_keeps_what_was_saved(self) -> None:
        self.assertEqual(["alpha", "beta"], self.seen["walk"]["asked"])
        self.assertEqual(["alpha"], self.seen["walk"]["written"])

    def test_escape_cannot_leave_a_wizard_run_but_closes_its_report_as_run(self) -> None:
        self.assertTrue(self.seen["wizard"]["wizard_answer"])


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        exit_with_parent()
        serve(int(sys.argv[2]))
    else:
        unittest.main()

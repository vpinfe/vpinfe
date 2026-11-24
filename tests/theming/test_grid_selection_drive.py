"""Select-all on a grid too big to send whole, in a real browser.

The ids alone pass the socket's one-megabyte cap, so the selection only arrives if it
is sent in parts and put back together.

Slow: serves a bare page holding one Manager grid and drives it in a real browser.
"""

from __future__ import annotations

import asyncio
import atexit
import functools
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support.browser_session import BrowserSession, chromium_path, free_port

REPO = Path(__file__).resolve().parents[2]
ROWS = 5000
ID_CHARS = 210

API = ("(() => { const el = document.querySelector('.ag-root-wrapper')"
       ".closest('.nicegui-aggrid'); return getElement(Number(el.id.slice(1))).api; })()")
HEADER_BOX = ".ag-header-select-all .ag-checkbox-input-wrapper"


def _rows() -> list[dict[str, str]]:
    return [{"id": f"{n:05d}".ljust(ID_CHARS, "x"), "name": f"Row {n}"}
            for n in range(ROWS)]


def serve(port: int) -> None:
    """The page: one grid, and what its selection handler last received."""
    from nicegui import app, ui

    from manager.kit import api, grid

    api.local_base_url = lambda: "http://127.0.0.1:9"
    rows = _rows()
    heard: dict = {"ids": [], "calls": 0, "hidden": 0}

    @ui.page("/")
    def page() -> None:
        built: dict = {}

        def picked(chosen: list[dict]) -> None:
            heard["ids"] = [row["id"] for row in chosen]
            heard["hidden"] = grid.hidden_count(built["table"])
            heard["calls"] += 1

        with ui.element("div").classes("w-full h-[600px] flex flex-col"):
            built["table"] = grid.build(
                [grid.identifier("name", "Name", help="The row's name")], rows,
                "drive.selection", on_select_rows=picked)

    @app.get("/heard")
    def said() -> dict:
        return heard

    ui.run(port=port, show=False, reload=False, title="Selection drive")


def _heard(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/heard", timeout=5) as answer:
        return json.loads(answer.read())


def wait_for_page(port: int, page: subprocess.Popen, log: Path) -> None:
    """Until the page answers on `/heard`."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if page.poll() is not None:
            raise AssertionError(f"the page exited:\n{log.read_text(encoding='utf-8')}")
        try:
            _heard(port)
            return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"the page never served:\n{log.read_text(encoding='utf-8')}")


def exit_with_parent() -> None:
    """First thing in a page's `serve` entry. `start_page` holds the other end of stdin,
    and this process exits the moment it closes."""
    def orphaned() -> None:
        sys.stdin.buffer.read()
        os._exit(0)

    threading.Thread(target=orphaned, daemon=True).start()


def start_page(module: str, port: int, config: str) -> subprocess.Popen:
    """`module`'s page on `port`, logging to `page.log` in `config`."""
    with (Path(config) / "page.log").open("w") as out:
        return subprocess.Popen([sys.executable, "-m", module, "serve", str(port)],
                                cwd=REPO, stdin=subprocess.PIPE, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                env={**os.environ, "VPINFE_CONFIG_DIR": config})


def stop_page(page: subprocess.Popen) -> None:
    page.terminate()
    try:
        page.wait(timeout=10)
    except subprocess.TimeoutExpired:
        page.kill()
        page.wait()
    if page.stdin:
        page.stdin.close()


def drive_page(module: str, drive: Callable[[str, int], Awaitable[dict]]) -> dict:
    """Serve `module`'s page, run `drive(browser binary, port)` against it, and stop it."""
    binary = chromium_path()
    if not binary:
        raise unittest.SkipTest("no Chromium on this machine")
    port = free_port()
    with TemporaryDirectory() as config:
        page = start_page(module, port, config)
        stop = functools.partial(stop_page, page)
        atexit.register(stop)
        try:
            wait_for_page(port, page, Path(config) / "page.log")
            return asyncio.run(drive(binary, port))
        finally:
            stop()
            atexit.unregister(stop)


class SelectionDrive(unittest.TestCase):
    seen: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.seen = drive_page("tests.theming.test_grid_selection_drive", cls._drive)

    @classmethod
    async def _drive(cls, binary: str, port: int) -> dict:
        seen: dict = {}

        async def settled(calls: int) -> dict:
            for _ in range(200):
                heard = await asyncio.to_thread(_heard, port)
                if heard["calls"] >= calls:
                    return heard
                await asyncio.sleep(0.1)
            raise AssertionError(f"the handler was called {heard['calls']} times, "
                                 f"never {calls}")

        async with BrowserSession(binary) as browser:
            await browser.navigate(f"http://127.0.0.1:{port}/")
            await browser.wait_for(API + f".getDisplayedRowCount() === {ROWS}", timeout=60.0)
            await browser.click(HEADER_BOX)
            seen["all"] = (await settled(1))["ids"]
            seen["ticked"] = await browser.evaluate(API + ".getSelectedNodes().length")
            await browser.click(HEADER_BOX)
            seen["cleared"] = (await settled(2))["ids"]
            await browser.evaluate(API + ".setGridOption('quickFilterText', 'Row 1')")
            await browser.wait_for(API + f".getDisplayedRowCount() < {ROWS}")
            seen["shown"] = await browser.evaluate(
                "(() => { const out = []; " + API + ".forEachNodeAfterFilter("
                "n => out.push(n.id)); return out; })()")
            await browser.click(HEADER_BOX)
            seen["filtered"] = (await settled(3))["ids"]
            await browser.evaluate(API + ".setGridOption('quickFilterText', 'Row 2')")
            seen["searched_away"] = await settled(4)
            seen["shown_away"] = await browser.evaluate(
                "(() => { const out = []; " + API + ".forEachNodeAfterFilter("
                "n => out.push(n.id)); return out; })()")
            await browser.evaluate(API + ".setGridOption('quickFilterText', '')")
            seen["searched_back"] = await settled(5)
        return seen

    def test_the_ids_alone_pass_the_socket_s_cap(self) -> None:
        """Or this would pass without the parts it exists to exercise."""
        self.assertGreater(sum(len(row["id"]) for row in _rows()), 1_000_000)

    def test_select_all_reaches_the_handler_with_every_id_in_order(self) -> None:
        self.assertEqual(self.seen["ticked"], ROWS)
        self.assertEqual(self.seen["all"], [row["id"] for row in _rows()])

    def test_clearing_reaches_it_as_nothing(self) -> None:
        self.assertEqual(self.seen["cleared"], [])

    def test_select_all_under_a_filter_takes_the_rows_on_screen(self) -> None:
        self.assertTrue(0 < len(self.seen["shown"]) < ROWS)
        self.assertEqual(self.seen["filtered"], self.seen["shown"])

    def test_a_search_that_hides_the_ticks_keeps_them_and_counts_them(self) -> None:
        away = self.seen["searched_away"]
        off_screen = set(self.seen["filtered"]) - set(self.seen["shown_away"])
        self.assertTrue(0 < len(off_screen) < len(self.seen["filtered"]))
        self.assertEqual(away["ids"], self.seen["filtered"])
        self.assertEqual(away["hidden"], len(off_screen))

    def test_clearing_the_search_shows_them_again(self) -> None:
        back = self.seen["searched_back"]
        self.assertEqual(back["ids"], self.seen["filtered"])
        self.assertEqual(back["hidden"], 0)


DRIVE_MODULES = ("tests.theming.test_grid_selection_drive",
                 "tests.theming.test_media_fill_drive")

# Starts a page as a drive does, says the page's pid once it serves, then waits to be killed.
PARENT = """
import sys, time
from pathlib import Path
from tests.theming.test_grid_selection_drive import start_page, wait_for_page
module, port, config = sys.argv[1], int(sys.argv[2]), sys.argv[3]
page = start_page(module, port, config)
wait_for_page(port, page, Path(config) / "page.log")
print(page.pid, flush=True)
time.sleep(120)
"""


def _session_ended(session: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.killpg(session, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


@unittest.skipIf(sys.platform.startswith("win"), "POSIX sessions and signals")
class PageServerTests(unittest.TestCase):
    def test_a_killed_parent_leaves_no_server(self) -> None:
        for module in DRIVE_MODULES:
            with self.subTest(module=module), TemporaryDirectory() as config:
                parent = subprocess.Popen(
                    [sys.executable, "-c", PARENT, module, str(free_port()), config],
                    cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    said = parent.stdout.readline() if parent.stdout else ""
                finally:
                    parent.kill()
                    _, err = parent.communicate()
                self.assertTrue(said.strip(), err)

                session = int(said)
                ended = _session_ended(session, within=10)
                if not ended:
                    os.killpg(session, signal.SIGKILL)
                self.assertTrue(ended, f"the page for {module} outlived its parent")


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        exit_with_parent()
        serve(int(sys.argv[2]))
    else:
        unittest.main()

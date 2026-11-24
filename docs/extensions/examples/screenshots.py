"""Takes the pictures the tutorial shows, one lesson at a time.

Each lesson's extension goes into a VPinFE of its own, started against a copy of a library
you name, and the Manager is photographed in a headless Chromium.  The practice site is
started here, fresh for every lesson, so each picture shows the three sample scores and
only what that lesson did to them.

    python docs/extensions/examples/screenshots.py --library PATH
    python docs/extensions/examples/screenshots.py --library PATH --only 05 --only 07

PATH is a folder of tables.  It has to hold Attack from Mars and The Addams Family, so the
scores that name them draw with their pictures.  It is the library of the throwaway VPinFE
each lesson starts, which writes to it, so give it a copy.  Pictures land in
`docs/extensions/images/`.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
IMAGES = HERE.parent / "images"
sys.path.insert(0, str(REPO))

from tests.support.browser_session import BrowserSession, chromium_path  # noqa: E402
from tests.support.live_instance import LiveInstance  # noqa: E402

WIDTH, HEIGHT = 1200, 720
KEY = "practice-key"
LIST = "/manager?view=community:example_scores:scores"
SETTINGS = "/manager?view=settings&page=extensions.example_scores"

Shot = Callable[[BrowserSession, LiveInstance], Awaitable[None]]


def fresh_site():
    """A practice site of its own, with the three sample scores."""
    spec = importlib.util.spec_from_file_location("practice_site", HERE / "practice_site.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.serve_in_background(module.PORT)


async def show(page: BrowserSession, inst: LiveInstance, path: str, ready: str) -> None:
    await page.send("Emulation.setDeviceMetricsOverride", {
        "width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False})
    await page.navigate(inst.manager_url(path))
    await page.wait_for(ready, timeout=60)
    await asyncio.sleep(1.0)
    await reveal(page, "Example Scores")


async def reveal(page: BrowserSession, text: str) -> None:
    """Scrolls every list or rail that holds `text` so that entry is in view."""
    await page.evaluate(
        "((text) => [...document.querySelectorAll('*')].filter(el => el.children.length === 0"
        " && el.innerText?.trim().toLowerCase() === text.toLowerCase())"
        ".forEach(el => el.scrollIntoView({block: 'center'})))"
        f"({json.dumps(text)})")
    await asyncio.sleep(0.4)


async def snap(page: BrowserSession, name: str) -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    shot = await page.send("Page.captureScreenshot", {"format": "png"})
    (IMAGES / f"{name}.png").write_bytes(base64.b64decode(shot["data"]))
    print(f"  {name}.png")


def has_text(text: str) -> str:
    return f"document.body.innerText.includes({json.dumps(text)})"


async def click_text(page: BrowserSession, selector: str, text: str, nth: int = 0) -> None:
    found = await page.evaluate(
        "((selector, text, nth) => {"
        " const all = [...document.querySelectorAll(selector)]"
        "  .filter(el => el.getClientRects().length && el.innerText.trim() === text);"
        " if (!all[nth]) return false;"
        " all[nth].setAttribute('data-shot', 'one'); return true; })"
        f"({json.dumps(selector)}, {json.dumps(text)}, {nth})")
    if not found:
        raise AssertionError(f"no {selector} reading {text!r}")
    await page.click('[data-shot="one"]')
    await page.evaluate("document.querySelector('[data-shot]')?.removeAttribute('data-shot')")
    await asyncio.sleep(0.6)


async def type_into(page: BrowserSession, selector: str, text: str, nth: int = 0) -> None:
    await page.click(selector, nth=nth)
    for char in text:
        await page.send("Input.dispatchKeyEvent", {"type": "char", "text": char})
    await page.press("Enter", "Enter")
    await asyncio.sleep(0.8)


async def extensions_rail(page: BrowserSession, inst: LiveInstance) -> None:
    await show(page, inst, "/manager?view=settings&page=extensions.vpinplay",
               has_text("VPinPlay"))
    await page.evaluate(
        "[...document.querySelectorAll('*')].find(el => el.children.length === 0"
        " && el.innerText?.trim() === 'VPinPlay')?.scrollIntoView({block: 'center'})")
    await asyncio.sleep(0.5)
    await snap(page, "00-extensions")


async def settings_page(page: BrowserSession, inst: LiveInstance, name: str) -> None:
    await show(page, inst, SETTINGS, has_text("VERSION"))
    await snap(page, name)


async def key_set(page: BrowserSession, inst: LiveInstance) -> None:
    await show(page, inst, SETTINGS, has_text("VERSION"))
    await snap(page, "02-settings")
    await type_into(page, "input[type=text]", "nope", nth=0)
    await snap(page, "02-settings-refused")


async def list_page(page: BrowserSession, inst: LiveInstance, name: str) -> None:
    await show(page, inst, LIST, "document.querySelectorAll('.ag-row').length > 0")
    await snap(page, name)


async def newest_view(page: BrowserSession, inst: LiveInstance) -> None:
    await show(page, inst, LIST, "document.querySelectorAll('.ag-row').length > 0")
    await page.click(".q-select", nth=0)
    await asyncio.sleep(0.6)
    await click_text(page, ".q-item", "Newest")
    await page.wait_for("document.querySelectorAll('.ag-row').length === 3", timeout=20)
    await asyncio.sleep(0.8)
    await snap(page, "04-reads-well")


async def title_menu(page: BrowserSession, inst: LiveInstance) -> None:
    await show(page, inst, LIST, "document.querySelectorAll('.ag-row').length > 0")
    await click_text(page, ".q-btn", "more_vert")
    await snap(page, "05-title-menu")


async def rows_menu(page: BrowserSession, inst: LiveInstance) -> None:
    await show(page, inst, LIST, "document.querySelectorAll('.ag-row').length > 0")
    await page.click(".ag-row .ag-selection-checkbox, .ag-row input[type=checkbox]", nth=0)
    await asyncio.sleep(0.6)
    await click_text(page, ".q-btn", "more_vert", nth=1)
    await snap(page, "06-rows")


async def dialog(page: BrowserSession, inst: LiveInstance) -> None:
    await show(page, inst, LIST, "document.querySelectorAll('.ag-row').length > 0")
    await click_text(page, ".q-btn", "add")
    await asyncio.sleep(1.0)
    await snap(page, "07-adding")


async def pane(page: BrowserSession, inst: LiveInstance) -> None:
    await show(page, inst, LIST, "document.querySelectorAll('.ag-row').length > 0")
    await page.click(".ag-row [col-id=player]", nth=0)
    await page.wait_for(has_text("POSTED BY"), timeout=20)
    await asyncio.sleep(1.0)
    await snap(page, "08-pane")


async def group(page: BrowserSession, inst: LiveInstance) -> None:
    games = inst.api("/api/v1/games")["games"]
    game = next(one["id"] for one in games if one["vps_id"] == "9Paf7-CL")
    await show(page, inst, f"/manager?view=games&game={game}&section=play",
               has_text("Best Score"))
    await snap(page, "09-panel-group")


LESSONS: dict[str, tuple[str, bool, list[Shot]]] = {
    # folder, whether the key is already set, the pictures
    "00": ("", False, [extensions_rail]),
    "01": ("01-loads", False, [lambda p, i: settings_page(p, i, "01-loads")]),
    "02": ("02-settings", False, [key_set]),
    "03": ("03-list", True, [lambda p, i: list_page(p, i, "03-list")]),
    "04": ("04-reads-well", True, [newest_view]),
    "05": ("05-title-menu", True, [title_menu]),
    "06": ("06-rows", True, [rows_menu]),
    "07": ("07-adding", True, [dialog]),
    "08": ("08-pane", True, [pane]),
    "09": ("09-panel-group", True, [group]),
    "10": ("10-shipping", True, [lambda p, i: settings_page(p, i, "10-shipping")]),
}


async def take(number: str, library: Path, binary: str) -> None:
    folder, keyed, shots = LESSONS[number]
    print(f"lesson {number}")
    site = fresh_site()
    inst = LiveInstance(library)
    try:
        if folder:
            shutil.copytree(HERE / "scores" / folder / "example_scores",
                            inst.config_dir / "extensions" / "example_scores")
            if keyed:
                settings = inst.config_dir / "extension_settings"
                settings.mkdir(exist_ok=True)
                (settings / "example_scores.json").write_text(json.dumps({"key": KEY}))
        with inst:
            async with BrowserSession(binary) as page:
                for shot in shots:
                    await shot(page, inst)
    finally:
        site.shutdown()
        site.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--only", action="append", choices=sorted(LESSONS))
    args = parser.parse_args()
    binary = chromium_path()
    if binary is None:
        sys.exit("No browser VPinFE can drive was found")
    for number in args.only or sorted(LESSONS):
        asyncio.run(take(number, args.library, binary))


if __name__ == "__main__":
    main()

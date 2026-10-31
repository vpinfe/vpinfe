"""Render the Manager in a language where every catalog entry is unreadable.

The AST checks answer "is there a literal in a display position?" - and four rounds of
this work showed that a string can reach the screen without ever being one: through a
constant, a dict value, a function's return, a component the check was never told about.
This asks the only question that catches all of those at once: **what English is still on
screen when every word the catalog owns has been mangled?**

It found the entire navigation rail, which four passes of static checking had called
clean.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from frontend.chromium_manager import get_chromium_path
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import write_game
from tests.support.live_instance import LiveInstance
from tests.support.manager_walk import ManagerWalk, clicked

ROOT = Path(__file__).resolve().parents[2]

# Sections whose screens are mostly not ours to translate, and so cannot be read this
# way: `logs` is log lines, English on purpose because a log is read by one person
# working out what happened and moves between installs; `themes` is third-party
# manifest text, and `about` is what the machine reports about itself. Their chrome is
# covered by the static checks in tests/invariants/test_i18n_catalog.py; this one would
# drown in their content.
CONTENT_HEAVY = frozenset({"logs", "themes", "about"})
# The collection every new install is seeded with, in each section of its panel.
PANELS =tuple(f"view=collections&collection=Last%20Played&section={section}"
               for section in ("collection_details", "collection_games"))
CATALOG = json.loads((ROOT / "common/i18n/catalogs/en.json").read_text(encoding="utf-8"))
GAME = "Attack from Mars"
DOTTED = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\b", re.IGNORECASE)
KEYS = {key.lower(): key for key in CATALOG}
# The Chrome command line on the Browser page is the program's own options, not our chrome.
ICONS = (".q-icon, .material-icons, .manager-options { visibility: hidden !important; }")


def _without_icons(expression: str) -> str:
    return ("(() => { const quiet = document.createElement('style');"
            f" quiet.textContent = {json.dumps(ICONS)}; document.head.append(quiet);"
            f" try {{ return {expression}; }} finally {{ quiet.remove(); }} }})()")


# Rendered elements only: the grid's paging bar is in every page, hidden, and unread.
PAGE_WORDS = _without_icons(
    "[document.body.innerText, ...[...document.querySelectorAll('[aria-label]')]"
    ".filter(e => e.getClientRects().length)"
    ".map(e => e.getAttribute('aria-label'))].join('\\n')")


def _keys_in(text: str) -> list[str]:
    """Catalog keys on screen as themselves, in either case: `innerText` applies
    `text-transform`."""
    return sorted({KEYS[one.lower()] for one in DOTTED.findall(text) if one.lower() in KEYS})


# Under the pseudo-locale every letter the catalog owns is replaced by one that is not
# ASCII, so anything still spelled in plain ASCII did not come through it. Matching on
# *that* rather than on the words the catalog happens to hold is what catches a string
# nobody has ever translated - the first version of this check intersected with the
# catalog's own vocabulary and let a planted "Diagnostics" straight through.
ASCII_WORD = re.compile(r"\b[A-Za-z]{4,}\b")


def _machine_words(*paths: Path) -> set[str]:
    """Words on screen only because of what this machine is called and where it runs.

    Devices prints the hostname, Metrics the watched paths and each graphics card's
    name, Browser where the browser was found and Displays each screen's model name, so
    all five carry words no catalog owns. Derived rather than listed, because a list is
    one machine's.
    """
    import socket
    import tempfile

    from common.host import display_service, graphics

    sources = [socket.gethostname(), tempfile.gettempdir(), str(Path.home()),
               *display_service.get_display_names(refresh=True),
               *graphics.names()[0],
               *(str(p) for p in paths)]
    return {w.lower() for s in sources for w in re.findall(r"[A-Za-z]{4,}", s)}


_PICKER = ("[...document.querySelectorAll('.q-btn')].filter(x => x.offsetParent)"
           ".find(x => (x.innerText || '').includes('more_vert'))")
_MENU_OPEN = "document.querySelector('.q-menu') !== null"

_PICKER_TEXT = _without_icons(
    "[...document.querySelectorAll('.q-menu, .q-item, .manager-group, .manager-menu-item')]"
    ".filter(x => x.offsetParent).map(x => x.innerText || '').join('\\n')")


async def _picker_text(walk: ManagerWalk) -> str:
    """What the column picker says, or "" on a page that has no picker."""
    if not await walk.browser.evaluate(f"!!{_PICKER}"):
        return ""
    await walk.act(clicked(walk.browser, f"(b => b && (b.click(), true))({_PICKER})"),
                   until=_MENU_OPEN)
    return await walk.browser.evaluate(_PICKER_TEXT) or ""


# Core has its words once `t` answers from the catalog rather than with the key.
_READY = ("document.body.dataset.ready === 'true'"
          " && window.vpin.t('word.all') !== 'word.all'")
_IDLE = "Object.keys(window.vpin._pendingCalls).length === 0"
_OVERLAY_S = 30.0


def _drawn(frame: str) -> str:
    return ("(f => !!f && f.style.display === 'block' && !!f.contentDocument"
            " && !!f.contentDocument.querySelector('.menu-item.selected'))"
            f"(document.getElementById({json.dumps(frame)})) && {_IDLE}")


async def _ready(browser: BrowserSession, instance: LiveInstance) -> None:
    await browser.navigate(instance.theme_url("playfield"))
    await browser.wait_for(_READY, timeout=90.0)


async def _open(browser: BrowserSession, overlay: str, frame: str) -> None:
    if await browser.evaluate(_drawn(frame)):
        raise AssertionError(f"{overlay} reads as drawn before it was opened")
    await browser.evaluate(f"window.vpin.toggleOverlay({json.dumps(overlay)})")
    await browser.wait_for(_drawn(frame), timeout=_OVERLAY_S)


async def _close(browser: BrowserSession, overlay: str) -> None:
    await browser.evaluate(f"window.vpin.toggleOverlay({json.dumps(overlay)})")
    await browser.wait_for("window.vpin.overlay === null", timeout=_OVERLAY_S)


async def _changed(browser: BrowserSession, expression: str, before: str) -> str:
    """`expression` once it is no longer `before` - or still `before` after
    `_OVERLAY_S`, for the assertion to say so."""
    with suppress(TimeoutError):
        return await browser.wait_for(
            f"(now => now !== {json.dumps(before)} && now)({expression})",
            timeout=_OVERLAY_S)
    return await browser.evaluate(expression)


class PseudoLocaleTests(unittest.TestCase):
    """Slow: boots a real instance and a real browser. Worth it - see the docstring."""

    BOOTS_PER_TEST = "Its only test, so booting per test is booting once."

    def test_every_section_says_nothing_in_english(self) -> None:
        """Not just the landing view. `?view=` is the address of each section, and a
        page nobody opens is a page nobody checked - the rail was found on the one view
        this did open, and there was no reason to think the rest were different."""
        if not chromium_path():
            self.skipTest("no Chromium on this machine")
        if not (ROOT / "common/i18n/catalogs/qps.json").is_file():
            self.skipTest("no pseudo-locale; run scripts/i18n.py --pseudo")

        # Words that reach the screen as data rather than as our chrome. Each is here
        # for a stated reason, because an allowance nobody can justify is how a check
        # like this quietly stops meaning anything.
        content = (
            # the fixture game's own title, and this install's name
            {w.lower() for w in re.findall(r"[A-Za-z]{4,}", GAME)}
            | {"local", "dev"}
            # the product's own name, which About and Devices print
            | {"vpinfe"}
            # a product's name, which is also what a new launcher is called: "Visual
            # Pinball". A name somebody typed is theirs.
            | {"visual", "pinball"}
            # the name the bundled VPinPlay gives its Community list, in the rail
            | {"vpinplay"}
            # VPinPlay's Server setting, which Settings > Extensions draws as the web
            # address it holds
            | {"https"}
            # the web address Network > Port prints for another device to open
            | {"http"}
            # the Home address on a bundled extension's page, a web address the manifest
            # holds and the page draws as written
            | {"github"}
            # an art source's own name, which Library > Media lists as a source
            | {"vpinmediadb"}
            # `Last Played` is a collection *name*, written into collections.json - it
            # is stored data, and translating it would rename what is on disk
            | {"last", "played"}
            # Table features named after the person or the product that made them -
            # `manager/kit/table_features.py` writes these as literals for the same reason
            # the launcher names above are literals. A translated nFozzy is a wrong one.
            | {"nfozzy", "ssf", "lut", "fastflips", "flexdmd"}
            # `alt_color` and `alt_sound` are asset kinds the registry does not name, so
            # `httpapi/assets.py:_label` builds a label from the identifier. That is the
            # documented fallback for a kind from outside; the fix is to register the
            # kind, which is a data change rather than a localization one.
            | {"color", "sound"}
            # The formats' own names, which Browser Program draws as chips
            # (`frontend_browser.FORMATS`). H.264, VP9, AV1, AAC and MP3 are under four
            # letters or carry a digit, so only these three reach the pattern.
            | {"hevc", "vorbis", "opus"})
        allowed = set(content)

        from manager import page as manager_page
        sections = sorted(set(manager_page.SECTIONS) - CONTENT_HEAVY)
        addresses = [f"view={view}" for view in sections] + list(PANELS)

        found: dict[str, list[str]] = {}
        keys: dict[str, list[str]] = {}
        settings_pages: list[str] = []

        def read(where: str, text: str) -> None:
            leaked = sorted({w.lower() for w in ASCII_WORD.findall(text)} - allowed)
            if leaked:
                found[where] = leaked
            if named := _keys_in(text):
                keys[where] = named

        async def look(instance) -> None:
            async with BrowserSession(chromium_path() or "") as browser:
                walk = ManagerWalk(browser, instance)
                for view in addresses:
                    await walk.visit(f"/manager?{view}")
                    read(view, await browser.evaluate(PAGE_WORDS) + await _picker_text(walk))
                await walk.visit("/manager?view=settings")
                async for _page in walk.sections():
                    where = await browser.evaluate("location.search")
                    settings_pages.append(where)
                    read(where, await browser.evaluate(PAGE_WORDS))

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_game(root, GAME)
            with LiveInstance(root,
                              extra_settings={("general", "language"): "qps"}) as instance:
                # System > Backups prints the folder a backup goes to, a path under config.
                allowed |= _machine_words(root, instance.config_dir,
                                          Path(instance.config_dir) / "backups",
                                          instance.home,
                                          Path(get_chromium_path("").path))
                asyncio.run(look(instance))

        self.assertIn("?view=settings&page=hardware.input", settings_pages)
        self.assertEqual(keys, {}, "these reached the screen as the catalog's own keys")
        self.assertEqual(found, {}, "these reached the screen without the catalog")


class FrontendPseudoLocaleTests(unittest.TestCase):
    """The cabinet's own chrome, which no static check reads.

    The overlays are iframes with no core of their own, and most of what they show is
    written at runtime by their own script - the audio item, every dropdown's current
    value, the paging line. None of that is a literal in a display position, so this is
    the only thing that sees it.
    """

    BOOTS_PER_TEST = ("The menus draw the collection, filters and sort the frontend "
                      "holds, so each test opens them on an instance no other test has "
                      "driven.")

    # The Manager's address is this machine's own names, not words the catalog owns.
    READ = ("(() => { const f = document.getElementById('%s');"
            " if (!f || !f.contentDocument) return '';"
            " const address = f.contentDocument.querySelector('.menu-qr-address');"
            " if (address) address.style.display = 'none';"
            " return f.contentDocument.body.innerText; })()")

    def test_the_menus_say_nothing_in_english(self) -> None:
        if not chromium_path():
            self.skipTest("no Chromium on this machine")
        if not (ROOT / "common/i18n/catalogs/qps.json").is_file():
            self.skipTest("no pseudo-locale; run scripts/i18n.py --pseudo")

        async def look(instance) -> dict[str, list[str]]:
            found: dict[str, list[str]] = {}
            async with BrowserSession(chromium_path() or "") as browser:
                await _ready(browser, instance)
                for overlay, frame in (("menu", "menu-frame"),
                                       ("collectionMenu", "collection-menu-frame")):
                    await _open(browser, overlay, frame)
                    text = await browser.evaluate(self.READ % frame) or ""
                    leaked = sorted({w.lower() for w in ASCII_WORD.findall(text)})
                    if leaked:
                        found[overlay] = leaked
                    await _close(browser, overlay)
            return found

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_game(root, GAME)
            with LiveInstance(root,
                              extra_settings={("general", "language"): "qps"}) as instance:
                leaked = asyncio.run(look(instance))

        self.assertEqual(leaked, {}, "these reached the cabinet without the catalog")

    def test_a_menu_rewrites_itself_when_the_catalog_lands_late(self) -> None:
        """An overlay that drew before the catalog arrived draws again when it does."""
        if not chromium_path():
            self.skipTest("no Chromium on this machine")
        if not (ROOT / "common/i18n/catalogs/qps.json").is_file():
            self.skipTest("no pseudo-locale; run scripts/i18n.py --pseudo")

        line = ("document.getElementById('collection-menu-frame').contentDocument"
                ".getElementById('paging-state').textContent")
        empty_it = """
          (() => {
            const frame = document.getElementById('collection-menu-frame').contentWindow;
            frame.__vpinWords = {};
            if (!frame.__vpinWordsChanged) return false;
            frame.__vpinWordsChanged();
            return true;
          })()
        """
        # Not a direct call to __vpinWordsChanged: that passes with core's half deleted.
        arrive = ("document.getElementById('collection-menu-frame')"
                  ".dispatchEvent(new Event('load'))")

        async def look(instance) -> tuple:
            async with BrowserSession(chromium_path() or "") as browser:
                await _ready(browser, instance)
                await _open(browser, "collectionMenu", "collection-menu-frame")
                said = await browser.evaluate(line)

                told = await browser.evaluate(empty_it)
                bare = await _changed(browser, line, said)

                await browser.evaluate(arrive)
                return said, bare, await _changed(browser, line, bare), told

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_game(root, GAME)
            with LiveInstance(root,
                              extra_settings={("general", "language"): "qps"}) as instance:
                said, bare, again, told = asyncio.run(look(instance))

        self.assertTrue(told, "an overlay declares no way to be told the catalog arrived")
        self.assertTrue(ASCII_WORD.search(bare),
                        f"blanking the words should leave English behind, got {bare!r}")
        self.assertEqual(again, said, "core handed the words over and the line did not "
                                      "change")


if __name__ == "__main__":
    unittest.main()

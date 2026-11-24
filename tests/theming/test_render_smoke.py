"""A real theme, in a real browser, against a real backend.

Every other test in this suite asserts on a piece. The breaks that reached the cabinet
were between the pieces, and all of them were only visible by looking: two hardcoded
`(bg|dmd|table)` lists meant no contract 2 theme could run at all while 907 Python and 95
JavaScript tests stayed green, and the next session found six more the same way.

So this starts `main.py --headless` against a throwaway library, opens the harness theme
in headless Chromium, and asserts the things a screenshot would have shown: the wheel
rendered, every request the page made returned a status, the media a theme asks for by
kind resolves, and moving the wheel moves it.

Skipped where there is no browser, so it never becomes the reason a Windows build fails.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

from tests.support import pictures
from tests.support.browser_session import BrowserSession, chromium_path
from tests.support.library import TempTree, write_game
from tests.support.live_instance import LiveInstance

# A 1x1 PNG. Real bytes, because the point is that the browser fetches and decodes it.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100fd5c9a6b0000000049454e44ae426082")

GAMES = ("Alpha Table", "Beta Table", "Gamma Table")


def _info(name: str) -> dict:
    return {"Info": {"Name": name, "Manufacturer": "Test", "Year": "1999"},
            "VPinFE": {}, "User": {}}


# Windows is out by design, not by accident. This was scoped to Linux from the start and
# a cabinet is not a Windows box - but the honest reason is that the runner kills the
# test process partway through with no summary and no traceback, and I have not found out
# why. Diagnosing it costs a push per attempt for a platform this test was never meant to
# gate. Recorded rather than quietly excluded: if VPinFE ever needs this on Windows, that
# is the thing to work out first.
_UNSUPPORTED = sys.platform.startswith("win")


def _needs_a_browser(cls):
    cls = unittest.skipIf(_UNSUPPORTED, "the render smoke test is scoped to Linux and macOS")(cls)
    return unittest.skipIf(chromium_path() is None, "no Chromium on this machine")(cls)


def _write_library(root: Path) -> None:
    for name in GAMES:
        write_game(root, name, info=_info(name),
                   medias={"wheel.png": PNG, "table.png": PNG})


class _Smoke(unittest.TestCase):
    """What the smoke tests drive a page with. `_instance` says which VPinFE they drive,
    and holds no test of its own."""

    def _instance(self):
        raise NotImplementedError

    # CI runs several times slower than a laptop, and this waits on a real app booting
    # and a real browser rendering. Generous rather than tuned: a flaky timeout would
    # teach everyone to ignore this test, which is worse than it being slow.
    READY_TIMEOUT = 90.0

    async def _open(self, browser: BrowserSession, instance: LiveInstance, window: str):
        """Open a window and wait for the theme to say it finished starting."""
        await browser.navigate(instance.theme_url(window))
        try:
            await browser.wait_for("document.body.dataset.ready === 'true'",
                                   timeout=self.READY_TIMEOUT)
        except TimeoutError as exc:
            raise AssertionError(self._diagnose(exc, browser, instance, window)) from exc

    def _diagnose(self, exc, browser, instance, window) -> str:
        """Say what the page and the instance were doing. A bare timeout sent the last
        CI failure back to guesswork, and guessing costs a push per attempt."""
        lines = [f"{window} never finished starting: {exc}",
                 f"  failed requests: {browser.failed_requests[:6] or 'none'}",
                 "  page log:"]
        lines += [f"    {line[:160]}" for line in browser.page_log[:15]] or ["    (silent)"]
        lines += ["  instance log:"]
        lines += [f"    {line[:160]}" for line in instance.output().splitlines()[-15:]]
        return "\n".join(lines)

    def _drive(self, window: str = "playfield"):
        """Boot, open the window, and hand back what the page and the browser saw."""
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await self._open(browser, instance, window)
                data = await browser.body_data()
                return data, browser, list(browser.failed_requests)

        with self._instance() as instance:
            return asyncio.run(run(instance))

    # -- the main menu, driven the way a player drives it --------------------
    #
    #
    # Nothing loaded `mainmenu.js` before this. The JS suite covers core's overlay
    # plumbing - opening, closing, which one owns the actions - and stops at the
    # iframe boundary, so the menu's own cursor had no coverage at all.

    # Same-origin, so the menu's own document is readable from the host page.
    SELECTED = ("(document.getElementById('menu-frame')?.contentDocument"
                "?.querySelector('.menu-item.selected')?.id) || ''")

    # What the page looked like when no item was selected. Splits "the menu never
    # opened" from "it opened empty" - a bare timeout names neither, and the answer
    # costs a push per guess.
    MENU_STATE = """JSON.stringify((() => {
      const frame = document.getElementById('menu-frame');
      const doc = frame && frame.contentDocument;
      const win = frame && frame.contentWindow;
      const items = doc ? Array.from(doc.querySelectorAll('.menu-item')) : [];
      // The menu's own variables. Same-origin, so its realm is readable, and its
      // state says which of the three ways this fails actually happened: no
      // navigable at all, one built while the frame was still hidden and so empty,
      // or a full one whose selection was never applied.
      const peek = (expr) => {
        try { return win.eval(expr); } catch (err) { return String(err); }
      };
      return {frame: !!frame,
              display: frame ? frame.style.display : null,
              src: frame ? frame.getAttribute('src') : null,
              readyState: doc ? doc.readyState : null,
              items: items.length,
              visible: items.filter(el => win.getComputedStyle(el).display !== 'none').length,
              menuLength: peek('typeof menu !== "undefined" && menu ? menu.length : null'),
              cursor: peek('typeof menu !== "undefined" && menu ? menu.cursor : null'),
              configLoaded: peek(
                  'typeof menuConfigLoaded !== "undefined" ? menuConfigLoaded : null')};
    })())"""

    # The throws two tests make on purpose. The instance is shared, so a later failure's
    # overlay excerpt would otherwise report one of them as its cause.
    THROWN_ON_PURPOSE = ("menu exploded", "theme exploded")

    def _overlay_errors(self, instance) -> list[str]:
        """What the overlay reported, from the whole log rather than the tail. A throw
        while the menu loads is thousands of lines back by the time this runs."""
        lines = [line for line in instance.output().splitlines()
                 if "playfield/" in line and not any(
                     thrown in line for thrown in self.THROWN_ON_PURPOSE)]
        return [line[:160] for line in lines[-6:]]

    async def _open_menu(self, browser, instance):
        await self._open(browser, instance, "playfield")
        await browser.press("m", "KeyM")
        try:
            return await browser.wait_for(self.SELECTED, timeout=self.READY_TIMEOUT)
        except TimeoutError as exc:
            state = await browser.evaluate(self.MENU_STATE)
            report = self._diagnose(exc, browser, instance, "the menu")
            overlay = self._overlay_errors(instance) or ["(none)"]
            raise AssertionError(
                f"{report}\n  menu: {state}\n  overlay log: " + "\n    ".join(overlay)) from exc


@_needs_a_browser
class RenderSmokeTests(_Smoke):
    """One instance for the class, and a browser of its own for each test. A test here
    sends the instance input and reads what comes back; the ones that change what it
    holds, the roster, boot their own in `PlayerListSmokeTests`."""

    shared: LiveInstance

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        tmp = TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        _write_library(Path(tmp.name))
        cls.shared = cls.enterClassContext(LiveInstance(Path(tmp.name)))

    def _instance(self):
        return nullcontext(self.shared)

    # -- the assertions a screenshot would have made ------------------------

    def test_the_wheel_renders_every_game(self) -> None:
        data, _browser, _failed = self._drive()
        self.assertEqual(data.get("rendered"), str(len(GAMES)),
                         f"the page rendered {data.get('rendered')} entries, not "
                         f"{len(GAMES)}; body was {data}")

    def test_the_theme_reports_no_failure_of_its_own(self) -> None:
        """The harness writes what went wrong into the body rather than throwing into a
        browser log nobody reads - a theme that throws at startup renders nothing, which
        looks exactly like a blank screen."""
        data, _browser, _failed = self._drive()
        self.assertIsNone(data.get("failures"), data.get("failures"))

    def test_nothing_the_page_asked_for_was_missing(self) -> None:
        """The 404 from the bootstrap that reached the cabinet is exactly this."""
        _data, _browser, failed = self._drive()
        self.assertEqual(failed, [])

    def test_the_window_knows_which_window_it_is(self) -> None:
        """`unknown` from name detection was the second half of that same break."""
        data, _browser, _failed = self._drive("playfield")
        self.assertEqual(data.get("window"), "playfield")

    def test_a_secondary_window_renders_too(self) -> None:
        """Blank backglass and scoreview is the break the next session found."""
        for window in ("backglass", "scoreview"):
            with self.subTest(window=window):
                data, _browser, failed = self._drive(window)
                self.assertEqual(data.get("rendered"), str(len(GAMES)))
                self.assertEqual(failed, [])

    def test_moving_the_wheel_moves_it(self) -> None:
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await self._open(browser, instance, "playfield")
                before = (await browser.body_data()).get("selected")
                await browser.press("ArrowRight", "ArrowRight")
                after = await browser.wait_for(
                    f"document.body.dataset.selected !== '{before}' "
                    f"&& document.body.dataset.selected")
                return before, after

        with self._instance() as instance:
            before, after = asyncio.run(run(instance))
        self.assertNotEqual(before, after)

    def test_a_press_over_the_api_moves_the_wheel(self) -> None:
        """The seam a phone drives the frontend through, end to end.

        Every piece of this has its own test and all of them passed while the wheel sat
        still: what is proved here is that a POST reaches the bus, the bus reaches the
        window, and the window moves - which is the only claim worth making.
        """
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await self._open(browser, instance, "playfield")
                before = (await browser.body_data()).get("selected")
                instance.post("/api/v1/input/actions",
                              {"action": "next", "source": "smoke"})
                after = await browser.wait_for(
                    f"document.body.dataset.selected !== '{before}' "
                    f"&& document.body.dataset.selected")
                return before, after

        with self._instance() as instance:
            before, after = asyncio.run(run(instance))
        self.assertNotEqual(before, after)

    # Counts every time the theme lands on a different game, because with a handful of
    # games a wheel that travelled all the way round would look like one that never
    # moved. Installed before the press, so nothing is counted that happened first.
    WATCH_MOVES = """
    window.__moves = 0;
    new MutationObserver(() => { window.__moves += 1; }).observe(
      document.body, {attributes: true, attributeFilter: ['data-selected']});
    """

    def test_holding_a_button_over_the_api_walks_the_wheel(self) -> None:
        """The whole chain, held: a press that is not released keeps going, and the
        release stops it.

        This is the half that makes a remote usable - a D-pad that steps once per tap
        cannot cross a library - and it is the same engine a cabinet flipper uses, which
        did not repeat at all before it existed.
        """
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await self._open(browser, instance, "playfield")
                await browser.evaluate(self.WATCH_MOVES)
                instance.post("/api/v1/input/actions",
                              {"action": "next", "phase": "press", "source": "smoke"})
                await browser.wait_for("window.__moves > 1", timeout=10.0)
                instance.post("/api/v1/input/actions",
                              {"action": "next", "phase": "release", "source": "smoke"})
                return await _settles(browser)

        with self._instance() as instance:
            settled, later = asyncio.run(run(instance))
        self.assertEqual(later, settled, "the wheel kept travelling after the release")

    def test_a_hold_nobody_releases_lets_go_on_its_own(self) -> None:
        """A phone locks its screen mid-hold and the release never arrives. Without the
        expiry that is a wheel that spins until somebody notices."""
        async def run(instance: LiveInstance):
            async with BrowserSession(chromium_path() or "") as browser:
                await self._open(browser, instance, "playfield")
                await browser.evaluate(self.WATCH_MOVES)
                instance.post("/api/v1/input/actions",
                              {"action": "next", "phase": "press", "source": "smoke",
                               "ttl_ms": 300})
                await browser.wait_for("window.__moves > 1", timeout=10.0)
                return await _settles(browser)

        with self._instance() as instance:
            stopped, later = asyncio.run(run(instance))
        self.assertEqual(later, stopped, "nothing let go of a hold that was never released")

    def test_the_remote_pad_drives_the_wheel(self) -> None:
        """The whole surface, end to end: a thumb on the remote's pad, a wheel on the
        other side of it.

        Two browsers against one instance, because that is the arrangement this exists
        for - a phone in a hand and a machine across the room. Every piece of the chain
        had its own passing test while the chain itself had never been exercised once.
        """
        with self._instance() as instance:
            asyncio.run(self._drive_the_pad(instance))

    async def _drive_the_pad(self, instance: LiveInstance) -> None:
        hold = ("(down) => { const el ="
                " document.querySelector('[data-hold-action=\"next\"]');"
                " el.dispatchEvent(new PointerEvent(down ? 'pointerdown' : 'pointerup',"
                " {bubbles: true})); return true; }")
        async with BrowserSession(chromium_path() or "") as theme, \
                BrowserSession(chromium_path() or "") as phone:
            await self._open(theme, instance, "playfield")
            await theme.evaluate(self.WATCH_MOVES)
            await phone.navigate(instance.manager_url("/remote?screen=wheel"))
            await phone.wait_for(
                "document.querySelectorAll('[data-hold-action]').length === 4",
                timeout=self.READY_TIMEOUT)
            # Held, not tapped: the page starts the hold on pointerdown and renews it,
            # so what is under test is a gesture rather than a single press.
            await phone.evaluate(f"({hold})(true)")
            await theme.wait_for("window.__moves > 1", timeout=15.0)
            await phone.evaluate(f"({hold})(false)")
            settled, later = await _settles(theme)
        self.assertEqual(later, settled,
                         "the wheel kept travelling after the thumb came off")

    def test_the_menu_opens_with_something_selected(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                return await self._open_menu(browser, instance), list(browser.page_log)

        with self._instance() as instance:
            selected, page_log = asyncio.run(run(instance))
        self.assertTrue(selected, "the menu opened with no item selected")
        self.assertEqual([line for line in page_log if "Uncaught" in line], [])

    def test_the_menu_cursor_moves(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                first = await self._open_menu(browser, instance)
                await browser.press("ArrowDown", "ArrowDown")
                moved = await browser.wait_for(
                    f"({self.SELECTED}) && ({self.SELECTED}) !== '{first}'")
                return first, moved

        with self._instance() as instance:
            first, moved = asyncio.run(run(instance))
        self.assertNotEqual(first, moved)

    def test_the_menu_cursor_wraps(self) -> None:
        """Stepping back from the first item lands on the last, not on nothing."""
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                first = await self._open_menu(browser, instance)
                await browser.press("ArrowUp", "ArrowUp")
                wrapped = await browser.wait_for(
                    f"({self.SELECTED}) && ({self.SELECTED}) !== '{first}'")
                return first, wrapped

        with self._instance() as instance:
            first, wrapped = asyncio.run(run(instance))
        self.assertNotEqual(first, wrapped)

    def test_back_closes_the_menu(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_menu(browser, instance)
                await browser.press("b", "KeyB")
                return await browser.wait_for(
                    "document.getElementById('menu-frame')?.style.display === 'none'")

        with self._instance() as instance:
            self.assertTrue(asyncio.run(run(instance)))

    # -- the collection menu, which carries a second cursor inside the first ---
    #
    # Its dropdown is a list inside a list: the rows navigate, and selecting one opens
    # a popup that navigates on the same keys. Nothing covered either.

    POPUP = ("(() => { const d = document.getElementById('collection-menu-frame')"
             "?.contentDocument; if (!d) return ''; "
             "const p = d.getElementById('dropdown-popup'); "
             "return (p && p.style.display === 'block') "
             "? (p.querySelector('.popup-option.selected')?.textContent || 'open') : ''; })()")

    COLLECTION_ROW = ("(document.getElementById('collection-menu-frame')?.contentDocument"
                      "?.querySelector('li.menu-item.selected')?.id) || ''")

    async def _open_collection_menu(self, browser, instance):
        await self._open(browser, instance, "playfield")
        await browser.press("c", "KeyC")
        return await browser.wait_for(self.COLLECTION_ROW)

    def test_the_collection_menu_opens_with_a_row_selected(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                return await self._open_collection_menu(browser, instance)

        with self._instance() as instance:
            self.assertTrue(asyncio.run(run(instance)))

    def test_the_collection_menu_cursor_moves(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                first = await self._open_collection_menu(browser, instance)
                await browser.press("ArrowDown", "ArrowDown")
                moved = await browser.wait_for(
                    f"({self.COLLECTION_ROW}) && ({self.COLLECTION_ROW}) !== '{first}'")
                return first, moved

        with self._instance() as instance:
            first, moved = asyncio.run(run(instance))
        self.assertNotEqual(first, moved)

    def test_the_dropdown_opens_and_its_own_cursor_moves(self) -> None:
        """The inner list. Selecting a row opens a popup that navigates on the same
        keys, and it kept a second hand-rolled cursor to do it."""
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_collection_menu(browser, instance)
                await browser.press("Enter", "Enter")
                first = await browser.wait_for(self.POPUP)
                await browser.press("ArrowDown", "ArrowDown")
                moved = await browser.wait_for(
                    f"({self.POPUP}) && ({self.POPUP}) !== '{first}'")
                return first, moved

        with self._instance() as instance:
            first, moved = asyncio.run(run(instance))
        self.assertNotEqual(first, moved)

    def test_the_menu_says_what_a_page_press_will_do(self) -> None:
        """The only place this is visible. A page press moves by the sort's groups or by
        a count, and which one depends on the collection - so it is told, not guessed."""
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_collection_menu(browser, instance)
                return await browser.wait_for(
                    "(document.getElementById('collection-menu-frame')?.contentDocument"
                    "?.getElementById('paging-state')?.textContent) || ''")

        with self._instance() as instance:
            said = asyncio.run(run(instance))
        self.assertTrue(said.startswith("Pages "), f"the menu said {said!r}")

    def test_back_closes_the_dropdown_before_the_menu(self) -> None:
        """Two nested lists, so back has to unwind one at a time."""
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_collection_menu(browser, instance)
                await browser.press("Enter", "Enter")
                await browser.wait_for(self.POPUP)
                await browser.press("b", "KeyB")
                closed = await browser.wait_for(f"!({self.POPUP})")
                still_open = await browser.evaluate(
                    "document.getElementById('collection-menu-frame')?.style.display")
                return closed, still_open

        with self._instance() as instance:
            closed, still_open = asyncio.run(run(instance))
        self.assertTrue(closed)
        self.assertEqual(still_open, "block", "back closed the whole menu, not the popup")

    # -- what a frame throws reaches the log -------------------------------
    #
    # An overlay is an iframe with its own browser log, and nothing reads it on a cabinet.
    # A ReferenceError in the menu left it not responding to any key with a clean log;
    # the only clue was pressing a button and watching nothing happen.

    def test_an_overlay_that_throws_says_so_in_the_log(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_menu(browser, instance)
                # eval inside the frame, so the closure belongs to the frame's realm.
                # Passing a parent-defined callback to contentWindow.setTimeout reports
                # the error on the *parent* - which passes this test while proving
                # nothing about the overlay path it is named for.
                await browser.evaluate(
                    'document.getElementById("menu-frame").contentWindow.eval('
                    '"setTimeout(function () { throw new Error(\'menu exploded\'); }, 0)")')
                await _logged(instance, "menu exploded")

        with self._instance() as instance:
            asyncio.run(run(instance))
            log = instance.output(tail=None)
        self.assertTrue(
            any("[playfield/menu] threw" in line and "menu exploded" in line
                for line in log.splitlines()),
            "the fault has to name the overlay, or it reads as the theme's")

    def test_the_theme_page_reports_its_own_throws(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open(browser, instance, "playfield")
                await browser.evaluate(
                    "setTimeout(() => { throw new Error('theme exploded'); }, 0)")
                await _logged(instance, "theme exploded")

        with self._instance() as instance:
            asyncio.run(run(instance))
            log = instance.output(tail=None)
        self.assertIn("theme exploded", log)


@_needs_a_browser
class PlayerListSmokeTests(TempTree, _Smoke):
    BOOTS_PER_TEST = ("They add players, and one needs the owner alone, so the roster "
                      "cannot be shared.")

    def setUp(self) -> None:
        super().setUp()
        _write_library(self.root)

    def _instance(self):
        return LiveInstance(self.root)

    # -- the Player list: shown only when the roster holds more than one ---------

    PLAYER_ITEM_TEXT = (
        "(() => { const doc = document.getElementById('menu-frame')?.contentDocument;"
        " const item = doc?.getElementById('player-item'); if (!item) return null;"
        " return doc.defaultView.getComputedStyle(item).display === 'none'"
        " ? '' : item.textContent; })()")

    PLAYER_ROWS = (
        "[...(document.getElementById('menu-frame')?.contentDocument"
        "?.querySelectorAll('.player-choice') || [])].map(el => ["
        "el.dataset.playerId, el.classList.contains('selected'),"
        " el.classList.contains('player-choice-up')])")

    # Each row's circle: [player id, has a picture, initials, round]. Null until every
    # picture drawn has loaded.
    PLAYER_AVATARS = (
        "(rows => rows.length && rows.every(r => { const i = r.querySelector('img');"
        " return !i || i.naturalWidth > 0; }) ? rows.map(r => {"
        " const a = r.querySelector('.player-avatar');"
        " return [r.dataset.playerId, !!a.querySelector('img'), a.dataset.initials || '',"
        " getComputedStyle(a).borderRadius]; }) : null)"
        "([...(document.getElementById('menu-frame')?.contentDocument"
        "?.querySelectorAll('.player-choice') || [])])")

    def test_the_player_list_draws_a_circle_before_each_name(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_menu(browser, instance)
                await browser.press("ArrowDown", "ArrowDown")
                await browser.wait_for(
                    f"({self.SELECTED}) === 'player-item' && ({self.PLAYER_ITEM_TEXT})")
                await browser.press("Enter", "Enter")
                circles = await browser.wait_for(self.PLAYER_AVATARS)
                # Start toggles the row under the cursor; its circle stays.
                await browser.press("ArrowDown", "ArrowDown")
                moved = await browser.wait_for(
                    f"(({self.PLAYER_ROWS}).find(r => r[1]) || [])[0]")
                before_up = next(row[2] for row in await browser.evaluate(self.PLAYER_ROWS)
                                 if row[0] == moved)
                await browser.press("Enter", "Enter")
                await browser.wait_for(
                    f"(({self.PLAYER_ROWS}).find(r => r[0] === {moved!r}) || [])[2]"
                    f" !== {str(before_up).lower()}")
                kept = await browser.evaluate(self.PLAYER_AVATARS)
                # A picture that will not load leaves the initials.
                fell_back = await browser.evaluate(
                    "new Promise(resolve => { const doc = document.getElementById("
                    "'menu-frame').contentDocument; const img = doc.querySelector("
                    "'.player-choice img'); const circle = img.closest('.player-avatar');"
                    " img.addEventListener('error', () => setTimeout(() => resolve("
                    "[!circle.querySelector('img'), circle.dataset.initials]), 0));"
                    " img.src = 'data:image/png;base64,AAAA'; })")
                return circles, kept, fell_back

        with self._instance() as instance:
            instance.wait_for_api()
            kid = instance.post("/api/v1/players", {"name": "Kid", "initials": "KID"})["id"]
            requests.put(
                instance.manager_url(f"/api/v1/players/{kid}/picture"),
                files={"file": ("red.png", pictures.png(80, 60, pictures.RED), "image/png")},
                timeout=10).raise_for_status()
            circles, kept, fell_back = asyncio.run(run(instance))

        with_picture = [row[0] for row in circles if row[1]]
        self.assertEqual(with_picture, [kid])
        self.assertTrue(all(row[3] == "50%" for row in circles))
        self.assertEqual({row[2] for row in circles if row[0] == kid}, {"KID"})
        self.assertEqual([row[:2] for row in kept], [row[:2] for row in circles])
        self.assertEqual(fell_back, [True, "KID"])

    def test_the_player_item_is_hidden_with_one_player(self) -> None:
        """The roster holds only the owner until a test, or a person, adds someone."""
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_menu(browser, instance)
                return await browser.evaluate(self.PLAYER_ITEM_TEXT)

        with self._instance() as instance:
            self.assertEqual(asyncio.run(run(instance)), "")

    def test_the_player_list_shows_and_toggles_who_is_up(self) -> None:
        async def run(instance):
            async with BrowserSession(chromium_path()) as browser:
                await self._open_menu(browser, instance)
                await browser.press("ArrowDown", "ArrowDown")
                label = await browser.wait_for(
                    f"({self.SELECTED}) === 'player-item' && ({self.PLAYER_ITEM_TEXT})")
                await browser.press("Enter", "Enter")
                first = await browser.wait_for(self.PLAYER_ROWS)
                # The flippers move the list's own cursor too.
                await browser.press("ArrowDown", "ArrowDown")
                moved = await browser.wait_for(
                    f"(({self.PLAYER_ROWS}).find(r => r[1]) || [])[0]")
                self.assertNotEqual(first[0][0], moved)
                # Start toggles the row now under the cursor.
                before_up = next(row[2] for row in first if row[0] == moved)
                await browser.press("Enter", "Enter")
                await browser.wait_for(
                    f"(({self.PLAYER_ROWS}).find(r => r[0] === {moved!r}) || [])[2]"
                    f" !== {str(before_up).lower()}")
                await browser.press("b", "KeyB")
                closed = await browser.wait_for(
                    f"!({self.PLAYER_ROWS}).length")
                return label, moved, closed

        with self._instance() as instance:
            instance.wait_for_api()
            instance.post("/api/v1/players", {"name": "Kid", "initials": "KID"})
            label, toggled_id, closed = asyncio.run(run(instance))
            roster = {p["id"]: p["up"] for p in instance.api("/api/v1/players")["players"]}

        self.assertIn("Player", label)
        self.assertTrue(closed)
        self.assertTrue(roster[toggled_id], "the row Start toggled did not end up up")


_LATE_STEP_S = 0.2
_LOG_S = 30.0


async def _settles(browser, patience: float = 15.0):
    """Wait for the page to let go of the hold and the wheel to stop moving, then say
    whether it stayed stopped.

    Two readings a fixed pause apart is the obvious way to write this and it is wrong:
    the repeat already in flight when the release was sent lands whenever the machine
    gets round to it, so under load a correct stop reads as a runaway. This waits for
    the count to hold still first, and only then asks whether it holds still again -
    each time for as long as a running hold goes between two steps.
    """
    try:
        await browser.wait_for("window.vpin._hold === null", timeout=patience)
    except TimeoutError:
        raise AssertionError("the page never let go of the hold") from None
    window = await browser.evaluate("window.vpin.repeatDelayMs") / 1000 + _LATE_STEP_S
    deadline = asyncio.get_event_loop().time() + patience
    seen = await browser.evaluate("window.__moves")
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(window)
        now = await browser.evaluate("window.__moves")
        if now == seen:
            await asyncio.sleep(window)
            return now, await browser.evaluate("window.__moves")
        seen = now
    raise AssertionError(f"the wheel never stopped moving ({seen} steps and counting)")


async def _logged(instance: LiveInstance, text: str) -> None:
    """Until the instance's log holds `text`, or `_LOG_S` - the assertion says which."""
    deadline = asyncio.get_event_loop().time() + _LOG_S
    while text not in instance.output(tail=None) \
            and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    unittest.main()

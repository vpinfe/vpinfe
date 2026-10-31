"""The test card the Manager puts on the playfield screen to ask whether it is rotated
correctly."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any
from unittest import mock

from common.i18n import t
from frontend import screen_card
from tests.support.browser_session import BrowserSession, chromium_path

SCREEN = SimpleNamespace(number=2, name="", width=1080, height=1920, x=0, y=0)
OTHER = SimpleNamespace(number=3, name="", width=1920, height=1080, x=1080, y=0)

# Kept before the module is patched, which the thread `show` starts would otherwise run.
_watch = screen_card._watch

# What a check that the card stayed up waits out: a dismissal is a loopback POST, answered
# in a few milliseconds, so this is long enough for one that was sent to have arrived.
QUIET_WINDOW_S = 0.5


def _window(exit_code: int | None = None) -> mock.Mock:
    return mock.Mock(**{"poll.return_value": exit_code})


def _post(address: str, path: str = "dismiss", origin: str | None = None) -> int:
    request = urllib.request.Request(address + path, data=b"", method="POST")
    if origin is not None:
        request.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:
            return answer.status
    except urllib.error.HTTPError as error:
        error.close()
        return error.code


def _until(done: Any, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(0.02)
    return False


def _get(address: str, path: str = "") -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(address + path, timeout=5) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, b""


class PageTests(unittest.TestCase):
    def test_it_says_which_end_is_the_top_and_which_the_bottom(self) -> None:
        page = screen_card.page(2, 0)

        for key in ("manager.screens.card_top", "manager.screens.card_top.help",
                    "manager.screens.card_bottom", "manager.screens.card_bottom.help"):
            self.assertIn(t(key), page)

    def test_it_says_the_screen_and_that_it_is_the_playfield(self) -> None:
        page = screen_card.page(2, 0)

        self.assertIn("<b>2</b>", page)
        self.assertIn(t("manager.screens.playfield"), page)

    def test_it_is_turned_by_the_rule_a_theme_gets_from_the_frontend(self) -> None:
        page = screen_card.page(2, 270)

        self.assertIn('class="vpinfe-upright card"', page)
        self.assertIn('data-vpinfe-upright="270"', page)
        self.assertIn("--vpinfe-upright-rotation:270deg", page)
        self.assertIn('href="/vpinfe-style.css"', page)

    def test_it_asks_for_its_turn_and_gives_up_when_nothing_answers(self) -> None:
        page = screen_card.page(2, 0)

        self.assertIn("fetch('/state'", page)
        self.assertIn(f"setInterval(ask,{int(screen_card.POLL * 1000)})", page)
        self.assertIn(f"failing>{int(screen_card.GIVE_UP * 1000)}", page)
        self.assertIn("window.close()", page)

    def test_it_closes_on_a_key_or_a_click_and_says_so(self) -> None:
        page = screen_card.page(2, 0)

        self.assertIn("addEventListener('keydown',leave)", page)
        self.assertIn("addEventListener('pointerdown',leave)", page)
        self.assertIn("fetch('/dismiss',{method:'POST'})", page)
        self.assertIn(t("manager.screens.card_dismiss"), page)

    def test_it_ignores_input_for_a_moment_after_it_appears(self) -> None:
        page = screen_card.page(2, 0)

        self.assertIn(f"Date.now()-born<{int(screen_card.GRACE * 1000)}", page)

    def test_a_modifier_or_a_held_key_does_not_close_it(self) -> None:
        page = screen_card.page(2, 0)

        for word in ("e.repeat", "e.ctrlKey", "e.altKey", "e.metaKey", "'Shift'", "'Meta'"):
            self.assertIn(word, page)

    def test_it_draws_no_markup_a_translation_could_bring(self) -> None:
        with mock.patch.object(screen_card, "t", return_value="<i>x</i>"):
            page = screen_card.page(2, 0)

        self.assertNotIn("<i>x</i>", page)
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", page)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = screen_card._Server(2, 90)
        thread = threading.Thread(target=self.server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_the_page_is_served_for_its_screen_and_turn(self) -> None:
        status, body = _get(self.server.address)

        self.assertEqual(status, 200)
        self.assertEqual(body.decode(), screen_card.page(2, 90))

    def test_the_turn_is_answered_as_it_stands_now(self) -> None:
        self.assertEqual(json.loads(_get(self.server.address, "state")[1]), {"rotation": 90})

        self.server.rotation = 180

        self.assertEqual(json.loads(_get(self.server.address, "state")[1]), {"rotation": 180})

    def test_the_frontend_s_stylesheet_is_served(self) -> None:
        status, body = _get(self.server.address, "vpinfe-style.css")

        self.assertEqual(status, 200)
        self.assertIn(b"vpinfe-upright", body)

    def test_nothing_else_is_served(self) -> None:
        self.assertEqual(_get(self.server.address, "etc/passwd")[0], 404)

    def test_a_dismiss_is_answered_and_closes_nothing_where_no_card_is_open(self) -> None:
        self.assertEqual(_post(self.server.address), 204)

    def test_only_a_dismiss_can_be_posted(self) -> None:
        self.assertEqual(_post(self.server.address, "state"), 404)

    def test_a_dismiss_from_another_origin_is_refused(self) -> None:
        self.assertEqual(_post(self.server.address, origin="http://example.test"), 403)
        self.assertEqual(
            _post(self.server.address, origin=self.server.address.rstrip("/")), 204)

    def test_it_listens_on_this_device_only(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class ShowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = self.enterContext(mock.patch.object(screen_card, "ChromiumManager"))
        self.launch = self.enterContext(mock.patch.object(screen_card.screen_numbers, "launch"))
        self.place = self.enterContext(mock.patch.object(screen_card.screen_numbers, "place_all"))
        self.report = self.enterContext(mock.patch.object(screen_card.screen_numbers, "report"))
        self.refocus = self.enterContext(mock.patch.object(screen_card.screen_numbers, "refocus"))
        self.compositor: Any = None
        self.enterContext(mock.patch.object(screen_card.screen_numbers, "window_commands",
                                            side_effect=lambda: self.compositor))
        self.enterContext(mock.patch.object(screen_card.screen_numbers, "focused",
                                            return_value="view-9"))
        self.enterContext(mock.patch.object(screen_card, "_watch"))
        self.proc = _window()
        self.launch.side_effect = lambda _manager, screen, url: (screen, self.proc, mock.Mock())
        self.addCleanup(screen_card.close)

    def _address(self) -> str:
        assert screen_card._card is not None
        return screen_card._card.server.address

    def test_the_card_is_opened_on_the_screen_at_a_page_served_here(self) -> None:
        screen_card.show(SCREEN, 90)

        [call] = self.launch.call_args_list
        self.assertIs(call.args[0], self.manager.return_value)
        self.assertIs(call.args[1], SCREEN)
        self.assertEqual(call.args[2], self._address())
        self.assertTrue(screen_card.is_open())
        self.assertEqual(_get(self._address())[1].decode(), screen_card.page(2, 90))

    def test_a_dismiss_from_the_page_closes_the_card_without_waiting_on_the_manager(self) -> None:
        screen_card.show(SCREEN, 90)
        address = self._address()

        self.assertEqual(_post(address), 204)

        self.assertTrue(_until(lambda: not screen_card.is_open()))
        self.manager.return_value.terminate_all.assert_called_once_with()
        self.refocus.assert_called_once_with(None, "view-9")
        self.assertFalse(screen_card.keep())

    def test_a_dismissed_card_can_be_shown_again(self) -> None:
        screen_card.show(SCREEN, 90)
        _post(self._address())
        self.assertTrue(_until(lambda: screen_card._card is None))

        screen_card.show(SCREEN, 90)

        self.assertTrue(screen_card.is_open())
        self.assertEqual(self.launch.call_count, 2)

    def test_a_page_left_from_a_closed_card_closes_nothing_of_the_next(self) -> None:
        screen_card.show(SCREEN, 90)
        old = screen_card._card
        assert old is not None
        screen_card.close()
        screen_card.show(OTHER, 0)

        screen_card._dismissed(old.server)

        self.assertTrue(screen_card.is_open())

    def test_a_card_not_yet_asked_for_is_not_open(self) -> None:
        self.assertFalse(screen_card.is_open())

    def test_a_card_whose_window_has_gone_is_not_open(self) -> None:
        self.proc.poll.return_value = 0
        screen_card.show(SCREEN, 0)

        self.assertFalse(screen_card.is_open())

    def test_a_card_that_is_open_is_turned_and_not_opened_again(self) -> None:
        screen_card.show(SCREEN, 0)

        screen_card.show(SCREEN, 180)

        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(json.loads(_get(self._address(), "state")[1]), {"rotation": 180})

    def test_turning_changes_what_the_open_page_is_told(self) -> None:
        screen_card.show(SCREEN, 0)

        screen_card.turn(270)

        self.assertEqual(json.loads(_get(self._address(), "state")[1]), {"rotation": 270})

    def test_a_card_open_on_another_screen_is_closed_for_one_on_this_screen(self) -> None:
        screen_card.show(SCREEN, 90)
        first = self._address()

        screen_card.show(OTHER, 90)

        self.assertEqual(self.launch.call_count, 2)
        self.assertEqual(self.launch.call_args.args[1], OTHER)
        self.manager.return_value.terminate_all.assert_called_once_with()
        self.assertNotEqual(self._address(), first)

    def test_turning_with_none_open_opens_nothing(self) -> None:
        screen_card.turn(90)

        self.assertIsNone(screen_card._card)

    def test_a_turn_that_is_no_quarter_is_none(self) -> None:
        screen_card.show(SCREEN, 90)

        screen_card.turn("auto")

        self.assertEqual(json.loads(_get(self._address(), "state")[1]), {"rotation": 0})

    def test_closing_takes_the_window_down_gives_focus_back_and_stops_serving(self) -> None:
        screen_card.show(SCREEN, 0)
        address = self._address()

        screen_card.close()

        self.manager.return_value.terminate_all.assert_called_once_with()
        self.refocus.assert_called_once_with(None, "view-9")
        self.assertFalse(screen_card.is_open())
        with self.assertRaises(OSError):
            _get(address, "state")

    def test_what_the_window_said_is_reported_once_it_is_closed(self) -> None:
        screen_card.show(SCREEN, 0)

        screen_card.close()

        self.report.assert_called_once()
        self.assertIs(self.report.call_args.args[0], SCREEN)

    def test_what_the_window_said_is_read_before_the_window_is_taken_down(self) -> None:
        screen_card.show(SCREEN, 0)
        order = mock.Mock()
        order.attach_mock(self.report, "report")
        order.attach_mock(self.manager.return_value.terminate_all, "terminate")

        screen_card.close()

        self.assertEqual([call[0] for call in order.mock_calls], ["report", "terminate"])

    def test_closing_with_none_open_does_nothing(self) -> None:
        screen_card.close()

        self.manager.return_value.terminate_all.assert_not_called()

    def test_a_window_that_cannot_open_raises_the_reason_and_leaves_nothing_up(self) -> None:
        self.launch.side_effect = FileNotFoundError("Chromium binary not found")

        with self.assertRaises(FileNotFoundError):
            screen_card.show(SCREEN, 0)

        self.assertFalse(screen_card.is_open())
        self.manager.return_value.terminate_all.assert_called_once_with()

    def test_a_card_that_fails_before_any_window_is_not_left_serving(self) -> None:
        made: list[Any] = []
        real = screen_card._Server

        def make(*args: Any) -> Any:
            made.append(real(*args))
            return made[-1]

        self.enterContext(mock.patch.object(screen_card, "_Server", side_effect=make))
        self.manager.side_effect = RuntimeError("no browser")

        with self.assertRaises(RuntimeError):
            screen_card.show(SCREEN, 0)

        self.assertFalse(screen_card.is_open())
        with self.assertRaises(OSError):
            _get(made[0].address, "state")

    def test_it_can_be_opened_again_after_it_failed(self) -> None:
        self.launch.side_effect = FileNotFoundError("gone")
        with self.assertRaises(FileNotFoundError):
            screen_card.show(SCREEN, 0)
        self.launch.side_effect = lambda _manager, screen, url: (screen, self.proc, mock.Mock())

        screen_card.show(SCREEN, 0)

        self.assertTrue(screen_card.is_open())

    def test_a_compositor_that_places_windows_is_asked_to_place_the_card(self) -> None:
        self.compositor = mock.Mock()

        screen_card.show(SCREEN, 0)

        self.place.assert_called_once()
        args = self.place.call_args.args
        self.assertIs(args[1], self.compositor)
        self.assertEqual(args[3](SCREEN), self._address())

    def test_no_compositor_means_no_placing(self) -> None:
        screen_card.show(SCREEN, 0)

        self.place.assert_not_called()


class HoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = self.enterContext(mock.patch.object(screen_card, "ChromiumManager"))
        self.enterContext(mock.patch.object(screen_card.screen_numbers, "launch",
                                            side_effect=self._launch))
        self.enterContext(mock.patch.object(screen_card.screen_numbers, "window_commands",
                                            return_value=None))
        self.enterContext(mock.patch.object(screen_card.screen_numbers, "focused",
                                            return_value=None))
        self.enterContext(mock.patch.object(screen_card.screen_numbers, "refocus"))
        self.enterContext(mock.patch.object(screen_card.screen_numbers, "report"))
        self.enterContext(mock.patch.object(screen_card, "_watch"))
        self.sleep = self.enterContext(mock.patch.object(screen_card.time, "sleep"))
        self.now = self.enterContext(mock.patch.object(screen_card.time, "monotonic",
                                                       return_value=100.0))
        self.proc = _window()
        self.addCleanup(screen_card.close)
        screen_card.show(SCREEN, 0)

    def _launch(self, _manager: Any, screen: Any, _url: str) -> Any:
        return screen, self.proc, mock.Mock()

    def test_a_card_nobody_holds_is_closed(self) -> None:
        held = screen_card._card
        assert held is not None
        self.now.return_value = 100.0 + screen_card.HOLD + 0.5

        _watch(held)

        self.assertFalse(screen_card.is_open())
        self.manager.return_value.terminate_all.assert_called_once_with()

    def test_a_card_that_is_held_stays_up(self) -> None:
        held = screen_card._card
        assert held is not None
        self.now.return_value = 100.0 + screen_card.HOLD - 1

        def after_three(_seconds: float) -> None:
            if self.sleep.call_count == 3:
                screen_card.close()

        self.sleep.side_effect = after_three

        _watch(held)

        self.assertEqual(self.sleep.call_count, 3)
        self.manager.return_value.terminate_all.assert_called_once_with()

    def test_keeping_it_puts_the_deadline_back(self) -> None:
        held = screen_card._card
        assert held is not None
        self.now.return_value = 105.0

        screen_card.keep()

        self.assertEqual(held.kept, 105.0)

    def test_keeping_says_whether_the_card_is_up(self) -> None:
        self.assertTrue(screen_card.keep())

        self.proc.poll.return_value = 0

        self.assertFalse(screen_card.keep())

    def test_keeping_one_that_is_not_open_says_so(self) -> None:
        screen_card.close()

        self.assertFalse(screen_card.keep())

    def test_a_card_whose_window_has_gone_is_closed_whoever_holds_it(self) -> None:
        held = screen_card._card
        assert held is not None
        self.proc.poll.return_value = 0

        _watch(held)

        self.assertIsNone(screen_card._card)

    def test_a_watch_on_a_card_already_closed_ends_without_closing_another(self) -> None:
        held = screen_card._card
        assert held is not None
        screen_card.close()
        screen_card.show(SCREEN, 90)
        newer = screen_card._card
        self.now.return_value = 100.0 + screen_card.HOLD + 5

        _watch(held)

        self.assertIs(screen_card._card, newer)


@unittest.skipIf(chromium_path() is None, "no Chromium on this machine")
class DismissDriveTests(unittest.TestCase):
    """The card page in a real browser, pressed the way a person presses it."""

    def _drive(self, grace: float, scenario: Any) -> int:
        dismissed = threading.Event()
        counted: list[int] = []

        def said(_server: Any) -> None:
            counted.append(1)
            dismissed.set()

        server = screen_card._Server(2, 0)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        async def run() -> None:
            async with BrowserSession(chromium_path() or "") as browser:
                with mock.patch.object(screen_card, "GRACE", grace):
                    await browser.navigate(server.address)
                    await browser.wait_for("document.querySelector('.card') && "
                                           "typeof born === 'number'")
                await scenario(browser, dismissed)

        with mock.patch.object(screen_card, "_dismissed", side_effect=said):
            asyncio.run(run())
        return len(counted)

    def test_a_key_closes_it(self) -> None:
        async def scenario(browser: BrowserSession, dismissed: threading.Event) -> None:
            await browser.press("a", "KeyA", 65)
            await asyncio.to_thread(dismissed.wait, 5)

        self.assertEqual(self._drive(0, scenario), 1)

    def test_a_click_closes_it(self) -> None:
        async def scenario(browser: BrowserSession, dismissed: threading.Event) -> None:
            await browser.click(".card")
            await asyncio.to_thread(dismissed.wait, 5)

        self.assertEqual(self._drive(0, scenario), 1)

    def test_nothing_closes_it_in_the_first_moment(self) -> None:
        async def scenario(browser: BrowserSession, dismissed: threading.Event) -> None:
            await browser.press("a", "KeyA", 65)
            await browser.click(".card")
            await asyncio.sleep(QUIET_WINDOW_S)

        self.assertEqual(self._drive(60, scenario), 0)

    def test_a_modifier_a_switch_of_window_and_a_held_key_leave_it_up(self) -> None:
        async def scenario(browser: BrowserSession, dismissed: threading.Event) -> None:
            await browser.press("Shift", "ShiftLeft", 16)
            await browser.send("Input.dispatchKeyEvent",
                               {"type": "keyDown", "key": "Tab", "code": "Tab",
                                "windowsVirtualKeyCode": 9, "modifiers": 1})
            await browser.send("Input.dispatchKeyEvent",
                               {"type": "keyDown", "key": "a", "code": "KeyA",
                                "windowsVirtualKeyCode": 65, "autoRepeat": True})
            await asyncio.sleep(QUIET_WINDOW_S)
            assert not dismissed.is_set()
            await browser.press("Enter", "Enter", 13)
            await asyncio.to_thread(dismissed.wait, 5)

        self.assertEqual(self._drive(0, scenario), 1)

    def test_taking_focus_starts_the_first_moment_again(self) -> None:
        async def scenario(browser: BrowserSession, dismissed: threading.Event) -> None:
            await browser.wait_for("Date.now() - born >= 1500")
            await browser.evaluate("window.dispatchEvent(new Event('focus'))")
            await browser.press("a", "KeyA", 65)
            await asyncio.sleep(QUIET_WINDOW_S)
            assert not dismissed.is_set()
            await browser.wait_for("Date.now() - born >= 1500")
            await browser.press("a", "KeyA", 65)
            await asyncio.to_thread(dismissed.wait, 5)

        self.assertEqual(self._drive(1.5, scenario), 1)

    def test_two_presses_close_it_once(self) -> None:
        async def scenario(browser: BrowserSession, dismissed: threading.Event) -> None:
            await browser.press("a", "KeyA", 65)
            await browser.click(".card")
            await asyncio.sleep(QUIET_WINDOW_S)

        self.assertEqual(self._drive(0, scenario), 1)


if __name__ == "__main__":
    unittest.main()

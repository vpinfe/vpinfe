"""The Input page hears the cabinet's controller from any device: a page whose switch is on
asks for it over the bus, each button reported comes back to the pages that asked, and the
cabinet can take them back."""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from nicegui import Client, ui

from common import events
from common.i18n import t
from manager.kit import confirm
from manager.settings import binding_table as table
from manager.settings import input_relay
from tests.manager.test_binding_table import _drawn
from tests.support.clicks import press, said


class _Page:
    """What a Manager page is to the relay: an id, a way to run script in it, and a way to
    be told when it goes."""

    def __init__(self, name: str) -> None:
        self.id = name
        self.is_deleted = False
        self.has_socket_connection = True
        self.ran: list[str] = []
        self.gone: list[Any] = []

    def run_javascript(self, code: str) -> None:
        self.ran.append(code)

    def on_disconnect(self, handler: Any) -> None:
        self.gone.append(handler)

    def on_delete(self, handler: Any) -> None:
        self.gone.append(handler)


class _Asked:
    def __init__(self) -> None:
        self.seen: list[dict] = []
        events.subscribe(events.INPUT_CAPTURE_ARMED, self._note)

    def _note(self, **payload: Any) -> None:
        self.seen.append(payload)

    @property
    def armed(self) -> list[bool]:
        return [one["armed"] for one in self.seen]


class _RelayCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        events.clear()
        input_relay.reset_for_tests()
        self.asked = _Asked()
        self.addCleanup(events.clear)
        self.addCleanup(input_relay.reset_for_tests)


class RelayTests(_RelayCase):
    async def test_a_press_goes_to_every_page_that_holds_the_buttons(self) -> None:
        one, two = _Page("one"), _Page("two")
        input_relay.arm(one)  # type: ignore[arg-type]
        input_relay.arm(two)  # type: ignore[arg-type]

        events.emit(events.INPUT_CAPTURE_PRESS, token="pad:standard/button:4", phase="down")
        await asyncio.sleep(0)

        for page in (one, two):
            self.assertEqual(page.ran, [
                'window.__bindingLive && window.__bindingLive.relay('
                '"pad:standard/button:4", true)'])

    async def test_a_release_says_so(self) -> None:
        page = _Page("one")
        input_relay.arm(page)  # type: ignore[arg-type]

        events.emit(events.INPUT_CAPTURE_PRESS, token="pad:2/button:0", phase="up")
        await asyncio.sleep(0)

        self.assertTrue(page.ran[0].endswith(', false)'))

    async def test_a_page_that_stopped_holding_them_hears_nothing(self) -> None:
        page = _Page("one")
        input_relay.arm(page)  # type: ignore[arg-type]
        input_relay.disarm(page)  # type: ignore[arg-type]

        events.emit(events.INPUT_CAPTURE_PRESS, token="pad:standard/button:4", phase="down")
        await asyncio.sleep(0)

        self.assertEqual(page.ran, [])

    async def test_a_page_that_has_gone_is_dropped_and_not_written_to(self) -> None:
        page = _Page("one")
        input_relay.arm(page)  # type: ignore[arg-type]
        page.is_deleted = True

        events.emit(events.INPUT_CAPTURE_PRESS, token="pad:standard/button:4", phase="down")
        await asyncio.sleep(0)

        self.assertEqual(page.ran, [])
        self.assertEqual(self.asked.armed, [True, False])

    async def test_the_cabinet_is_given_its_buttons_back_when_the_last_page_stops(self) -> None:
        one, two = _Page("one"), _Page("two")
        input_relay.arm(one)  # type: ignore[arg-type]
        input_relay.arm(two)  # type: ignore[arg-type]

        input_relay.disarm(one)  # type: ignore[arg-type]
        self.assertNotIn(False, self.asked.armed)
        input_relay.disarm(two)  # type: ignore[arg-type]

        self.assertEqual(self.asked.armed[-1], False)
        self.assertEqual(self.asked.armed.count(False), 1)

    async def test_a_page_that_closes_stops_holding_them(self) -> None:
        page = _Page("one")
        input_relay.arm(page)  # type: ignore[arg-type]

        for handler in page.gone:
            handler()

        self.assertEqual(self.asked.armed, [True, False])

    async def test_asking_again_leaves_one_holder_and_asks_again(self) -> None:
        page = _Page("one")
        input_relay.arm(page)  # type: ignore[arg-type]
        input_relay.arm(page)  # type: ignore[arg-type]

        self.assertEqual(len(page.gone), 2)
        input_relay.disarm(page)  # type: ignore[arg-type]
        input_relay.arm(page)  # type: ignore[arg-type]
        self.assertEqual(len(page.gone), 2, "the hooks are added once per page")
        self.assertEqual(self.asked.armed, [True, True, False, True])
        self.assertEqual({one["ttl_ms"] for one in self.asked.seen if one["armed"]},
                         {input_relay.TTL_MS})

    async def test_a_page_with_no_connection_yet_is_not_asked_for(self) -> None:
        page = _Page("one")
        page.has_socket_connection = False

        input_relay.arm(page)  # type: ignore[arg-type]

        self.assertEqual(self.asked.armed, [])
        self.assertEqual(page.gone, [])

    async def test_the_ask_lapses_well_after_the_next_one_is_due(self) -> None:
        self.assertGreater(input_relay.TTL_MS / 1000, 2 * input_relay.REFRESH_S)


class WhoHasThemTests(_RelayCase):
    async def test_the_ask_names_the_holder_for_the_cabinet_to_show(self) -> None:
        page = _Page("one")

        input_relay.arm(page, browser="Safari on Mac", device="office-laptop")  # type: ignore[arg-type]

        self.assertEqual((self.asked.seen[-1]["browser"], self.asked.seen[-1]["device"]),
                         ("Safari on Mac", "office-laptop"))

    async def test_two_pages_both_hear_and_the_newest_is_named(self) -> None:
        old, new = _Page("old"), _Page("new")
        input_relay.arm(old, browser="Safari on Mac", device="office-laptop")  # type: ignore[arg-type]
        await asyncio.sleep(0.01)
        input_relay.arm(new, browser="Chrome on Windows", device="den-pc")  # type: ignore[arg-type]
        input_relay.arm(old, browser="Safari on Mac", device="office-laptop")  # type: ignore[arg-type]

        events.emit(events.INPUT_CAPTURE_PRESS, token="key:KeyA", phase="down")
        await asyncio.sleep(0)

        self.assertEqual(len(old.ran), 1)
        self.assertEqual(len(new.ran), 1)
        self.assertEqual(self.asked.seen[-1]["device"], "den-pc")

    async def test_the_older_page_is_named_again_when_the_newer_goes(self) -> None:
        old, new = _Page("old"), _Page("new")
        input_relay.arm(old, device="office-laptop")  # type: ignore[arg-type]
        await asyncio.sleep(0.01)
        input_relay.arm(new, device="den-pc")  # type: ignore[arg-type]

        input_relay.disarm(new)  # type: ignore[arg-type]

        self.assertEqual(self.asked.seen[-1]["device"], "office-laptop")
        self.assertTrue(self.asked.seen[-1]["armed"])

    async def test_a_page_on_the_cabinet_is_named_with_no_device(self) -> None:
        input_relay.arm(_Page("here"), browser="Chrome on Linux")  # type: ignore[arg-type]

        self.assertEqual(self.asked.seen[-1]["device"], "")


class TakenBackTests(_RelayCase):
    async def test_a_button_held_at_the_cabinet_takes_them_from_every_page(self) -> None:
        one, two = _Page("one"), _Page("two")
        input_relay.arm(one)  # type: ignore[arg-type]
        input_relay.arm(two)  # type: ignore[arg-type]

        events.emit(events.INPUT_CAPTURE_RELEASED)
        await asyncio.sleep(0)

        for page in (one, two):
            self.assertEqual(len(page.ran), 1)
            self.assertIn("taken()", page.ran[0])
        self.assertEqual(input_relay.holders(), 0)
        self.assertFalse(self.asked.seen[-1]["armed"])

    async def test_a_refresh_already_on_its_way_does_not_hand_them_back(self) -> None:
        page = _Page("one")
        input_relay.arm(page)  # type: ignore[arg-type]
        events.emit(events.INPUT_CAPTURE_RELEASED)

        input_relay.arm(page)  # type: ignore[arg-type]

        self.assertEqual(input_relay.holders(), 0)
        self.assertFalse(self.asked.seen[-1]["armed"])

    async def test_the_person_turning_the_switch_on_does(self) -> None:
        page = _Page("one")
        input_relay.arm(page)  # type: ignore[arg-type]
        events.emit(events.INPUT_CAPTURE_RELEASED)

        input_relay.resume(page)  # type: ignore[arg-type]
        input_relay.arm(page)  # type: ignore[arg-type]

        self.assertEqual(input_relay.holders(), 1)
        self.assertTrue(self.asked.seen[-1]["armed"])


class TheWindowsAnInputPageOpenedTests(_RelayCase):
    def setUp(self) -> None:
        super().setUp()
        self.up = True
        self.playing = False
        for held in (patch.object(input_relay, "frontend_up", side_effect=lambda: self.up),
                     patch.object(input_relay, "table_playing",
                                  side_effect=lambda: self.playing),
                     patch.object(input_relay, "_close_windows")):
            self.closed = held.start() if held.attribute == "_close_windows" else \
                (held.start() and getattr(self, "closed", None))
            self.addCleanup(held.stop)
        self.close = input_relay._close_windows

    async def _leave(self, page: _Page) -> None:
        page.is_deleted = True
        for handler in page.gone:
            handler()
        with patch.object(input_relay, "LEAVE_GRACE_S", 0):
            input_relay._check_later(page.id)
            await asyncio.sleep(0.02)

    def _page(self) -> _Page:
        page = _Page("one")
        input_relay.present(page)  # type: ignore[arg-type]
        return page

    async def test_they_close_when_the_last_page_leaves(self) -> None:
        page = self._page()
        input_relay.close_with_the_last_page()

        await self._leave(page)

        self.close.assert_called_once()  # type: ignore[attr-defined]

    async def test_they_stay_while_another_page_is_still_there(self) -> None:
        one = self._page()
        two = _Page("two")
        input_relay.present(two)  # type: ignore[arg-type]
        input_relay.close_with_the_last_page()

        await self._leave(one)

        self.close.assert_not_called()  # type: ignore[attr-defined]

    async def test_they_stay_when_the_person_chose_to_leave_them_open(self) -> None:
        page = self._page()
        input_relay.close_with_the_last_page()
        input_relay.leave_open()

        await self._leave(page)

        self.close.assert_not_called()  # type: ignore[attr-defined]

    async def test_they_stay_while_a_table_plays_and_close_when_it_ends(self) -> None:
        page = self._page()
        input_relay.close_with_the_last_page()
        self.playing = True

        await self._leave(page)
        self.close.assert_not_called()  # type: ignore[attr-defined]

        self.playing = False
        with patch.object(input_relay, "LEAVE_GRACE_S", 0):
            await asyncio.sleep(0.05)
        self.close.assert_called_once()  # type: ignore[attr-defined]

    async def test_windows_that_were_already_open_are_never_closed(self) -> None:
        page = self._page()

        await self._leave(page)

        self.close.assert_not_called()  # type: ignore[attr-defined]

    async def test_windows_that_closed_some_other_way_are_not_closed_when_they_reopen(
            self) -> None:
        page = self._page()
        input_relay.close_with_the_last_page()

        events.emit(events.FRONTEND_STATE_CHANGED, state={"running": False})
        events.emit(events.FRONTEND_STATE_CHANGED, state={"running": True})
        await self._leave(page)

        self.close.assert_not_called()  # type: ignore[attr-defined]

    async def test_a_frontend_already_closed_is_left_alone(self) -> None:
        page = self._page()
        input_relay.close_with_the_last_page()
        self.up = False

        await self._leave(page)

        self.close.assert_not_called()  # type: ignore[attr-defined]


class _TableCase(_RelayCase):
    elsewhere = True

    def setUp(self) -> None:
        super().setUp()
        self.body = ui.column()
        self.up = True
        self.playing = False
        self.closed = MagicMock()
        self.script = MagicMock()
        for held in (patch.object(table, "opened_elsewhere", return_value=self.elsewhere),
                     patch.object(Client, "run_javascript", self.script),
                     patch.object(Client, "has_socket_connection", new_callable=PropertyMock,
                                  return_value=True),
                     patch.object(input_relay, "frontend_up", side_effect=lambda: self.up),
                     patch.object(input_relay, "table_playing",
                                  side_effect=lambda: self.playing),
                     patch.object(input_relay, "_close_windows", self.closed),
                     patch.object(table, "holder_of", return_value=("Safari on Mac",
                                                                   "office-laptop")),
                     patch.object(table.offload, "io",
                                  new=AsyncMock(side_effect=lambda f, *a: f(*a)))):
            held.start()
            self.addCleanup(held.stop)

    def drawn(self, **kwargs: Any) -> table.Table:
        control, _ = _drawn(into=self.body, **kwargs)
        return control

    @staticmethod
    def _words(control: table.Table) -> list[str]:
        return [one.text for one in control.send.descendants() if isinstance(one, ui.label)]

    @staticmethod
    def _switch_label(control: table.Table) -> str:
        return t("manager.binding_table.send_events", device=control._cab)

    @staticmethod
    def _relay_words(control: table.Table) -> list[str]:
        assert control._relay is not None
        return [one.text for one in control._relay.descendants() if isinstance(one, ui.label)]

    @staticmethod
    def _where_text(control: table.Table) -> str:
        assert control._where is not None
        return control._where.text

    @staticmethod
    def _switch(control: table.Table) -> ui.switch | None:
        found = [one for one in control.send.descendants() if isinstance(one, ui.switch)]
        return found[0] if found else None


class TheSwitchTests(_TableCase):
    async def test_the_page_asks_for_the_buttons_as_it_opens(self) -> None:
        control = self.drawn()

        await control._begin()

        self.assertEqual(self.asked.armed, [True])
        self.assertEqual((self.asked.seen[0]["browser"], self.asked.seen[0]["device"]),
                         ("Safari on Mac", "office-laptop"))

    async def test_the_switch_is_on_and_sits_under_the_box_not_in_it(self) -> None:
        control = self.drawn()

        switch = self._switch(control)

        assert switch is not None
        self.assertTrue(switch.value)
        self.assertEqual(self._words(control)[0], self._switch_label(control))
        self.assertNotIn(switch, list(control.line.descendants()))

    async def test_nothing_is_said_beside_it_while_all_is_normal(self) -> None:
        control = self.drawn()

        self.assertEqual(self._words(control), [self._switch_label(control)])

    async def test_the_intro_says_the_device_s_buttons_are_heard_while_it_is_on(self) -> None:
        control = self.drawn(how_open=True)

        self.assertEqual(self._where_text(control),
                         t("manager.binding_table.where_device", device=control._cab))

    async def test_the_intro_names_the_browser_s_keys_when_the_switch_is_off(self) -> None:
        control = self.drawn(how_open=True)
        await control._begin()

        control._switched(SimpleNamespace(value=False))

        self.assertEqual(self._where_text(control), t("manager.binding_table.where_browser"))

    async def test_what_the_switch_is_for_sits_under_it_in_a_closed_explanation(self) -> None:
        control = self.drawn(how_open=True)

        assert control._relay is not None
        self.assertFalse(control._relay.value)  # type: ignore[attr-defined]
        self.assertEqual(control._relay.props["label"], t("manager.binding_table.relay_title"))
        self.assertEqual(self._relay_words(control),
                         [t("manager.binding_table.how_relay", device=control._cab)])

    async def test_a_device_name_is_shown_as_text_in_the_explanation(self) -> None:
        with patch.object(table, "display_name", return_value="<i>Cab</i>"):
            control = self.drawn(how_open=True)

        [words] = self._relay_words(control)

        self.assertIn("<i>Cab</i>", words)

    async def test_a_table_drawn_without_the_intro_has_no_where_line(self) -> None:
        self.assertIsNone(self.drawn()._where)

    async def test_turning_it_off_lets_them_go_and_on_asks_again(self) -> None:
        control = self.drawn()
        await control._begin()

        control._switched(SimpleNamespace(value=False))
        control._switched(SimpleNamespace(value=True))

        self.assertEqual(self.asked.armed, [True, False, True])

    async def test_turning_it_off_lets_go_of_what_the_cabinet_was_holding(self) -> None:
        control = self.drawn()
        await control._begin()

        control._switched(SimpleNamespace(value=False))

        self.script.assert_called_once_with(table._DROP_RELAYED_JS)

    async def test_a_page_that_never_held_them_has_nothing_to_let_go_of(self) -> None:
        control = self.drawn()

        control._sync_lease()

        self.script.assert_not_called()

    async def test_a_page_out_of_view_lets_them_go_and_asks_again_when_it_returns(self) -> None:
        control = self.drawn()
        await control._begin()

        said(control.line, "visible", False)
        said(control.line, "visible", True)

        self.assertEqual(self.asked.armed, [True, False, True])

    async def test_a_page_that_opens_out_of_view_does_not_hold_them(self) -> None:
        control = self.drawn()

        said(control.line, "visible", False)
        await control._begin()

        self.assertEqual(self.asked.armed, [])

    async def test_the_page_says_whether_it_is_in_view_as_it_loads(self) -> None:
        self.assertIn("  seen();\n", table._LIVE_JS)
        self.assertIn("if (document.hidden) allUp();", table._LIVE_JS)

    async def test_a_take_back_lets_every_button_up_on_the_page(self) -> None:
        self.assertIn("live.taken = () => { allUp();", table._LIVE_JS)

    async def test_it_keeps_asking_on_every_tick(self) -> None:
        control = self.drawn()
        await control._begin()

        await control._keep()
        await control._keep()

        self.assertEqual(self.asked.armed, [True, True, True])

    async def test_a_page_that_is_gone_gives_them_back(self) -> None:
        control = self.drawn()
        await control._begin()

        control.container.delete()
        await control._keep()

        self.assertEqual(self.asked.armed, [True, False])

    async def test_a_listening_row_does_not_change_who_holds_them(self) -> None:
        control = self.drawn()
        await control._begin()

        said(control.line, "listen", "back")
        await press(control._captured, SimpleNamespace(args={"row": "back", "token": ""}))

        self.assertEqual(self.asked.armed, [True])

    async def test_listening_says_only_how_to_stop_beside_the_switch(self) -> None:
        control = self.drawn()

        said(control.line, "listen", "back")

        self.assertEqual(self._words(control)[-1], "Click away to stop")


class WhenItLetsGoTests(_TableCase):
    async def test_two_minutes_with_nothing_pressed_turns_it_off_and_says_so(self) -> None:
        control = self.drawn()
        await control._begin()
        control._active_at = time.monotonic() - table.IDLE_S - 1

        await control._keep()

        self.assertFalse(control.sending)
        self.assertFalse(self.asked.seen[-1]["armed"])
        self.assertEqual(self._words(control)[-1], t("manager.binding_table.off_idle"))
        switch = self._switch(control)
        assert switch is not None
        self.assertFalse(switch.value)

    async def test_a_press_heard_keeps_it_going(self) -> None:
        control = self.drawn()
        await control._begin()
        control._active_at = time.monotonic() - table.IDLE_S + 5

        control._heard(SimpleNamespace(args="key:KeyA"))
        control._active_at -= table.IDLE_S - 10
        await control._keep()

        self.assertTrue(control.sending)

    async def test_touching_the_page_keeps_it_going(self) -> None:
        control = self.drawn()
        control._active_at = time.monotonic() - table.IDLE_S + 5

        said(control.line, "touched")

        self.assertGreater(time.monotonic() - control._active_at, -1)
        self.assertLess(time.monotonic() - control._active_at, 2)

    async def test_the_cabinet_taking_them_turns_it_off_and_says_who(self) -> None:
        control = self.drawn()
        await control._begin()

        said(control.line, "taken")

        self.assertFalse(control.sending)
        self.assertEqual(self._words(control)[-1],
                         t("manager.binding_table.off_taken", device=control._cab))

    async def test_two_minutes_is_what_the_page_says(self) -> None:
        self.assertEqual(table.IDLE_S, 120)
        self.assertIn("2 minutes", t("manager.binding_table.off_idle"))

    async def test_turning_it_on_during_a_table_still_lifts_the_take_back(self) -> None:
        control = self.drawn()
        await control._begin()
        events.emit(events.INPUT_CAPTURE_RELEASED)
        said(control.line, "taken")
        self.playing = True
        await control._keep()

        control._switched(SimpleNamespace(value=True))
        self.playing = False
        await control._keep()

        self.assertTrue(self.asked.seen[-1]["armed"])
        self.assertEqual(input_relay.holders(), 1)

    async def test_the_intro_names_the_browser_s_keys_while_the_buttons_are_paused(self) -> None:
        control = self.drawn(how_open=True)
        await control._begin()

        self.playing = True
        await control._keep()

        self.assertEqual(self._where_text(control), t("manager.binding_table.where_browser"))

    async def test_a_table_playing_pauses_it_and_closing_picks_it_up(self) -> None:
        control = self.drawn()
        await control._begin()

        self.playing = True
        await control._keep()
        self.assertFalse(self.asked.seen[-1]["armed"])
        self.assertTrue(control.sending, "the switch stays on")
        self.assertEqual(self._words(control)[-1],
                         t("manager.binding_table.paused", device=control._cab))

        self.playing = False
        await control._keep()
        self.assertTrue(self.asked.seen[-1]["armed"])
        self.assertEqual(self._words(control), [self._switch_label(control)])


class TheOfferTests(_TableCase):
    def setUp(self) -> None:
        super().setUp()
        self.up = False
        self.api = MagicMock()
        self.api.perform_action.return_value = {"performed": True}
        held = patch.object(table, "ApiClient", return_value=self.api)
        held.start()
        self.addCleanup(held.stop)

    def _ask(self, answer: confirm.Answer) -> Any:
        held = patch.object(table.confirm, "ask", AsyncMock(return_value=answer))
        asked = held.start()
        self.addCleanup(held.stop)
        return asked

    async def test_a_closed_frontend_offers_to_open_in_place_of_the_switch(self) -> None:
        control = self.drawn()

        self.assertIsNone(self._switch(control))
        self.assertEqual(self._words(control),
                         [t("manager.binding_table.frontend_closed", device=control._cab)])
        buttons = [one for one in control.send.descendants() if isinstance(one, ui.button)]
        self.assertEqual([one.text for one in buttons], [t("action.frontend.start")])

    async def test_the_intro_says_only_the_browser_s_keys_are_heard_when_closed(self) -> None:
        control = self.drawn(how_open=True)

        self.assertEqual(self._where_text(control), t("manager.binding_table.where_browser"))

    async def test_the_intro_does_not_name_a_switch_that_is_not_drawn(self) -> None:
        control = self.drawn(how_open=True)
        paragraph = control._relay
        assert paragraph is not None
        self._ask(confirm.Answer.CONFIRM)

        self.assertFalse(paragraph.visible)

        await press(control._open_windows)

        self.assertFalse(paragraph.visible)

        self.up = True
        await control._keep()

        self.assertTrue(paragraph.visible)

    async def test_cancelling_opens_nothing(self) -> None:
        control = self.drawn()
        self._ask(confirm.Answer.CANCEL)

        await press(control._open_windows)

        self.api.perform_action.assert_not_called()
        self.assertFalse(control.opening)

    async def test_the_question_names_both_ways_to_say_yes(self) -> None:
        control = self.drawn()
        asked = self._ask(confirm.Answer.CANCEL)

        await press(control._open_windows)

        question = asked.call_args
        self.assertEqual(question.args[0],
                         t("manager.binding_table.open_question", device=control._cab))
        self.assertEqual(question.kwargs["confirm"], "Open for This Page")
        self.assertEqual(question.kwargs["second"][0], "Open and Leave Open")

    async def test_open_for_this_page_opens_and_closes_them_with_the_last_page(self) -> None:
        control = self.drawn()
        self._ask(confirm.Answer.CONFIRM)

        await press(control._open_windows)

        self.api.perform_action.assert_called_once()
        self.assertEqual(self.api.perform_action.call_args.args[:2], ("frontend", "start"))
        self.assertTrue(input_relay._close_when_gone)
        self.assertTrue(control.opening)
        self.assertEqual(self._words(control),
                         [t("manager.binding_table.opening", device=control._cab)])

    async def test_open_and_leave_open_does_not_close_them_later(self) -> None:
        control = self.drawn()
        self._ask(confirm.Answer.SECOND)

        await press(control._open_windows)

        self.api.perform_action.assert_called_once()
        self.assertFalse(input_relay._close_when_gone)

    async def test_the_switch_comes_back_on_when_the_frontend_reports_in(self) -> None:
        control = self.drawn()
        self._ask(confirm.Answer.CONFIRM)
        await press(control._open_windows)

        self.up = True
        await control._keep()

        self.assertFalse(control.opening)
        self.assertTrue(control.sending)
        self.assertTrue(self.asked.seen[-1]["armed"])
        self.assertIsNotNone(self._switch(control))

    async def test_a_frontend_that_never_opens_is_given_up_on(self) -> None:
        control = self.drawn()
        self._ask(confirm.Answer.CONFIRM)
        await press(control._open_windows)
        control._opening_since -= table.OPEN_WAIT_S + 1

        with patch.object(ui, "notify") as notified:
            await control._keep()

        self.assertFalse(control.opening)
        notified.assert_called_once()

    async def test_a_refused_start_is_said_and_nothing_waits(self) -> None:
        control = self.drawn()
        self._ask(confirm.Answer.CONFIRM)
        self.api.perform_action.return_value = {"performed": False}

        with patch.object(ui, "notify") as notified:
            await press(control._open_windows)

        notified.assert_called_once()
        self.assertFalse(control.opening)
        self.assertFalse(input_relay._close_when_gone)


class ATableOnTheServingDevice(_TableCase):
    elsewhere = False

    def setUp(self) -> None:
        super().setUp()
        self.up = False

    async def test_the_intro_says_this_device_s_own_keys_and_buttons_are_heard(self) -> None:
        control = self.drawn(how_open=True)

        self.assertEqual(self._where_text(control), t("manager.binding_table.where_here"))

    async def test_the_intro_has_no_paragraph_about_a_switch_that_is_not_there(self) -> None:
        control = self.drawn(how_open=True)

        self.assertIsNone(control._relay)

    async def test_it_draws_no_switch_or_offer_with_the_frontend_up_or_closed(self) -> None:
        for up in (False, True):
            with self.subTest(frontend_up=up):
                self.up = up
                control = self.drawn()

                self.assertIsNone(self._switch(control))
                self.assertEqual(self._words(control), [])
                self.assertFalse(control.send.visible)

    async def test_it_asks_for_the_buttons_too_and_names_no_device(self) -> None:
        with patch.object(table, "holder_of", return_value=("Chrome on Linux", "")):
            control = self.drawn()
            await control._begin()

        self.assertEqual(self.asked.armed, [True])
        self.assertEqual(self.asked.seen[0]["device"], "")

    async def test_two_minutes_with_nothing_pressed_does_not_let_them_go(self) -> None:
        control = self.drawn()
        await control._begin()
        control._active_at = time.monotonic() - table.IDLE_S - 1

        await control._keep()

        self.assertTrue(control.sending)
        self.assertNotIn(False, self.asked.armed)

    async def test_listening_says_how_to_stop_where_the_switch_would_be(self) -> None:
        control = self.drawn()

        said(control.line, "listen", "back")

        self.assertEqual(self._words(control), [t("manager.binding_table.click_away")])
        self.assertTrue(control.send.visible)

    async def test_the_cabinet_taking_them_is_undone_by_listening_for_a_row_again(self) -> None:
        control = self.drawn()
        await control._begin()
        said(control.line, "taken")
        self.assertFalse(control.sending)

        said(control.line, "listen", "back")

        self.assertTrue(control.sending)
        self.assertEqual(self.asked.armed, [True, False, True])

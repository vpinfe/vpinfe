"""The Security page's Sign In section: who is waiting, and the browsers signed in."""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

from nicegui import ui

from common.devices import serving
from common.security import sign_in
from manager.kit import panel
from manager.security import authenticator_panel, security, signed_in_rows, token_panel
from manager.settings import settings

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()

NOW = 2_000_000_000.0
IPHONE = {"id": "a1", "kind": sign_in.OWNER, "address": "192.168.1.31",
          "agent": "Safari on iPhone", "created": 1_000_000.0, "last_used": 1_000_100.0,
          "current": False}


def _request(**over: object) -> sign_in.Request:
    return sign_in.Request(**{"id": "r1", "address": "192.168.1.55", "asked": NOW, **over})  # type: ignore[arg-type]


class WhatTheRowsSay(unittest.TestCase):
    def test_the_kinds_have_their_own_words(self) -> None:
        self.assertEqual(signed_in_rows.kind_label(sign_in.OWNER), "Owner")
        self.assertEqual(signed_in_rows.kind_label(sign_in.REMOTE), "Remote")

    def test_a_browser_is_named_as_it_names_itself(self) -> None:
        [row] = signed_in_rows.rows([IPHONE], "")

        self.assertEqual(row["browser"], "Safari on iPhone")
        self.assertEqual(row["address"], "192.168.1.31")
        self.assertEqual(row["access"], "Owner")

    def test_a_browser_that_never_said_what_it_is_says_so(self) -> None:
        [row] = signed_in_rows.rows([{**IPHONE, "agent": ""}], "")

        self.assertEqual(row["browser"], "Unknown browser")

    def test_the_browser_in_use_is_marked_and_no_other(self) -> None:
        rows = signed_in_rows.rows([IPHONE, {**IPHONE, "id": "b2"}], "b2")

        self.assertEqual({row["id"]: row["here"] for row in rows}, {"a1": False, "b2": True})

    def test_the_most_recently_used_comes_first(self) -> None:
        rows = signed_in_rows.rows(
            [{**IPHONE, "id": "old", "last_used": 10.0},
             {**IPHONE, "id": "new", "last_used": 99.0},
             {**IPHONE, "id": "mid", "last_used": 50.0}], "")

        self.assertEqual([row["id"] for row in rows], ["new", "mid", "old"])

    def test_first_seen_is_a_day_with_the_exact_time_to_hover(self) -> None:
        [row] = signed_in_rows.rows([IPHONE], "")

        self.assertTrue(row["created_day"])
        self.assertNotIn(":", row["created_day"])
        self.assertIn(row["created_day"], row["created_at"])
        self.assertIn(":", row["created_at"])

    def test_the_stamps_sort_as_text_in_time_order(self) -> None:
        early, late = signed_in_rows.rows(
            [{**IPHONE, "id": "late", "created": 2_000.0, "last_used": 9.0},
             {**IPHONE, "id": "early", "created": 1_000.0, "last_used": 8.0}], "")[::-1]

        self.assertLess(early["created"], late["created"])
        self.assertLess(early["last_used"], late["last_used"])


class WhatTheSummarySays(unittest.TestCase):
    def test_how_many_browsers(self) -> None:
        self.assertEqual(signed_in_rows.count_said([IPHONE]), "1 browser")
        self.assertEqual(signed_in_rows.count_said([IPHONE, IPHONE]), "2 browsers")

    def test_when_the_newest_was_used(self) -> None:
        stale = {**IPHONE, "last_used": time.time() - 7200}
        fresh = {**IPHONE, "id": "b2", "last_used": time.time() - 3 * 3600}

        self.assertEqual(signed_in_rows.latest_said([fresh, stale]), "Last used 2 hours ago")

    def test_nobody_signed_in_is_plain_words_and_opens_nothing(self) -> None:
        found = signed_in_rows.entry("Signed In", [], mock.Mock())

        self.assertEqual(found, ("Signed In", "No other browser signed in"))

    def test_someone_signed_in_is_a_summary_that_opens_the_list(self) -> None:
        choose = mock.Mock()

        label, draw, pick = signed_in_rows.entry("Signed In", [IPHONE], choose)

        self.assertEqual(label, "Signed In")
        self.assertTrue(callable(draw))
        self.assertIsInstance(pick, panel.Pick)
        self.assertIs(pick.on_pick, choose)


class WhatTheSectionShows(unittest.TestCase):
    def shown(self, rows: list[dict], request: sign_in.Request | None = None,
              bind: str = "0.0.0.0") -> list[tuple]:
        return security.entries(rows, request, mock.AsyncMock(), bind)

    def test_the_heading_comes_first_and_is_sign_in(self) -> None:
        [heading, *_] = self.shown([IPHONE])

        self.assertEqual(heading, (panel.HEADING, "Sign In"))

    def test_a_section_other_devices_can_reach_has_a_help_line_and_the_summary(self) -> None:
        found = self.shown([IPHONE])

        self.assertEqual([entry[0] for entry in found][:2], [panel.HEADING, panel.LEDE])
        self.assertEqual(found[-1][0], "Signed In")
        self.assertEqual(len(found), 3)

    def test_nothing_is_waiting_unless_a_request_is_live(self) -> None:
        labels = [entry[0] for entry in self.shown([IPHONE])]

        self.assertNotIn("Waiting", labels)

    def test_a_waiting_device_has_a_row_before_the_signed_in_summary(self) -> None:
        labels = [entry[0] for entry in self.shown([IPHONE], _request())]

        self.assertEqual(labels[-2:], ["Waiting", "Signed In"])

    def test_a_device_can_wait_with_nobody_signed_in(self) -> None:
        labels = [entry[0] for entry in self.shown([], _request())]

        self.assertEqual(labels[-2:], ["Waiting", "Signed In"])

    def test_the_waiting_row_has_no_buttons(self) -> None:
        [row] = [entry for entry in self.shown([], _request()) if entry[0] == "Waiting"]

        self.assertEqual(len(row), 2)

    def test_where_no_other_device_can_reach_us_there_is_no_help_line_and_nothing_waits(
            self) -> None:
        self.assertEqual([entry[0] for entry in self.shown([], bind="127.0.0.1")],
                         [panel.HEADING, "Signed In"])


class TheHelpLine(unittest.TestCase):
    def test_it_says_where_the_code_is_shown_and_when_a_browser_signs_out(self) -> None:
        said = security.lede_for([IPHONE], "0.0.0.0")

        self.assertIn("VPinFE device's screen", said)
        self.assertIn("90 days", said)

    def test_it_is_there_before_anyone_has_signed_in(self) -> None:
        self.assertIn("VPinFE device's screen", security.lede_for([], "0.0.0.0"))

    def test_with_nobody_able_to_reach_us_it_is_only_the_rule_for_what_is_signed_in(self) -> None:
        said = security.lede_for([IPHONE], "127.0.0.1")

        self.assertIn("90 days", said)
        self.assertNotIn("screen", said)

    def test_with_nobody_able_to_reach_us_and_nobody_signed_in_it_is_not_there(self) -> None:
        self.assertEqual(security.lede_for([], "127.0.0.1"), "")

    def test_which_addresses_are_reachable_from_elsewhere(self) -> None:
        for bind, expected in (("0.0.0.0", True), ("192.168.1.5", True), ("::", True),
                               ("127.0.0.1", False), ("::1", False), ("localhost", False)):
            with self.subTest(bind=bind):
                self.assertEqual(serving.reachable_from_elsewhere(bind), expected)


class TheWaitingRowKnowsWhenToRedraw(unittest.TestCase):
    def key(self, request: sign_in.Request | None, at: float) -> object:
        with mock.patch.object(security, "time", mock.Mock(time=lambda: at)):
            return security.waiting_key(request)

    def test_no_request_is_no_row(self) -> None:
        self.assertIsNone(self.key(None, NOW))

    def test_the_same_request_in_the_same_minute_is_the_same_row(self) -> None:
        self.assertEqual(self.key(_request(), NOW + 5), self.key(_request(), NOW + 50))

    def test_the_age_it_states_moves_on_each_minute(self) -> None:
        self.assertNotEqual(self.key(_request(), NOW + 30), self.key(_request(), NOW + 90))

    def test_a_new_request_is_a_new_row(self) -> None:
        self.assertNotEqual(self.key(_request(), NOW), self.key(_request(id="r2"), NOW))

    def test_a_request_whose_code_is_shown_is_a_new_row(self) -> None:
        self.assertNotEqual(self.key(_request(), NOW),
                            self.key(_request(state=sign_in.SHOWN), NOW))


class SigningBrowsersOut(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = mock.Mock()
        self.enterContext(mock.patch.object(
            signed_in_rows.run, "io_bound",
            new=mock.AsyncMock(side_effect=lambda fn, *args: fn(*args))))

    async def test_each_browser_picked_is_signed_out(self) -> None:
        left = await signed_in_rows.end_sessions(self.library, ["a1", "b2"], "")

        self.assertEqual([call.args for call in self.library.sign_out_session.call_args_list],
                         [("a1",), ("b2",)])
        self.assertFalse(left)

    async def test_the_browser_in_use_is_left_for_the_page_to_end_itself(self) -> None:
        left = await signed_in_rows.end_sessions(self.library, ["a1", "mine", "b2"], "mine")

        self.assertEqual([call.args for call in self.library.sign_out_session.call_args_list],
                         [("a1",), ("b2",)])
        self.assertTrue(left)

    async def test_only_the_browser_in_use_leaves_nothing_to_sign_out_here(self) -> None:
        left = await signed_in_rows.end_sessions(self.library, ["mine"], "mine")

        self.library.sign_out_session.assert_not_called()
        self.assertTrue(left)


class ThePageChangesWhereItStands(unittest.IsolatedAsyncioTestCase):
    """A change on the page reads again and draws the sections inside the same region."""

    def setUp(self) -> None:
        self.library = mock.Mock()
        self.library.sessions.return_value = []
        self.library.tokens.return_value = []
        self.library.encrypted.return_value = {}
        self.library.authenticator.return_value = {}
        self.enterContext(mock.patch.object(
            security.offload, "io", new=mock.AsyncMock(side_effect=lambda fn, *a: fn(*a))))
        self.enterContext(mock.patch.object(security.paths, "get_ini_config"))
        self.enterContext(mock.patch.object(security.serving_state, "network"))
        self.parts: dict[str, mock.Mock] = {}
        for name, module in (("authenticator", authenticator_panel), ("token", token_panel),
                             ("encrypted", security.encrypted_panel)):
            self.parts[name] = self.enterContext(mock.patch.object(
                module, "page_parts", return_value=[]))
        self.tasks: list = []
        self.enterContext(mock.patch.object(
            security.background_tasks, "create",
            side_effect=lambda coro, **_: self.tasks.append(coro)))

    async def _drawn(self) -> tuple[panel.Region, mock.Mock, ui.element]:
        redraw = mock.Mock()
        with _PAGE:
            region = await security.page(self.library, redraw, {})
            assert isinstance(region, panel.Region)
            box = ui.column()
            with box:
                region()
        return region, redraw, box

    async def test_it_hands_back_a_region(self) -> None:
        region, _redraw, _box = await self._drawn()

        self.assertIsInstance(region, panel.Region)

    async def test_a_change_reads_again_and_refills_without_redrawing_the_page(self) -> None:
        region, redraw, _box = await self._drawn()
        refill = self.parts["token"].call_args.args[1]
        before = region._box
        self.library.tokens.return_value = [{"id": "a"}]

        with _PAGE:
            refill()
        await asyncio.gather(*self.tasks)

        self.assertIs(before, region._box)
        redraw.assert_not_called()
        self.assertEqual(2, self.library.tokens.call_count)
        self.assertEqual([{"id": "a"}], self.parts["token"].call_args.args[2])

    async def test_every_section_gets_the_same_refill(self) -> None:
        await self._drawn()

        given = {part.call_args.args[1] for part in self.parts.values()}

        self.assertEqual(1, len(given))

    async def test_a_read_that_fails_says_so_and_draws_what_it_holds(self) -> None:
        region, redraw, _box = await self._drawn()
        refill = self.parts["token"].call_args.args[1]
        held = self.parts["token"].call_args.args[2]
        self.library.tokens.side_effect = OSError("gone")

        with _PAGE, mock.patch.object(security.ui, "notify") as notice:
            refill()
            await asyncio.gather(*self.tasks)

        notice.assert_called_once()
        self.assertEqual(2, self.parts["token"].call_count)
        self.assertEqual(held, self.parts["token"].call_args.args[2])
        redraw.assert_not_called()


class WhereItLives(unittest.TestCase):
    def test_it_is_a_page_of_the_vpinfe_group(self) -> None:
        group = dict(settings.DEVICE_INDEX)["manager.settings.group_vpinfe"]

        self.assertIn(settings.SECURITY_PAGE, [page[0] for page in group])
        self.assertIn(settings.SECURITY_PAGE, settings.PAGES)


if __name__ == "__main__":
    unittest.main()

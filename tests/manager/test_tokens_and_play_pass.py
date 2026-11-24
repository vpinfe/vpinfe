"""What the Tokens section lists, what a paired device is sent, and what a Remote pass sees."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from nicegui import core, ui
from nicegui.client import Client
from nicegui.slot import Slot

from common.config import paths
from common.security import access_keys, api_tokens, sign_in
from httpapi import auth
from manager.kit import acting, panel
from manager.security import token_panel
from tests.support.clicks import press, said

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _row(token_id: str, access: str, *, can_power: bool = False,
         last_used: float = 0.0) -> dict:
    return {"id": token_id, "name": "name " + token_id, "access": access,
            "can_power": can_power, "created": 1.0, "last_used": last_used}


class TheTokensSectionTests(unittest.TestCase):
    def entries(self, rows):
        return token_panel.entries(rows, lambda: None, lambda _id: None, lambda _id, _on: None)

    def test_no_tokens_say_so_and_still_offer_to_make_one(self) -> None:
        entries = self.entries([])

        self.assertEqual(entries[0][0], panel.HEADING)
        self.assertEqual(entries[-2][0], panel.FULL)

    def test_each_token_is_a_row_named_for_the_token(self) -> None:
        entries = self.entries([_row("a", api_tokens.FULL), _row("b", api_tokens.PLAY)])

        names = [one[0] for one in entries if isinstance(one[0], str) and one[0].startswith("name")]
        self.assertEqual(names, ["name a", "name b"])

    def test_a_token_never_used_says_so(self) -> None:
        self.assertEqual(token_panel.last_used(_row("a", api_tokens.FULL)),
                         token_panel.t("manager.tokens.never_used"))

    def test_the_access_is_named_in_the_users_words(self) -> None:
        self.assertNotEqual(token_panel.access_label(api_tokens.FULL),
                            token_panel.access_label(api_tokens.PLAY))


def _the(kind: type, scope: ui.element) -> list[Any]:
    return [one for one in scope.descendants() if isinstance(one, kind)]


class TheNewTokenDialog(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(ui, "run_javascript")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _asked(self, act: Any) -> Any:
        """The dialog opened, `act` done to it, and what it answered."""
        async def run() -> Any:
            async def ask() -> Any:
                with _PAGE:
                    return await token_panel._ask_new()

            was = set(_the(ui.dialog, _PAGE.client.layout))
            asking = asyncio.create_task(ask())
            await asyncio.sleep(0)
            box = next(one for one in _the(ui.dialog, _PAGE.client.layout)
                       if one not in was and one.value)
            act(box)
            return await asking

        return asyncio.run(run())

    def _create(self, box: ui.dialog) -> None:
        _the(ui.input, box)[0].value = "Scoreboard"
        said(next(one for one in _the(ui.button, box)
                  if one.text == token_panel.t("manager.tokens.create")), "click")

    def test_it_starts_on_play_with_the_power_switch_off_and_unavailable(self) -> None:
        seen: dict[str, Any] = {}

        def act(box: ui.dialog) -> None:
            power = _the(ui.switch, box)[0]
            seen.update(access=_the(ui.toggle, box)[0].value, power=(power.value, power.enabled),
                        says_play=any(one.text == token_panel.t("manager.tokens.play_does")
                                      for one in _the(ui.label, box)))
            self._create(box)

        answer = self._asked(act)

        self.assertEqual(seen, {"access": api_tokens.PLAY, "power": (False, False),
                                "says_play": True})
        self.assertEqual(answer, ("Scoreboard", api_tokens.PLAY, False))

    def test_choosing_full_control_makes_the_power_switch_available(self) -> None:
        def act(box: ui.dialog) -> None:
            _the(ui.toggle, box)[0].value = api_tokens.FULL
            self.assertTrue(_the(ui.switch, box)[0].enabled)
            _the(ui.switch, box)[0].value = True
            self._create(box)

        self.assertEqual(self._asked(act), ("Scoreboard", api_tokens.FULL, True))

    def test_going_back_to_play_switches_power_off_again(self) -> None:
        def act(box: ui.dialog) -> None:
            _the(ui.toggle, box)[0].value = api_tokens.FULL
            _the(ui.switch, box)[0].value = True
            _the(ui.toggle, box)[0].value = api_tokens.PLAY
            self._create(box)

        self.assertEqual(self._asked(act), ("Scoreboard", api_tokens.PLAY, False))


class RevokingAToken(unittest.TestCase):
    def _revoke(self, *, said_yes: bool) -> tuple[mock.AsyncMock, mock.AsyncMock, mock.Mock]:
        self.library, redraw = mock.Mock(), mock.Mock()
        rows = [_row("a", api_tokens.FULL), _row("b", api_tokens.PLAY)]
        with mock.patch.object(token_panel, "entries") as entries:
            token_panel.page_parts(self.library, redraw, rows)
        revoke = entries.call_args.args[2]
        ask = mock.AsyncMock(return_value=said_yes)
        taken = mock.AsyncMock()
        with mock.patch.object(token_panel.confirm, "ask", ask), \
                mock.patch.object(token_panel.run, "io_bound", taken):
            asyncio.run(press(revoke, "b"))
        return ask, taken, redraw

    def test_it_names_the_token_and_what_revoking_does_before_stopping_it(self) -> None:
        ask, taken, redraw = self._revoke(said_yes=True)

        self.assertEqual(ask.call_args.args[0], "Revoke “name b”?")
        self.assertIn("cannot be restored", ask.call_args.kwargs["detail"])
        taken.assert_awaited_once_with(self.library.revoke_token, "b")
        redraw.assert_called_once()

    def test_cancelling_leaves_the_token_working(self) -> None:
        ask, taken, redraw = self._revoke(said_yes=False)

        ask.assert_awaited_once()
        taken.assert_not_awaited()
        redraw.assert_not_called()


class ThePairingTokenTests(unittest.TestCase):
    def test_a_device_never_paired_is_sent_nothing(self) -> None:
        self.assertIsNone(access_keys.pairing_header("nothing-paired"))
        self.assertIsNone(access_keys.pairing_header(""))

    def test_a_paired_device_is_sent_its_token_and_forgetting_stops_it(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        with mock.patch.object(paths, "SECRETS_PATH", Path(holder.name) / "secrets.json"):
            access_keys.keep_pairing_token("dev", "vpf_x")
            header = access_keys.pairing_header("dev")
            access_keys.forget_pairing_token("dev")
            forgotten = access_keys.pairing_header("dev")

        self.assertEqual(header, {"Authorization": "bearer vpf_x"})
        self.assertIsNone(forgotten)


class APlayPassTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.addCleanup(sign_in.use, sign_in.instance())
        self.signer = sign_in.SignIn(Path(holder.name) / "sessions.json")
        sign_in.use(self.signer)

    def page_for(self, peer, cookies):
        request = SimpleNamespace(client=SimpleNamespace(host=peer), headers={},
                                  cookies=cookies, method="GET")
        client = SimpleNamespace(request=request)
        return mock.patch.object(acting, "_page_client", return_value=client)

    def test_a_remote_pass_on_another_device_is_play_only(self) -> None:
        token, _ = self.signer.open(kind=sign_in.REMOTE, address="192.168.1.21")

        with self.page_for("192.168.1.21", {auth.COOKIE: token}):
            self.assertTrue(acting.play_only())

    def test_an_owner_is_not(self) -> None:
        token, _ = self.signer.open(kind=sign_in.OWNER, address="192.168.1.20")

        with self.page_for("192.168.1.20", {auth.COOKIE: token}):
            self.assertFalse(acting.play_only())

    def test_this_device_is_not(self) -> None:
        with self.page_for("127.0.0.1", {}):
            self.assertFalse(acting.play_only())

    def test_a_page_with_no_request_is_not(self) -> None:
        with mock.patch.object(acting, "_page_client",
                               return_value=SimpleNamespace(request=None)):
            self.assertFalse(acting.play_only())

    def test_no_page_is_not(self) -> None:
        with mock.patch.object(acting, "_page_client", return_value=None):
            self.assertFalse(acting.play_only())


class BrowserIsHereTests(unittest.TestCase):
    def page_for(self, peer, cookies):
        request = SimpleNamespace(client=SimpleNamespace(host=peer), headers={},
                                  cookies=cookies, method="GET")
        return mock.patch.object(acting, "_page_client",
                                 return_value=SimpleNamespace(request=request))

    def test_a_page_on_this_device_is_here(self) -> None:
        with self.page_for("127.0.0.1", {}):
            self.assertTrue(acting.browser_is_here())

    def test_a_page_on_another_device_is_not(self) -> None:
        with self.page_for("192.168.1.21", {}):
            self.assertFalse(acting.browser_is_here())

    def test_a_forwarded_page_is_not_even_from_this_device(self) -> None:
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"),
                                  headers={"x-forwarded-for": "192.168.1.21"},
                                  cookies={}, method="GET")
        with mock.patch.object(acting, "_page_client",
                               return_value=SimpleNamespace(request=request)):
            self.assertFalse(acting.browser_is_here())

    def test_no_page_is_not(self) -> None:
        with mock.patch.object(acting, "_page_client", return_value=None):
            self.assertFalse(acting.browser_is_here())


class WorkWithNoPageTests(unittest.TestCase):
    def test_inside_a_page_it_is_that_pages_client(self) -> None:
        async def inside() -> Any:
            with _PAGE:
                return acting._page_client()

        self.assertIs(asyncio.run(inside()), _PAGE.client)

    def test_it_carries_nothing_and_makes_no_client_without_a_request(self) -> None:
        """NiceGUI's start-up, before any page exists, hands work to the pool."""
        async def hand_over() -> Any:
            return acting._page_acting()

        with mock.patch.object(Client, "instances", {}), \
                mock.patch.object(core, "script_mode", False), \
                mock.patch.object(core, "script_client", None), \
                mock.patch.dict(Slot.stacks, clear=True):
            carried = asyncio.run(hand_over())
            made = dict(Client.instances)

        self.assertIsNone(carried)
        self.assertEqual(made, {})


if __name__ == "__main__":
    unittest.main()

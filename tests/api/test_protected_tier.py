"""The three tiers a caller can hold, and what each one reaches.

Play launches and rates. Library changes what VPinFE knows. Protected decides what runs, hands
out access, destroys the way back or takes the machine down, and only this device or an
Owner holding a live Confirm reaches it; a token never does.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

import httpapi
from common.runtime import lifecycle
from common.security import api_tokens, sign_in
from httpapi import auth, protected_settings, scopes


def _request(peer, cookies=None, method="GET", **headers):
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(client=client, headers=headers, cookies=cookies or {}, method=method)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TierCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        self.clock = Clock()
        self.signer = sign_in.SignIn(root / "sessions.json", self.clock)
        self.addCleanup(self.signer.wait_for_saves)
        self.tokens = api_tokens.Tokens(root / "tokens.json", self.clock)
        self.addCleanup(sign_in.use, sign_in.instance())
        self.addCleanup(api_tokens.use, api_tokens.instance())
        sign_in.use(self.signer)
        api_tokens.use(self.tokens)
        self.policy = auth.SignInPolicy()

    def owner_cookie(self) -> tuple[dict[str, str], sign_in.Session]:
        token, session = self.signer.open(kind=sign_in.OWNER, address="192.168.1.20")
        return {auth.COOKIE: token}, session

    def remote_cookie(self) -> dict[str, str]:
        token, _ = self.signer.open(kind=sign_in.REMOTE, address="192.168.1.21")
        return {auth.COOKIE: token}

    def identify(self, peer="192.168.1.20", cookies=None, **headers) -> auth.Identity:
        return self.policy.identify(_request(peer, cookies, **headers))


class WhatEachCallerHoldsTests(TierCase):
    def test_this_device_holds_every_scope(self) -> None:
        identity = self.identify("127.0.0.1")

        self.assertTrue(identity.can(scopes.PROTECTED))
        self.assertTrue(identity.can(scopes.SYSTEM_ADMIN))

    def test_an_owner_holds_the_library_but_not_the_protected_tier_until_confirmed(self) -> None:
        cookies, session = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)

        before = self.identify(cookies=cookies)
        self.signer.confirm_grant(session.id)
        after = self.identify(cookies=cookies)

        self.assertTrue(before.can(scopes.CONFIG_WRITE))
        self.assertFalse(before.can(scopes.PROTECTED))
        self.assertTrue(after.can(scopes.PROTECTED))
        self.assertTrue(after.can(scopes.SYSTEM_ADMIN))

    def test_a_remote_pass_holds_play_and_nothing_more(self) -> None:
        identity = self.identify(cookies=self.remote_cookie())

        self.assertEqual(identity.scopes, scopes.PLAY)

    def test_a_full_control_token_holds_the_library_and_never_the_protected_tier(self) -> None:
        secret, _ = self.tokens.make("home", api_tokens.FULL)

        identity = self.identify(authorization="Bearer " + secret)

        self.assertTrue(identity.can(scopes.CONFIG_WRITE))
        self.assertFalse(identity.can(scopes.PROTECTED))
        self.assertFalse(identity.can(scopes.SYSTEM_ADMIN))

    def test_the_power_switch_gives_a_full_control_token_the_shutdown_scope_only(self) -> None:
        secret, _ = self.tokens.make("home", api_tokens.FULL, can_power=True)

        identity = self.identify(authorization="Bearer " + secret)

        self.assertTrue(identity.can(scopes.SYSTEM_ADMIN))
        self.assertFalse(identity.can(scopes.PROTECTED))

    def test_a_play_token_holds_play_whatever_the_power_switch_says(self) -> None:
        secret, token = self.tokens.make("kiosk", api_tokens.PLAY, can_power=True)

        identity = self.identify(authorization="Bearer " + secret)

        self.assertFalse(token.can_power)
        self.assertEqual(identity.scopes, scopes.PLAY)

    def test_a_token_that_is_not_made_here_is_a_stranger(self) -> None:
        identity = self.identify(authorization="Bearer vpf_nothing")

        self.assertEqual(identity.scopes, frozenset({scopes.INSTANCE_READ}))

    def test_a_token_cannot_be_used_by_a_cookie_alone(self) -> None:
        secret, _ = self.tokens.make("home", api_tokens.FULL)

        identity = self.identify(cookies={auth.COOKIE: secret})

        self.assertEqual(identity.scopes, frozenset({scopes.INSTANCE_READ}))

    def test_opening_the_frontend_is_an_owner_scope_a_token_or_a_pass_never_holds(self) -> None:
        cookies, _ = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)
        secret, _ = self.tokens.make("home", api_tokens.FULL)

        self.assertTrue(self.identify(cookies=cookies).can(scopes.FRONTEND_OPEN))
        self.assertFalse(self.identify(cookies=self.remote_cookie()).can(scopes.FRONTEND_OPEN))
        self.assertFalse(self.identify(authorization="Bearer " + secret).can(scopes.FRONTEND_OPEN))

    def test_guests_are_joined_at_play(self) -> None:
        self.assertLessEqual({scopes.PLAY_PLAYERS, scopes.PLAY_RATE}, scopes.PLAY)
        self.assertNotIn(scopes.PLAYERS_WRITE, scopes.PLAY)


class TheConfirmTests(TierCase):
    def test_a_refusal_for_an_owner_asks_for_a_confirm(self) -> None:
        cookies, _ = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)
        identity = self.identify(cookies=cookies)

        with self.assertRaises(auth.ForbiddenError) as caught:
            auth.check(identity, scopes.PROTECTED)

        self.assertEqual(caught.exception.details["reason"], auth.CONFIRM_NEEDED)

    def test_a_refusal_for_a_token_says_it_never_can(self) -> None:
        secret, _ = self.tokens.make("home", api_tokens.FULL)
        identity = self.identify(authorization="Bearer " + secret)

        with self.assertRaises(auth.ForbiddenError) as caught:
            auth.check(identity, scopes.PROTECTED)

        self.assertEqual(caught.exception.details["reason"], auth.PROTECTED)

    def test_the_confirm_lasts_while_the_person_keeps_using_it(self) -> None:
        cookies, session = self.owner_cookie()
        identity = self.identify(cookies=cookies)

        self.clock.advance(sign_in.CONFIRM_SECONDS - 60)
        auth.check(identity, scopes.PROTECTED)
        self.clock.advance(sign_in.CONFIRM_SECONDS - 60)

        self.assertTrue(self.signer.is_confirmed(session.id))

    def test_the_confirm_ends_when_it_is_left_alone(self) -> None:
        _, session = self.owner_cookie()

        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)

        self.assertFalse(self.signer.is_confirmed(session.id))

    def test_using_a_library_scope_does_not_extend_the_confirm(self) -> None:
        cookies, session = self.owner_cookie()
        identity = self.identify(cookies=cookies)

        self.clock.advance(sign_in.CONFIRM_SECONDS - 60)
        auth.check(identity, scopes.CONFIG_WRITE)
        self.clock.advance(120)

        self.assertFalse(self.signer.is_confirmed(session.id))

    def test_the_right_digits_confirm_and_wrong_ones_spend_a_try(self) -> None:
        _, session = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)
        asked = self.signer.confirm_ask(session.id, "192.168.1.20")

        with self.assertRaises(sign_in.SignInError) as caught:
            self.signer.confirm_verify(session.id, "000000" if asked.code != "000000" else "111111")
        self.signer.confirm_verify(session.id, asked.code)

        self.assertEqual(caught.exception.reason, sign_in.WRONG)
        self.assertTrue(self.signer.is_confirmed(session.id))

    def test_a_remote_pass_cannot_ask_for_a_confirm(self) -> None:
        _, session = self.signer.open(kind=sign_in.REMOTE, address="192.168.1.21")

        with self.assertRaises(sign_in.SignInError):
            self.signer.confirm_ask(session.id, "192.168.1.21")


class TheActingHeaderTests(TierCase):
    def test_a_manager_call_is_held_to_the_browser_it_acts_for(self) -> None:
        _, session = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)

        identity = self.identify("127.0.0.1", **{auth.ACTING_HEADER: auth.sign_acting(session.id)})

        self.assertTrue(identity.can(scopes.CONFIG_WRITE))
        self.assertFalse(identity.can(scopes.PROTECTED))

    def test_a_manager_call_for_nobody_is_a_stranger(self) -> None:
        identity = self.identify("127.0.0.1", **{auth.ACTING_HEADER: auth.sign_acting(auth.NOBODY)})

        self.assertEqual(identity.scopes, frozenset({scopes.INSTANCE_READ}))

    def test_a_call_that_says_nothing_stays_this_devices_own(self) -> None:
        self.assertTrue(self.identify("127.0.0.1").can(scopes.PROTECTED))

    def test_a_header_from_another_device_counts_for_nothing(self) -> None:
        _, session = self.owner_cookie()
        header = {auth.ACTING_HEADER: auth.sign_acting(session.id)}

        self.assertIsNone(auth.acting_for(_request("192.168.1.20", **header)))

    def test_a_stale_header_counts_for_nothing(self) -> None:
        stale = {auth.ACTING_HEADER: self.signed(-5)}

        self.assertIsNone(auth.acting_for(_request("127.0.0.1", **stale)))

    def test_a_header_with_a_forged_signature_counts_for_nothing(self) -> None:
        forged = f"abc.{int(time.time()) + 30}.0.{'0' * 64}"

        self.assertIsNone(auth.acting_for(_request("127.0.0.1", **{auth.ACTING_HEADER: forged})))

    def signed(self, offset: int) -> str:
        body = f"abc.{int(time.time()) + offset}.0"
        return f"{body}.{auth._acting_mac(body)}"

    def test_a_header_that_does_not_hold_leaves_the_caller_a_stranger(self) -> None:
        forged = f"abc.{int(time.time()) + 30}.0.{'0' * 64}"
        for name, value in (("garbage", "not-a-header"), ("forged", forged),
                            ("expired", self.signed(-5))):
            with self.subTest(header=name):
                identity = self.identify("127.0.0.1", **{auth.ACTING_HEADER: value})

                self.assertEqual(identity.scopes, frozenset({scopes.INSTANCE_READ}))
                self.assertFalse(identity.can(scopes.PROTECTED))

    def test_a_header_naming_a_session_that_has_ended_is_a_stranger(self) -> None:
        identity = self.identify("127.0.0.1", **{auth.ACTING_HEADER: auth.sign_acting("gone")})

        self.assertFalse(identity.can(scopes.PROTECTED))


class AProtectedRouteOverTheWireTests(TierCase):
    """The same answers through the real app, from a peer that is not this device."""

    def call(self, peer, *, cookies=None, **headers):
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False,
                            client=(peer, 50000), base_url="http://" + peer)
        for name, value in (cookies or {}).items():
            client.cookies.set(name, value)
        return client.post("/auth/tokens", json={"name": "made", "access": "play"},
                           headers=headers)

    def reason(self, response) -> str:
        return response.json()["error"]["details"]["reason"]

    def test_a_stranger_is_refused(self) -> None:
        response = self.call("192.168.1.50")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.tokens.tokens(), [])

    def test_a_token_is_refused_whatever_its_access(self) -> None:
        for access in (api_tokens.FULL, api_tokens.PLAY):
            secret, _ = self.tokens.make(access, access, can_power=True)
            with self.subTest(access=access):
                response = self.call("192.168.1.50", authorization="Bearer " + secret)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.reason(response), auth.PROTECTED)
        self.assertEqual(len(self.tokens.tokens()), 2)

    def test_a_remote_pass_is_refused(self) -> None:
        response = self.call("192.168.1.50", cookies=self.remote_cookie())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reason(response), auth.PROTECTED)

    def test_an_owner_is_asked_to_confirm_and_then_let_through(self) -> None:
        cookies, session = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)

        refused = self.call("192.168.1.50", cookies=cookies)
        self.signer.confirm_grant(session.id)
        allowed = self.call("192.168.1.50", cookies=cookies)

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.reason(refused), auth.CONFIRM_NEEDED)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(len(self.tokens.tokens()), 1)

    def test_this_device_is_let_through(self) -> None:
        self.assertEqual(self.call("127.0.0.1").status_code, 200)

    def press(self, action, peer="192.168.1.50", *, cookies=None, **headers):
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False,
                            client=(peer, 50000), base_url="http://" + peer)
        for name, value in (cookies or {}).items():
            client.cookies.set(name, value)
        return client.post("/input/actions", json={"action": action, "phase": "tap"},
                           headers=headers)

    def test_quitting_by_the_input_action_needs_the_power_scope_however_it_is_spelled(self) -> None:
        secret, _ = self.tokens.make("play", api_tokens.PLAY)
        cookies, _ = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)
        for spelled in ("exit", " exit", "exit\n"):
            with self.subTest(spelled=spelled, caller="a Play token"):
                response = self.press(spelled, authorization="Bearer " + secret)

                self.assertEqual(response.status_code, 403)
            with self.subTest(spelled=spelled, caller="an Owner not confirmed"):
                response = self.press(spelled, cookies=cookies)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.reason(response), auth.CONFIRM_NEEDED)


class OpeningTheFrontendOverTheWireTests(TierCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(lifecycle.reset_for_tests)
        self.opened: list = []
        lifecycle.register_performer(lifecycle.FRONTEND, lifecycle.START, self.opened.append)
        lifecycle.register_performer(lifecycle.FRONTEND, lifecycle.RESTART, self.opened.append)

    def ask(self, scope, action, *, cookies=None, **headers):
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False,
                            client=("192.168.1.50", 50000), base_url="http://192.168.1.50")
        for name, value in (cookies or {}).items():
            client.cookies.set(name, value)
        return client.post("/actions", json={"scope": scope, "action": action},
                           headers=headers)

    def test_an_owner_opens_it_without_a_confirm(self) -> None:
        cookies, _ = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)

        response = self.ask(lifecycle.FRONTEND, lifecycle.START, cookies=cookies)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.opened), 1)

    def test_an_owner_without_a_confirm_still_cannot_restart_the_frontend(self) -> None:
        cookies, _ = self.owner_cookie()
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)

        response = self.ask(lifecycle.FRONTEND, lifecycle.RESTART, cookies=cookies)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["details"]["reason"], auth.CONFIRM_NEEDED)
        self.assertEqual(self.opened, [])

    def test_a_remote_pass_and_a_full_control_token_cannot_open_it(self) -> None:
        secret, _ = self.tokens.make("home", api_tokens.FULL)

        for caller, kwargs in (("a Remote pass", {"cookies": self.remote_cookie()}),
                               ("a token", {"authorization": "Bearer " + secret})):
            with self.subTest(caller=caller):
                response = self.ask(lifecycle.FRONTEND, lifecycle.START, **kwargs)

                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.opened, [])


class TheTokenStoreTests(TierCase):
    def test_a_secret_is_kept_only_as_a_hash(self) -> None:
        secret, _ = self.tokens.make("home", api_tokens.FULL)

        stored = (Path(self.tokens._file())).read_text(encoding="utf-8")

        self.assertNotIn(secret, stored)

    def test_the_tokens_survive_a_restart(self) -> None:
        secret, token = self.tokens.make("home", api_tokens.PLAY)

        again = api_tokens.Tokens(self.tokens._file(), self.clock)

        found = again.lookup(secret)
        assert found is not None
        self.assertEqual(found.id, token.id)

    def test_a_revoked_token_calls_nothing(self) -> None:
        secret, token = self.tokens.make("home", api_tokens.FULL)

        self.assertTrue(self.tokens.revoke(token.id))

        self.assertIsNone(self.tokens.lookup(secret))
        self.assertFalse(self.tokens.revoke(token.id))

    def test_using_a_token_records_when(self) -> None:
        secret, token = self.tokens.make("home", api_tokens.FULL)
        self.clock.advance(120)

        self.tokens.lookup(secret)

        self.assertEqual(self.tokens.tokens()[0].last_used, self.clock.now)

    def test_the_power_switch_only_exists_on_full_control(self) -> None:
        _, play = self.tokens.make("kiosk", api_tokens.PLAY)

        changed = self.tokens.set_power(play.id, True)

        assert changed is not None
        self.assertFalse(changed.can_power)
        self.assertIsNone(self.tokens.set_power("nothing", True))

    def test_an_unknown_access_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.tokens.make("home", "owner")

    def test_a_secret_without_the_prefix_is_not_looked_up(self) -> None:
        self.assertIsNone(self.tokens.lookup("not-a-token"))


class WhatDecidesWhatRunsTests(unittest.TestCase):
    def test_a_command_is_protected_and_a_plain_setting_is_not(self) -> None:
        self.assertTrue(protected_settings.setting_is_protected("commands", "anything"))
        self.assertTrue(protected_settings.setting_is_protected("table_commands", "x"))
        self.assertTrue(protected_settings.setting_is_protected("backups", "folder"))
        self.assertFalse(protected_settings.setting_is_protected("display", "theme"))

    def test_sending_a_section_back_unedited_is_not_a_change(self) -> None:
        held = {"commands": {"start": "run.sh"}}

        patch = {"commands": {"start": "run.sh"}}

        self.assertFalse(protected_settings.settings_change(patch, held))

    def test_editing_a_command_is_a_change(self) -> None:
        held = {"commands": {"start": "run.sh"}}

        self.assertTrue(protected_settings.settings_change({"commands": {"start": "x.sh"}}, held))

    def test_clearing_a_command_is_a_change_too(self) -> None:
        held = {"commands": {"start": "run.sh"}}

        self.assertTrue(protected_settings.settings_change({"commands": {"start": ""}}, held))

    def test_a_launcher_field_that_runs_something_is_protected_only_when_set(self) -> None:
        self.assertTrue(protected_settings.launcher_body_protected({"values": {"bin_path": "/x"}}))
        self.assertFalse(protected_settings.launcher_body_protected({"values": {"bin_path": ""}}))
        self.assertFalse(protected_settings.launcher_body_protected({"values": {"label": "x"}}))
        self.assertFalse(protected_settings.launcher_body_protected({"values": None}))

    def test_a_launcher_item_is_a_change_only_when_a_program_or_command_differs(self) -> None:
        held = [{"id": "a", "settings": {"bin_path": "/x"}}]

        same = protected_settings.launchers_change(
            [{"id": "a", "settings": {"bin_path": "/x", "label": "new"}}], held)
        different = protected_settings.launchers_change(
            [{"id": "a", "settings": {"bin_path": "/y"}}], held)
        added = protected_settings.launchers_change(
            [{"id": "b", "settings": {"args": "--go"}}], held)

        self.assertFalse(same)
        self.assertTrue(different)
        self.assertTrue(added)


if __name__ == "__main__":
    unittest.main()

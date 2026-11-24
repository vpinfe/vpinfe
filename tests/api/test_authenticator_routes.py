"""The authenticator over HTTP: who may set it up, and what its six digits open.

The machine is the test client's default peer; another device is a client whose peer is a
LAN address.
"""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import httpapi
from common.config import paths
from common.security import authenticator, sign_in
from httpapi import auth

OTHER = "192.168.1.31"
START = 2_000_010.0


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


class AuthenticatorOverHttp(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        for name, value in (("CONFIG_DIR", root / "config"),
                            ("SECRETS_PATH", root / "config" / "secrets.json")):
            patched = mock.patch.object(paths, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.clock = Clock()
        self.signer = sign_in.SignIn(root / "sessions.json", self.clock)
        self.held = authenticator.Authenticator(self.clock)
        self.addCleanup(sign_in.use, sign_in.instance())
        self.addCleanup(authenticator.use, authenticator.instance())
        sign_in.use(self.signer)
        authenticator.use(self.held)
        auth.set_policy(auth.SignInPolicy())
        self.addCleanup(auth.set_policy, auth.SignInPolicy())
        self.api = httpapi.create_api_app()
        self.machine = TestClient(self.api, raise_server_exceptions=False)

    def computer(self, address: str = OTHER) -> TestClient:
        return TestClient(self.api, raise_server_exceptions=False, client=(address, 50000))

    def code(self, secret: str) -> str:
        return authenticator._code(base64.b32decode(secret), int(self.clock.now // 30))

    def set_up(self) -> str:
        made = self.machine.post("/auth/authenticator/setup")
        self.assertEqual(made.status_code, 200, made.text)
        secret = made.json()["secret"]
        done = self.machine.put("/auth/authenticator", json={"code": self.code(secret)})
        self.assertEqual(done.status_code, 200, done.text)
        self.clock.now += authenticator.STEP_SECONDS
        return secret

    def signed_in(self) -> TestClient:
        """An Owner whose sign-in Confirm has run out."""
        client = self.computer()
        token, _ = self.signer.open(kind=sign_in.OWNER, address=OTHER)
        client.cookies.set(auth.COOKIE, token)
        self.clock.now += sign_in.CONFIRM_SECONDS + 1
        return client


class SettingUp(AuthenticatorOverHttp):
    def test_nothing_is_set_up_to_begin_with(self) -> None:
        self.assertEqual(self.machine.get("/auth/authenticator").json(),
                         {"enabled": False, "screen_codes": True})

    def test_this_device_sets_one_up_with_no_confirm(self) -> None:
        secret = self.set_up()

        self.assertTrue(secret)
        self.assertEqual(self.machine.get("/auth/authenticator").json(),
                         {"enabled": True, "screen_codes": True})

    def test_the_enrolment_names_the_key_and_the_address_for_a_qr(self) -> None:
        made = self.machine.post("/auth/authenticator/setup").json()

        self.assertIn(made["secret"], made["uri"])
        self.assertTrue(made["uri"].startswith("otpauth://totp/"))

    def test_a_wrong_first_code_sets_nothing_up(self) -> None:
        self.machine.post("/auth/authenticator/setup")

        refused = self.machine.put("/auth/authenticator", json={"code": "000000"})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"]["details"]["reason"], sign_in.WRONG)
        self.assertFalse(self.held.enabled())

    def test_a_signed_in_owner_needs_a_confirm_to_set_one_up_or_remove_it(self) -> None:
        owner = self.signed_in()

        for call in (lambda: owner.post("/auth/authenticator/setup"),
                     lambda: owner.put("/auth/authenticator", json={"code": "000000"}),
                     lambda: owner.delete("/auth/authenticator"),
                     lambda: owner.put("/auth/authenticator/screen_codes", json={"on": False})):
            refused = call()
            self.assertEqual(refused.status_code, 403)
            self.assertEqual(refused.json()["error"]["details"]["reason"], auth.CONFIRM_NEEDED)

    def test_a_device_not_signed_in_reads_nothing_about_it(self) -> None:
        self.assertEqual(self.computer().get("/auth/authenticator").status_code, 403)

    def test_removing_it_leaves_nothing_set_up(self) -> None:
        self.set_up()

        self.machine.delete("/auth/authenticator")

        self.assertEqual(self.machine.get("/auth/authenticator").json()["enabled"], False)


class SigningIn(AuthenticatorOverHttp):
    def test_six_digits_sign_in_from_any_address_and_open_the_rest(self) -> None:
        secret = self.set_up()
        client = self.computer("203.0.113.9")
        self.assertEqual(client.get("/play/state").status_code, 403)

        done = client.post("/auth/authenticator/session", json={"code": self.code(secret)})

        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(client.get("/play/state").status_code, 200)
        self.assertEqual(self.machine.get("/auth/sessions").json()["sessions"][0]["kind"],
                         sign_in.OWNER)

    def test_the_session_it_opens_is_marked_with_the_cookie_flags_of_any_other(self) -> None:
        secret = self.set_up()

        done = self.computer().post("/auth/authenticator/session",
                                    json={"code": self.code(secret)})

        header = done.headers["set-cookie"].lower()
        self.assertIn("httponly", header)
        self.assertIn("samesite=lax", header)

    def test_a_code_works_once(self) -> None:
        secret = self.set_up()
        code = self.code(secret)
        self.computer().post("/auth/authenticator/session", json={"code": code})

        again = self.computer().post("/auth/authenticator/session", json={"code": code})

        self.assertEqual(again.status_code, 403)
        self.assertEqual(again.json()["error"]["details"]["reason"], sign_in.SPENT)

    def test_a_wrong_code_signs_nobody_in(self) -> None:
        self.set_up()
        client = self.computer()

        refused = client.post("/auth/authenticator/session", json={"code": "000000"})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(client.get("/play/state").status_code, 403)

    def test_five_wrong_codes_make_it_wait(self) -> None:
        self.set_up()
        client = self.computer()
        for _ in range(sign_in.TRIES):
            client.post("/auth/authenticator/session", json={"code": "000000"})

        waiting = client.post("/auth/authenticator/session", json={"code": "000000"})

        self.assertEqual(waiting.status_code, 429)
        self.assertGreater(waiting.json()["error"]["details"]["wait"], 0)

    def test_the_wait_is_said_in_minutes_with_no_code_to_get(self) -> None:
        self.set_up()
        client = self.computer()
        for _ in range(sign_in.TRIES + 1):
            waiting = client.post("/auth/authenticator/session", json={"code": "000000"})

        said = waiting.json()["error"]["message"]
        self.assertIn("1 minute", said)
        self.assertNotIn("get a code", said)

    def test_one_devices_wrong_codes_leave_another_able_to_sign_in(self) -> None:
        secret = self.set_up()
        stranger = self.computer("192.168.1.66")
        for _ in range(sign_in.TRIES):
            stranger.post("/auth/authenticator/session", json={"code": "000000"})

        owner = self.computer().post("/auth/authenticator/session",
                                     json={"code": self.code(secret)})

        self.assertEqual(owner.status_code, 200)

    def test_an_ignored_address_cannot_try_the_app(self) -> None:
        secret = self.set_up()
        with mock.patch.object(self.signer, "is_ignored", return_value=True):
            refused = self.computer().post("/auth/authenticator/session",
                                           json={"code": self.code(secret)})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"]["details"]["reason"], sign_in.IGNORED)

    def test_with_none_set_up_it_says_so(self) -> None:
        refused = self.computer().post("/auth/authenticator/session", json={"code": "123456"})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"]["details"]["reason"], sign_in.NOT_SET)

    def test_a_page_of_another_site_cannot_type_a_code(self) -> None:
        secret = self.set_up()

        refused = self.computer().post("/auth/authenticator/session",
                                       json={"code": self.code(secret)},
                                       headers={"sec-fetch-site": "cross-site"})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.signer.sessions(), [])


class Confirming(AuthenticatorOverHttp):
    def test_six_digits_give_a_signed_in_owner_a_confirm(self) -> None:
        secret = self.set_up()
        owner = self.signed_in()

        done = owner.post("/auth/authenticator/confirm", json={"code": self.code(secret)})

        self.assertEqual(done.status_code, 200, done.text)
        self.assertTrue(done.json()["confirmed"])
        self.assertEqual(owner.delete("/auth/authenticator").status_code, 200)

    def test_wrong_confirms_are_counted_against_the_sign_in_not_its_address(self) -> None:
        secret = self.set_up()
        thief = self.signed_in()
        owner = self.computer()
        token, _ = self.signer.open(kind=sign_in.OWNER, address=OTHER)
        owner.cookies.set(auth.COOKIE, token)
        for _ in range(sign_in.TRIES):
            thief.post("/auth/authenticator/confirm", json={"code": "000000"})

        locked = thief.post("/auth/authenticator/confirm", json={"code": self.code(secret)})
        done = owner.post("/auth/authenticator/confirm", json={"code": self.code(secret)})

        self.assertEqual(locked.status_code, 429, locked.text)
        self.assertEqual(done.status_code, 200, done.text)

    def test_a_browser_not_signed_in_cannot_confirm(self) -> None:
        secret = self.set_up()

        refused = self.computer().post("/auth/authenticator/confirm",
                                       json={"code": self.code(secret)})

        self.assertEqual(refused.status_code, 403)

    def test_a_remote_pass_cannot_confirm(self) -> None:
        secret = self.set_up()
        token, _ = self.signer.open(kind=sign_in.REMOTE, address=OTHER)
        phone = self.computer()
        phone.cookies.set(auth.COOKIE, token)

        refused = phone.post("/auth/authenticator/confirm", json={"code": self.code(secret)})

        self.assertEqual(refused.status_code, 403)


class TheOnScreenCode(AuthenticatorOverHttp):
    def test_switching_it_off_stops_the_device_handing_out_a_sign_in_code(self) -> None:
        self.set_up()

        off = self.machine.put("/auth/authenticator/screen_codes", json={"on": False})
        asked = self.computer().post("/auth/code")

        self.assertEqual(off.json()["screen_codes"], False)
        self.assertEqual(asked.status_code, 403)
        self.assertEqual(asked.json()["error"]["details"]["reason"], sign_in.CODES_OFF)

    def test_switching_it_on_again_brings_it_back(self) -> None:
        self.set_up()
        self.machine.put("/auth/authenticator/screen_codes", json={"on": False})

        self.machine.put("/auth/authenticator/screen_codes", json={"on": True})

        self.assertEqual(self.computer().post("/auth/code").status_code, 200)

    def test_the_authenticator_still_signs_in_with_the_codes_off(self) -> None:
        secret = self.set_up()
        self.machine.put("/auth/authenticator/screen_codes", json={"on": False})

        done = self.computer().post("/auth/authenticator/session",
                                    json={"code": self.code(secret)})

        self.assertEqual(done.status_code, 200)

    def test_it_cannot_be_switched_off_with_no_authenticator(self) -> None:
        refused = self.machine.put("/auth/authenticator/screen_codes", json={"on": False})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.computer().post("/auth/code").status_code, 200)

    def test_removing_the_authenticator_brings_the_codes_back(self) -> None:
        self.set_up()
        self.machine.put("/auth/authenticator/screen_codes", json={"on": False})

        self.machine.delete("/auth/authenticator")

        self.assertEqual(self.computer().post("/auth/code").status_code, 200)


class TheLostPhone(AuthenticatorOverHttp):
    def test_a_browser_already_signed_in_keeps_working_after_a_removal(self) -> None:
        secret = self.set_up()
        client = self.computer()
        client.post("/auth/authenticator/session", json={"code": self.code(secret)})

        self.machine.delete("/auth/authenticator")

        self.assertEqual(client.get("/play/state").status_code, 200)

    def test_setting_up_again_turns_the_old_phone_off(self) -> None:
        old = self.set_up()
        new = self.set_up()

        old_try = self.computer().post("/auth/authenticator/session",
                                       json={"code": self.code(old)})
        new_try = self.computer().post("/auth/authenticator/session",
                                       json={"code": self.code(new)})

        self.assertEqual(old_try.status_code, 403)
        self.assertEqual(new_try.status_code, 200, new_try.text)


if __name__ == "__main__":
    unittest.main()

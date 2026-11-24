"""The authenticator: a standard time-based code, spent once, guessed at a few times only."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.security import authenticator, machine_secrets, sign_in
from tests.support.skips import needs_posix_permissions

# RFC 6238 appendix B: the SHA-1 key, and the six digits it gives at each time.
RFC_KEY = b"12345678901234567890"
RFC_CODES = {59: "287082", 1111111109: "081804", 1111111111: "050471",
             1234567890: "005924", 2000000000: "279037", 20000000000: "353130"}
SECRET = base64.b32encode(RFC_KEY).decode("ascii")
START = 1_700_000_010.0


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


class WithSecrets(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.config = Path(holder.name) / "config"
        self.file = self.config / "secrets.json"
        for name, value in (("CONFIG_DIR", self.config), ("SECRETS_PATH", self.file)):
            patched = mock.patch.object(paths, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.clock = Clock()
        self.held = authenticator.Authenticator(self.clock)

    def code_at(self, seconds: float, secret: str = SECRET) -> str:
        return authenticator._code(base64.b32decode(secret), int(seconds // 30))

    def enrol(self) -> None:
        machine_secrets.put(authenticator.KEY, {"secret": SECRET, "last": 0, "screen_codes": True})


class TheCode(WithSecrets):
    def test_it_is_the_standard_code_every_authenticator_app_shows(self) -> None:
        for seconds, expected in RFC_CODES.items():
            self.assertEqual(self.code_at(seconds), expected, seconds)

    def test_the_address_an_app_reads_carries_the_secret_and_the_settings(self) -> None:
        uri = authenticator.uri_for(SECRET, "Game Room")

        self.assertTrue(uri.startswith("otpauth://totp/VPinFE%3AGame%20Room?"))
        for part in (f"secret={SECRET}", "issuer=VPinFE", "algorithm=SHA1", "digits=6",
                     "period=30"):
            self.assertIn(part, uri)


class SettingUp(WithSecrets):
    def test_nothing_is_set_up_until_a_code_from_the_new_secret_is_accepted(self) -> None:
        made = self.held.begin("Game Room")

        self.assertFalse(self.held.enabled())
        self.assertEqual(len(base64.b32decode(made.secret)), authenticator.SECRET_BYTES)
        self.held.activate(self.code_at(self.clock.now, made.secret))
        self.assertTrue(self.held.enabled())

    def test_a_wrong_first_code_sets_nothing_up(self) -> None:
        self.held.begin("Game Room")

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.activate("000000")

        self.assertEqual(caught.exception.reason, sign_in.WRONG)
        self.assertFalse(self.held.enabled())

    def test_a_first_code_with_nothing_begun_is_refused(self) -> None:
        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.activate("123456")

        self.assertEqual(caught.exception.reason, sign_in.NO_CODE)

    def test_a_secret_nobody_confirmed_in_time_is_gone(self) -> None:
        made = self.held.begin("Game Room")
        self.clock.now += authenticator.PENDING_SECONDS + 1

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.activate(self.code_at(self.clock.now, made.secret))

        self.assertEqual(caught.exception.reason, sign_in.NO_CODE)
        self.assertFalse(self.held.enabled())

    def test_setting_up_again_keeps_the_old_secret_until_the_new_one_is_proved(self) -> None:
        self.enrol()
        self.held.begin("Game Room")

        self.assertEqual(machine_secrets.read()[authenticator.KEY]["secret"], SECRET)

    def test_setting_up_again_keeps_the_on_screen_codes_as_they_were_left(self) -> None:
        self.enrol()
        self.held.set_screen_codes(False)
        made = self.held.begin("Game Room")

        self.held.activate(self.code_at(self.clock.now, made.secret))

        self.assertFalse(self.held.screen_codes())

    @needs_posix_permissions
    def test_the_secret_is_kept_where_only_this_user_reads_it(self) -> None:
        made = self.held.begin("Game Room")
        self.held.activate(self.code_at(self.clock.now, made.secret))

        stored = json.loads(self.file.read_text())[authenticator.KEY]

        self.assertEqual(stored["secret"], made.secret)
        self.assertEqual(self.file.stat().st_mode & 0o077, 0)

    def test_what_was_set_up_survives_a_new_process(self) -> None:
        self.enrol()

        self.assertTrue(authenticator.Authenticator(self.clock).enabled())


class UsingIt(WithSecrets):
    def setUp(self) -> None:
        super().setUp()
        self.enrol()

    def test_the_code_for_now_is_accepted(self) -> None:
        self.held.verify(self.code_at(self.clock.now))

    def test_the_step_before_and_the_step_after_are_accepted_for_a_slow_clock(self) -> None:
        self.held.verify(self.code_at(self.clock.now - 30))
        self.clock.now += 120
        self.held.verify(self.code_at(self.clock.now + 30))

    def test_the_code_as_an_app_groups_it_is_accepted(self) -> None:
        code = self.code_at(self.clock.now)

        self.held.verify(f" {code[:3]} {code[3:]} ")

    def test_a_secret_that_cannot_be_read_is_treated_as_not_set_up(self) -> None:
        machine_secrets.put(authenticator.KEY, {"secret": "not base32 !", "last": 0})

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify("123456")

        self.assertEqual(caught.exception.reason, sign_in.NOT_SET)

    def test_two_steps_away_is_refused(self) -> None:
        for offset in (-60, 60):
            with self.assertRaises(sign_in.SignInError) as caught:
                self.held.verify(self.code_at(self.clock.now + offset))
            self.assertEqual(caught.exception.reason, sign_in.WRONG)

    def test_a_code_is_spent_once_used(self) -> None:
        code = self.code_at(self.clock.now)
        self.held.verify(code)

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify(code)

        self.assertEqual(caught.exception.reason, sign_in.SPENT)

    def test_an_earlier_step_cannot_be_used_after_a_later_one(self) -> None:
        self.held.verify(self.code_at(self.clock.now + 30))

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify(self.code_at(self.clock.now - 30))

        self.assertEqual(caught.exception.reason, sign_in.SPENT)

    def test_the_next_code_works_once_the_step_has_passed(self) -> None:
        self.held.verify(self.code_at(self.clock.now))
        self.clock.now += authenticator.STEP_SECONDS

        self.held.verify(self.code_at(self.clock.now))

    def test_a_spent_step_stays_spent_after_a_restart(self) -> None:
        code = self.code_at(self.clock.now)
        self.held.verify(code)

        with self.assertRaises(sign_in.SignInError) as caught:
            authenticator.Authenticator(self.clock).verify(code)

        self.assertEqual(caught.exception.reason, sign_in.SPENT)

    def test_nothing_is_accepted_with_no_authenticator(self) -> None:
        self.held.remove()

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify(self.code_at(self.clock.now))

        self.assertEqual(caught.exception.reason, sign_in.NOT_SET)


class GuessingIt(WithSecrets):
    def setUp(self) -> None:
        super().setUp()
        self.enrol()

    def miss(self, times: int) -> None:
        for _ in range(times):
            with self.assertRaises(sign_in.SignInError):
                self.held.verify("000000")

    def test_the_last_wrong_try_locks_it_and_even_the_right_code_waits(self) -> None:
        self.miss(sign_in.TRIES)

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify(self.code_at(self.clock.now))

        self.assertEqual(caught.exception.reason, sign_in.TOO_SOON)
        self.assertEqual(caught.exception.wait, authenticator.LOCK_SECONDS + 1)

    def test_it_opens_again_when_the_wait_is_over(self) -> None:
        self.miss(sign_in.TRIES)
        self.clock.now += authenticator.LOCK_SECONDS + 1

        self.held.verify(self.code_at(self.clock.now))

    def test_each_lock_without_a_right_code_between_waits_twice_as_long(self) -> None:
        waits = []
        for _ in range(3):
            self.miss(sign_in.TRIES)
            with self.assertRaises(sign_in.SignInError) as caught:
                self.held.verify("000000")
            waits.append(caught.exception.wait)
            self.clock.now += caught.exception.wait
        self.assertEqual(waits, [authenticator.LOCK_SECONDS + 1,
                                 2 * authenticator.LOCK_SECONDS + 1,
                                 4 * authenticator.LOCK_SECONDS + 1])

    def test_the_wait_stops_growing_at_an_hour(self) -> None:
        for _ in range(12):
            self.miss(sign_in.TRIES)
            self.clock.now += authenticator.LOCK_LIMIT_SECONDS + 1

        self.miss(sign_in.TRIES)
        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify("000000")

        self.assertLessEqual(caught.exception.wait, authenticator.LOCK_LIMIT_SECONDS + 1)

    def test_a_right_code_forgives_the_wrong_ones_before_it(self) -> None:
        self.miss(sign_in.TRIES - 1)
        self.held.verify(self.code_at(self.clock.now))
        self.clock.now += authenticator.STEP_SECONDS

        self.miss(sign_in.TRIES - 1)
        self.held.verify(self.code_at(self.clock.now))

    def test_a_strangers_wrong_codes_do_not_lock_the_owner_out(self) -> None:
        for _ in range(sign_in.TRIES):
            with self.assertRaises(sign_in.SignInError):
                self.held.verify("000000", "192.168.1.66")

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify(self.code_at(self.clock.now), "192.168.1.66")
        self.assertEqual(caught.exception.reason, sign_in.TOO_SOON)
        self.held.verify(self.code_at(self.clock.now), "192.168.1.20")

    def test_the_table_of_addresses_does_not_grow_without_end(self) -> None:
        for number in range(authenticator.GUARDS_KEPT * 3):
            with self.assertRaises(sign_in.SignInError):
                self.held.verify("000000", f"10.0.{number // 250}.{number % 250}")

        self.assertLessEqual(len(self.held._guards), authenticator.GUARDS_KEPT)

    def test_a_table_full_of_locked_addresses_still_does_not_grow(self) -> None:
        for number in range(authenticator.GUARDS_KEPT * 2):
            for _ in range(sign_in.TRIES):
                with self.assertRaises(sign_in.SignInError):
                    self.held.verify("000000", f"10.1.{number // 250}.{number % 250}")
            self.clock.now += authenticator.LOCK_LIMIT_SECONDS
            self.held._everyone.until = 0.0
            self.held._everyone.locks = 0

        self.assertLessEqual(len(self.held._guards), authenticator.GUARDS_KEPT)

    def test_wrong_codes_from_many_addresses_add_up_and_everyone_waits(self) -> None:
        for number in range(authenticator.ALL_WRONG):
            with self.assertRaises(sign_in.SignInError):
                self.held.verify("000000", f"192.168.1.{number}")

        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.verify(self.code_at(self.clock.now), "192.168.1.200")
        self.assertEqual(caught.exception.reason, sign_in.TOO_SOON)

        self.clock.now += authenticator.LOCK_SECONDS + 1
        self.held.verify(self.code_at(self.clock.now), "192.168.1.200")


class TheOnScreenCodes(WithSecrets):
    def test_they_show_until_an_authenticator_exists_whatever_was_asked(self) -> None:
        self.assertTrue(self.held.screen_codes())
        with self.assertRaises(sign_in.SignInError) as caught:
            self.held.set_screen_codes(False)
        self.assertEqual(caught.exception.reason, sign_in.NOT_SET)
        self.assertTrue(self.held.screen_codes())

    def test_they_can_be_switched_off_and_on_once_one_exists(self) -> None:
        self.enrol()

        self.held.set_screen_codes(False)
        self.assertFalse(self.held.screen_codes())
        self.held.set_screen_codes(True)
        self.assertTrue(self.held.screen_codes())

    def test_removing_the_authenticator_brings_them_back(self) -> None:
        self.enrol()
        self.held.set_screen_codes(False)

        self.held.remove()

        self.assertFalse(self.held.enabled())
        self.assertTrue(self.held.screen_codes())

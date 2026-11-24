"""Signing a browser in: the code's life, the tries, the spacing and the sessions it opens."""

from __future__ import annotations

import io
import json
import logging
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from common.runtime import log_setup
from common.security import sign_in
from common.security.sign_in import SignIn, SignInError


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SignInCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.file = Path(holder.name) / "sessions.json"
        self.clock = Clock()
        self.sign_in = SignIn(self.file, self.clock)
        self.addCleanup(self.sign_in.wait_for_saves)

    def shown(self, address: str = "192.168.1.20") -> sign_in.Request:
        request = self.sign_in.ask(address)
        revealed = self.sign_in.reveal(request.id)
        assert revealed is not None
        return revealed

    def refusal(self, call) -> SignInError:
        with self.assertRaises(SignInError) as caught:
            call()
        return caught.exception


class TheCodeTests(SignInCase):
    def test_an_ask_makes_no_digits_until_the_machine_shows_them(self) -> None:
        request = self.sign_in.ask("192.168.1.20")

        self.assertEqual(request.state, sign_in.WAITING)
        self.assertEqual(request.code, "")

    def test_showing_makes_six_digits(self) -> None:
        request = self.shown()

        self.assertRegex(request.code, r"^\d{6}$")
        self.assertEqual(request.state, sign_in.SHOWN)

    def test_the_right_code_signs_the_browser_in(self) -> None:
        request = self.shown()

        token, session = self.sign_in.verify(request.id, request.code, address="192.168.1.20")

        self.assertEqual(self.sign_in.lookup(token), session)
        self.assertEqual(session.kind, sign_in.OWNER)

    def test_nobody_sees_the_request_gone_before_the_session_is_there(self) -> None:
        request = self.shown()
        waiting_while_opening: list[bool] = []
        real_open = self.sign_in.open

        def open_noting(**kwargs):
            waiting_while_opening.append(self.sign_in.pending() is not None)
            return real_open(**kwargs)

        with mock.patch.object(self.sign_in, "open", open_noting):
            self.sign_in.verify(request.id, request.code, address="192.168.1.20")

        self.assertEqual([True], waiting_while_opening)
        self.assertIsNone(self.sign_in.pending())
        self.assertEqual(1, len(self.sign_in.sessions()))

    def test_a_code_cannot_be_used_before_it_is_shown(self) -> None:
        request = self.sign_in.ask("192.168.1.20")

        error = self.refusal(lambda: self.sign_in.verify(
            request.id, "000000", address="192.168.1.20"))

        self.assertEqual(error.reason, sign_in.NO_CODE)

    def test_a_code_is_good_only_for_the_address_that_asked(self) -> None:
        request = self.shown("192.168.1.20")

        error = self.refusal(lambda: self.sign_in.verify(
            request.id, request.code, address="192.168.1.99"))

        self.assertEqual(error.reason, sign_in.NO_CODE)

    def test_a_code_is_spent_by_use(self) -> None:
        request = self.shown()
        self.sign_in.verify(request.id, request.code, address="192.168.1.20")

        error = self.refusal(lambda: self.sign_in.verify(
            request.id, request.code, address="192.168.1.20"))

        self.assertEqual(error.reason, sign_in.NO_CODE)

    def test_the_fifth_wrong_try_spends_the_code(self) -> None:
        request = self.shown()
        wrong = "000000" if request.code != "000000" else "111111"

        reasons = [self.refusal(lambda: self.sign_in.verify(
            request.id, wrong, address="192.168.1.20")).reason for _ in range(5)]

        self.assertEqual(reasons, [sign_in.WRONG] * 4 + [sign_in.USED_UP])
        error = self.refusal(lambda: self.sign_in.verify(
            request.id, request.code, address="192.168.1.20"))
        self.assertEqual(error.reason, sign_in.NO_CODE)

    def test_a_code_that_is_not_ascii_is_a_wrong_code(self) -> None:
        request = self.shown()

        error = self.refusal(lambda: self.sign_in.verify(
            request.id, "12345\u0663", address="192.168.1.20"))

        self.assertEqual(error.reason, sign_in.WRONG)

    def test_a_code_lapses_after_ten_minutes(self) -> None:
        request = self.shown()
        self.clock.advance(sign_in.CODE_SECONDS + 1)

        error = self.refusal(lambda: self.sign_in.verify(
            request.id, request.code, address="192.168.1.20"))

        self.assertEqual(error.reason, sign_in.NO_CODE)
        self.assertIsNone(self.sign_in.pending())


class TheSpacingTests(SignInCase):
    def test_the_same_address_asking_again_sees_the_same_request(self) -> None:
        first = self.sign_in.ask("192.168.1.20")

        self.assertEqual(self.sign_in.ask("192.168.1.20").id, first.id)

    def test_another_browser_at_the_same_address_is_another_asker(self) -> None:
        first = self.sign_in.ask("192.168.1.20", "one")
        self.assertEqual(self.sign_in.ask("192.168.1.20", "one").id, first.id)

        error = self.refusal(lambda: self.sign_in.ask("192.168.1.20", "two"))

        self.assertEqual(error.reason, sign_in.TOO_SOON)

    def test_a_code_is_spent_only_by_the_browser_that_asked(self) -> None:
        request = self.sign_in.ask("192.168.1.20", "one")
        shown = self.sign_in.reveal(request.id)
        assert shown is not None

        error = self.refusal(lambda: self.sign_in.verify(
            request.id, shown.code, address="192.168.1.20", browser="two"))

        self.assertEqual(error.reason, sign_in.NO_CODE)
        token, _ = self.sign_in.verify(request.id, shown.code, address="192.168.1.20",
                                       browser="one")
        self.assertIsNotNone(self.sign_in.lookup(token))

    def test_another_address_must_wait_thirty_seconds(self) -> None:
        self.sign_in.ask("192.168.1.20")

        error = self.refusal(lambda: self.sign_in.ask("192.168.1.99"))

        self.assertEqual(error.reason, sign_in.TOO_SOON)
        self.assertGreater(error.wait, 0)
        self.clock.advance(sign_in.ASK_SPACING_SECONDS + 1)
        self.assertEqual(self.sign_in.ask("192.168.1.99").address, "192.168.1.99")

    def test_the_wait_for_another_address_counts_down(self) -> None:
        self.assertEqual(self.sign_in.next_ask_in(), 0)
        self.sign_in.ask("192.168.1.20")

        self.assertEqual(self.sign_in.next_ask_in(), sign_in.ASK_SPACING_SECONDS)
        self.clock.advance(10)
        self.assertEqual(self.sign_in.next_ask_in(), sign_in.ASK_SPACING_SECONDS - 10)
        self.clock.advance(sign_in.ASK_SPACING_SECONDS)
        self.assertEqual(self.sign_in.next_ask_in(), 0)

    def test_a_code_says_how_long_it_has_left(self) -> None:
        request = self.sign_in.ask("192.168.1.20")

        self.assertEqual(self.sign_in.seconds_left(request), sign_in.CODE_SECONDS)
        self.clock.advance(sign_in.CODE_SECONDS + 5)
        self.assertEqual(self.sign_in.seconds_left(request), 0)

    def test_a_new_ask_replaces_the_live_one(self) -> None:
        first = self.sign_in.ask("192.168.1.20")
        self.clock.advance(sign_in.ASK_SPACING_SECONDS + 1)
        second = self.sign_in.ask("192.168.1.99")

        self.assertNotEqual(first.id, second.id)
        pending = self.sign_in.pending()
        assert pending is not None
        self.assertEqual(pending.id, second.id)

    def test_a_code_on_screen_is_not_taken_over_by_another_address(self) -> None:
        shown = self.shown()
        self.clock.advance(sign_in.ASK_SPACING_SECONDS + 1)

        error = self.refusal(lambda: self.sign_in.ask("192.168.1.99"))

        self.assertEqual(error.reason, sign_in.TOO_SOON)
        pending = self.sign_in.pending()
        assert pending is not None
        self.assertEqual(pending.id, shown.id)
        self.sign_in.dismiss(shown.id)
        self.assertEqual(self.sign_in.ask("192.168.1.99").address, "192.168.1.99")

    def test_three_codes_spent_in_a_row_double_the_wait(self) -> None:
        for _ in range(sign_in.SPENT_BEFORE_DOUBLING):
            request = self.shown()
            for _ in range(sign_in.TRIES):
                try:
                    self.sign_in.verify(request.id, "x", address="192.168.1.20")
                except SignInError:
                    pass
            self.clock.advance(sign_in.ASK_SPACING_SECONDS * 4 + 1)

        self.sign_in.ask("192.168.1.20")
        self.clock.advance(sign_in.ASK_SPACING_SECONDS + 1)
        error = self.refusal(lambda: self.sign_in.ask("192.168.1.99"))
        self.assertEqual(error.reason, sign_in.TOO_SOON)

    def test_a_good_sign_in_clears_the_count_of_spent_codes(self) -> None:
        request = self.shown()
        self.sign_in.verify(request.id, request.code, address="192.168.1.20")

        self.assertEqual(self.sign_in._spacing(), sign_in.ASK_SPACING_SECONDS)


class ThePairingSpacingTests(SignInCase):
    def spend_a_code(self) -> None:
        asked = self.sign_in.pair_ask("192.168.1.30")
        for _ in range(sign_in.TRIES):
            self.refusal(lambda: self.sign_in.pair_verify(asked.id, "x"))

    def test_asking_again_at_once_is_turned_away(self) -> None:
        self.sign_in.pair_ask("192.168.1.30")

        error = self.refusal(lambda: self.sign_in.pair_ask("192.168.1.31"))

        self.assertEqual(error.reason, sign_in.TOO_SOON)

    def test_codes_spent_in_a_row_make_the_wait_grow_to_a_limit(self) -> None:
        for _ in range(sign_in.SPENT_BEFORE_DOUBLING + 2):
            self.spend_a_code()
            self.clock.advance(sign_in.PAIR_SPACING_LIMIT_SECONDS + 1)
        self.spend_a_code()
        self.clock.advance(sign_in.CONFIRM_ASK_SECONDS + 1)

        error = self.refusal(lambda: self.sign_in.pair_ask("192.168.1.30"))

        self.assertEqual(error.reason, sign_in.TOO_SOON)
        self.assertGreater(error.wait, sign_in.CONFIRM_ASK_SECONDS)

    def test_the_wait_stops_growing_at_the_limit(self) -> None:
        for _ in range(20):
            self.spend_a_code()
            self.clock.advance(sign_in.PAIR_SPACING_LIMIT_SECONDS + 1)

        self.assertEqual(self.sign_in.pair_ask("192.168.1.30").address, "192.168.1.30")

    def test_a_right_code_puts_the_wait_back(self) -> None:
        for _ in range(sign_in.SPENT_BEFORE_DOUBLING + 1):
            self.spend_a_code()
            self.clock.advance(sign_in.PAIR_SPACING_LIMIT_SECONDS + 1)
        asked = self.sign_in.pair_ask("192.168.1.30")
        assert self.sign_in._pair is not None
        code = self.sign_in._pair.code
        self.sign_in.pair_verify(asked.id, code)

        self.clock.advance(sign_in.CONFIRM_ASK_SECONDS + 1)

        self.assertEqual(self.sign_in.pair_ask("192.168.1.30").address, "192.168.1.30")


class TheMachinesSideTests(SignInCase):
    def test_ignoring_refuses_that_address_from_then_on(self) -> None:
        request = self.sign_in.ask("192.168.1.20")
        self.sign_in.ignore(request.id)
        self.clock.advance(3600)

        error = self.refusal(lambda: self.sign_in.ask("192.168.1.20"))

        self.assertEqual(error.reason, sign_in.IGNORED)
        self.assertIsNone(self.sign_in.pending())

    def test_ignoring_one_address_leaves_another_able_to_ask(self) -> None:
        request = self.sign_in.ask("192.168.1.20")
        self.sign_in.ignore(request.id)
        self.clock.advance(sign_in.ASK_SPACING_SECONDS + 1)

        self.assertEqual(self.sign_in.ask("192.168.1.99").address, "192.168.1.99")

    def test_the_machine_is_told_when_the_request_changes(self) -> None:
        heard: list[object] = []
        self.sign_in.on_request(heard.append)

        request = self.sign_in.ask("192.168.1.20")
        self.sign_in.reveal(request.id)
        self.sign_in.ignore(request.id)

        self.assertEqual([getattr(h, "state", None) for h in heard],
                         [sign_in.WAITING, sign_in.SHOWN, None])

    def test_showing_a_request_that_has_gone_does_nothing(self) -> None:
        request = self.sign_in.ask("192.168.1.20")
        self.clock.advance(sign_in.CODE_SECONDS + 1)

        self.assertIsNone(self.sign_in.reveal(request.id))

    def test_a_request_waits_for_show_while_a_screen_can_draw_it(self) -> None:
        self.assertEqual(self.sign_in.ask("192.168.1.20").state, sign_in.WAITING)

    def test_with_no_screen_a_request_is_revealed_at_once(self) -> None:
        self.sign_in.screen = lambda: False

        self.assertEqual(self.sign_in.ask("192.168.1.20").state, sign_in.SHOWN)

    def test_a_request_left_waiting_is_revealed_when_the_screen_goes(self) -> None:
        asked = self.sign_in.ask("192.168.1.20")
        self.sign_in.screen = lambda: False

        self.sign_in.reveal_if_unseen()

        pending = self.sign_in.pending()
        assert pending is not None
        self.assertEqual((pending.id, pending.state), (asked.id, sign_in.SHOWN))

    def test_asking_again_after_the_screen_went_reveals_the_waiting_request(self) -> None:
        browser = "one browser"
        asked = self.sign_in.ask("192.168.1.20", browser)
        self.sign_in.screen = lambda: False

        again = self.sign_in.ask("192.168.1.20", browser)

        self.assertEqual((again.id, again.state), (asked.id, sign_in.SHOWN))

    def test_a_screen_still_up_leaves_a_waiting_request_waiting(self) -> None:
        self.sign_in.ask("192.168.1.20")

        self.sign_in.reveal_if_unseen()

        pending = self.sign_in.pending()
        assert pending is not None
        self.assertEqual(pending.state, sign_in.WAITING)

    def test_every_code_reaches_the_terminal_and_never_the_log_file(self) -> None:
        root = logging.getLogger()
        held, level = list(root.handlers), root.level

        def restore() -> None:
            root.handlers.clear()
            root.handlers.extend(held)
            root.setLevel(level)

        self.addCleanup(restore)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            written = log_setup.configure_logging(Path(folder.name))
            asked = self.sign_in.ask("192.168.1.20")
            sign_in_code = self.sign_in.reveal(asked.id)
            token, session = self.sign_in.verify(asked.id, asked.code, address="192.168.1.20",
                                                 agent="Safari on iPhone")
            confirm = self.sign_in.confirm_ask(session.id, "192.168.1.20")
            pairing = self.sign_in.pair_ask("192.168.1.21")
            for handler in root.handlers:
                handler.flush()

        assert sign_in_code is not None
        logged = written.read_text(encoding="utf-8")
        for code in (sign_in_code.code, confirm.code, pairing.code):
            self.assertIn(code, out.getvalue())
            self.assertNotIn(code, logged)
        self.assertIn("192.168.1.20 wants to sign in", logged)
        for handler in list(root.handlers):
            if handler not in held:
                root.removeHandler(handler)
                handler.close()

    def test_a_request_on_the_machine_itself_shows_at_once(self) -> None:
        self.assertEqual(self.sign_in.reveal_now("127.0.0.1").state, sign_in.SHOWN)


class TheSessionTests(SignInCase):
    def signed_in(self, address: str = "192.168.1.20") -> tuple[str, sign_in.Session]:
        request = self.shown(address)
        return self.sign_in.verify(request.id, request.code, address=address,
                                   agent="Safari on iPhone")

    def test_an_unknown_secret_opens_nothing(self) -> None:
        self.assertIsNone(self.sign_in.lookup("nope"))
        self.assertIsNone(self.sign_in.lookup(""))

    def test_the_file_holds_a_hash_and_never_the_secret(self) -> None:
        token, _ = self.signed_in()

        self.assertNotIn(token, self.file.read_text())

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_the_file_is_for_this_user_alone(self) -> None:
        self.signed_in()

        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)

    def test_a_session_outlives_a_restart(self) -> None:
        token, session = self.signed_in()

        again = SignIn(self.file, self.clock)

        self.assertEqual(again.lookup(token), session)

    def test_a_session_unused_for_ninety_days_signs_out(self) -> None:
        token, _ = self.signed_in()
        self.clock.advance(sign_in.SESSION_SECONDS + 1)

        self.assertIsNone(self.sign_in.lookup(token))
        self.assertEqual(self.sign_in.sessions(), [])

    def test_use_keeps_a_session_alive(self) -> None:
        token, _ = self.signed_in()
        for _ in range(4):
            self.clock.advance(sign_in.SESSION_SECONDS // 2)
            self.assertIsNotNone(self.sign_in.lookup(token))

    def test_use_is_recorded_so_the_list_can_say_when(self) -> None:
        token, session = self.signed_in()
        self.clock.advance(3 * 3600)
        self.sign_in.lookup(token)

        self.assertEqual(self.sign_in.sessions()[0].last_used, session.created + 3 * 3600)

    def test_recording_use_is_written_off_the_calling_thread(self) -> None:
        """A lookup runs on the event loop, and a save waits on the disk."""
        token, _ = self.signed_in()
        self.clock.advance(3 * 3600)
        writers: list[threading.Thread] = []
        real = sign_in.machine_secrets.write_private

        def write(path: Path, data: object) -> None:
            writers.append(threading.current_thread())
            real(path, data)

        with mock.patch.object(sign_in.machine_secrets, "write_private", write):
            self.sign_in.lookup(token)
            self.sign_in.wait_for_saves()

        self.assertEqual(len(writers), 1)
        self.assertIsNot(writers[0], threading.current_thread())
        kept = json.loads(self.file.read_text())
        self.assertEqual([row["last_used"] for row in kept.values()], [self.clock.now])

    def test_a_lookup_does_not_wait_for_a_save_that_is_waiting_on_the_disk(self) -> None:
        token, _ = self.signed_in()
        self.clock.advance(3 * 3600)
        started, release = threading.Event(), threading.Event()
        real = sign_in.machine_secrets.write_private

        def slow(path: Path, data: object) -> None:
            started.set()
            release.wait(5)
            real(path, data)

        with mock.patch.object(sign_in.machine_secrets, "write_private", slow):
            self.sign_in.lookup(token)
            self.assertTrue(started.wait(5))
            answered: list[object] = []
            asker = threading.Thread(target=lambda: answered.append(self.sign_in.lookup(token)))
            asker.start()
            asker.join(2)
            release.set()
            self.sign_in.wait_for_saves()

        self.assertFalse(asker.is_alive())
        self.assertEqual(len(answered), 1)

    def test_signing_one_out_leaves_the_others(self) -> None:
        first, one = self.signed_in()
        self.clock.advance(sign_in.ASK_SPACING_SECONDS + 1)
        second, _ = self.signed_in("192.168.1.30")

        self.assertTrue(self.sign_in.sign_out(one.id))

        self.assertIsNone(self.sign_in.lookup(first))
        self.assertIsNotNone(self.sign_in.lookup(second))

    def test_sign_out_everywhere_ends_every_session(self) -> None:
        first, _ = self.signed_in()
        self.clock.advance(sign_in.ASK_SPACING_SECONDS + 1)
        second, _ = self.signed_in("192.168.1.30")

        self.assertEqual(self.sign_in.sign_out_everywhere(), 2)

        self.assertIsNone(self.sign_in.lookup(first))
        self.assertIsNone(self.sign_in.lookup(second))
        self.assertEqual(json.loads(self.file.read_text()), {})

    def test_signing_out_by_secret_ends_that_browser(self) -> None:
        token, _ = self.signed_in()

        self.sign_in.sign_out_token(token)

        self.assertIsNone(self.sign_in.lookup(token))

    def test_the_list_says_who_and_from_where(self) -> None:
        self.signed_in()

        row = self.sign_in.sessions()[0]

        self.assertEqual((row.address, row.agent, row.kind),
                         ("192.168.1.20", "Safari on iPhone", sign_in.OWNER))

    def test_a_damaged_row_in_the_file_is_skipped(self) -> None:
        token, _ = self.signed_in()
        held = json.loads(self.file.read_text())
        held["broken"] = {"id": "x"}
        self.file.write_text(json.dumps(held))

        again = SignIn(self.file, self.clock)

        self.assertIsNotNone(again.lookup(token))


class TheAskerTests(SignInCase):
    def test_a_sign_in_keeps_who_asked_and_from_where(self) -> None:
        request = self.sign_in.ask("192.168.1.20", agent="Safari on iPad", place="ipad.lan")

        self.assertEqual((request.agent, request.place), ("Safari on iPad", "ipad.lan"))

    def test_a_confirm_names_the_browser_that_holds_the_session(self) -> None:
        asked = self.sign_in.ask("192.168.1.20", agent="Safari on iPad")
        self.sign_in.reveal(asked.id)
        _, session = self.sign_in.verify(asked.id, asked.code, address="192.168.1.20",
                                         agent="Safari on iPad")

        confirm = self.sign_in.confirm_ask(session.id, "192.168.1.20", place="ipad.lan")

        self.assertEqual((confirm.agent, confirm.place), ("Safari on iPad", "ipad.lan"))

    def test_a_pairing_keeps_the_name_the_other_install_gave(self) -> None:
        pairing = self.sign_in.pair_ask("192.168.1.30", "Game Room", "gameroom.lan")

        self.assertEqual((pairing.name, pairing.place), ("Game Room", "gameroom.lan"))

    def test_a_pairing_name_is_fit_to_draw(self) -> None:
        pairing = self.sign_in.pair_ask("192.168.1.30", "  Game\x1b[31m Room\n" + "x" * 100)

        self.assertNotIn("\x1b", pairing.name)
        self.assertNotIn("\n", pairing.name)
        self.assertEqual(len(pairing.name), sign_in.NAME_LENGTH)


class TheAgentLine(unittest.TestCase):
    def test_it_names_the_browser_and_the_device(self) -> None:
        cases = {
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1": "Safari on iPhone",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36 Edg/120.0": "Edge on Windows",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Gecko/20100101 Firefox/121.0":
                "Firefox on Mac",
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36":
                "Chrome on Android",
        }
        for agent, line in cases.items():
            with self.subTest(line=line):
                self.assertEqual(sign_in.describe_agent(agent), line)

    def test_what_it_cannot_place_it_leaves_out(self) -> None:
        self.assertEqual(sign_in.describe_agent(""), "")
        self.assertEqual(sign_in.describe_agent("curl/8.0"), "")
        self.assertEqual(sign_in.describe_agent("Safari/604.1"), "Safari")
        self.assertEqual(sign_in.describe_agent("X11; Linux x86_64"), "Linux")


if __name__ == "__main__":
    unittest.main()

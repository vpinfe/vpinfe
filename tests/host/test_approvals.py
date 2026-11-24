"""A program asks first, the Owner decides, and an approval is spent on one request."""

from __future__ import annotations

import unittest

from common.security import approvals


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ApprovalsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.store = approvals.Approvals(self.clock)
        self.told = 0
        self.store.on_change(self._tell)

    def _tell(self) -> None:
        self.told += 1

    def ask(self, token="t1", method="DELETE", path="/games/a/folder", body=None):
        return self.store.ask(token, "Home", method, path, body)

    def held(self, ask_id: str) -> approvals.Ask:
        found = self.store.get(ask_id)
        assert found is not None
        return found

    def on_screen(self) -> approvals.Ask:
        found = self.store.on_screen()
        assert found is not None
        return found


class AskingTests(ApprovalsCase):
    def test_an_ask_waits_for_the_owner_and_says_so(self) -> None:
        made = self.ask()

        self.assertEqual(made.state, approvals.WAITING)
        self.assertEqual([one.id for one in self.store.waiting()], [made.id])
        self.assertEqual(self.told, 1)

    def test_the_same_request_asked_again_is_the_same_ask(self) -> None:
        first = self.ask(body={"a": 1, "b": 2})
        again = self.ask(body={"b": 2, "a": 1})

        self.assertEqual(first.id, again.id)
        self.assertEqual(len(self.store.waiting()), 1)

    def test_another_token_asking_for_the_same_request_is_another_ask(self) -> None:
        self.assertNotEqual(self.ask(token="t1").id, self.ask(token="t2").id)

    def test_a_body_too_large_to_read_is_refused(self) -> None:
        with self.assertRaises(approvals.AskError) as caught:
            self.ask(body={"text": "x" * approvals.MAX_BODY_CHARS})

        self.assertEqual(caught.exception.reason, approvals.TOO_BIG)

    def test_one_token_cannot_fill_the_screen(self) -> None:
        for number in range(approvals.PER_TOKEN):
            self.ask(path=f"/games/{number}/folder")

        with self.assertRaises(approvals.AskError) as caught:
            self.ask(path="/games/more/folder")

        self.assertEqual(caught.exception.reason, approvals.TOO_MANY)
        self.assertEqual(len(self.store.waiting()), approvals.PER_TOKEN)

    def test_all_the_tokens_together_are_held_to_a_limit_too(self) -> None:
        for number in range(approvals.IN_ALL):
            self.ask(token=f"t{number}")

        with self.assertRaises(approvals.AskError):
            self.ask(token="one more")

    def test_an_answered_ask_no_longer_counts_against_the_limit(self) -> None:
        for number in range(approvals.PER_TOKEN):
            made = self.ask(path=f"/games/{number}/folder")
        self.store.decline(made.id)

        self.assertEqual(self.ask(path="/games/more/folder").state, approvals.WAITING)


class AnsweringTests(ApprovalsCase):
    def test_approving_lets_the_one_request_through_once(self) -> None:
        made = self.ask(body={"a": 1})
        self.store.approve(made.id)

        first = self.store.redeem(made.id, "t1", "DELETE", "/games/a/folder", '{"a":1}')
        second = self.store.redeem(made.id, "t1", "DELETE", "/games/a/folder", '{"a":1}')

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.held(made.id).state, approvals.USED)

    def test_a_request_that_differs_in_anything_leaves_the_approval_alone(self) -> None:
        made = self.ask(body={"a": 1})
        self.store.approve(made.id)

        for token, method, path, body in (
                ("t2", "DELETE", "/games/a/folder", '{"a":1}'),
                ("t1", "POST", "/games/a/folder", '{"a":1}'),
                ("t1", "DELETE", "/games/b/folder", '{"a":1}'),
                ("t1", "DELETE", "/games/a/folder", '{"a":2}'),
                ("t1", "DELETE", "/games/a/folder", "")):
            with self.subTest(token=token, method=method, path=path, body=body):
                self.assertFalse(self.store.redeem(made.id, token, method, path, body))

        self.assertEqual(self.held(made.id).state, approvals.APPROVED)

    def test_an_ask_nobody_approved_cannot_be_spent(self) -> None:
        made = self.ask()

        self.assertFalse(self.store.redeem(made.id, "t1", "DELETE", "/games/a/folder", ""))

    def test_declining_ends_it_and_nothing_can_spend_it(self) -> None:
        made = self.ask()
        self.store.decline(made.id)

        self.assertEqual(self.held(made.id).state, approvals.DECLINED)
        self.assertIsNone(self.store.approve(made.id))
        self.assertFalse(self.store.redeem(made.id, "t1", "DELETE", "/games/a/folder", ""))
        self.assertEqual(self.store.waiting(), [])

    def test_a_declined_ask_cannot_be_approved_afterwards(self) -> None:
        made = self.ask()
        self.store.decline(made.id)

        self.assertIsNone(self.store.approve(made.id))

    def test_the_program_can_take_back_only_its_own_ask(self) -> None:
        made = self.ask()

        self.assertIsNone(self.store.decline(made.id, token_id="t2"))
        self.assertIsNotNone(self.store.decline(made.id, token_id="t1"))

    def test_a_revoked_tokens_asks_go_and_the_others_stay(self) -> None:
        mine = self.ask()
        theirs = self.store.ask("t2", "Other", "POST", "/y")
        self.store.approve(mine.id)
        before = self.told

        self.store.forget_token("t1")

        self.assertIsNone(self.store.get(mine.id))
        self.assertEqual([one.id for one in self.store.waiting()], [theirs.id])
        self.assertFalse(self.store.redeem(mine.id, "t1", "DELETE", "/games/a/folder", ""))
        self.assertEqual(self.told, before + 1)

    def test_an_unknown_id_is_nothing(self) -> None:
        self.assertIsNone(self.store.approve("nope"))
        self.assertFalse(self.store.redeem("nope", "t1", "DELETE", "/x", ""))


class LapsingTests(ApprovalsCase):
    def test_an_ask_nobody_answered_lapses_after_an_hour(self) -> None:
        made = self.ask()

        self.clock.advance(approvals.LAPSE_SECONDS + 1)

        self.assertEqual(self.store.waiting(), [])
        self.assertEqual(self.held(made.id).state, approvals.LAPSED)
        self.assertIsNone(self.store.approve(made.id))

    def test_an_approval_lapses_an_hour_after_it_was_given(self) -> None:
        made = self.ask()
        self.clock.advance(approvals.LAPSE_SECONDS - 60)
        self.store.approve(made.id)
        self.clock.advance(approvals.LAPSE_SECONDS - 60)

        self.assertEqual(self.store.seconds_left(self.held(made.id)), 60)
        self.clock.advance(120)

        self.assertFalse(self.store.redeem(made.id, "t1", "DELETE", "/games/a/folder", ""))
        self.assertEqual(self.held(made.id).state, approvals.LAPSED)

    def test_something_over_is_forgotten_after_another_hour(self) -> None:
        made = self.ask()
        self.store.decline(made.id)

        self.clock.advance(approvals.LAPSE_SECONDS + 1)

        self.assertIsNone(self.store.get(made.id))

    def test_a_lapse_tells_the_listeners(self) -> None:
        self.ask()
        told = self.told

        self.clock.advance(approvals.LAPSE_SECONDS + 1)
        self.store.waiting()

        self.assertGreater(self.told, told)


class TheScreenTests(ApprovalsCase):
    def test_the_screen_shows_the_oldest_ask_and_moves_on_when_one_is_left(self) -> None:
        first = self.ask(path="/games/1/folder")
        self.clock.advance(5)
        second = self.ask(path="/games/2/folder")

        self.assertEqual(self.on_screen().id, first.id)
        self.store.leave_screen(first.id)

        self.assertEqual(self.on_screen().id, second.id)
        self.assertEqual({one.id for one in self.store.waiting()}, {first.id, second.id})

    def test_nothing_is_on_the_screen_once_everything_is_answered(self) -> None:
        made = self.ask()
        self.store.approve(made.id)

        self.assertIsNone(self.store.on_screen())


class BodyTests(unittest.TestCase):
    def test_the_same_json_in_another_spelling_is_the_same_body(self) -> None:
        self.assertEqual(approvals.canonical_bytes(b'{ "b": 2,\n "a": 1 }'),
                         approvals.canonical({"a": 1, "b": 2}))

    def test_no_body_and_a_null_body_are_the_same_request(self) -> None:
        self.assertEqual(approvals.canonical_bytes(b""), "")
        self.assertEqual(approvals.canonical_bytes(b"null"), "")

    def test_a_body_that_is_not_json_matches_nothing(self) -> None:
        self.assertIsNone(approvals.canonical_bytes(b"--boundary\r\nContent"))


if __name__ == "__main__":
    unittest.main()

"""Commands somebody asks to run around a table, and what each may say.

argv and never a shell, a timeout because a hang here means no table ever launches
again, and a name nothing declares refused by name rather than resolved to nothing.
"""

from __future__ import annotations

import unittest

from common import tokens
from common.host import commands


class PlanTests(unittest.TestCase):
    def test_a_path_with_a_space_stays_one_argument(self) -> None:
        """Split first and resolve each argument. Resolving into the line and splitting
        after is how one path becomes two arguments, or worse."""
        plans = commands.planned("play {table}",
                                 {"table": "/games/My Game/My Table.vpx"},
                                 context=tokens.TABLE)

        self.assertEqual(plans, [["play", "/games/My Game/My Table.vpx"]])

    def test_one_command_per_line(self) -> None:
        plans = commands.planned("one\ntwo", {}, context=tokens.TABLE)

        self.assertEqual(plans, [["one"], ["two"]])

    def test_blank_lines_and_notes_are_not_commands(self) -> None:
        plans = commands.planned("# a note\n\n  \nreal", {}, context=tokens.TABLE)

        self.assertEqual(plans, [["real"]])

    def test_a_name_this_context_does_not_offer_is_refused_by_name(self) -> None:
        """`--config {settings_file}` resolved to nothing is a dangling flag the program
        reads as whatever comes next."""
        with self.assertRaises(tokens.UnknownTokenError) as raised:
            commands.planned("go {nonsense}", {}, context=tokens.TABLE)

        self.assertIn("nonsense", str(raised.exception))

    def test_a_name_that_belongs_to_another_context_is_refused_too(self) -> None:
        """VPinFE starting has no table to talk about."""
        with self.assertRaises(tokens.UnknownTokenError):
            commands.planned("go {table}", {"table": "x"}, context=tokens.VPINFE)

    def test_what_only_means_something_afterwards_is_refused_before(self) -> None:
        with self.assertRaises(tokens.UnknownTokenError):
            commands.planned("go {exit_code}", {}, context=tokens.TABLE)

        self.assertEqual(
            commands.planned("go {exit_code}", {"exit_code": "0"},
                             context=tokens.TABLE, after=True),
            [["go", "0"]])

    def test_an_unclosed_quote_is_reported_rather_than_guessed_at(self) -> None:
        with self.assertRaises(ValueError):
            commands.planned('go "unclosed', {}, context=tokens.TABLE)


class RunTests(unittest.TestCase):
    def test_a_set_that_works_says_it_ran(self) -> None:
        outcome = commands.run("true", {}, context=tokens.TABLE)

        self.assertTrue(outcome.ran)
        self.assertEqual(outcome.failed, [])

    def test_best_effort_keeps_going_past_a_failure(self) -> None:
        """A share that did not mount should stop a launch. An audio route that did not
        switch should not strand the machine."""
        outcome = commands.run("false\ntrue", {}, context=tokens.TABLE)

        self.assertEqual([one.ok for one in outcome.results], [False, True])

    def test_required_stops_at_the_first_failure(self) -> None:
        """The rest of the set was written expecting the first to have worked."""
        with self.assertRaises(commands.CommandRefusedError):
            commands.run("false\ntrue", {}, context=tokens.TABLE,
                         on_failure=commands.REQUIRED)

    def test_a_command_that_hangs_is_given_up_on(self) -> None:
        """Without this, one mistake means no table launches ever again - and the launch
        path suppresses the frontend's input across that window."""
        outcome = commands.run("sleep 30", {}, context=tokens.TABLE, timeout=1)

        self.assertIn("still running", outcome.failed[0].said)

    def test_a_program_that_is_not_there_is_a_failure_not_a_crash(self) -> None:
        outcome = commands.run("/nope/not/a/program", {}, context=tokens.TABLE)

        self.assertEqual(len(outcome.failed), 1)

    def test_nothing_to_run_is_not_a_run(self) -> None:
        """Which is what decides whether the other half has to run."""
        self.assertFalse(commands.run("", {}, context=tokens.TABLE).ran)
        self.assertFalse(commands.run("# only a note", {}, context=tokens.TABLE).ran)

    def test_a_line_that_cannot_be_read_refuses_where_it_matters(self) -> None:
        with self.assertRaises(commands.CommandRefusedError):
            commands.run("go {nonsense}", {}, context=tokens.TABLE,
                         on_failure=commands.REQUIRED)

    def test_and_is_only_logged_where_it_does_not(self) -> None:
        outcome = commands.run("go {nonsense}", {}, context=tokens.TABLE)

        self.assertFalse(outcome.ran)
        self.assertEqual(len(outcome.failed), 1)


class OfferedTests(unittest.TestCase):
    def test_a_surface_can_list_what_a_command_may_say(self) -> None:
        """Declared, because a feature people guess at is one they do not use."""
        names = [one.name for one in tokens.offered(tokens.TABLE)]

        self.assertIn("table", names)
        self.assertIn("rom", names)
        self.assertNotIn("exit_code", names)

    def test_vpinfe_starting_has_no_game_to_talk_about(self) -> None:
        names = [one.name for one in tokens.offered(tokens.VPINFE)]

        self.assertNotIn("table", names)
        self.assertNotIn("program", names)

    def test_every_token_says_what_it_is(self) -> None:
        """It is shown beside the field, so a name with no sentence is a name nobody
        can use."""
        for one in tokens.TOKENS:
            with self.subTest(token=one.name):
                self.assertTrue(tokens.stands_for(one).strip())

    def test_a_surface_can_say_which_name_is_wrong_while_it_is_typed(self) -> None:
        self.assertEqual(
            tokens.unknown_names("{table} {nope} {rom}", context=tokens.TABLE), ["nope"])


if __name__ == "__main__":
    unittest.main()

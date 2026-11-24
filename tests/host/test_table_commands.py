"""The two halves that bracket a table, and what happens when only the first one runs.

The install's set runs outside the launcher's, the second half runs whenever the first
did - even where the program never started - and a VPinFE that dies between them finishes
the job on its next start rather than leaving a machine somebody has to put back by hand.
"""

from __future__ import annotations

import configparser
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from common import apps, tokens
from common.config.config_store import ConfigStore
from common.host import commands, table_commands
from common.i18n import t
from common.players import players
from tests.support.library import own_registry


def _config(**settings: str) -> ConfigStore:
    parser = configparser.ConfigParser()
    parser["table_commands"] = dict(settings)
    return cast(ConfigStore, parser)


def _launcher(**settings: str):
    fields = tuple(SimpleNamespace(key=key) for key in settings)
    return SimpleNamespace(display_name="Visual Pinball", fields=lambda: fields,
                           value=settings.get, in_effect=settings.get)


def _game():
    return SimpleNamespace(full_path_game="/games/Attack from Mars",
                           game_dir_name="Attack from Mars", location_id="loc1",
                           meta_config={})


def _playing(table="/games/Attack from Mars/afm.vpx", key="", entry_id="t1"):
    return apps.Entry(entry_id=entry_id, table=table,
                      game_dir="/games/Attack from Mars", key=key)


class OrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ran: list[str] = []
        patch = mock.patch.object(
            table_commands.commands, "run",
            side_effect=lambda text, *a, **k: self._record(text))
        patch.start()
        self.addCleanup(patch.stop)
        pending = mock.patch.object(table_commands, "_remember")
        pending.start()
        self.addCleanup(pending.stop)
        forget = mock.patch.object(table_commands, "_forget")
        forget.start()
        self.addCleanup(forget.stop)

    def _record(self, text: str) -> commands.Outcome:
        self.ran.append(text)
        return commands.Outcome(ran=True)

    def test_the_install_runs_outside_the_launcher(self) -> None:
        """Execution order, not evidence they are one feature: what the machine needs is
        true whichever launcher plays the table."""
        around = table_commands.before(
            _game(), _playing(),
            _launcher(on_table_start="launcher-pre", on_table_exit="launcher-post"),
            _config(on_start="install-pre", on_exit="install-post"))
        table_commands.after(around, started_at=None)

        self.assertEqual(
            self.ran,
            ["install-pre", "launcher-pre", "launcher-post", "install-post"])

    def test_nothing_written_runs_nothing_and_owes_nothing(self) -> None:
        around = table_commands.before(_game(), _playing(), _launcher(), _config())
        table_commands.after(around)

        self.assertEqual(self.ran, [])
        self.assertFalse(around.ran)


class ValueTests(unittest.TestCase):
    def test_a_command_is_told_about_the_table_it_is_running_around(self) -> None:
        values = table_commands._values(
            _game(), _playing(),
            _launcher(bin_path="/opt/vpx/VPinballX", ini_path="/cfg/VPinballX.ini"))

        self.assertEqual(values["game_dir"], "/games/Attack from Mars")
        self.assertEqual(values["table"], "/games/Attack from Mars/afm.vpx")
        self.assertEqual(values["table_stem"], "afm")
        self.assertEqual(values["game_name"], "Attack from Mars")
        self.assertEqual(values["program"], "/opt/vpx/VPinballX")
        self.assertEqual(values["settings_file"], "/cfg/VPinballX.ini")
        self.assertEqual(values["location"], "loc1")

    def test_a_command_names_the_one_player_up(self) -> None:
        up = [players.Player("p1", initials="ABC")]

        self.assertEqual(table_commands._values(_game(), _playing(), None, up)["player"],
                         "ABC")

    def test_with_several_up_a_command_names_nobody(self) -> None:
        """The same rule a score with no initials follows: never somebody who was not
        the one playing."""
        up = [players.Player("p1", initials="ABC"), players.Player("p2", initials="OWN")]

        self.assertEqual(table_commands._values(_game(), _playing(), None, up)["player"], "")

    def test_the_player_is_a_name_a_table_command_may_use(self) -> None:
        self.assertEqual(commands.planned("note {player}", {"player": "ABC"},
                                          context=tokens.TABLE), [["note", "ABC"]])

    def test_what_a_table_never_had_is_empty_rather_than_missing(self) -> None:
        """Every name the context offers has to resolve, or a command that mentions one
        of them fails on a table that happens not to have it."""
        values = table_commands._values(_game(), _playing(entry_id=""), None)

        for name in ("id", "key", "rom", "program", "settings_file"):
            self.assertEqual(values[name], "", name)


class KeyedEntryTests(unittest.TestCase):
    """An entry with no file. Every name still resolves - `{table}` to nothing, because
    there is no file, and `{key}` to the name its app knows it by."""

    def test_a_keyed_entry_has_a_key_and_no_table(self) -> None:
        values = table_commands._values(
            _game(), _playing(table="", key="mm", entry_id="t2"), None)

        self.assertEqual(values["key"], "mm")
        self.assertEqual(values["table"], "")
        self.assertEqual(values["table_stem"], "")
        self.assertEqual(values["game_dir"], "/games/Attack from Mars")


class FailureTests(unittest.TestCase):
    def setUp(self) -> None:
        for name in ("_remember", "_forget"):
            patch = mock.patch.object(table_commands, name)
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_required_failure_stops_the_launch(self) -> None:
        with mock.patch.object(table_commands.commands, "run",
                               side_effect=commands.CommandRefusedError("no share")):
            with self.assertRaises(commands.CommandRefusedError) as raised, \
                    self.assertLogs(table_commands.logger, "WARNING") as logged:
                table_commands.before(_game(), _playing(), _launcher(),
                                      _config(on_start="mount",
                                              start_required="true"))

        self.assertEqual(str(raised.exception),
                         t("error.table_commands.global_failed"))
        self.assertIn("no share", logged.output[0])

    def test_and_the_other_half_still_owes_a_run(self) -> None:
        """Whatever did run put the machine somewhere, and that has to be undone whether
        or not the rest of the set worked."""
        def run(text, *_a, **_k):
            if text == "mount":
                raise commands.CommandRefusedError("no share")
            return commands.Outcome(ran=True)

        with mock.patch.object(table_commands.commands, "run", side_effect=run):
            with self.assertRaises(commands.CommandRefusedError), \
                    self.assertLogs(table_commands.logger, "WARNING"):
                table_commands.before(
                    _game(), _playing(), _launcher(),
                    _config(on_start="mount", start_required="true"))

        self.assertTrue(cast(Any, table_commands._remember).called)

    def test_a_launcher_failure_that_was_not_required_lets_the_table_start(self) -> None:
        with mock.patch.object(table_commands.commands, "run",
                               return_value=commands.Outcome(ran=True)) as run:
            table_commands.before(
                _game(), _playing(),
                _launcher(on_table_start="sink", on_start_required="false"),
                _config())

        self.assertEqual(run.call_args.kwargs["on_failure"], commands.BEST_EFFORT)


class UnfinishedTests(unittest.TestCase):
    """If VPinFE dies mid-session the second half never runs, and a muted machine or a
    stopped service is otherwise something somebody has to work out."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.registry = own_registry(self, Path(home.name) / "devices.json")

    def owed(self) -> object:
        return self.registry.self_get(table_commands.PENDING_KEY)

    def test_nothing_left_behind_is_nothing_to_do(self) -> None:
        self.assertFalse(table_commands.finish_unfinished())

    def test_the_next_start_runs_what_the_last_one_owed(self) -> None:
        with mock.patch.object(table_commands.commands, "run",
                               return_value=commands.Outcome(ran=True)):
            table_commands.before(
                _game(), _playing(), _launcher(on_table_exit="unmute"),
                _config(on_start="mute", on_exit="restart-service"))

        self.assertIsNotNone(self.owed())

        ran: list[str] = []

        def run(text: str, *_a: Any, **_k: Any) -> commands.Outcome:
            ran.append(text)
            return commands.Outcome(ran=True)

        with mock.patch.object(table_commands.commands, "run", side_effect=run):
            self.assertTrue(table_commands.finish_unfinished())

        self.assertEqual(ran, ["unmute", "restart-service"])
        self.assertIsNone(self.owed())

    def test_and_stops_owing_it_once_the_half_has_run(self) -> None:
        with mock.patch.object(table_commands.commands, "run",
                               return_value=commands.Outcome(ran=True)):
            around = table_commands.before(
                _game(), _playing(), _launcher(),
                _config(on_start="mute", on_exit="unmute"))
            table_commands.after(around, started_at=None)

        self.assertIsNone(self.owed())
        self.assertFalse(table_commands.finish_unfinished())


if __name__ == "__main__":
    unittest.main()

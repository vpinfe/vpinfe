"""The one launch path, which the wheel, the Remote page and the API all take.

The behavior pinned here used to be split across two implementations that
disagreed - most visibly, only one of them recorded that a table had been played.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import types
import unittest
from collections.abc import Callable
from configparser import ConfigParser
from dataclasses import asdict
from pathlib import PurePath
from typing import Any, cast
from unittest import mock

from common import events, mounts
from common.games import player_records, tables
from common.games.library_folders import Location, LocationStore
from common.games.score_parser import ParsedEntry
from common.host import commands, launch, launch_state, table_commands
from common.players import players


class _FakePopen:
    def __init__(self, lines=(), returncode=0):
        self.stdout = list(lines)
        self.waited = False
        # What the program exited with. A real Popen always has one, and a command set
        # to run after the table is told it.
        self.returncode = returncode

    def wait(self):
        self.waited = True
        return self.returncode


def _recording_popen(started: list[Any]) -> Callable[..., _FakePopen]:
    def popen(cmd: Any, **_kwargs: Any) -> _FakePopen:
        started.append(cmd)
        return _FakePopen()

    return popen


def stamped(state: dict) -> dict:
    """A launching state with its start time checked for being there and left out of
    the comparison, since the clock is not something a test sets."""
    assert state["started_at"] is None or isinstance(state["started_at"], str)
    return {key: value for key, value in state.items() if key != "started_at"}


def _game(name="Example"):
    return types.SimpleNamespace(
        full_path_vpx_file=f"/games/{name}/{name}.vpx",
        full_path_game=f"/games/{name}",
        game_dir_name=name,
        meta_config={},
        location_id="",
    )


def _launcher(bin_path: str = "/opt/vpx"):
    from common.games.launchers import Launcher

    return Launcher(launcher_id="l1", app="vpx", display_name="Visual Pinball X",
                    settings={"bin_path": bin_path})


def _reading(*entries: tuple[str, int]) -> dict:
    """A high score table as it is read: (initials, score), ranked in the order given."""
    return {"rom": "example", "resolved_rom": "example", "score_kind": "Leaderboard",
            "entries": [asdict(ParsedEntry(section="HIGH SCORES", rank=rank,
                                           initials=initials, score=score))
                        for rank, (initials, score) in enumerate(entries, 1)]}


class LaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        launch_state.clear()
        self.addCleanup(events.clear)
        self.addCleanup(launch_state.clear)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        players.reset_for_tests(pathlib.Path(folder.name) / "players.json")
        self.addCleanup(players.reset_for_tests)
        self.records_dir = pathlib.Path(folder.name) / "player_records"
        player_records.reset_for_tests(self.records_dir)
        self.addCleanup(player_records.reset_for_tests)
        self.roster = players.get_roster()
        made = self.roster.ensure_owner(ConfigParser())
        assert made is not None
        self.owner = self.roster.update_player(made.player_id, initials="OWN")

    def _run(self, popen=None, game=None, readings=None,
             source=launch_state.SOURCE_API, table=None, **overrides):
        """Launch with every collaborator stubbed, so only the orchestration runs.

        `readings` are the high score table before the launch and after the game, each
        with where it was read from.
        """
        popen = popen or (lambda cmd, **kwargs: _FakePopen())
        patches = {
            "_launcher_for": lambda game, vpx_path: (_launcher(), ""),
            "_binary_of": lambda launcher: "/opt/vpx",
            "_plan": lambda table, binary, launcher, **plan: (
                ["/opt/vpx", "-play", "x.vpx"], "Startup done"),
            "parse_launch_env_overrides": lambda raw: {},
            "delete_vpinball_log_on_start_if_configured": lambda *a, **k: None,
        }
        patches.update(overrides)

        with mock.patch.object(launch, "game_play_service") as play, \
                mock.patch.multiple(launch, **patches):
            play.parse_score_from_nvram.side_effect = list(
                readings or [(None, None), (None, None)])
            launch.launch_game(game or _game(), types.SimpleNamespace(config={}),
                                source=source, table=table, popen=popen)
        return play


class LifecycleTests(LaunchTests):
    def test_the_lifecycle_events_go_out_in_order(self) -> None:
        seen = []
        for name in (events.TABLE_LAUNCHING, events.TABLE_LAUNCHED, events.TABLE_EXITED):
            events.subscribe(name, lambda _n=name, **_: seen.append(_n))

        self._run(popen=lambda cmd, **k: _FakePopen(["Startup done\n"]))

        self.assertEqual(seen, ["table.launching", "table.launched", "table.exited"])

    def test_a_persons_own_commands_run_outside_everything_else(self) -> None:
        """"Before the table" has to mean before all of it, hooks included. Anywhere
        further in and what it means shifts as our own sequence changes."""
        seen = []
        for name in (events.TABLE_LAUNCHING, events.TABLE_EXITED):
            events.subscribe(name, lambda _n=name, **_: seen.append(_n))

        def before(game, table, launcher, ini_config, up=None):
            seen.append("pre")
            return table_commands.Around(ran=True)

        with mock.patch.object(table_commands, "before", before), \
                mock.patch.object(table_commands, "after",
                                  lambda around, **k: seen.append("post")):
            self._run(popen=lambda cmd, **k: _FakePopen(["Startup done\n"]))

        self.assertEqual(seen, ["pre", "table.launching", "table.exited", "post"])

    def test_a_command_that_was_set_to_stop_the_launch_stops_it(self) -> None:
        """And it stops it before anything is announced, so there is nothing to undo."""
        seen = []
        events.subscribe(events.TABLE_LAUNCHING, lambda **_: seen.append("launching"))

        with mock.patch.object(table_commands, "before",
                               side_effect=commands.CommandRefusedError("no share")):
            with self.assertRaises(launch.LaunchUnavailableError) as raised:
                self._run()

        self.assertIn("no share", str(raised.exception))
        self.assertEqual(seen, [])

    def test_a_game_that_never_starts_reports_no_launched(self) -> None:
        """table.launched means the table is up, not that a process exists."""
        seen = []
        for name in (events.TABLE_LAUNCHING, events.TABLE_LAUNCHED, events.TABLE_EXITED):
            events.subscribe(name, lambda _n=name, **_: seen.append(_n))

        self._run(popen=lambda cmd, **k: _FakePopen(["some other output\n"]))

        self.assertEqual(seen, ["table.launching", "table.exited"])

    def test_exited_still_fires_when_the_launch_blows_up(self) -> None:
        """Whoever heard launching has to hear exited, or the frontend never gets
        its input back."""
        seen = []
        events.subscribe(events.TABLE_EXITED, lambda **_: seen.append("exited"))

        def boom(cmd, **kwargs):
            raise RuntimeError("popen failed")

        with self.assertRaises(RuntimeError):
            self._run(popen=boom)

        self.assertEqual(seen, ["exited"])

    def test_a_hook_that_refuses_stops_the_launch_before_anything_starts(self) -> None:
        """Releasing the peripherals is a hook. If it fails, VPX must not start."""
        started: list[Any] = []
        events.hook(events.TABLE_LAUNCHING, lambda **_: (_ for _ in ()).throw(
            RuntimeError("device busy")))

        with self.assertRaises(RuntimeError):
            self._run(popen=_recording_popen(started))

        self.assertEqual(started, [], "nothing was launched")
        self.assertFalse(launch_state.current().launching)

    def test_the_launch_is_announced_and_then_cleared(self) -> None:
        during = []
        events.subscribe(events.TABLE_LAUNCHED,
                         lambda **_: during.append(launch_state.current().as_dict()))

        self._run(popen=lambda cmd, **k: _FakePopen(["Startup done\n"]))

        self.assertEqual([stamped(one) for one in during],
                         [{"launching": True, "game_name": "Example", "source": "api",
                           "paused": False, "game_id": None}])
        self.assertFalse(launch_state.current().launching)


class PauseTests(LaunchTests):
    """The table's own lines say when it paused, and its own key pauses it."""

    def test_the_tables_lines_are_the_paused_state(self) -> None:
        seen = []
        events.subscribe(events.PLAY_STATE_CHANGED,
                         lambda state, **_: seen.append(state["paused"]))

        self._run(popen=lambda cmd, **k: _FakePopen([
            "Startup done\n", "Pausing Game\n", "Unpausing Game\n", "Pausing Game\n"]))

        self.assertEqual(seen, [False, True, False, True, False])

    def test_a_recording_has_no_pause(self) -> None:
        seen = []
        events.subscribe(events.PLAY_STATE_CHANGED,
                         lambda state, **_: seen.append(state["paused"]))

        self._run(source=launch_state.SOURCE_CAPTURE, popen=lambda cmd, **k: _FakePopen(
            ["Startup done\n", "Pausing Game\n"]))

        self.assertNotIn(True, seen)

    def test_the_pause_key_is_the_apps_pressed_through_the_simulator(self) -> None:
        pressed: list = []

        def lines():
            yield "Startup done\n"
            pressed.append(launch_state.toggle_pause())

        def popen(cmd, **k):
            running = _FakePopen()
            running.stdout = lines()
            return running

        def press_code(code: str) -> bool:
            pressed.append(code)
            return True

        with mock.patch.object(launch.key_simulator, "press_code", side_effect=press_code), \
                mock.patch("apps.vpx.pause.VPXPause.key", return_value="KeyP"):
            self._run(popen=popen)

        self.assertEqual(pressed, ["KeyP", True])
        self.assertFalse(launch_state.toggle_pause(), "nothing runs once it has exited")


class ControlsTests(LaunchTests):
    """What the app lets be pressed is held for the table's life, and a recording has none."""

    def _held_while_running(self, **run) -> list:
        held: list = []

        def lines():
            yield "Startup done\n"
            held.append(launch_state.state_and_controls()[1])

        def popen(cmd, **k):
            running = _FakePopen()
            running.stdout = lines()
            return running

        with mock.patch("apps.vpx.pause.VPXPause.key", return_value="KeyP"):
            self._run(popen=popen, **run)
        return held

    def test_the_apps_controls_are_held_while_the_table_runs(self) -> None:
        (running,) = self._held_while_running()

        self.assertEqual(running.app, "vpx")
        self.assertIn("credit_1", [one.id for one in running.offered])
        self.assertEqual(running.key("pause"), "KeyP")
        self.assertIsNone(launch_state.state_and_controls()[1], "released once it has exited")

    def test_a_recording_has_none(self) -> None:
        self.assertEqual(self._held_while_running(source=launch_state.SOURCE_CAPTURE), [None])

    def test_they_are_held_before_the_table_process_exists(self) -> None:
        before_start: list = []

        def popen(cmd, **k):
            before_start.append(launch_state.state_and_controls()[1])
            return _FakePopen()

        self._run(popen=popen)

        self.assertIsNotNone(before_start[0])


class PlayDataTests(LaunchTests):
    """The gap that made consolidating worth doing: only the wheel used to do this."""

    def test_a_launch_from_any_source_is_recorded_as_a_play(self) -> None:
        play = self._run()

        play.increment_start_count.assert_called_once()

    def test_runtime_and_score_are_recorded_when_the_game_exits(self) -> None:
        before, after = _reading(("OWN", 300)), _reading(("OWN", 400))
        game = _game()

        play = self._run(game=game, readings=[(before, "/nv/example.nv"),
                                              (after, "/nv/example.nv")])

        play.add_play_time.assert_called_once()
        play.keep_high_scores.assert_called_once_with(game, "", after, before,
                                                      "/nv/example.nv", table_id="")
        play.delete_nvram_if_configured.assert_called_once()

    def test_a_table_with_no_rom_is_kept_under_its_score_files_name(self) -> None:
        """A first game on an EM table makes its score file, so the name is not known
        until the game is over."""
        game = _game()
        after = _reading(("OWN", 400))

        with mock.patch.object(launch.em_scores, "key_of", side_effect=["", "Scores.txt"]):
            play = self._run(game=game, readings=[(None, None), (after, "/u/Scores.txt")])

        self.assertEqual([call.args[1] for call in play.parse_score_from_nvram.call_args_list],
                         ["", "Scores.txt"])
        play.keep_high_scores.assert_called_once_with(game, "Scores.txt", after, None,
                                                     "/u/Scores.txt", table_id="")

    def test_the_played_tables_rom_is_read_and_cleared(self) -> None:
        """A game whose tables declare different ROMs: the default's is not the one a
        mod on its own ROM plays."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for name in ("Example.vpx", "Example (Mod).vpx"):
            pathlib.Path(folder.name, name).write_bytes(b"")
        game = _game()
        game.full_path_game = folder.name
        game.meta_config = {"tables": {
            "t1": {"id": "t1", "filename": "Example.vpx", "rom": "abc_l1"},
            "t2": {"id": "t2", "filename": "Example (Mod).vpx", "rom": "abc_mod"}},
            "vpinfe": {"default_table": "t1"}}
        after = _reading(("OWN", 400))

        play = self._run(game=game, readings=[(None, None), (after, "/nv")],
                         table="Example (Mod).vpx")

        self.assertEqual([call.args[1] for call in play.parse_score_from_nvram.call_args_list],
                         ["abc_mod", "abc_mod"])
        self.assertEqual(play.keep_high_scores.call_args.args[1], "abc_mod")
        self.assertEqual(play.delete_nvram_if_configured.call_args.args[1]["rom"], "abc_mod")

    def test_the_table_that_was_launched_is_the_one_credited(self) -> None:
        """A folder can hold several tables, and the API can launch any of them."""
        game = _game()
        game.full_path_vpx_file = "/games/Example/Example (VR).vpx"

        play = self._run(game=game)

        self.assertEqual(play.increment_start_count.call_args.args[1], "Example (VR).vpx")
        self.assertEqual(play.add_play_time.call_args.args[2], "Example (VR).vpx")

    def test_the_record_is_announced_after_it_is_written(self) -> None:
        """Anything that shows play data reads it on this, not on `exited` - the
        runtime and the score are written after the exit goes out."""
        seen = []
        events.subscribe(events.TABLE_EXITED, lambda **_: seen.append("exited"))
        events.subscribe(events.TABLE_PLAY_RECORDED,
                         lambda **_: seen.append("recorded"))

        self._run()

        self.assertEqual(seen, ["exited", "recorded"])

    def test_a_game_that_never_started_records_nothing_to_announce(self) -> None:
        seen = []
        events.subscribe(events.TABLE_PLAY_RECORDED, lambda **_: seen.append("recorded"))

        def boom(cmd, **kwargs):
            raise RuntimeError("popen failed")

        with self.assertRaises(RuntimeError):
            self._run(popen=boom)

        self.assertEqual(seen, [])


class RecordingTests(LaunchTests):
    """A launch to record media, which is a launch and not a play."""

    def _heard(self, source=launch_state.SOURCE_CAPTURE, **run) -> dict[str, dict]:
        heard: dict[str, dict] = {}
        for name in (events.TABLE_LAUNCHING, events.TABLE_LAUNCHED, events.TABLE_EXITED,
                     events.TABLE_PLAY_RECORDED):
            events.subscribe(name, lambda _n=name, **payload: heard.setdefault(_n, payload))
        self._run(source=source, popen=lambda cmd, **k: _FakePopen(["Startup done\n"]),
                  **run)
        return heard

    def test_a_recording_counts_no_start_and_records_no_play(self) -> None:
        play = self._run(source=launch_state.SOURCE_CAPTURE,
                         readings=[(_reading(("OWN", 300)), "/nv"),
                                   (_reading(("OWN", 400)), "/nv")])

        for name in ("increment_start_count", "add_play_time", "keep_high_scores",
                     "parse_score_from_nvram"):
            self.assertFalse(getattr(play, name).called, name)

    def test_a_recording_still_clears_nvram_where_the_game_asks(self) -> None:
        play = self._run(source=launch_state.SOURCE_CAPTURE)

        play.delete_nvram_if_configured.assert_called_once()

    def test_nobody_is_up_for_a_recording_and_no_play_is_announced(self) -> None:
        heard = self._heard()

        self.assertEqual(heard[events.TABLE_LAUNCHED]["up"], [])
        self.assertNotIn(events.TABLE_PLAY_RECORDED, heard)

    def test_a_person_s_own_commands_still_run_around_a_recording(self) -> None:
        seen = []

        def before(game, table, launcher, ini_config, up=None):
            seen.append(("pre", up))
            return table_commands.Around(ran=True)

        with mock.patch.object(table_commands, "before", before), \
                mock.patch.object(table_commands, "after",
                                  lambda around, **k: seen.append("post")):
            self._run(source=launch_state.SOURCE_CAPTURE)

        self.assertEqual(seen, [("pre", []), "post"], "and they name nobody as playing")

    def test_every_table_event_says_who_started_it(self) -> None:
        for source in (launch_state.SOURCE_API, launch_state.SOURCE_CAPTURE):
            with self.subTest(source=source):
                events.clear()

                heard = self._heard(source=source)

                self.assertEqual({name: payload["source"] for name, payload in heard.items()},
                                 dict.fromkeys(heard, source))
                self.assertEqual(heard[events.TABLE_EXITED]["table_id"],
                                 heard[events.TABLE_LAUNCHING]["table_id"])


class LaunchEnvTests(unittest.TestCase):
    def test_one_name_and_value_to_a_line_all_reach_the_launch(self) -> None:
        env = launch.parse_launch_env_overrides("FIRST=1\nSECOND='two words'\n")

        self.assertEqual(env, {"FIRST": "1", "SECOND": "two words"})


class RecordingCommandTests(LaunchTests):
    """What a recording's table is launched with, which lasts as long as the table."""

    def setUp(self) -> None:
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = pathlib.Path(folder.name)
        self.folder = root / "capture" / "launch"
        patcher = mock.patch.object(launch, "CAPTURE_LAUNCH_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings_file = root / "VPinballX.ini"
        self.settings_file.write_text("[Player]\nSyncMode = 3\n", encoding="utf-8")

    def _launch(self, source=launch_state.SOURCE_CAPTURE, app="vpx", popen=None,
                **launch_kwargs) -> dict:
        """What was run, and what the folder held while it ran."""
        from common.games.launchers import Launcher

        launcher = Launcher(launcher_id="l1", app=app, display_name="Launcher",
                            settings={"bin_path": "/opt/vpx", "args": "",
                                      "ini_path": str(self.settings_file)})
        seen: dict = {}

        def run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["held"] = sorted(one.name for one in self.folder.glob("*"))
            return _FakePopen(["Startup done\n"])

        with mock.patch.object(launch, "game_play_service") as play, \
                mock.patch.multiple(launch, _launcher_for=lambda *a: (launcher, ""),
                                    _binary_of=lambda found: "/opt/vpx"):
            play.parse_score_from_nvram.return_value = (None, None)
            launch.launch_game(_game(), cast(Any, types.SimpleNamespace(config={})),
                               source=source,
                               popen=popen or run, **launch_kwargs)
        return seen

    def test_a_recording_is_launched_through_a_copy_of_the_settings_file(self) -> None:
        seen = self._launch()

        self.assertIn(str(self.folder / "VPinballX.ini"), seen["cmd"])
        self.assertIn("VPinballX.ini", seen["held"])
        self.assertEqual(self.settings_file.read_text(encoding="utf-8"),
                         "[Player]\nSyncMode = 3\n")

    def test_the_copy_is_gone_once_the_table_closes(self) -> None:
        self._launch()

        self.assertFalse(self.folder.exists())

    def test_and_gone_when_the_launch_blows_up(self) -> None:
        def boom(cmd, **kwargs):
            raise RuntimeError("popen failed")

        with self.assertRaises(RuntimeError):
            self._launch(popen=boom)

        self.assertFalse(self.folder.exists())

    def test_one_left_behind_by_a_launch_that_never_closed_is_emptied_first(self) -> None:
        self.folder.mkdir(parents=True)
        (self.folder / "table.ini").write_text("[Player]\n", encoding="utf-8")

        seen = self._launch()

        self.assertEqual(seen["held"], ["VPinballX.ini"])

    def test_whether_the_sound_is_recorded_reaches_the_app(self) -> None:
        with mock.patch.object(launch.apps.default_app().capture, "command",
                               return_value=["/opt/vpx"]) as command:
            self._launch(record_sound=True)

        self.assertIs(command.call_args.kwargs["sound"], True)

    def test_a_play_is_launched_with_no_copy(self) -> None:
        seen = self._launch(source=launch_state.SOURCE_API)

        self.assertIn(str(self.settings_file), seen["cmd"])
        self.assertFalse(self.folder.exists())

    def test_an_app_with_no_capture_hook_is_recorded_as_it_is_played(self) -> None:
        recorded = self._launch(app="generic")["cmd"]
        played = self._launch(app="generic", source=launch_state.SOURCE_API)["cmd"]

        self.assertEqual(recorded, played)


class SessionTests(LaunchTests):
    """Who a game counts for, and whose its new scores are."""

    def _recorded(self, readings, **run) -> dict:
        heard = []
        events.subscribe(events.TABLE_PLAY_RECORDED, lambda **payload: heard.append(payload))
        self._run(readings=readings, **run)
        (payload,) = heard
        return payload

    def _alex(self) -> players.Player:
        return self.roster.add_player("Alex", "ABC")

    def _library_writes(self, play) -> list[str]:
        return [name for name in ("increment_start_count", "add_play_time")
                if getattr(play, name).called]

    def test_the_owner_s_session_goes_in_the_library(self) -> None:
        play = self._run(readings=[(_reading(("AAA", 300)), "/nv"),
                                   (_reading(("OWN", 400), ("AAA", 300)), "/nv")])

        self.assertEqual(self._library_writes(play),
                         ["increment_start_count", "add_play_time"])

    def test_the_owner_up_beside_another_player_still_goes_in_the_library(self) -> None:
        self.roster.set_who_is_up([self.owner.player_id, self._alex().player_id])

        play = self._run(readings=[(None, None), (_reading(("OWN", 400)), "/nv")])

        self.assertEqual(self._library_writes(play),
                         ["increment_start_count", "add_play_time"])

    def test_a_session_the_owner_was_not_up_for_stays_out_of_the_library(self) -> None:
        self.roster.set_who_is_up([self._alex().player_id])

        play = self._run(readings=[(_reading(("AAA", 300)), "/nv"),
                                   (_reading(("ABC", 400), ("AAA", 300)), "/nv")])

        self.assertEqual(self._library_writes(play), [])
        play.keep_high_scores.assert_called_once()

    def test_a_new_score_goes_to_whose_initials_it_carries_up_or_not(self) -> None:
        alex = self._alex()

        recorded = self._recorded([(_reading(("AAA", 300), ("BBB", 200)), "/nv"),
                                   (_reading(("AAA", 300), ("ABC", 250), ("BBB", 200)),
                                    "/nv")])

        (only,) = recorded["new_entries"]
        self.assertEqual(only["player"], alex.as_payload())
        self.assertEqual([(entry["initials"], entry["score"]) for entry in only["entries"]],
                         [("ABC", 250)])
        self.assertEqual(recorded["up"], [self.owner.as_payload()])

    def test_a_blank_score_goes_to_the_one_player_up(self) -> None:
        guest = self.roster.add_guest("GST")

        recorded = self._recorded([(_reading(("AAA", 300)), "/nv"),
                                   (_reading(("AAA", 300), ("", 200)), "/nv")])

        (only,) = recorded["new_entries"]
        self.assertEqual(only["player"], guest.as_payload())
        self.assertEqual([(entry["initials"], entry["score"]) for entry in only["entries"]],
                         [("GST", 200)])
        self.assertEqual(recorded["reading"]["entries"][1]["initials"], "GST")

    def test_question_marks_go_to_the_one_player_up_and_stay_question_marks(self) -> None:
        """Only a blank takes their initials, as 2.x filled them."""
        recorded = self._recorded([(_reading(("AAA", 300)), "/nv"),
                                   (_reading(("AAA", 300), ("???", 200), ("", 100)),
                                    "/nv")])

        self.assertEqual([entry["initials"] for entry in recorded["reading"]["entries"]],
                         ["AAA", "???", "OWN"])
        (only,) = recorded["new_entries"]
        self.assertEqual(only["player"], self.owner.as_payload())

    def test_the_machine_s_table_is_kept_as_it_wrote_it(self) -> None:
        play = self._run(readings=[(None, None), (_reading(("", 400)), "/nv")])

        self.assertEqual(play.keep_high_scores.call_args.args[2]["entries"][0]["initials"],
                         "")

    def test_a_blank_score_goes_to_nobody_with_two_up(self) -> None:
        self.roster.set_who_is_up([self.owner.player_id, self._alex().player_id])

        recorded = self._recorded([(_reading(("AAA", 300)), "/nv"),
                                   (_reading(("AAA", 300), ("", 200)), "/nv")])

        self.assertEqual(recorded["new_entries"], [])
        self.assertEqual(recorded["reading"]["entries"][1]["initials"], "")

    def test_a_one_number_reading_that_moved_goes_to_the_one_player_up(self) -> None:
        recorded = self._recorded([({"rom": "example", "value": 1000}, "/ini"),
                                   ({"rom": "example", "value": 1500}, "/ini")])

        (only,) = recorded["new_entries"]
        self.assertEqual(only["player"], self.owner.as_payload())
        self.assertEqual([(entry["initials"], entry["score"]) for entry in only["entries"]],
                         [("OWN", 1500)])

    def test_who_is_up_is_taken_as_the_game_starts(self) -> None:
        """A change of who is up mid-game is for the next one: this game's play and its
        blank scores stay with whoever was up when it began."""
        alex = self._alex()
        events.subscribe(events.TABLE_LAUNCHED,
                         lambda **_: self.roster.set_who_is_up([alex.player_id]))

        recorded = self._recorded(
            [(_reading(("", 300)), "/nv"), (_reading(("", 300), ("", 200)), "/nv")],
            popen=lambda cmd, **k: _FakePopen(["Startup done\n"]))

        self.assertEqual(recorded["up"], [self.owner.as_payload()])
        (only,) = recorded["new_entries"]
        self.assertEqual(only["player"], self.owner.as_payload())

    def test_the_table_s_commands_are_told_who_the_game_counts_for(self) -> None:
        """One reading of who is up, before the commands: a change while the launch is
        under way cannot leave `{player}` naming somebody the game did not count for."""
        alex = self._alex()
        told = []
        events.subscribe(events.TABLE_LAUNCHING,
                         lambda **_: self.roster.set_who_is_up([alex.player_id]))

        def before(game, table, launcher, ini_config, up=None):
            told.append([player.player_id for player in up or []])
            return table_commands.Around()

        with mock.patch.object(table_commands, "before", before):
            recorded = self._recorded([(None, None), (None, None)],
                                      popen=lambda cmd, **k: _FakePopen(["Startup done\n"]))

        self.assertEqual(told, [[self.owner.player_id]])
        self.assertEqual(recorded["up"], [self.owner.as_payload()])

    def test_both_events_carry_who_played_as_plain_data(self) -> None:
        heard: dict[str, Any] = {}
        for name in (events.TABLE_LAUNCHED, events.TABLE_PLAY_RECORDED):
            events.subscribe(name, lambda _n=name, **payload: heard.setdefault(_n, payload))
        game = _game()
        game.meta_config = {"vpinfe": {"game_id": "g1"},
                            "tables": {"t1": {"id": "t1", "filename": "Example.vpx"}}}

        self._run(game=game, popen=lambda cmd, **k: _FakePopen(["Startup done\n"]),
                  readings=[(_reading(("AAA", 300)), "/nv"),
                            (_reading(("OWN", 400), ("AAA", 300)), "/nv")])

        launched, recorded = heard[events.TABLE_LAUNCHED], heard[events.TABLE_PLAY_RECORDED]
        self.assertIs(recorded["game"], game, "what subscribers had before is still there")
        self.assertIn("ini_config", recorded)
        self.assertEqual(launched["up"], [self.owner.as_payload()])
        self.assertEqual((launched["table_id"], launched["game_id"]), ("t1", "g1"))
        plain = {key: value for key, value in recorded.items()
                 if key not in ("game", "ini_config")}
        self.assertEqual(sorted(plain),
                         ["game_id", "new_entries", "private", "reading", "seconds",
                          "source", "table_id", "up"])
        self.assertEqual(json.loads(json.dumps(plain)), plain)
        self.assertEqual((plain["table_id"], plain["game_id"]), ("t1", "g1"))
        self.assertIsInstance(plain["seconds"], int)
        self.assertEqual(plain["new_entries"][0]["player"]["initials"], "OWN")
        self.assertIs(launched["private"], False)
        self.assertIs(plain["private"], False)


class RecordTests(LaunchTests):
    """A session's play and scores in the record of each player who is not the owner."""

    def setUp(self) -> None:
        super().setUp()
        self.kept = self.roster.add_player("Jordan", "ABC")
        self.game = _game()
        self.game.meta_config = {"vpinfe": {"game_id": "g1"}, "tables": {
            "t1": {"id": "t1", "filename": "Example.vpx", "rom": "example"}}}

    def _play(self, readings=None, game=None, **run):
        return self._run(game=game or self.game, readings=readings, **run)

    def _theirs(self, player: players.Player) -> dict:
        return player_records.get_records().game(player, "g1")

    def test_a_player_s_session_lands_in_their_record_and_not_the_library(self) -> None:
        self.roster.set_who_is_up([self.kept.player_id])

        play = self._play()

        self.assertFalse(play.increment_start_count.called)
        self.assertFalse(play.add_play_time.called)
        record = self._theirs(self.kept)
        self.assertEqual(record["play_count"], 1)
        self.assertIsNotNone(record["last_played"])
        self.assertTrue((self.records_dir / f"{self.kept.player_id}.json").exists())

    def test_up_beside_the_owner_both_records_move(self) -> None:
        self.roster.set_who_is_up([self.owner.player_id, self.kept.player_id])

        play = self._play()

        play.increment_start_count.assert_called_once()
        play.add_play_time.assert_called_once()
        self.assertEqual(self._theirs(self.kept)["play_count"], 1)

    def test_the_owner_alone_leaves_every_other_record_alone(self) -> None:
        self._play()

        self.assertFalse(self.records_dir.exists())

    def test_a_score_made_by_a_player_who_was_not_up_is_their_best(self) -> None:
        self._play(readings=[(_reading(("AAA", 300)), "/nv"),
                             (_reading(("ABC", 450), ("AAA", 300)), "/nv")])

        record = self._theirs(self.kept)
        self.assertEqual(record["play_count"], 0, "a score is not a play")
        self.assertEqual((record["best_score"]["initials"], record["best_score"]["score"],
                          record["best_score"]["rom"]), ("ABC", 450, "example"))

    def test_a_guest_s_session_is_held_and_gone_once_they_sign_out(self) -> None:
        guest = self.roster.add_guest("GST")

        self._play(readings=[(_reading(("AAA", 300)), "/nv"),
                             (_reading(("AAA", 300), ("", 200)), "/nv")])

        record = self._theirs(guest)
        self.assertEqual((record["play_count"], record["best_score"]["initials"]),
                         (1, "GST"))
        self.assertFalse(self.records_dir.exists())
        self.roster.remove(guest.player_id)
        player_records.get_records().forget(guest.player_id)
        self.assertEqual(player_records.get_records().games(guest), {})

    def test_a_player_removed_during_the_game_is_not_written_back(self) -> None:
        self.roster.set_who_is_up([self.kept.player_id])

        def leaves(**_launched) -> None:
            self.roster.remove(self.kept.player_id)
            player_records.get_records().forget(self.kept.player_id)
        events.subscribe(events.TABLE_LAUNCHED, leaves)
        heard = []
        events.subscribe(events.TABLE_PLAY_RECORDED, lambda **payload: heard.append(payload))

        self._play(popen=lambda cmd, **k: _FakePopen(["Startup done\n"]))

        self.assertFalse((self.records_dir / f"{self.kept.player_id}.json").exists())
        self.assertEqual(len(heard), 1, "the game is still announced")

    def test_a_game_with_no_id_records_nothing_for_them_and_still_ends(self) -> None:
        self.roster.set_who_is_up([self.kept.player_id])
        heard = []
        events.subscribe(events.TABLE_PLAY_RECORDED, lambda **payload: heard.append(payload))

        self._play(game=_game())

        self.assertFalse(self.records_dir.exists())
        self.assertEqual(len(heard), 1)



class PrivateTests(LaunchTests):
    """Whether the game played is Private, which a community extension reads before it
    sends anything."""

    def _heard(self, game, **run) -> dict[str, dict]:
        heard: dict[str, dict] = {}
        for name in (events.TABLE_LAUNCHED, events.TABLE_PLAY_RECORDED):
            events.subscribe(name, lambda _n=name, **payload: heard.setdefault(_n, payload))
        self._run(game=game, popen=lambda cmd, **k: _FakePopen(["Startup done\n"]), **run)
        return heard

    def _on_disk(self, private: bool):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        game = _game()
        game.full_path_game = folder.name
        game.meta_config = {"vpinfe": {"game_id": "g1", "private": False}}
        info = pathlib.Path(folder.name) / f"{game.game_dir_name}.info"
        info.write_text(json.dumps({"vpinfe": {"schema": 2, "game_id": "g1",
                                                "private": private}}), encoding="utf-8")
        return game, info

    def test_both_events_say_a_private_game_is_private(self) -> None:
        game = _game()
        game.meta_config = {"vpinfe": {"game_id": "g1", "private": True}}

        heard = self._heard(game)

        self.assertIs(heard[events.TABLE_LAUNCHED]["private"], True)
        self.assertIs(heard[events.TABLE_PLAY_RECORDED]["private"], True)

    def test_the_file_answers_over_the_record_held_since_the_launch(self) -> None:
        game, _info = self._on_disk(private=True)

        heard = self._heard(game)

        self.assertIs(heard[events.TABLE_LAUNCHED]["private"], True)
        self.assertIs(heard[events.TABLE_PLAY_RECORDED]["private"], True)

    def test_a_game_marked_private_during_play_is_private_when_it_ends(self) -> None:
        game, info = self._on_disk(private=False)
        events.subscribe(events.TABLE_LAUNCHED, lambda **_: info.write_text(json.dumps(
            {"vpinfe": {"schema": 2, "game_id": "g1", "private": True}}), encoding="utf-8"))

        heard = self._heard(game)

        self.assertIs(heard[events.TABLE_LAUNCHED]["private"], False)
        self.assertIs(heard[events.TABLE_PLAY_RECORDED]["private"], True)

    def test_a_file_that_cannot_be_read_leaves_the_record_held_to_answer(self) -> None:
        game, info = self._on_disk(private=False)
        game.meta_config["vpinfe"]["private"] = True
        info.write_text("{ not json", encoding="utf-8")

        heard = self._heard(game)

        self.assertIs(heard[events.TABLE_PLAY_RECORDED]["private"], True)


class RefusalTests(LaunchTests):
    def _check(self, game=None, table=None, launcher_exists=True, launcher=True):
        found = _launcher("/opt/vpx" if launcher_exists else "/nope/vpx") \
            if launcher else None
        with mock.patch.object(launch, "_launcher_for",
                               lambda game, vpx_path: (found, "")), \
                mock.patch.object(launch, "resolve_launcher_path",
                                  lambda value: types.SimpleNamespace(
                                      exists=lambda: launcher_exists)):
            return launch.check_launchable(game or _game(),
                                           types.SimpleNamespace(config={}), table)

    def test_no_launcher_configured_is_refused_with_a_reason(self) -> None:
        """An install with none at all. The sentence has to point at where one is made,
        because there is nothing on this machine to correct."""
        with self.assertRaises(launch.LaunchUnavailableError) as caught:
            self._check(launcher=False)

        self.assertIn("Frontend - Launchers", str(caught.exception))

    def test_a_launcher_that_is_not_there_is_refused(self) -> None:
        with self.assertRaises(launch.LaunchUnavailableError) as caught:
            self._check(launcher_exists=False)

        self.assertIn("Visual Pinball X", str(caught.exception),
                      "the refusal names the launcher, not a config key")

    def test_a_second_launch_is_refused_while_one_is_running(self) -> None:
        """Two VPX processes would fight over the same hardware."""
        launch_state.set_launching("Example", source=launch_state.SOURCE_FRONTEND)

        with self.assertRaises(launch.LaunchBusyError):
            self._check()

    def test_a_table_the_game_does_not_have_is_refused(self) -> None:
        """Named files are checked against the folder, so this cannot reach outside it."""
        with mock.patch.object(launch.os.path, "isdir", return_value=True), \
                mock.patch.object(launch.os, "listdir", return_value=["Example.vpx"]), \
                mock.patch.object(launch.os.path, "isfile", return_value=True):
            with self.assertRaises(launch.UnknownTableError):
                self._check(table="../../etc/passwd")

    def test_a_table_the_game_does_have_is_accepted(self) -> None:
        with mock.patch.object(launch.os.path, "isdir", return_value=True), \
                mock.patch.object(launch.os, "listdir", return_value=["Other.vpx"]), \
                mock.patch.object(launch.os.path, "isfile", return_value=True):
            resolved = self._check(table="Other.vpx")

        self.assertTrue(resolved.endswith("Other.vpx"))

    def test_the_default_is_the_game_s_own_file(self) -> None:
        self.assertEqual(PurePath(self._check()).parts[-3:],
                         ("games", "Example", "Example.vpx"))


class FallbackTests(LaunchTests):
    def test_a_table_on_a_switched_off_launcher_says_so_once_at_launch(self) -> None:
        """Checked, then launched, the way the API and the Remote page both do it."""
        from common.games.launchers import LauncherStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        program = pathlib.Path(tmp.name, "vpx")
        program.touch()
        store = LauncherStore(tmp.name)
        off = _launcher().__class__(launcher_id="l2", app="vpx", display_name="VPX 10.7",
                                    enabled=False, settings={"bin_path": "/opt/old"})
        store.save([_launcher(str(program)), off], {"t1": "l2"})
        game = _game()
        game.meta_config = {"tables": {"t1": {"id": "t1", "filename": "Example.vpx"}}}

        with mock.patch.object(launch.launchers, "get_launcher_store",
                               return_value=store), \
                self.assertLogs("vpinfe.common.host.launch", "WARNING") as logged:
            launch.check_launchable(game, cast(Any, types.SimpleNamespace(config={})))
            self._run(game=game, _launcher_for=launch._launcher_for,
                      _binary_of=launch._binary_of)

        self.assertEqual(len(logged.output), 1)
        self.assertIn("Example.vpx names launcher VPX 10.7, which is switched off; "
                      "launching with Visual Pinball X instead", logged.output[0])


class LaunchedByTests(unittest.TestCase):
    """What a launch of a table would run, asked without launching it."""

    def setUp(self) -> None:
        from common.games.launchers import LauncherStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LauncherStore(tmp.name)
        self.store.save([], {})
        patcher = mock.patch.object(launch.launchers, "get_launcher_store",
                                    return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_table_is_asked_of_its_own_launcher_with_its_own_file(self) -> None:
        mapped = _launcher().__class__(launcher_id="l2", app="vpx", display_name="Other",
                                       settings={"bin_path": "/opt/other",
                                                 "ini_path": "/opt/other.ini"})
        self.store.save([_launcher(), mapped], {"t1": "l2"})
        game = _game()
        game.meta_config = {"tables": {"t1": {"id": "t1", "filename": "Example.vpx"}}}

        found = launch.launched_by(game)
        assert found is not None
        app, entry, settings = found

        self.assertEqual(app.id, "vpx")
        self.assertEqual((entry.entry_id, entry.table),
                         ("t1", os.path.join("/games/Example", "Example.vpx")))
        self.assertEqual((settings["bin_path"], settings["ini_path"]),
                         (str(pathlib.Path("/opt/other")), "/opt/other.ini"))

    def test_with_no_game_it_is_the_default_launcher_and_no_entry(self) -> None:
        self.store.save([_launcher()], {})

        found = launch.launched_by()
        assert found is not None
        app, entry, settings = found

        self.assertEqual((app.id, entry, settings["bin_path"]),
                         ("vpx", launch.apps.Entry(), str(pathlib.Path("/opt/vpx"))))

    def test_nothing_to_launch_with_is_none(self) -> None:
        self.assertIsNone(launch.launched_by())


def _keyed_game(app="generic", key="mm"):
    """A game whose only entry has no file at all - a ROM its emulator looks up, a
    Pinball FX table id. The folder holds the record and the media and nothing else."""
    return types.SimpleNamespace(
        full_path_vpx_file="",
        full_path_game="/games/Medieval Madness",
        game_dir_name="Medieval Madness",
        meta_config={"tables": {"t9": {"id": "t9", "app": app, "key": key}}},
        location_id="",
    )


class KeyedEntryTests(LaunchTests):
    """An entry with no file. Nothing about a key says whose it is, so the entry names
    its app - and that is what picks the launcher, in place of a suffix that is not
    there."""

    def _run_keyed(self, popen=None, table=None):
        found = _launcher("/opt/fx")
        found = found.__class__(launcher_id="l2", app="generic",
                                display_name="Generic", settings={"bin_path": "/opt/fx"})
        with mock.patch.object(launch.launchers, "get_launcher_store",
                               return_value=types.SimpleNamespace(
                                   launchers=lambda: [found],
                                   mappings=lambda: {},
                                   mapped=lambda _id: "")), \
                mock.patch.object(launch, "resolve_launcher_path",
                                  lambda value: types.SimpleNamespace(
                                      exists=lambda: True, __str__=lambda s: "/opt/fx")):
            return launch.check_launchable(_keyed_game(),
                                           types.SimpleNamespace(config={}), table)

    def test_a_game_with_no_file_is_still_launchable(self) -> None:
        """The one thing that must not happen: a folder holding only a keyed entry
        reading as a game nothing can play."""
        self.assertEqual(self._run_keyed(), "generic:mm")

    def test_it_can_be_named_by_the_key_its_app_knows_it_by(self) -> None:
        self.assertEqual(self._run_keyed(table="generic:mm"), "generic:mm")

    def test_a_key_this_game_does_not_have_is_refused(self) -> None:
        with self.assertRaises(launch.UnknownTableError):
            self._run_keyed(table="generic:nosuchrom")

    def test_the_launcher_comes_from_the_app_the_entry_declares(self) -> None:
        """Not from a suffix. There is no filename to take one off."""
        found = _launcher().__class__(launcher_id="l2", app="generic",
                                      display_name="Generic",
                                      settings={"bin_path": "/opt/fx"})
        with mock.patch.object(launch.launchers, "get_launcher_store",
                               return_value=types.SimpleNamespace(
                                   launchers=lambda: [found],
                                   mappings=lambda: {},
                                   mapped=lambda _id: "")):
            launcher, _asked = launch._launcher_for(
                "t9", {"app": "generic", "key": "mm"})

        self.assertIs(launcher, found)

    def test_the_command_is_built_from_the_key_and_not_a_path(self) -> None:
        entry = launch.apps.Entry(entry_id="t9", key="mm",
                                  game_dir="/games/Medieval Madness")
        found = _launcher().__class__(launcher_id="l2", app="generic",
                                      display_name="Generic",
                                      settings={"bin_path": "/opt/fx", "args": ""})

        cmd, marker = launch._plan(entry, "/opt/fx", found)

        self.assertEqual(cmd, ["/opt/fx", "mm"])
        self.assertEqual(marker, "", "a generic program cannot say when it is up")


class ReferencedEntryTests(LaunchTests):
    """A table that lives somewhere else - a .vpx on a share, or one file shared by
    several games. The record and the media are here; the file is not."""

    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.elsewhere = os.path.join(self.tmp.name, "shared", "afm.vpx")
        os.makedirs(os.path.dirname(self.elsewhere))
        pathlib.Path(self.elsewhere).touch()
        self.game_dir = os.path.join(self.tmp.name, "games", "AFM")
        os.makedirs(self.game_dir)

    def _game_with(self, reference: str):
        return types.SimpleNamespace(
            full_path_vpx_file="", full_path_game=self.game_dir, game_dir_name="AFM",
            meta_config={"tables": {"r1": {"id": "r1", "path": reference}}},
            location_id="")

    def _check(self, game, table=None):
        found = _launcher()
        with mock.patch.object(launch, "_launcher_for",
                               lambda table_id, entry: (found, "")), \
                mock.patch.object(launch, "resolve_launcher_path",
                                  lambda value: types.SimpleNamespace(
                                      exists=lambda: True)):
            return launch.check_launchable(game, types.SimpleNamespace(config={}), table)

    def test_an_absolute_reference_launches_what_it_points_at(self) -> None:
        self.assertEqual(self._check(self._game_with(self.elsewhere)), self.elsewhere)

    def test_a_relative_one_is_anchored_on_the_game_folder(self) -> None:
        """Which is what lets a library be moved or shared as one piece."""
        got = self._check(self._game_with("../../shared/afm.vpx"))

        self.assertEqual(got, os.path.normpath(self.elsewhere))

    def test_it_can_be_named_by_the_path_the_record_holds(self) -> None:
        self.assertEqual(self._check(self._game_with(self.elsewhere), self.elsewhere),
                         self.elsewhere)

    def test_a_reference_to_nothing_is_refused_as_unreachable(self) -> None:
        """Not as missing. Nothing here is lost - the usual cause is a share that has
        not mounted, and offering to forget the entry would be the wrong answer."""
        with self.assertRaises(launch.ReferenceUnreachableError) as caught:
            self._check(self._game_with("/nowhere/at/all.vpx"))

        self.assertIn("not reachable", str(caught.exception))

    def test_and_that_refusal_is_still_a_launch_refusal(self) -> None:
        """So every caller that already handles one keeps working."""
        self.assertTrue(issubclass(launch.ReferenceUnreachableError,
                                   launch.LaunchUnavailableError))

    def test_the_file_it_points_at_says_which_app_plays_it(self) -> None:
        """A reference names a file, so it answers the same way a table in the folder
        does - there is no `app` on the record and none is needed."""
        self.assertEqual(tables.app_of({"path": self.elsewhere}), "vpx")

    def test_the_command_is_built_from_the_resolved_path(self) -> None:
        entry = launch.apps.Entry(entry_id="r1", table=self.elsewhere,
                                  game_dir=self.game_dir)
        found = _launcher().__class__(launcher_id="l3", app="generic",
                                      display_name="Generic",
                                      settings={"bin_path": "/opt/x", "args": ""})

        cmd, _marker = launch._plan(entry, "/opt/x", found)

        self.assertEqual(cmd, ["/opt/x", self.elsewhere])


class LocationNotConnectedTests(LaunchTests):
    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = LocationStore(pathlib.Path(tmp.name) / "config")
        store.put(Location("nas", str(pathlib.Path("/mnt/launch-nas/tables").absolute())))
        self.origin = mounts.Origin(mounts.NFS, "nas.lan", "/export", "/mnt/launch-nas")
        for patcher in (
                mock.patch.object(launch.library_folders, "get_location_store",
                                  return_value=store),
                mock.patch.object(mounts, "where",
                                  return_value=mounts.Where(self.origin, connected=False))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.game = _game()
        self.game.location_id = "nas"

    def test_the_frontend_s_launch_is_refused_before_anything_starts(self) -> None:
        started: list[Any] = []
        with self.assertRaises(launch.ReferenceUnreachableError) as caught:
            self._run(game=self.game, popen=_recording_popen(started))

        self.assertEqual(started, [])
        self.assertIn("launch-nas/tables", str(caught.exception))

    def test_the_check_before_a_launch_refuses_it_too(self) -> None:
        with self.assertRaises(launch.ReferenceUnreachableError):
            launch.check_launchable(self.game,
                                    cast(Any, types.SimpleNamespace(config={})))


if __name__ == "__main__":
    unittest.main()

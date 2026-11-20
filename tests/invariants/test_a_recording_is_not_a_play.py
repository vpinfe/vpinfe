"""A launch to record media is not a play: it writes no play record, no start count and no
player session, and leaves the wheel's last game where it was.

Two halves, because either alone passes while the rule is broken. The launch is driven
through the real path over a real game folder, with a play launch beside it showing that
every check sees a write. And every call in the tree to something that writes play data
has to be one that launch drives, so a new writer cannot be added out of its sight.
"""

from __future__ import annotations

import ast
import json
import unittest
from configparser import ConfigParser
from pathlib import Path
from unittest import mock

from common import events
from common.config.config_store import ConfigStore
from common.devices import device_registry
from common.games import player_records
from common.games.launchers import Launcher
from common.host import launch, launch_state
from common.players import players
from frontend import play_events
from tests.support import trees
from tests.support.library import TempTree, fake_game, game_info, write_game

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("apps", "common", "manager", "extensions", "frontend", "httpapi")

PLAY_SERVICE = REPO / "common" / "games" / "game_play_service.py"
# What writes a game's record. A play-service function calling one writes play data.
RECORD_WRITERS = frozenset({"persist_game_meta", "keep_game_meta"})
# What writes a session into the record of a player other than the owner.
SESSION_WRITERS = frozenset({"count_start", "add_time", "offer_scores"})
LAST_GAME_WRITER = "save_last_launched"

# Every function in the tree that calls a play-data writer. The launches below go
# through each of them.
DRIVEN = {
    ("common/host/launch.py", "launch_game"),
    ("common/host/launch.py", "_record_play"),
    ("frontend/play_events.py", "on_launching"),
}

def _called(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _play_data_writers() -> frozenset[str]:
    tree = trees.tree_for(PLAY_SERVICE)
    return frozenset(
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        and any(isinstance(call, ast.Call) and _called(call) in RECORD_WRITERS
                for call in ast.walk(node)))


def _writes_play_data(call: ast.Call, writers: frozenset[str]) -> bool:
    name = _called(call)
    return name in writers or name in SESSION_WRITERS or name == LAST_GAME_WRITER


def _callers_of(path: Path, writers: frozenset[str]) -> set[tuple[str, str]]:
    relative = path.relative_to(REPO).as_posix()
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inside = (child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                      else function)
            if isinstance(child, ast.Call) and _writes_play_data(child, writers):
                found.add((relative, function or "<module>"))
            visit(child, inside)

    visit(trees.tree_for(path), "")
    return found


def _tree_callers() -> set[tuple[str, str]]:
    writers = _play_data_writers()
    found: set[tuple[str, str]] = set()
    for root in SOURCE_ROOTS:
        for path in sorted(trees.source_files(REPO / root)):
            if path != PLAY_SERVICE:
                found |= _callers_of(path, writers)
    return found


class EveryWriterIsDrivenTests(unittest.TestCase):
    def test_the_play_service_has_writers_to_look_for(self) -> None:
        self.assertIn("increment_start_count", _play_data_writers())

    def test_every_call_that_writes_play_data_is_one_the_launch_goes_through(self) -> None:
        found = _tree_callers()

        self.assertEqual(
            found - DRIVEN, set(),
            "These write play data outside the calls a recording is driven through "
            "below. Drive a capture launch through each one in this file, or show it "
            "cannot be reached from one, and list it in DRIVEN")
        self.assertEqual(DRIVEN - found, set(),
                         "Listed in DRIVEN, and no longer writes play data")


class _Bridge:
    def send_event_all_with_iframe(self, message: dict) -> None:
        pass


class ARecordingIsNotAPlayTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        for clean in (events.clear, launch_state.clear, play_events.reset_for_tests):
            clean()
            self.addCleanup(clean)
        players.reset_for_tests(self.root / "players.json")
        self.addCleanup(players.reset_for_tests)
        player_records.reset_for_tests(self.root / "player_records")
        self.addCleanup(player_records.reset_for_tests)
        roster = players.get_roster()
        owner = roster.ensure_owner(ConfigParser())
        assert owner is not None
        roster.update_player(owner.player_id, initials="OWN")
        kept = roster.add_player("Jordan", "ABC")
        roster.set_who_is_up([owner.player_id, kept.player_id])

        self.ini = ConfigStore(str(self.root / "vpinfe.ini"))
        play_events.register(_Bridge(), None, self.ini)  # type: ignore[arg-type]

        info = game_info(game_id="Gme1111111",
                         tables={"t1": {"id": "t1", "filename": "Example.vpx"}})
        folder = write_game(self.root / "tables", info=info)
        self.game = fake_game(folder, meta=json.loads(
            (folder / "Example.info").read_text(encoding="utf-8")))

        self.recorded: list[dict] = []
        events.subscribe(events.TABLE_PLAY_RECORDED,
                         lambda **payload: self.recorded.append(payload))

    def _files(self) -> dict[str, bytes]:
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in sorted(self.root.rglob("*")) if path.is_file()}

    @staticmethod
    def _last_game() -> str:
        return device_registry.get_device_registry().self_get("last_table", "")

    def _launch(self, source: str) -> dict[str, bytes]:
        """What changed on disk, file by file. Only resolving the program is stubbed;
        the program is a process that prints its startup marker and exits."""
        before = self._files()
        launcher = Launcher(launcher_id="l1", app="vpx", display_name="Visual Pinball X",
                            settings={"bin_path": "/opt/vpx"})
        with mock.patch.multiple(
                launch,
                _launcher_for=lambda table_id, entry: (launcher, ""),
                _binary_of=lambda found: "/opt/vpx",
                _log_files_of=lambda found, binary: (),
                _plan=lambda *args, **kwargs: (["/opt/vpx", "-play", "Example.vpx"],
                                               "Startup done")):
            launch.launch_game(self.game, self.ini, source=source,
                               popen=lambda cmd, **kwargs: _Program())  # type: ignore[arg-type]
        after = self._files()
        return {name: after.get(name, b"") for name in before.keys() | after.keys()
                if before.get(name) != after.get(name)}

    def test_a_play_writes_everything_the_recording_is_checked_for(self) -> None:
        changed = self._launch(launch_state.SOURCE_API)

        self.assertIn("tables/Example/Example.info", changed, "the start count")
        self.assertNotEqual(self._last_game(), "", "the last game")
        self.assertTrue(any(name.startswith("player_records/") for name in changed),
                        "the other player's session")
        self.assertEqual(len(self.recorded), 1)

    def test_a_recording_writes_none_of_it(self) -> None:
        changed = self._launch(launch_state.SOURCE_CAPTURE)

        self.assertEqual(sorted(changed), [])
        self.assertEqual(self._last_game(), "")
        self.assertEqual(self.recorded, [])


class _Program:
    def __init__(self) -> None:
        self.stdout = ["Startup done\n"]
        self.returncode = 0

    def wait(self) -> int:
        return self.returncode

    def poll(self) -> int:
        return self.returncode


if __name__ == "__main__":
    unittest.main()

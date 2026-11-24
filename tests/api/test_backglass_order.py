"""The order a table draws its backglass and score display in follows what its backglass
file places: the file's DMD switch, its undo, and the launcher's own order all settle it."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from starlette.testclient import TestClient

import httpapi
from apps.vpx import screens as vs
from apps.vpx.config import VPXConfig
from common import failures, service_errors
from common.apps.contract import Program, TableOrder
from common.games import (
    backglass_ops,
    backglass_order,
    game_identity,
    launcher_ops,
    order_record,
)
from common.games.formats import directb2s_parser
from common.host import launch_state
from common.media import sized_media
from common.runtime import jobs
from tests.api.test_backglass_picture import FOLDER, GAME_ID, _directb2s
from tests.apps import test_vpx_screens as vs_tests
from tests.support.library import TempTree, fake_game, write_game

PUT = f"/games/{GAME_ID}/assets/backglass/displays"
UNDO = f"{PUT}/undo"
FILE = "t1.directb2s"
SECOND = "t2.directb2s"
TABLE = "t1.vpx"
SCORE = {"target": "ScoreView", "x": 2, "y": 3, "w": 12, "h": 4}
SWAPPED = {"ScoreView.Priority.B2S": "2", "ScoreView.Priority.Nixie": "1"}
MOVED = {"ScoreView.Priority.B2S": "5", "ScoreView.Priority.Nixie": "1"}
CLEARED = {"ScoreView.Priority.B2S": "", "ScoreView.Priority.Nixie": ""}


def _finished(job: jobs.Job | None) -> jobs.Job:
    assert job is not None
    for _ in range(500):
        if job.state != jobs.RUNNING:
            break
        threading.Event().wait(0.01)
    return job


class Settled(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Title": "Example"},
                "vpinfe": {"schema": 2, "id": GAME_ID, "default_table": "t1"},
                "tables": {"t1": {"id": "t1", "filename": "t1.vpx"},
                           "t2": {"id": "t2", "filename": "t2.vpx"},
                           "t3": {"id": "t3", "filename": "t3.vpx"}}}
        glass = _directb2s(size=(40, 30), scoreview=(24, 8))
        self.folder = write_game(
            self.root, FOLDER, info=info, vpx=False,
            files={"t1.vpx": b"vpx", FILE: glass, "t2.vpx": b"vpx", SECOND: glass,
                   "t3.vpx": b"vpx"})
        self.rows = [
            {"id": "t1", "launcher": "L1", "launcher_app_configurable": True,
             "filename": "t1.vpx"},
            {"id": "t2", "launcher": "L1", "launcher_app_configurable": True,
             "filename": "t2.vpx"},
            {"id": "t3", "launcher": "", "launcher_app_configurable": False,
             "filename": "t3.vpx"}]
        self.written: list[tuple[str, dict[str, str]]] = []
        self.asked: list[tuple[str, bool, dict[str, str]]] = []
        self.refusing = False
        self.moved = False
        self.unknown = False
        self.flags: list[bool] = []
        self.running = launch_state.LaunchState()
        self.catalog_reads = 0
        game = self.game = fake_game(self.folder, FOLDER, meta=info)
        for patcher in (
                patch("common.games.game_repository.catalog", side_effect=self._catalog(game)),
                patch("common.games.game_repository.all_games", return_value=[game]),
                patch.object(sized_media, "ROOT", self.root / "sized"),
                patch.object(directb2s_parser, "ROOT", self.root / "glass"),
                patch.object(order_record, "PATH", self.root / "draw_order.json"),
                patch.object(launch_state, "current", side_effect=lambda: self.running),
                patch.object(backglass_ops, "_table_rows", side_effect=lambda *_a: self.rows),
                *self.app()):
            patcher.start()
            self.addCleanup(patcher.stop)
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        directb2s_parser._facts.clear()
        self.addCleanup(directb2s_parser._facts.clear)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def app(self) -> list[Any]:
        """What stands in for the launcher's app: it says what each table holds and writes it."""
        return [patch.object(launcher_ops, "backglass_order", side_effect=self._order),
                patch.object(launcher_ops, "write_table_settings", side_effect=self._write)]

    def _catalog(self, game: Any) -> Any:
        def read() -> dict[str, Any]:
            self.catalog_reads += 1
            return {GAME_ID: game}
        return read

    def _order(self, launcher_id: str, target: str, in_file: bool,
               written: dict[str, str] | None = None) -> TableOrder:
        name = Path(target).name
        self.asked.append((name, in_file, dict(written or {})))
        if self.unknown:
            return TableOrder()
        if in_file and written and self.moved:
            return TableOrder(write=dict(MOVED), ours=dict(MOVED))
        if in_file:
            return (TableOrder(ours=SWAPPED) if written == SWAPPED
                    else TableOrder(write=dict(SWAPPED), ours=dict(SWAPPED)))
        return TableOrder(write=dict(CLEARED), ours={}) if written else TableOrder()

    def _write(self, table_id: str, values: dict[str, str], settle: bool = True) -> dict[str, Any]:
        if self.refusing:
            raise service_errors.BlockedError("read-only")
        self.flags.append(settle)
        self.written.append((table_id, dict(values)))
        return {"written": sorted(values), "cleared": []}

    def put(self, body: dict | None = None) -> Any:
        return self.client.put(PUT, params={"path": FILE}, json=body or SCORE)


class FollowingTheFile(Settled):
    def test_a_score_view_dmd_gives_the_table_that_reads_the_file_its_order(self) -> None:
        response = self.put()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.asked, [(TABLE, True, {})])
        self.assertEqual(self.written, [("t1", SWAPPED)])
        self.assertEqual(order_record.held(), {"t1": SWAPPED})

    def test_a_table_whose_file_places_no_dmd_and_has_no_record_is_never_asked(self) -> None:
        self.put()

        self.assertNotIn("t2.vpx", {name for name, _, _ in self.asked})

    def test_a_table_with_no_launcher_of_its_own_is_never_asked(self) -> None:
        self.put()

        self.assertEqual(self.written and {table for table, _ in self.written}, {"t1"})

    def test_a_backglass_window_dmd_is_not_a_score_view_dmd(self) -> None:
        self.put({"target": "Backglass", "x": 1, "y": 1, "w": 9, "h": 3})

        self.assertEqual(self.asked, [])
        self.assertEqual(self.written, [])
        self.assertEqual(order_record.held(), {})

    def test_taking_the_dmd_out_asks_again_with_the_pair_that_was_recorded(self) -> None:
        self.put()
        self.asked.clear()

        self.client.put(PUT, params={"path": FILE}, json={"target": "ScoreView"})

        self.assertEqual(self.asked, [(TABLE, False, SWAPPED)])
        self.assertEqual(self.written[-1], ("t1", CLEARED))
        self.assertEqual(order_record.held(), {})

    def test_undo_settles_the_tables_again(self) -> None:
        token = self.put().json()["undo"]
        self.asked.clear()

        response = self.client.post(UNDO, json={"undo": token})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.asked, [(TABLE, False, SWAPPED)])

    def test_a_table_that_cannot_be_written_is_named_and_does_not_fail_the_change(self) -> None:
        self.refusing = True

        response = self.put()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["displays"], [SCORE])
        self.assertEqual(response.json()["order_failed"],
                         [{"table": "t1", "name": TABLE, "reason": "read-only"}])
        self.assertEqual(order_record.held(), {}, "what was not written is not recorded")

    def test_a_change_that_leaves_every_table_written_names_none(self) -> None:
        self.assertEqual(self.put().json()["order_failed"], [])

    def test_a_refused_change_settles_nothing(self) -> None:
        with patch("common.games.backglass_displays.file_move.playing", return_value=True):
            response = self.put()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.asked, [])


class OnTheSettingsFiles(Settled):
    """The same, with the program's own settings files in place of the stand-in for the app:
    what is written is the table's `.ini` beside it, and what is read is that and the
    launcher's."""

    RANKS = ("[ScoreView]\nPriority.PUP = 3\nPriority.Nixie = 2\nPriority.B2S = 1\n"
             "\n[Plugin.B2S]\nEnable = 1\n")

    def app(self) -> list[Any]:
        self.app_ini = self.root / "VPinballX.ini"
        self.app_ini.write_text(self.RANKS, encoding="utf-8")
        self.failing_ini = False
        original = Path.write_text

        def write_text(path: Path, *args: Any, **kwargs: Any) -> int:
            if self.failing_ini and path.suffix == ".ini":
                raise PermissionError("read-only")
            return original(path, *args, **kwargs)

        launcher = SimpleNamespace(app="vpx", launcher_id="L1")
        return [
            patch.object(Path, "write_text", write_text),
            patch.object(VPXConfig, "program", return_value=Program(
                frozenset({"B2S", "Nixie", "PUP"}), {}, 6300, dmd_in_file=True)),
            patch.object(launcher_ops, "launcher_or_refuse", return_value=launcher),
            patch.object(launcher_ops, "_launcher_of", return_value=launcher),
            patch.object(launcher_ops, "_app_settings_surface", return_value=VPXConfig()),
            patch.object(launcher_ops, "_launcher_settings",
                         return_value={"ini_path": str(self.app_ini)}),
            patch.object(launcher_ops, "_game_file",
                         side_effect=lambda table: str(self.folder / f"{table}.vpx")),
            patch.object(launcher_ops, "_writable_before_writing")]

    @property
    def own(self) -> Path:
        return self.folder / "t1.ini"

    def test_a_dmd_in_the_file_writes_the_swapped_pair_beside_the_table_and_records_it(
            self) -> None:
        self.put()

        self.assertEqual(self.own.read_text(encoding="utf-8").replace("\r\n", "\n"),
                         "[ScoreView]\nPriority.B2S = 2\nPriority.Nixie = 1\n")
        self.assertEqual(order_record.held(), {"t1": SWAPPED})
        self.assertEqual(self.app_ini.read_text(encoding="utf-8"), self.RANKS)

    def test_a_write_that_fails_keeps_the_record_that_was_there(self) -> None:
        self.put()
        self.app_ini.write_text(self.RANKS.replace("Nixie = 2", "Nixie = 5"), encoding="utf-8")
        self.failing_ini = True

        failed = backglass_order.settle(GAME_ID)

        self.assertEqual([one["table"] for one in failed], ["t1"])
        self.assertEqual(order_record.held(), {"t1": SWAPPED})

    def test_a_first_write_that_fails_leaves_no_record(self) -> None:
        self.failing_ini = True

        response = self.put()

        self.assertEqual([one["table"] for one in response.json()["order_failed"]], ["t1"])
        self.assertEqual((order_record.held(), self.own.exists()), ({}, False))

    def test_the_dmd_going_takes_back_the_pair_this_wrote_and_its_record(self) -> None:
        self.put()

        self.client.put(PUT, params={"path": FILE}, json={"target": "ScoreView"})

        self.assertFalse(self.own.exists())
        self.assertEqual(order_record.held(), {})

    def test_a_pair_somebody_set_by_hand_is_left_when_the_dmd_goes(self) -> None:
        self.own.write_text("[ScoreView]\nPriority.B2S = 2\nPriority.Nixie = 1\n",
                            encoding="utf-8")
        self.put()
        self.client.put(PUT, params={"path": FILE}, json={"target": "ScoreView"})

        self.assertTrue(self.own.exists())
        self.assertEqual(order_record.held(), {})

    def test_a_table_forgotten_and_made_again_with_an_equal_pair_does_not_inherit_the_record(
            self) -> None:
        self.put()
        order_record.drop(["t1"])

        self.client.put(PUT, params={"path": FILE}, json={"target": "ScoreView"})

        self.assertTrue(self.own.exists())


class Boom(BaseException):
    """What a pass dies of that no handler for a failed table catches."""


class WhatIsKept(Settled):
    def second(self) -> None:
        self.client.put(PUT, params={"path": SECOND}, json=SCORE)

    def test_a_table_is_written_without_settling_the_library_again(self) -> None:
        self.put()

        self.assertEqual(self.flags, [False])

    def test_a_pass_that_dies_partway_leaves_what_it_finished_recorded(self) -> None:
        self.refusing = True
        self.put()
        self.second()
        self.refusing = False
        self.written.clear()

        def die(table_id: str, values: dict[str, str], settle: bool = True) -> dict[str, Any]:
            if table_id == "t2":
                raise Boom()
            return self._write(table_id, values, settle)

        with patch.object(launcher_ops, "write_table_settings", side_effect=die):
            with self.assertRaises(Boom):
                backglass_order.settle(GAME_ID)

        self.assertEqual(self.written, [("t1", SWAPPED)])
        self.assertEqual(order_record.held(), {"t1": SWAPPED, "t2": SWAPPED},
                         "the pair being written when it died is already recorded")

    def test_a_record_that_cannot_be_written_writes_no_settings_and_names_the_table(self) -> None:
        failing = OSError("disk full")
        with patch.object(order_record, "write_atomic", side_effect=failing):
            response = self.put()

        self.assertEqual(self.written, [])
        self.assertEqual(response.json()["order_failed"],
                         [{"table": "t1", "name": TABLE, "reason": failures.why(failing)}])
        self.assertEqual(order_record.held(), {})

    def test_a_record_that_cannot_be_read_names_the_tables_and_writes_nothing(self) -> None:
        order_record.PATH.write_text("{not json", encoding="utf-8")

        response = self.put()

        failed = response.json()["order_failed"]
        self.assertEqual([one["table"] for one in failed], ["t1"])
        self.assertIn(str(order_record.PATH), failed[0]["reason"])
        self.assertEqual(self.written, [])
        self.assertEqual(order_record.PATH.read_text(encoding="utf-8"), "{not json")

    def test_a_record_that_cannot_be_read_is_not_a_reason_to_stop_the_tables_with_no_dmd(
            self) -> None:
        order_record.PATH.write_text("[]", encoding="utf-8")

        response = self.client.put(PUT, params={"path": FILE},
                                   json={"target": "Backglass", "x": 1, "y": 1, "w": 9, "h": 3})

        self.assertEqual(response.json()["order_failed"], [])

    def test_a_pair_taken_back_whose_settings_cannot_be_written_stays_recorded(self) -> None:
        self.put()
        self.refusing = True

        self.client.put(PUT, params={"path": FILE}, json={"target": "ScoreView"})

        self.assertEqual(order_record.held(), {"t1": SWAPPED})

    def test_a_table_the_app_cannot_tell_about_keeps_its_record_and_is_not_written(self) -> None:
        self.put()
        self.written.clear()
        self.unknown = True

        self.client.put(PUT, params={"path": FILE}, json={"target": "ScoreView"})

        self.assertEqual((order_record.held(), self.written), ({"t1": SWAPPED}, []))

    def test_a_game_folder_that_is_not_there_keeps_its_record(self) -> None:
        self.put()
        shutil.rmtree(self.folder)

        failed = backglass_order.settle(GAME_ID)

        self.assertEqual((failed, order_record.held()), ([], {"t1": SWAPPED}))

    def test_a_game_folder_with_nothing_in_it_keeps_its_record(self) -> None:
        self.put()
        shutil.rmtree(self.folder)
        self.folder.mkdir()

        failed = backglass_order.settle(GAME_ID)

        self.assertEqual((failed, order_record.held()), ([], {"t1": SWAPPED}))

    def test_a_game_folder_that_cannot_be_listed_is_read_file_by_file_as_before(self) -> None:
        self.put()

        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            scored = backglass_ops.score_dmds(self.game)

        self.assertEqual([(one.table, one.in_file) for one in scored],
                         [("t1", True), ("t2", False)])

    def test_a_game_with_no_folder_path_keeps_its_record(self) -> None:
        self.put()
        self.game.full_path_game = None
        self.written.clear()

        failed = backglass_order.settle(GAME_ID)

        self.assertEqual((failed, order_record.held(), self.written), ([], {"t1": SWAPPED}, []))


class ReplacingAPair(Settled):
    """The table's own settings are modelled, so what a pass that dies leaves can be run."""

    PROGRAM = vs_tests.ATablesOwnOrderTests.PROGRAM
    LAUNCHER = vs_tests.ATablesOwnOrderTests.LAUNCHER
    RANKED = {"ScoreView.Priority.Nixie": "4", "ScoreView.Priority.B2S": "7"}

    def app(self) -> list[Any]:
        self.ini: dict[str, str] = {}
        self.launcher = dict(self.LAUNCHER)
        self.dies = False
        return [patch.object(launcher_ops, "backglass_order", side_effect=self._modelled),
                patch.object(launcher_ops, "write_table_settings", side_effect=self._held)]

    def _modelled(self, launcher_id: str, target: str, in_file: bool,
                  written: dict[str, str] | None = None) -> TableOrder:
        return vs.table_order(self.PROGRAM, self.launcher, self.ini, in_file, written)

    def _held(self, table_id: str, values: dict[str, str], settle: bool = True) -> dict[str, Any]:
        if self.dies and any(values.values()):
            raise Boom()
        self.ini = {key: value for key, value in {**self.ini, **values}.items() if value}
        return {"written": sorted(values), "cleared": []}

    def test_a_pair_replaced_by_a_pass_that_dies_is_put_back_by_the_next(self) -> None:
        self.put()
        self.launcher.update({"ScoreView.Priority.Nixie": "7", "ScoreView.Priority.B2S": "4"})
        self.dies = True

        with self.assertRaises(Boom):
            backglass_order.settle(GAME_ID)
        self.assertEqual(self.ini, {}, "the old pair is not left under a record of the new one")
        self.dies = False
        backglass_order.settle(GAME_ID)

        self.assertEqual((self.ini, order_record.held()), (self.RANKED, {"t1": self.RANKED}))

    def test_a_pair_replaced_on_other_keys_leaves_none_of_the_old_one_behind(self) -> None:
        old, new = {"Rank.A": "1"}, {"Rank.B": "2"}
        self.ini = dict(old)
        order_record.keep({"t1": old})
        self.dies = True

        with patch.object(launcher_ops, "backglass_order",
                          return_value=TableOrder(write=new, ours=new)):
            with self.assertRaises(Boom):
                backglass_order.settle(GAME_ID)

        self.assertEqual(self.ini, {})

    def test_a_pair_replaced_is_taken_out_while_the_record_still_names_it(self) -> None:
        self.put()
        self.launcher.update({"ScoreView.Priority.Nixie": "7", "ScoreView.Priority.B2S": "4"})
        seen: list[tuple[dict[str, str], dict[str, dict[str, str]]]] = []
        inner = self._held

        def watch(table_id: str, values: dict[str, str], settle: bool = True) -> dict[str, Any]:
            seen.append((dict(values), order_record.held()))
            return inner(table_id, values, settle)

        with patch.object(launcher_ops, "write_table_settings", side_effect=watch):
            backglass_order.settle(GAME_ID)

        self.assertEqual(seen, [(CLEARED, {"t1": SWAPPED}),
                                (self.RANKED, {"t1": self.RANKED})])


class Waiting(Settled):
    """A caller waits for the game it wants and for nothing else."""

    def setUp(self) -> None:
        super().setUp()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        patcher = patch.object(game_identity, "game_id", side_effect=lambda game: game.gid)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_in_thread(self, work: Any) -> threading.Thread:
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        return thread

    def test_two_passes_over_one_game_run_one_after_the_other(self) -> None:
        game = SimpleNamespace(gid="G-A")
        inside = threading.Event()
        guard = threading.Lock()
        active = [0]
        peak = [0]

        def hold(*_a: Any) -> None:
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            inside.set()
            self.release.wait(5)
            with guard:
                active[0] -= 1

        with patch.object(backglass_order, "_game", side_effect=hold):
            first = self.run_in_thread(lambda: backglass_order._settle([game]))
            self.assertTrue(inside.wait(5))
            second = self.run_in_thread(lambda: backglass_order._settle([game]))
            threading.Event().wait(0.2)
            self.release.set()
            first.join(5)
            second.join(5)

        self.assertEqual(peak[0], 1)

    def test_a_pass_inside_one_game_does_not_hold_up_another(self) -> None:
        first = SimpleNamespace(gid="G-A")
        other = next(SimpleNamespace(gid=f"G-{n}") for n in range(1000)
                     if backglass_order._lock_of(f"G-{n}") is not backglass_order._lock_of("G-A"))
        inside = threading.Event()
        seen: list[str] = []

        def hold(game: Any, *_a: Any) -> None:
            seen.append(game.gid)
            if game is first:
                inside.set()
                self.release.wait(5)

        with patch.object(backglass_order, "_game", side_effect=hold):
            self.run_in_thread(lambda: backglass_order._settle([first]))
            self.assertTrue(inside.wait(5))
            second = self.run_in_thread(lambda: backglass_order._settle([other]))
            second.join(2)
            finished = not second.is_alive()
            self.release.set()

        self.assertTrue(finished)
        self.assertEqual(seen, ["G-A", other.gid])

    def test_a_second_launcher_write_waits_until_the_first_has_started_its_pass(self) -> None:
        order: list[str] = []
        inside = threading.Event()

        def first() -> None:
            with backglass_order.launcher_write():
                order.append("first in")
                inside.set()
                self.release.wait(5)
                order.append("first out")

        def second() -> None:
            with backglass_order.launcher_write():
                order.append("second in")

        one = self.run_in_thread(first)
        self.assertTrue(inside.wait(5))
        two = self.run_in_thread(second)
        threading.Event().wait(0.2)
        self.release.set()
        one.join(5)
        two.join(5)

        self.assertEqual(order, ["first in", "first out", "second in"])


class ReadingTheFiles(Settled):
    """What `score_dmds` says of each table: from the file it reads, once while it is unchanged."""

    def scored(self, launcher_id: str = "") -> dict[str, bool]:
        return {one.table: one.in_file for one in backglass_ops.score_dmds(self.game, launcher_id)}

    def test_a_table_is_scored_by_whether_its_file_places_a_score_view_dmd(self) -> None:
        self.assertEqual(self.scored(), {"t1": False, "t2": False})
        self.put()

        self.assertEqual(self.scored(), {"t1": True, "t2": False})

    def test_a_table_that_reads_no_backglass_file_is_scored_false_not_left_out(self) -> None:
        (self.folder / SECOND).unlink()

        self.assertEqual(self.scored(), {"t1": False, "t2": False})

    def test_a_damaged_file_leaves_its_table_out(self) -> None:
        (self.folder / SECOND).write_bytes(b"not a backglass")

        self.assertEqual(self.scored(), {"t1": False})

    def test_a_launcher_is_asked_only_for_the_tables_it_plays(self) -> None:
        self.assertEqual(self.scored("L2"), {})

    def test_the_game_file_is_the_table_s_own_so_its_settings_sit_beside_it(self) -> None:
        one = backglass_ops.score_dmds(self.game)[0]

        self.assertEqual((one.table, one.launcher, one.file),
                         ("t1", "L1", str(self.folder / TABLE)))

    def test_a_file_unchanged_is_not_read_again(self) -> None:
        self.scored()

        with patch.object(directb2s_parser, "read") as read:
            self.scored()

        read.assert_not_called()


class FollowingTheLauncher(Settled):
    """A write at the launcher's scope of a setting the order depends on settles its tables
    as a job, and a write at a table's scope settles that table."""

    def setUp(self) -> None:
        super().setUp()
        self.config = SimpleNamespace(
            scopes=lambda: ("launcher", "entry"), write=lambda *_a: frozenset(),
            order_keys=lambda: {"ScoreView.Priority.Nixie"})
        for patcher in (
                patch.object(launcher_ops, "launcher_or_refuse",
                             return_value=SimpleNamespace(app="vpx", launcher_id="L1")),
                patch.object(launcher_ops, "_app_settings_surface", return_value=self.config),
                patch.object(launcher_ops, "_launcher_settings", return_value={}),
                patch.object(launcher_ops, "_game_file", side_effect=lambda table: table),
                patch.object(launcher_ops, "_writable_before_writing")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, body: dict[str, Any]) -> dict[str, Any]:
        answer = launcher_ops.write_config("L1", body)
        assert isinstance(answer, dict)
        return answer

    def test_a_launcher_write_of_a_key_that_decides_the_order_settles_as_a_job(self) -> None:
        self.put()
        self.written.clear()
        self.asked.clear()
        self.catalog_reads = 0

        answer = self.write({"values": {"ScoreView.Priority.Nixie": "5"}})
        job = _finished(jobs.get(answer["settling"]))

        self.assertEqual(job.state, jobs.DONE)
        self.assertEqual(self.asked, [(TABLE, True, SWAPPED)])
        self.assertEqual(self.catalog_reads, 1, "the library is read once")
        self.assertIsInstance(job.result, dict)

    def test_a_recorded_pair_is_rewritten_and_recorded_when_the_launcher_ranks_change(
            self) -> None:
        self.put()
        self.moved = True

        answer = self.write({"values": {"ScoreView.Priority.Nixie": "5"}})
        _finished(jobs.get(answer["settling"]))

        self.assertEqual(self.written[-1], ("t1", MOVED))
        self.assertEqual(order_record.held(), {"t1": MOVED})

    def test_the_job_names_the_tables_it_could_not_write(self) -> None:
        self.put()
        self.moved = True
        self.refusing = True

        answer = self.write({"values": {"ScoreView.Priority.Nixie": "5"}})
        job = _finished(jobs.get(answer["settling"]))

        assert isinstance(job.result, dict)
        self.assertEqual(job.result["failed"],
                         [{"table": "t1", "name": TABLE, "reason": "read-only"}])
        self.assertEqual(job.problems, 1)
        self.assertEqual(order_record.held(), {"t1": SWAPPED}, "a failed write keeps the record")

    def test_the_pass_is_started_while_the_launcher_write_still_holds_off_another(self) -> None:
        held: list[bool] = []
        start = backglass_order.settle_launcher

        def watch(launcher_id: str) -> jobs.Job:
            held.append(backglass_order._STARTING.locked())
            return start(launcher_id)

        with patch.object(backglass_order, "settle_launcher", side_effect=watch):
            answer = self.write({"values": {"ScoreView.Priority.Nixie": "5"}})
        _finished(jobs.get(answer["settling"]))

        self.assertEqual(held, [True])

    def test_a_launcher_write_of_any_other_key_starts_no_job(self) -> None:
        answer = self.write({"values": {"Player.FXAA": "1"}})

        self.assertNotIn("settling", answer)
        self.assertEqual(self.asked, [])

    def test_a_launcher_write_is_refused_before_it_writes_while_a_pass_runs(self) -> None:
        release = threading.Event()
        jobs.submit(jobs.KIND_BACKGLASS_ORDER, lambda _job: release.wait(5))
        self.addCleanup(release.set)
        self.config.write = Mock(return_value=frozenset())

        with self.assertRaises(jobs.JobBusyError):
            self.write({"values": {"ScoreView.Priority.Nixie": "5"}})

        self.config.write.assert_not_called()

    def test_a_launcher_write_that_creates_the_settings_settles_too(self) -> None:
        self.config.create = Mock()
        self.config.newest_version = "10.8"
        self.put()
        self.asked.clear()

        with patch.object(launcher_ops, "config_files", return_value={}):
            answer = self.write({"values": {"ScoreView.Priority.Nixie": "5"}, "create": True})
        _finished(jobs.get(answer["settling"]))

        self.config.create.assert_called_once()
        self.assertEqual(self.asked, [(TABLE, True, SWAPPED)])

    def test_a_write_into_several_tables_settles_them_inside_its_job(self) -> None:
        self.put()
        self.asked.clear()
        self.moved = True

        started = launcher_ops.write_config("L1", {
            "scope": "entry", "tables": ["t1", "t2"],
            "values": {"ScoreView.Priority.Nixie": "5"}})
        assert isinstance(started, jobs.Job)
        job = _finished(started)

        self.assertEqual(job.state, jobs.DONE)
        self.assertEqual(self.asked, [(TABLE, True, SWAPPED)])

    def test_a_table_write_of_an_order_key_settles_that_table(self) -> None:
        self.put()
        self.asked.clear()

        self.write({"scope": "entry", "table": "t1",
                                         "values": {"ScoreView.Priority.Nixie": "5"}})

        self.assertEqual(self.asked, [(TABLE, True, SWAPPED)])

    def test_a_table_write_of_any_other_key_settles_nothing(self) -> None:
        self.write({"scope": "entry", "table": "t1",
                                         "values": {"Player.FXAA": "1"}})

        self.assertEqual(self.asked, [])

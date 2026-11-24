"""Moving what an upgrade left behind: the settings that change names, the box that goes into
each backglass file, and the one undo that takes both back."""

from __future__ import annotations

import stat
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.apps.contract import MOVE, NAME, PLACE, TURN_ON, Leftover
from common.games import (
    backglass_displays,
    backglass_ops,
    backglass_order,
    launcher_ops,
    upgrade_ops,
)
from common.games.formats import directb2s_parser
from common.host import launch_state
from common.media import sized_media
from common.runtime import jobs
from tests.api.test_backglass_picture import FOLDER, GAME_ID, _directb2s
from tests.support.library import TempTree, fake_game, write_game

B2S = "Plugin.B2S."
LEGACY = "Plugin.B2SLegacy."
FILE = "t1.directb2s"
SECOND = "t2.directb2s"
PROGRAM = {"plugins": ["B2S", "Nixie"], "build": 6300, "dmd_in_file": True,
           "defaults": {B2S + "Enable": "1", LEGACY + "Enable": "0"}}

GLASS = Leftover(LEGACY + "BackglassDMD", PLACE, "dmd_backglass", target="Backglass",
                 box=(256, 0, 512, 384), frame=(1024, 768), cut_if=LEGACY + "B2SHideGrill",
                 drops=((LEGACY + "BackglassDMDX", "256"), (LEGACY + "BackglassDMDW", "512")))
LEGACY_SCORE = Leftover(LEGACY + "ScoreViewDMD", PLACE, "dmd_scoreview", target="ScoreView",
                        box=(256, 0, 512, 384), frame=(1024, 768),
                        cut_if=LEGACY + "B2SHideGrill",
                        drops=((LEGACY + "ScoreViewDMDX", "256"),))
FRONT = Leftover(B2S + "BackglassDMD", PLACE, "dmd_backglass", target="Backglass",
                 box=(10, 5, 20, 10), drops=((B2S + "BackglassDMDX", "10"),))
SCORE = Leftover(B2S + "ScoreViewDMD", PLACE, "dmd_scoreview", target="ScoreView",
                 box=(2, 1, 12, 4), drops=((B2S + "ScoreViewDMDX", "2"),))
GRILL = Leftover(LEGACY + "B2SHideGrill", MOVE, "hide_grill", "1", B2S + "ShowGrill", "0",
                 ((LEGACY + "B2SHideGrill", "1"),))
FANTASY = Leftover(LEGACY + "B2SDualMode", MOVE, "dual_mode", "2", B2S + "DualMode", "2",
                   ((LEGACY + "B2SDualMode", "2"),))
OFF = Leftover(B2S + "Enable", TURN_ON, "b2s_off", "0", B2S + "Enable", "1")
HIDDEN = Leftover(LEGACY + "B2SHideDMD", NAME, "hide_dmd", "1")


class Upgrade(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Title": "Example"},
                "vpinfe": {"schema": 2, "id": GAME_ID, "default_table": "t1"},
                "tables": {"t1": {"id": "t1", "filename": "t1.vpx"},
                           "t2": {"id": "t2", "filename": "t2.vpx"}}}
        glass = _directb2s(size=(40, 30), scoreview=(24, 8))
        self.folder = write_game(
            self.root, FOLDER, info=info, vpx=False,
            files={"t1.vpx": b"vpx", FILE: glass, "t2.vpx": b"vpx", SECOND: glass})
        self.path = self.folder / FILE
        self.second = self.folder / SECOND
        self.original = self.path.read_bytes()
        self.rows = [{"id": "t1", "launcher": "L1", "filename": "t1.vpx"}]
        self.failing = False
        self.settling = ""
        self.items: list[Leftover] = []
        self.values: dict[str, str] = {}
        self.writes: list[dict[str, str]] = []
        self.running = launch_state.LaunchState()
        game = fake_game(self.folder, FOLDER, meta=info)
        for patcher in (
                patch("common.games.game_repository.catalog", return_value={GAME_ID: game}),
                patch.object(sized_media, "ROOT", self.root / "sized"),
                patch.object(directb2s_parser, "ROOT", self.root / "glass"),
                patch.object(launch_state, "current", side_effect=lambda: self.running),
                patch.object(backglass_ops, "_table_rows", side_effect=lambda *_a: self.rows),
                patch.object(launcher_ops, "left_behind", side_effect=lambda _id: self.items),
                patch.object(launcher_ops, "launcher_or_refuse",
                             return_value=SimpleNamespace(app="vpx")),
                patch.object(launcher_ops, "effective_values",
                             side_effect=lambda *_a: dict(self.values)),
                patch.object(launcher_ops, "program_of", return_value=PROGRAM),
                patch.object(launcher_ops, "refuse_unwritable"),
                patch.object(launcher_ops, "write_config", side_effect=self._write)):
            patcher.start()
            self.addCleanup(patcher.stop)
        directb2s_parser._facts.clear()
        self.addCleanup(directb2s_parser._facts.clear)

    def _write(self, _launcher: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.failing:
            raise OSError("disk full")
        self.writes.append(dict(body["values"]))
        self.values.update(body["values"])
        return {"written": sorted(body["values"]), "cleared": [],
                **({"settling": self.settling} if self.settling else {})}

    def both(self) -> None:
        self.rows = [{"id": "t1", "launcher": "L1", "filename": "t1.vpx"},
                     {"id": "t2", "launcher": "L1", "filename": "t2.vpx"}]

    def displays(self, path: Any = None) -> list[tuple[str, int, int, int, int]]:
        directb2s_parser._facts.clear()
        facts = directb2s_parser.read(path or self.path)
        assert facts is not None
        return [(one.target, *one.placement) for one in facts.displays]


class Listing(Upgrade):
    def test_a_box_counts_the_files_that_would_take_it(self) -> None:
        self.items = [GLASS]

        rows = upgrade_ops.listing("L1")["items"]

        self.assertEqual([(row["key"], row["action"], row["files"]) for row in rows],
                         [(GLASS.key, PLACE, 1)])

    def test_a_row_carries_its_words(self) -> None:
        self.items = [GRILL]

        rows = upgrade_ops.listing("L1")["items"]

        self.assertTrue(rows[0]["text"])

    def test_a_file_that_places_the_window_already_takes_none(self) -> None:
        self.items = [GLASS]
        self.path.write_bytes(self.original.replace(
            b"</DirectB2SData>",
            b'<Displays><DMD Target="Backglass" X="1" Y="1" W="9" H="3"/></Displays>\n'
            b"</DirectB2SData>"))

        self.assertEqual(upgrade_ops.listing("L1")["items"][0]["files"], 0)

    def test_a_window_the_file_has_no_art_for_takes_none(self) -> None:
        self.path.write_bytes(_directb2s(size=(40, 30)))
        self.items = [SCORE]

        self.assertEqual(upgrade_ops.listing("L1")["items"][0]["files"], 0)


class Move(Upgrade):
    def test_legacy_s_frame_becomes_pixels_of_the_art(self) -> None:
        self.items = [GLASS]

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("Backglass", 10, 15, 20, 15)])

    def test_y_counts_down_from_the_top_of_the_art(self) -> None:
        self.items = [SCORE]

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("ScoreView", 2, 3, 12, 4)])

    def test_the_grill_is_not_part_of_the_space_a_box_is_counted_in(self) -> None:
        self.path.write_bytes(_directb2s(size=(40, 30), header='  <GrillHeight Value="10" />\n'))
        self.items = [GRILL, GLASS]
        self.values = {LEGACY + "B2SHideGrill": "1"}

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("Backglass", 10, 10, 20, 10)])

    def test_a_shown_grill_leaves_the_whole_art(self) -> None:
        shown = Leftover(GRILL.key, MOVE, "hide_grill", "0", B2S + "ShowGrill", "1",
                         GRILL.drops)
        self.path.write_bytes(_directb2s(size=(40, 30), header='  <GrillHeight Value="10" />\n'))
        self.items = [shown, GLASS]
        self.values = {LEGACY + "B2SHideGrill": "0"}

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("Backglass", 10, 15, 20, 15)])

    def test_a_legacy_box_with_no_grill_setting_is_counted_on_the_whole_art(self) -> None:
        self.path.write_bytes(_directb2s(size=(40, 30), header='  <GrillHeight Value="10" />\n'))
        self.items = [GLASS]

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("Backglass", 10, 15, 20, 15)])

    def test_a_legacy_box_is_cut_by_legacy_s_grill_whatever_b2s_shows(self) -> None:
        self.path.write_bytes(_directb2s(size=(40, 30), header='  <GrillHeight Value="10" />\n'))
        self.items = [GLASS]
        self.values = {LEGACY + "B2SHideGrill": "1", B2S + "ShowGrill": "1"}

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("Backglass", 10, 10, 20, 10)])

    def test_a_legacy_score_view_box_is_converted_to_the_score_view_art(self) -> None:
        self.items = [LEGACY_SCORE]

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("ScoreView", 6, 4, 12, 4)])

    def test_a_window_a_file_gained_while_moving_is_not_written_over(self) -> None:
        twin = Leftover(B2S + "BackglassDMD", PLACE, "dmd_backglass", target="Backglass",
                        box=(1, 1, 5, 5), drops=((B2S + "BackglassDMDX", "1"),))
        self.items = [FRONT, twin]

        upgrade_ops.move("L1")

        self.assertEqual(self.displays(), [("Backglass", 10, 15, 20, 10)])

    def test_the_settings_are_written_once_and_the_old_ones_blanked(self) -> None:
        self.items = [GRILL, FANTASY, GLASS]

        answer = upgrade_ops.move("L1")

        self.assertEqual(self.writes, [{
            B2S + "ShowGrill": "0", LEGACY + "B2SHideGrill": "",
            B2S + "DualMode": "2", LEGACY + "B2SDualMode": "",
            LEGACY + "BackglassDMDX": "", LEGACY + "BackglassDMDW": ""}])
        self.assertEqual(answer["moved"], [GRILL.key, FANTASY.key, GLASS.key])
        self.assertTrue(answer["undo"])

    def test_a_box_with_no_file_to_go_into_keeps_its_settings(self) -> None:
        self.path.write_bytes(_directb2s(size=(40, 30)))
        self.items = [SCORE, FANTASY]

        answer = upgrade_ops.move("L1")

        self.assertEqual(answer["moved"], [FANTASY.key])
        self.assertNotIn(B2S + "ScoreViewDMDX", self.writes[0])

    def test_a_file_that_cannot_be_written_holds_its_box_back_whole(self) -> None:
        self.items = [GLASS, FANTASY]
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        answer = upgrade_ops.move("L1")

        self.assertEqual(answer["moved"], [FANTASY.key])
        self.assertEqual([one["key"] for one in answer["held_back"]], [GLASS.key])
        self.assertEqual([one["path"] for one in answer["held_back"][0]["files"]], [FILE])
        self.assertNotIn(LEGACY + "BackglassDMDX", self.writes[0])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_moving_twice_writes_nothing_the_second_time(self) -> None:
        self.items = [GLASS]
        upgrade_ops.move("L1")
        self.items = []

        answer = upgrade_ops.move("L1")

        self.assertEqual((answer["moved"], answer["undo"]), ([], ""))
        self.assertEqual(len(self.writes), 1)

    def test_what_is_only_named_is_never_written(self) -> None:
        self.items = [HIDDEN, OFF]

        answer = upgrade_ops.move("L1")

        self.assertEqual((answer["moved"], self.writes), ([], []))


class TurnOn(Upgrade):
    def test_the_setting_a_row_names_is_turned_on(self) -> None:
        self.items = [OFF]

        answer = upgrade_ops.turn_on("L1", OFF.key)

        self.assertEqual(self.writes, [{B2S + "Enable": "1"}])
        self.assertTrue(answer["undo"])

    def test_a_row_that_is_not_one_to_turn_on_is_refused(self) -> None:
        self.items = [GRILL]

        with self.assertRaises(service_errors.NotFoundError):
            upgrade_ops.turn_on("L1", GRILL.key)


class Undo(Upgrade):
    def test_the_file_and_the_settings_go_back(self) -> None:
        self.items = [GRILL, GLASS]
        self.values = {LEGACY + "B2SHideGrill": "1", LEGACY + "BackglassDMDX": "256"}
        token = upgrade_ops.move("L1")["undo"]

        upgrade_ops.undo("L1", token)

        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.writes[-1][LEGACY + "B2SHideGrill"], "1")
        self.assertEqual(self.writes[-1][LEGACY + "BackglassDMDX"], "256")
        self.assertEqual(self.writes[-1][B2S + "ShowGrill"], "")

    def test_it_is_undone_once(self) -> None:
        self.items = [FANTASY]
        token = upgrade_ops.move("L1")["undo"]
        upgrade_ops.undo("L1", token)

        with self.assertRaises(service_errors.NotFoundError):
            upgrade_ops.undo("L1", token)

    def test_a_setting_changed_since_is_left_as_it_is(self) -> None:
        self.items = [FANTASY]
        token = upgrade_ops.move("L1")["undo"]
        self.values[B2S + "DualMode"] = "1"

        with self.assertRaises(service_errors.BlockedError):
            upgrade_ops.undo("L1", token)

        self.assertEqual(len(self.writes), 1)

    def test_a_file_edited_since_stays_and_the_settings_stay_with_it(self) -> None:
        self.items = [GLASS]
        token = upgrade_ops.move("L1")["undo"]
        self.path.write_bytes(self.path.read_bytes() + b"\n")

        with self.assertRaises(service_errors.BlockedError) as raised:
            upgrade_ops.undo("L1", token)

        self.assertEqual(raised.exception.details["files"][0]["path"], FILE)
        self.assertEqual(len(self.writes), 1)

    def test_another_launcher_s_token_is_not_honored(self) -> None:
        self.items = [FANTASY]
        token = upgrade_ops.move("L1")["undo"]

        with self.assertRaises(service_errors.NotFoundError):
            upgrade_ops.undo("L2", token)


class Ordering(Upgrade):
    """A box carried into a file, or taken out again, changes what the file places, so the
    order of the tables that read it is settled again."""

    def test_a_move_that_wrote_a_file_settles_that_game_once_and_its_undo_again(self) -> None:
        self.both()
        self.items = [SCORE]
        with patch.object(backglass_order, "settle") as settle:
            token = upgrade_ops.move("L1")["undo"]

            self.assertEqual(settle.call_args_list, [((GAME_ID,),)])
            upgrade_ops.undo("L1", token)

            self.assertEqual(settle.call_args_list, [((GAME_ID,),), ((GAME_ID,),)])

    def test_a_move_that_wrote_no_file_settles_nothing(self) -> None:
        self.items = [FANTASY]
        with patch.object(backglass_order, "settle") as settle:
            upgrade_ops.move("L1")

        settle.assert_not_called()

    def test_a_table_that_could_not_be_written_is_named_by_the_move_and_by_its_undo(self) -> None:
        self.items = [SCORE]
        gone = [{"table": "t1", "name": "t1.vpx", "reason": "read-only"}]
        with patch.object(backglass_order, "settle", return_value=gone):
            answer = upgrade_ops.move("L1")
            undone = upgrade_ops.undo("L1", answer["undo"])

        self.assertEqual(answer["order_failed"], gone)
        self.assertEqual(undone["order_failed"], gone)

    def test_a_move_with_every_table_written_names_none(self) -> None:
        self.items = [SCORE]
        with patch.object(backglass_order, "settle", return_value=[]):
            self.assertEqual(upgrade_ops.move("L1")["order_failed"], [])

    def test_a_write_that_started_a_pass_is_answered_by_it_and_the_files_are_not_settled_twice(
            self) -> None:
        self.both()
        self.items = [SCORE]
        self.settling = "job-1"
        with patch.object(backglass_order, "settle") as settle:
            answer = upgrade_ops.move("L1")
            undone = upgrade_ops.undo("L1", answer["undo"])

        settle.assert_not_called()
        self.assertEqual((answer["settling"], answer["order_failed"]), ("job-1", []))
        self.assertEqual((undone["settling"], undone["order_failed"]), ("job-1", []))

    def test_an_answer_with_no_pass_started_carries_no_settling(self) -> None:
        self.items = [SCORE]
        with patch.object(backglass_order, "settle", return_value=[]):
            answer = upgrade_ops.move("L1")
            undone = upgrade_ops.undo("L1", answer["undo"])

        self.assertNotIn("settling", answer)
        self.assertNotIn("settling", undone)

    def test_turning_a_setting_on_answers_the_pass_it_started(self) -> None:
        self.items = [OFF]
        self.settling = "job-2"

        self.assertEqual(upgrade_ops.turn_on("L1", OFF.key)["settling"], "job-2")

    def test_an_undo_whose_write_fails_still_settles_the_files_it_put_back(self) -> None:
        self.items = [SCORE]
        with patch.object(backglass_order, "settle", return_value=[]) as settle:
            token = upgrade_ops.move("L1")["undo"]
            settle.reset_mock()
            self.failing = True

            with self.assertRaises(OSError):
                upgrade_ops.undo("L1", token)

        settle.assert_called_once_with(GAME_ID)

    def test_a_pass_over_the_library_still_running_refuses_the_move_and_the_undo_whole(
            self) -> None:
        self.items = [SCORE]
        with patch.object(backglass_order, "settle", return_value=[]):
            token = upgrade_ops.move("L1")["undo"]
        written = self.path.read_bytes()
        release = threading.Event()
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        jobs.submit(jobs.KIND_BACKGLASS_ORDER, lambda _job: release.wait(5))
        self.addCleanup(release.set)

        with self.assertRaises(jobs.JobBusyError):
            upgrade_ops.undo("L1", token)
        with self.assertRaises(jobs.JobBusyError):
            upgrade_ops.move("L1")

        self.assertEqual(self.path.read_bytes(), written, "the file is untouched")


class Safety(Upgrade):
    """What a refusal or a failure leaves: nothing half done."""

    def test_two_boxes_in_one_file_are_taken_back_newest_first(self) -> None:
        self.items = [FRONT, SCORE]
        token = upgrade_ops.move("L1")["undo"]
        self.assertEqual(len(self.displays()), 2)

        upgrade_ops.undo("L1", token)

        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.writes[-1][B2S + "BackglassDMDX"], "10")

    def test_a_box_going_into_more_files_than_the_journal_keeps_is_held_back_whole(self) -> None:
        self.items = [FRONT, FANTASY]
        self.both()

        with patch.object(backglass_displays, "_KEPT", 1):
            answer = upgrade_ops.move("L1")

        self.assertEqual(answer["moved"], [FANTASY.key])
        self.assertEqual(answer["held_back"], [{"key": FRONT.key, "files": [], "limit": 1}])
        self.assertEqual((self.path.read_bytes(), self.second.read_bytes()),
                         (self.original, self.original))
        self.assertNotIn(B2S + "BackglassDMDX", self.writes[0])
        self.assertEqual(self.writes[0][B2S + "DualMode"], "2")

    def test_the_boxes_that_fit_go_and_the_first_that_does_not_stays(self) -> None:
        self.items = [FRONT, SCORE]

        with patch.object(backglass_displays, "_KEPT", 1):
            answer = upgrade_ops.move("L1")

        self.assertEqual(answer["moved"], [FRONT.key])
        self.assertEqual([one["key"] for one in answer["held_back"]], [SCORE.key])
        self.assertEqual([one[0] for one in self.displays()], ["Backglass"])
        upgrade_ops.undo("L1", answer["undo"])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_a_change_too_large_to_keep_stops_the_move_before_it_starts(self) -> None:
        self.items = [FRONT, FANTASY]

        with patch.object(backglass_displays, "_MAX_BYTES", 10), \
                self.assertRaises(service_errors.BlockedError):
            upgrade_ops.move("L1")

        self.assertEqual((self.path.read_bytes(), self.writes), (self.original, []))

    def test_changes_that_fit_one_by_one_but_not_together_are_refused(self) -> None:
        self.items = [FRONT, SCORE]
        size = backglass_displays.check(GAME_ID, FILE, "Backglass", (1, 1, 5, 5))

        with patch.object(backglass_displays, "_MAX_BYTES", size + 1), \
                self.assertRaises(service_errors.BlockedError):
            upgrade_ops.move("L1")

        self.assertEqual(self.path.read_bytes(), self.original)

    def test_settings_that_fail_to_write_put_the_files_back(self) -> None:
        self.items = [FRONT, SCORE, FANTASY]
        self.failing = True

        with self.assertRaises(OSError):
            upgrade_ops.move("L1")

        self.assertEqual(self.path.read_bytes(), self.original)

    def test_one_file_that_refuses_holds_the_whole_box_before_any_is_written(self) -> None:
        self.both()
        self.items = [FRONT]
        self.second.chmod(stat.S_IREAD)
        self.addCleanup(self.second.chmod, stat.S_IREAD | stat.S_IWRITE)

        answer = upgrade_ops.move("L1")

        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual([one["key"] for one in answer["held_back"]], [FRONT.key])
        self.assertEqual([one["path"] for one in answer["held_back"][0]["files"]], [SECOND])
        self.assertEqual((answer["moved"], answer["undo"], self.writes), ([], "", []))

    def test_an_undo_with_one_file_edited_since_touches_nothing(self) -> None:
        self.both()
        self.items = [FRONT]
        token = upgrade_ops.move("L1")["undo"]
        placed = self.path.read_bytes()
        edited = self.second.read_bytes() + b"\n"
        self.second.write_bytes(edited)

        with self.assertRaises(service_errors.BlockedError) as raised:
            upgrade_ops.undo("L1", token)

        self.assertEqual(raised.exception.details["files"][0]["path"], SECOND)
        self.assertEqual((self.path.read_bytes(), self.second.read_bytes()), (placed, edited))
        self.assertEqual(len(self.writes), 1)
        self.second.write_bytes(edited[:-1])
        upgrade_ops.undo("L1", token)
        self.assertEqual((self.path.read_bytes(), self.second.read_bytes()),
                         (self.original, self.original))


class Routes(Upgrade):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_listing_the_move_and_the_undo(self) -> None:
        self.items = [FANTASY, GLASS]

        listed = self.client.get("/launchers/L1/leftovers")
        moved = self.client.post("/launchers/L1/leftovers/move")
        undone = self.client.post("/launchers/L1/leftovers/undo",
                                  json={"undo": moved.json()["undo"]})

        self.assertEqual([row["key"] for row in listed.json()["items"]],
                         [FANTASY.key, GLASS.key])
        self.assertEqual(moved.json()["moved"], [FANTASY.key, GLASS.key])
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_an_undo_that_cannot_be_done_is_a_conflict(self) -> None:
        self.items = [GLASS]
        token = self.client.post("/launchers/L1/leftovers/move").json()["undo"]
        self.path.write_bytes(self.path.read_bytes() + b"\n")

        refused = self.client.post("/launchers/L1/leftovers/undo", json={"undo": token})

        self.assertEqual(refused.status_code, 409)

    def test_turn_on_by_key(self) -> None:
        self.items = [OFF]

        done = self.client.post("/launchers/L1/leftovers/turn-on", json={"key": OFF.key})

        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(self.writes, [{B2S + "Enable": "1"}])

    def test_a_change_too_large_for_the_journal_is_a_conflict(self) -> None:
        self.items = [FRONT, SCORE]

        with patch.object(backglass_displays, "_MAX_BYTES", 10):
            refused = self.client.post("/launchers/L1/leftovers/move")

        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_the_first_box_over_the_journal_and_every_box_after_it_stay(self) -> None:
        self.second.write_bytes(_directb2s(size=(40, 30)))
        self.both()
        self.items = [LEGACY_SCORE, FRONT, SCORE]

        with patch.object(backglass_displays, "_KEPT", 2):
            answer = upgrade_ops.move("L1")

        self.assertEqual(answer["held_back"], [{"key": FRONT.key, "files": [], "limit": 2},
                                               {"key": SCORE.key, "files": [], "limit": 2}])
        self.assertEqual([one[0] for one in self.displays()], ["ScoreView"])
        self.assertEqual(self.second.read_bytes(), _directb2s(size=(40, 30)))

    def test_a_box_over_the_journal_is_named_with_its_limit(self) -> None:
        self.items = [FRONT, SCORE]

        with patch.object(backglass_displays, "_KEPT", 1):
            done = self.client.post("/launchers/L1/leftovers/move")

        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["held_back"], [{"key": SCORE.key, "files": [], "limit": 1}])

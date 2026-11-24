"""Record Media for one game or table: its rows, what each choice says it will touch, the
body Record sends, what the end of a recording says, and the review of what it kept."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from common.capture import placing
from common.capture.run import FILL, REPLACE_ALL
from manager.kit import panel
from manager.library import record
from manager.workbench import game_media

NOT_SHOWN = {"key": placing.NOT_SHOWN, "fix": "none", "remedy": None,
             "params": {"app": "Visual Pinball X", "window": "scoreview"}}
TOPPER = {**NOT_SHOWN, "params": {"app": "Visual Pinball X", "window": "topper"}}


def _row(kind: str, does: str = "fill", source: str | None = None, *,
         reason: dict | None = None, file: str | None = None,
         goes: bool = False) -> dict[str, Any]:
    return {"kind": kind, "does": does, "source": source, "reason": reason,
            "file": file, "goes": goes}


def _plan(*rows: dict[str, Any], replacing: dict[str, int] | None = None,
          seconds: int = 59) -> dict[str, Any]:
    by_source = dict(replacing or {})
    return {"targets": [{"kinds": list(rows)}],
            "recording": [row["kind"] for row in rows if row["does"] != "leave"],
            "fills": sum(1 for row in rows if row["does"] == "fill"
                         or (row["does"] == "replace" and not row["goes"])),
            "asks": sum(1 for row in rows if row["does"] == "propose"),
            "replacing": sum(by_source.values()), "replacing_by_source": by_source,
            "estimate_seconds": seconds}


class RowTests(unittest.TestCase):
    def test_kinds_run_window_by_window_then_audio_and_only_those_kept(self) -> None:
        self.assertEqual(record.order(), [
            "playfield", "playfield_video", "playfield_fss", "backglass", "backglass_video",
            "scoreview", "scoreview_video", "topper", "topper_video", "audio"])
        self.assertEqual(record.order({"playfield_video", "audio", "wheel"}),
                         ["playfield_video", "audio"])

    def test_a_window_stopped_by_one_reason_is_one_row(self) -> None:
        slots = {"topper": _row("topper", reason=TOPPER),
                 "topper_video": _row("topper_video", reason=TOPPER),
                 "scoreview_video": _row("scoreview_video", reason=NOT_SHOWN)}

        rows = record.rows_of(["scoreview", "scoreview_video", "topper", "topper_video"],
                              slots)

        said = "Visual Pinball X doesn't show the Topper on a screen of its own"
        self.assertEqual(rows, [
            (("scoreview",), ""),
            (("scoreview_video",),
             "Visual Pinball X doesn't show the DMD on a screen of its own"),
            (("topper", "topper_video"), said)])

    def test_a_slot_says_what_it_holds_now(self) -> None:
        self.assertEqual([record.holds(_row("wheel", source=source))
                          for source in (None, "vpinmediadb", "user", "unknown", "capture",
                                       "backglass_file", "b2spro_file")],
                         ["Missing", "VPinMediaDB", "Yours", "Yours", "Recorded",
                          "DirectB2S File", "B2S Pro File"])

    def test_audio_starts_as_the_devices_sound_and_a_dimmed_kind_never_ticks(self) -> None:
        kinds = ["playfield_video", "topper", "audio"]
        slots = {"topper": _row("topper", reason=TOPPER)}

        self.assertEqual(record.ticked(kinds, slots, {}, sound=False),
                         {"playfield_video": True, "topper": False, "audio": False})
        self.assertEqual(record.ticked(kinds, slots, {}, sound=True)["audio"], True)
        self.assertEqual(record.ticked(kinds, slots, {"playfield_video": False,
                                                      "topper": True}, sound=False),
                         {"playfield_video": False, "topper": False, "audio": False})

    def test_the_full_single_screen_picture_is_a_row_of_its_own_that_starts_unticked(self) -> None:
        kinds = ["playfield", "playfield_fss"]
        no_view: dict[str, Any] = {"key": "capture.view.no_fss", "fix": "none", "remedy": None,
                                   "params": {}}

        self.assertEqual(record.rows_of(kinds, {}), [(("playfield",), ""),
                                                     (("playfield_fss",), "")])
        self.assertEqual(record.ticked(kinds, {}, {}, sound=False),
                         {"playfield": True, "playfield_fss": False})
        self.assertEqual(record.ticked(kinds, {}, {"playfield_fss": True}, sound=False),
                         {"playfield": True, "playfield_fss": True})
        self.assertEqual(
            record.rows_of(kinds, {"playfield_fss": _row("playfield_fss", reason=no_view)})[1],
            (("playfield_fss",), "This table has no Full Single Screen view"))

    def test_a_dimmed_row_keeps_what_the_browser_held_for_it(self) -> None:
        slots = {"topper": _row("topper", reason=TOPPER)}

        self.assertEqual(record.remember({"topper": True}, {"topper": False,
                                                            "audio": True}, slots),
                         {"topper": True, "audio": True})


class TouchesTests(unittest.TestCase):
    def test_what_each_choice_touches(self) -> None:
        cases = (
            (_plan(_row("playfield_video"), _row("backglass", "leave", "user")),
             "Fills 1 - replaces none"),
            (_plan(_row("playfield_video"),
                   _row("backglass_video", "replace", "vpinmediadb", goes=True),
                   replacing={"vpinmediadb": 1}),
             "Fills 1 - replaces 1 downloaded"),
            (_plan(_row("playfield", "replace", "user", goes=True),
                   _row("backglass_video", "replace", "vpinmediadb", goes=True),
                   replacing={"vpinmediadb": 1, "user": 1}),
             "Fills none - replaces 2, 1 of them yours"),
            (_plan(_row("playfield", "replace", "unknown", goes=True),
                   replacing={"unknown": 1}),
             "Fills none - replaces 1 of yours"),
            (_plan(_row("playfield_video"), _row("backglass", "propose", "user"),
                   _row("scoreview", "propose", "vpinmediadb")),
             "Fills 1 - asks about 2"),
            (_plan(_row("playfield", "leave", "user")), "Nothing to record"),
        )
        for plan, said in cases:
            with self.subTest(said):
                self.assertEqual(record.touches(plan), said)

    def test_a_shared_file_other_tables_keep_is_a_fill_not_a_replace(self) -> None:
        plan = _plan(_row("playfield_video", "replace", "vpinmediadb", goes=False))

        self.assertEqual(record.touches(plan), "Fills 1 - replaces none")

    def test_the_estimate_is_said_in_minutes(self) -> None:
        self.assertEqual([record.about(seconds) for seconds in (20, 59, 95, 1500)],
                         ["About 1 min", "About 1 min", "About 2 min", "About 25 min"])


class BodyTests(unittest.TestCase):
    def test_the_body_names_the_table_the_kinds_ticked_and_the_settings_changed(self) -> None:
        body = record.wanted("g1", "t1", FILL, ["playfield", "playfield_video", "audio"],
                             {"playfield": False, "playfield_video": True, "audio": True},
                             {"length": 25})

        self.assertEqual(body, {"tables": [{"game": "g1", "table": "t1"}], "existing": "fill",
                                "kinds": ["playfield_video", "audio"],
                                "settings": {"length": 25}})
        self.assertEqual(record.target("g1", "")["games"], ["g1"])

    def test_a_run_that_deletes_carries_the_count_the_plan_said(self) -> None:
        body = record.wanted("g1", "", REPLACE_ALL, ["playfield"], {"playfield": True}, {})
        plan = _plan(_row("playfield", "replace", "user", file="medias/table.png",
                          goes=True), replacing={"user": 1})

        self.assertEqual(record.confirmed(body, plan)["confirmed"], {"count": 1})
        self.assertNotIn("confirmed", record.confirmed(body, _plan(_row("playfield"))))
        self.assertEqual(record.going(plan), ["medias/table.png - Yours"])


class SettingsTests(unittest.TestCase):
    LENGTH = {"key": "length", "type": "int", "default": "20", "unit": "seconds"}
    SOURCE = {"key": "sound_source", "type": "string", "default": "", "blank": "Automatic"}

    def test_a_value_like_the_devices_or_a_number_cleared_is_the_devices(self) -> None:
        held = record.Settings([self.LENGTH], {"length": 20})

        held.set(self.LENGTH, 25)
        self.assertEqual(held.changed, {"length": 25})
        held.set(self.LENGTH, "")
        self.assertEqual(held.changed, {})
        held.set(self.LENGTH, 30)
        held.set(self.LENGTH, 20)
        self.assertEqual(held.changed, {})

    def test_the_line_names_the_devices_value_only_once_it_differs(self) -> None:
        self.assertEqual(record.device_default(self.LENGTH, 20, 20), "")
        self.assertEqual(record.device_default(self.LENGTH, 20, 25), "Device default: 20 s")
        self.assertEqual(record.device_default(self.SOURCE, "", "monitor"),
                         "Device default: Automatic")


class OutcomeTests(unittest.TestCase):
    def test_the_cabs_run_says_what_landed_and_why_the_dmd_did_not(self) -> None:
        table = {"state": "recorded", "placed": [{"kind": "playfield_video"},
                                                 {"kind": "playfield"},
                                                 {"kind": "backglass_video"}],
                 "failed": [{"kind": "scoreview_video", "reason": NOT_SHOWN}],
                 "proposed": [], "reason": None}

        self.assertEqual(record.outcome(table), (
            "Recorded 3 files, 1 failed", "warning",
            "DMD Video: Visual Pinball X doesn't show the DMD on a screen of its own"))

    def test_a_reason_is_said_once_for_every_kind_it_stopped(self) -> None:
        failed = [{"kind": "scoreview", "reason": NOT_SHOWN},
                  {"kind": "scoreview_video", "reason": NOT_SHOWN},
                  {"kind": "audio", "reason": {"key": "capture.outcome.silent",
                                               "params": {}}}]

        self.assertEqual(record.failures(failed),
                         "DMD, DMD Video: Visual Pinball X doesn't show the DMD on a screen "
                         "of its own; Table Audio: Nothing to hear during attract")

    def test_a_table_that_did_not_start_says_why(self) -> None:
        table = {"state": "failed", "placed": [], "failed": [], "proposed": [],
                 "reason": {"key": "capture.outcome.start_failed", "params": {},
                            "detail": "No Visual Pinball on this machine"}}

        self.assertEqual(record.outcome(table), (
            "Couldn't record", "negative",
            "Would not start (No Visual Pinball on this machine)"))

    def test_nothing_to_do_and_a_close_at_the_cabinet_are_not_failures(self) -> None:
        skipped = {"state": "skipped", "reason": {"key": "capture.outcome.nothing_to_record",
                                                  "params": {}}}
        closed = {"state": "closed", "reason": {"key": "capture.outcome.closed",
                                                "params": {}}}

        self.assertEqual(record.outcome(skipped)[:2],
                         ("Every slot asked for already has a file", "info"))
        self.assertEqual(record.outcome(closed)[1], "warning")

    def test_recordings_kept_for_a_decision_count_as_recorded(self) -> None:
        table = {"state": "recorded", "placed": [], "failed": [],
                 "proposed": [{"kind": "playfield_video", "id": "a1"}], "reason": None}

        self.assertEqual(record.outcome(table), ("Recorded 1 file", "positive", ""))


class Library:
    def __init__(self, jobs: list[dict[str, Any]] | None = None) -> None:
        self.jobs = list(jobs or [])
        self.used: list[tuple[str, bool]] = []
        self.waiting: dict[str, Any] = {"count": 1, "bytes": 10, "proposals": [
            {"id": "a1", "game_id": "g1", "table_id": "", "kind": "playfield_video",
             "file": "playfield_video.mp4", "size": 4_700_000,
             "url": "/api/v1/capture/proposals/a1/file",
             "replaces": {"path": "medias/table.mp4", "source": "vpinmediadb",
                          "goes": True}}]}
        self.displaced: list[str] = ["medias/(Playfield) Game.mp4"]

    def capture_job(self, _job_id: str) -> dict[str, Any]:
        return self.jobs.pop(0)

    def capture_proposals(self) -> dict[str, Any]:
        return self.waiting

    def use_proposal(self, proposal_id: str, use: bool) -> dict[str, Any]:
        self.used.append((proposal_id, use))
        return {}

    def displaced_by(self, _game: str, _table: str, _kind: str, _name: str) -> list[str]:
        return self.displaced


def _io(call: Any, *args: Any, **kwargs: Any) -> Any:
    return call(*args, **kwargs)


class EndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(mock.patch.object(record, "ui"))
        self.enterContext(mock.patch.object(record, "_POLL_S", 0))
        self.enterContext(mock.patch.object(record.offload, "io",
                                            mock.AsyncMock(side_effect=_io)))

    async def test_the_end_is_said_and_what_was_kept_opens_for_review(self) -> None:
        library = Library([{"state": "running"}, {"state": "done", "result": {"tables": [{
            "state": "recorded", "placed": [{"kind": "playfield"}],
            "failed": [{"kind": "scoreview_video", "reason": NOT_SHOWN}],
            "proposed": [{"kind": "playfield_video", "id": "a1"}], "reason": None}]}}])
        then = mock.AsyncMock()

        with mock.patch.object(record, "review", mock.AsyncMock()) as review:
            table = await record.finished(library, "j1", then)

        self.assertEqual(table["state"], "recorded")
        said = self.ui.notify.call_args
        self.assertEqual((said.args[0], said.kwargs["type"]),
                         ("Recorded 2 files, 1 failed", "warning"))
        then.assert_awaited_once()
        review.assert_awaited_once_with(library, then, ["a1"])

    async def test_a_job_that_failed_says_so_and_reviews_nothing(self) -> None:
        library = Library([{"state": "failed", "error": "A table is already launching"}])

        with mock.patch.object(record, "review", mock.AsyncMock()) as review:
            self.assertEqual(await record.finished(library, "j1", mock.Mock()), {})

        self.assertEqual(self.ui.notify.call_args.args[0], "Couldn't record")
        review.assert_not_awaited()


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.enterContext(mock.patch.object(record, "ui"))
        self.enterContext(mock.patch.object(record.mediaview, "preview"))
        self.enterContext(mock.patch.object(record.panel, "facts"))
        self.state = self.enterContext(mock.patch.object(record.panel, "state"))
        self.action = self.enterContext(mock.patch.object(record.panel, "action"))
        self.enterContext(mock.patch.object(record.offload, "io",
                                            mock.AsyncMock(side_effect=_io)))
        self.library = Library()
        self.decided: list[bool] = []
        record.proposal(self.library, self.library.waiting["proposals"][0],
                        self.decided.append)

    def _act(self, label: str) -> Any:
        return next(call.args[1] for call in self.action.call_args_list
                    if call.args[0] == label)

    async def test_use_this_asks_first_naming_every_file_it_deletes(self) -> None:
        with mock.patch.object(record.confirm, "replace",
                               mock.AsyncMock(return_value=True)) as asked:
            await self._act("Use This")()

        asked.assert_awaited_once_with("Playfield Video", ["medias/(Playfield) Game.mp4",
                                                           "medias/table.mp4"])
        self.assertEqual(self.library.used, [("a1", True)])
        self.assertEqual(self.state.call_args.args, ("Used", "on"))
        self.assertEqual(self.decided, [True])

    def test_a_file_that_stays_is_not_named_and_none_is_named_twice(self) -> None:
        self.assertEqual(record.deleted(["medias/table.mp4"],
                                        {"path": "medias/table.mp4", "goes": True}),
                         ["medias/table.mp4"])
        self.assertEqual(record.deleted([], {"path": "medias/table.mp4", "goes": False}), [])
        self.assertEqual(record.deleted([], None), [])

    async def test_use_this_answered_no_places_nothing(self) -> None:
        with mock.patch.object(record.confirm, "replace", mock.AsyncMock(return_value=False)):
            await self._act("Use This")()

        self.assertEqual(self.library.used, [])
        self.assertEqual(self.decided, [])

    async def test_discard_throws_the_recording_away(self) -> None:
        await self._act("Discard")()

        self.assertEqual(self.library.used, [("a1", False)])
        self.assertEqual(self.state.call_args.args, ("Discarded", "off"))
        self.assertEqual(self.decided, [False])


class EntryTests(unittest.TestCase):
    """Record Media closes the Media section, and says why where it cannot run."""

    def _entry(self, capabilities: list[dict[str, Any]], lens: str = "") -> Any:
        library = SimpleNamespace(discovery=lambda: {"capabilities": capabilities})
        context = {"library": library, "lens": lens, "game_id": "g1",
                   "game": {"name": "Attack from Mars"}, "tables": [], "state": {}}
        with mock.patch.object(panel, "action") as action:
            drawn = game_media._record_media(context, lambda: None)
        return drawn, action

    def test_dimmed_with_the_devices_reason(self) -> None:
        drawn, action = self._entry([{"name": "capture", "available": False,
                                      "reason": "Recording isn't supported on macOS yet"}])

        self.assertIsNotNone(drawn)
        self.assertEqual(action.call_args.args[0], "Record Media…")
        self.assertEqual((action.call_args.kwargs["enabled"], action.call_args.kwargs["hint"]),
                         (False, "Recording isn't supported on macOS yet"))

    def test_not_drawn_where_the_install_does_not_record(self) -> None:
        drawn, action = self._entry([{"name": "launch", "available": True}])

        self.assertIsNone(drawn)
        action.assert_not_called()

    def test_the_verb_is_refused_with_the_devices_reason(self) -> None:
        for capabilities, hint, runs in (
                ([{"name": "capture", "available": True}], "", True),
                ([{"name": "capture", "available": False, "reason": "Needs FFmpeg"}],
                 "Needs FFmpeg", False)):
            library = SimpleNamespace(discovery=lambda capabilities=capabilities:
                                      {"capabilities": capabilities})
            with self.subTest(hint):
                verb = record.verb(library, lambda: None)
                assert verb is not None
                self.assertEqual(verb.hint, hint)
                self.assertEqual(verb.run is not None, runs)

        library = SimpleNamespace(discovery=lambda: {"capabilities": []})
        self.assertIsNone(record.verb(library, lambda: None))


# The reference cab: no screen shows the DMD, so every game fails its DMD video.
def _game(state: str = "recorded", **extra: Any) -> dict[str, Any]:
    return {"state": state, "placed": [{"kind": "playfield_video"}],
            "failed": [{"kind": "scoreview_video", "reason": NOT_SHOWN}],
            "proposed": [], "reason": None, **extra}


class ManyTests(unittest.TestCase):
    def test_the_body_names_games_and_tables_each_in_its_list(self) -> None:
        self.assertEqual(record.targets([("g1", ""), ("g2", "t2"), ("g3", "")]),
                         {"games": ["g1", "g3"], "tables": [{"game": "g2", "table": "t2"}]})

    def test_each_kind_says_how_many_have_no_file_and_how_many_have_one(self) -> None:
        self.assertEqual([record.counted({"missing": missing, "have": have})
                          for missing, have in ((18, 6), (24, 0), (0, 1))],
                         ["18 missing, 6 have one", "24 missing", "1 has one"])

    def test_the_confirm_counts_what_goes_by_whose_it_is(self) -> None:
        plan = {"replacing_by_source": {"vpinmediadb": 14, "user": 3, "unknown": 1}}

        self.assertEqual(record.by_source(plan), ["14 from VPinMediaDB", "4 of yours"])

    def test_a_run_on_the_reference_cab_reads_well_with_a_failure_on_every_game(
            self) -> None:
        said = record.run_outcome({"tables": [_game() for _ in range(24)],
                                   "run": {"state": "done", "done": 24, "of": 24}})

        self.assertEqual(said, (
            "Recorded 24 games", "warning",
            "DMD Video: Visual Pinball X doesn't show the DMD on a screen of its own"))

    def test_games_that_failed_whole_are_counted_by_reason(self) -> None:
        wont = {"key": "capture.outcome.start_failed", "params": {}, "detail": ""}
        tables = [_game(), _game(), _game("failed", placed=[], failed=[], reason=wont),
                  _game(proposed=[{"kind": "backglass_video", "id": "a1"}])]

        said, level, caption = record.run_outcome({"tables": tables,
                                                   "run": {"state": "done"}})

        self.assertEqual(said, "Recorded 3 games, 1 failed, 1 waits for a decision")
        self.assertEqual(caption, "DMD Video: Visual Pinball X doesn't show the DMD on a "
                                  "screen of its own; Would not start: 1 game")

    def test_a_paused_run_says_where_and_why(self) -> None:
        said = record.run_outcome({"tables": [_game()], "run": {
            "state": "paused", "done": 6, "of": 24,
            "reason": {"key": "capture.run.space", "params": {"device": "Cab 1"}}}})

        self.assertEqual(said, ("Recording paused at 7 of 24", "warning",
                                "Cab 1 is nearly out of space"))

    def test_a_plan_of_many_holds_each_kinds_counts_and_of_one_its_own_row(self) -> None:
        total = {"kind": "playfield", "reason": None, "missing": 1, "have": 0}
        own = _row("playfield", "fill")

        many = record.slots_of({"kinds": [total], "targets": [{"kinds": [own]}] * 2})
        one = record.slots_of({"kinds": [total], "targets": [{"kinds": [own]}]})

        self.assertNotIn("does", many["playfield"])
        self.assertEqual((one["playfield"]["does"], one["playfield"]["missing"]), ("fill", 1))


if __name__ == "__main__":
    unittest.main()

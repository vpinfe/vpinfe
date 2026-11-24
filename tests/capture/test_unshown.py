"""A window the desktop showed nowhere, remembered for its table: learned by a recording,
left out of what a plan records and counts and of what each place offers to fill, and
forgotten once seen or once what placed it changes."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from common.capture import placing, preflight, run, session, unshown
from common.capture.adapters import Window
from common.capture.run import Request
from manager.library import record
from manager.remote import remote_record
from tests.capture.test_run import FOLDER, GAME_ID, MOD, _Library, _Started
from tests.capture.test_session import FROM_VPX, UP, VIDEOS, Cabinet, _Sessions
from tests.theming.test_frontend_recording import _Frontend

TABLE = "/games/Example/Example.vpx"
DMD_UP = [*UP, Window("VPinballX_BGFX", "Visual Pinball Score View", "HDMI-A-1")]


def _placer(table: str = TABLE, placed_by: str = "settings-1") -> placing.Placer:
    return placing.Placer("Visual Pinball X", table, placed_by)


class _Memory:
    """The memory in a folder of its own."""

    def keep_apart(self: Any) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        patcher = patch.object(unshown, "FILE", Path(held.name) / "capture" / "unshown.json")
        patcher.start()
        self.addCleanup(patcher.stop)


class LearnTests(_Memory, _Sessions):
    def setUp(self) -> None:
        super().setUp()
        self.keep_apart()

    def record_placed(self, kinds: tuple[str, ...], desktop: list[Window],
                      placer: placing.Placer | None = None) -> session.Result:
        return self.record(Cabinet(), kinds, desktop=desktop,
                           shown=replace(FROM_VPX, placer=placer or _placer()))

    def remembered(self, placer: placing.Placer | None = None) -> dict[str, Any]:
        with patch.object(placing, "placer", return_value=placer or _placer()), \
                patch("common.host.launch.this_devices_copy", side_effect=lambda game: game):
            return unshown.of("game1", object())

    def test_a_window_to_record_that_the_desktop_showed_nowhere_is_remembered(self) -> None:
        self.record_placed(VIDEOS, UP)

        said = self.remembered()
        self.assertEqual(list(said), ["scoreview"])
        self.assertEqual((said["scoreview"]["key"], said["scoreview"]["fix"]),
                         (placing.NOT_SHOWN_LAST, "none"))
        self.assertEqual(preflight.words(said["scoreview"]),
                         "Visual Pinball X showed no DMD window when last recorded")

    def test_only_for_that_table_under_what_placed_it(self) -> None:
        self.record_placed(VIDEOS, UP)

        self.assertTrue(self.remembered())
        self.assertEqual(self.remembered(_placer(placed_by="settings-2")), {})
        self.assertEqual(self.remembered(_placer(table="/games/Other/Other.vpx")), {})

    def test_a_recording_of_the_table_that_sees_it_forgets_it(self) -> None:
        self.record_placed(VIDEOS, UP)
        self.assertTrue(self.remembered())
        self.record_placed(("playfield_video",), DMD_UP)

        self.assertEqual(self.remembered(), {})
        self.assertFalse(unshown._read())

    def test_what_placed_it_changing_starts_it_afresh(self) -> None:
        self.record_placed(VIDEOS, UP)
        self.assertTrue(self.remembered())
        self.record_placed(("playfield_video",), UP, _placer(placed_by="settings-2"))

        self.assertEqual(self.remembered(_placer(placed_by="settings-2")), {})
        self.assertFalse(unshown._read())

    def test_nothing_is_learned_where_the_desktop_cannot_say_or_it_was_not_asked_for(
            self) -> None:
        self.record_placed(VIDEOS, [UP[2]])
        self.record_placed(("playfield_video", "backglass_video"), UP)

        self.assertFalse(unshown.FILE.exists())


class _Remembered(_Memory):
    """The fake library's game, its default table's DMD remembered as not shown."""

    def remember(self: Any) -> None:
        tables = {None: f"{FOLDER}.vpx", "Mod.vpx": "Mod.vpx"}

        def placer(_game: Any, table: str | None = None) -> placing.Placer:
            return _placer(table=tables[table])

        patcher = patch.object(placing, "placer", side_effect=placer)
        patcher.start()
        self.addCleanup(patcher.stop)
        unshown.learn(GAME_ID, _placer(table=tables[None]), {"playfield": "DP-1"},
                      ["scoreview"])


class PlanTests(_Remembered, _Library):
    def setUp(self) -> None:
        super().setUp()
        self.keep_apart()
        self.remember()

    def rows(self, planned: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {row["kind"]: row for row in planned["targets"][0]["kinds"]}

    def test_its_kinds_are_left_for_that_table_with_why(self) -> None:
        planned = self.plan(existing=run.REPLACE_ALL)
        rows = self.rows(planned)

        for kind in ("scoreview", "scoreview_video"):
            with self.subTest(kind):
                self.assertEqual(rows[kind]["does"], run.LEFT)
                self.assertEqual(rows[kind]["reason"]["key"], placing.NOT_SHOWN_LAST)
        self.assertEqual(planned["recording"],
                         ["playfield", "playfield_video", "backglass", "backglass_video"])

    def test_the_games_other_table_still_records_it(self) -> None:
        planned = self.plan(games=[], tables=[(GAME_ID, MOD)], kinds=["scoreview"])

        self.assertEqual(self.rows(planned)["scoreview"]["does"], run.FILLED)

    def test_a_kind_counts_only_the_slots_that_can_be_recorded(self) -> None:
        one = self.plan(kinds=["scoreview", "playfield"])
        both = self.plan(tables=[(GAME_ID, MOD)], kinds=["scoreview"])

        self.assertEqual({row["kind"]: (row["reason"]["key"] if row["reason"] else None,
                                        row["missing"]) for row in one["kinds"]},
                         {"scoreview": (placing.NOT_SHOWN_LAST, 0), "playfield": (None, 1)})
        self.assertEqual((both["kinds"][0]["reason"], both["kinds"][0]["missing"]), (None, 1))

    def test_the_record_dialog_dims_it_with_why(self) -> None:
        slots = record.slots_of(self.plan(kinds=["scoreview", "playfield"]))

        self.assertEqual(record.blocked(slots["scoreview"]),
                         "Visual Pinball X showed no DMD window when last recorded")
        self.assertEqual(record.ticked(["scoreview", "playfield"], slots, {}, False),
                         {"scoreview": False, "playfield": True})

    def test_the_remotes_sheet_neither_lists_it_nor_offers_to_fill_it(self) -> None:
        planned = self.plan()

        self.assertEqual(remote_record.lacking(planned),
                         "No Playfield or Backglass Video")
        self.assertEqual(remote_record.offered(self.plan(kinds=["scoreview"])), [])


class RunTests(_Remembered, _Started):
    def setUp(self) -> None:
        super().setUp()
        self.keep_apart()
        self.remember()

    def test_missing_only_skips_a_game_left_with_nothing_else_and_says_why(self) -> None:
        job = self.finished(run.start(Request(games=[GAME_ID], kinds=["scoreview"])))

        assert isinstance(job.result, dict)
        self.assertEqual(self.sessions, [])
        table = job.result["tables"][0]
        self.assertEqual((table["state"], table["reason"]["key"]),
                         (session.SKIPPED, placing.NOT_SHOWN_LAST))


class OfferTests(_Remembered, _Frontend):
    """The frontend's menu, for a game whose only empty slot is the remembered one."""

    def setUp(self) -> None:
        super().setUp()
        self.keep_apart()
        for name in ("table.png", "bg.mp4"):
            (self.root / FOLDER / "medias" / name).write_bytes(b"x")

    def test_missing_only_is_offered_until_it_is_remembered(self) -> None:
        before = self.page.recording_offer(0)
        self.remember()
        after = self.page.recording_offer(0)

        self.assertEqual(before["label"], "Record Missing Media")
        self.assertEqual((after["label"], [one["existing"] for one in after["choices"]]),
                         ("Record Media", [run.CHOOSE]))

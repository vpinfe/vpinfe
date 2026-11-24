"""The Remote's part in recording: what it reads of a target, what a game's sheet says it
lacks and offers, a run's card, and the review it drives on the frontend."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from manager.kit.api import ApiError
from manager.remote import remote_record


def _waiting(proposal_id: str, game_id: str, kind: str, *, replaces: bool = True
             ) -> dict[str, Any]:
    return {"id": proposal_id, "game_id": game_id, "table_id": "", "kind": kind,
            "name": f"Game {game_id}", "replaces": {"path": "x", "goes": True}
            if replaces else None}


class Target:
    """The routes the Remote calls, answering as a target does."""

    def __init__(self, *rows: dict[str, Any], records: bool = True) -> None:
        self.rows = list(rows)
        self.records = records
        self.shown: list[tuple[str, str]] = []
        self.ended = 0
        self.decided: list[tuple[str, bool]] = []
        self.run: dict[str, Any] = {}

    def capabilities(self) -> list[dict[str, Any]]:
        return [{"name": "capture", "available": True, "reason": None}] if self.records \
            else [{"name": "launch", "available": True}]

    def capture_run(self) -> dict[str, Any]:
        return self.run

    def capture_proposals(self) -> dict[str, Any]:
        return {"count": len(self.rows), "proposals": list(self.rows)}

    def show_proposal(self, proposal_id: str, showing: str) -> None:
        self.shown.append((proposal_id, showing))

    def end_preview(self) -> None:
        self.ended += 1

    def use_proposal(self, proposal_id: str, use: bool) -> dict[str, Any]:
        self.decided.append((proposal_id, use))
        self.rows = [row for row in self.rows if row["id"] != proposal_id]
        return {}


def _io(call: Any, *args: Any, **kwargs: Any) -> Any:
    return call(*args, **kwargs)


def _plan(**kinds: tuple[int, int, bool]) -> dict[str, Any]:
    return {"kinds": [{"kind": kind, "missing": missing, "have": have,
                       "reason": {"key": "capture.screen.not_shown"} if blocked else None}
                      for kind, (missing, have, blocked) in kinds.items()]}


class ReadTests(unittest.TestCase):
    def test_a_target_that_records_says_its_run_and_what_waits(self) -> None:
        target = Target(_waiting("a", "g1", "playfield_video"))
        target.run = {"state": "running", "done": 2, "of": 24}

        found = remote_record.read(target)

        self.assertEqual(found["capture"]["name"], "capture")
        self.assertEqual(found["run"]["of"], 24)
        self.assertEqual([row["id"] for row in found["waiting"]], ["a"])

    def test_one_that_does_not_record_or_cannot_say_records_nothing(self) -> None:
        empty: dict[str, Any] = {"capture": None, "run": {}, "waiting": []}
        self.assertEqual(remote_record.read(Target(records=False)), empty)

        broken = Target()
        with mock.patch.object(broken, "capture_proposals", side_effect=ApiError("gone")):
            self.assertEqual(remote_record.read(broken), empty)


class WordsTests(unittest.TestCase):
    def test_how_far_a_run_has_come_counts_the_game_in_hand(self) -> None:
        self.assertEqual(remote_record.place({"done": 2, "of": 24}), "3 of 24")
        self.assertEqual(remote_record.place({"done": 24, "of": 24}), "24 of 24")
        self.assertEqual(remote_record.place({"done": 0, "of": 1}), "")

    def test_what_a_game_lacks_among_the_kinds_the_target_records(self) -> None:
        plan = _plan(playfield=(0, 1, False), playfield_video=(1, 0, False),
                     backglass_video=(1, 0, False), scoreview_video=(1, 0, False),
                     topper_video=(1, 0, True))

        self.assertEqual(remote_record.lacking(plan),
                         "No Playfield Video, Backglass Video or DMD Video")
        self.assertEqual(remote_record.lacking(_plan(playfield_video=(1, 0, False))),
                         "No Playfield Video")
        self.assertEqual(remote_record.lacking(_plan(playfield=(0, 1, False))), "")

    def test_missing_only_where_something_is_missing_and_replace_where_something_is_there(
            self) -> None:
        self.assertEqual(remote_record.offered(_plan(playfield=(1, 0, False))), ["fill"])
        self.assertEqual(remote_record.offered(_plan(playfield=(0, 1, False))), ["choose"])
        self.assertEqual(remote_record.offered(_plan(playfield=(1, 0, False),
                                                     backglass=(0, 1, False))),
                         ["fill", "choose"])
        self.assertEqual(remote_record.offered(_plan(topper=(1, 0, True))), [])

    def test_the_review_goes_a_game_at_a_time_window_by_window(self) -> None:
        rows = [_waiting("a", "g1", "backglass_video"), _waiting("b", "g2", "playfield"),
                _waiting("c", "g1", "playfield_video")]

        self.assertEqual([row["id"] for row in remote_record.queue(rows)], ["c", "a", "b"])


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(mock.patch.object(remote_record, "ui"))
        self.enterContext(mock.patch.object(remote_record.offload, "io",
                                            mock.AsyncMock(side_effect=_io)))
        self.enterContext(mock.patch.object(remote_record.run, "io_bound",
                                            mock.AsyncMock(side_effect=_io)))
        self.target = Target(_waiting("a", "g1", "playfield_video"),
                             _waiting("b", "g1", "backglass_video"),
                             _waiting("c", "g2", "playfield_video", replaces=False))
        self.state: dict[str, Any] = {"waiting": list(self.target.rows), "reviewing": None}
        self.redraw = mock.Mock()
        self.buttons: dict[str, Any] = {}
        self.enterContext(mock.patch.object(remote_record.panel, "remote_action",
                                            self._button))

    def _button(self, label: str, act: Any = None, **_kwargs: Any) -> Any:
        self.buttons[label] = act
        return mock.MagicMock()

    def _draw(self) -> None:
        self.buttons.clear()
        remote_record.controller(self.state, lambda: self.target, self.redraw)

    async def _begin(self) -> None:
        await remote_record.begin(self.state, lambda: self.target, self.redraw)
        self._draw()

    async def test_each_recording_is_shown_after_first_and_use_this_moves_on(self) -> None:
        await self._begin()
        self.assertEqual(self.target.shown, [("a", "after")])

        await self.buttons["Use This"]()
        self._draw()
        await self.buttons["Discard"]()

        self.assertEqual(self.target.decided, [("a", True), ("b", False)])
        self.assertEqual(self.target.shown, [("a", "after"), ("b", "after"), ("c", "after")])
        self.assertEqual([row["id"] for row in self.state["waiting"]], ["c"])

    async def test_skip_decides_nothing_and_the_last_one_ends_the_preview(self) -> None:
        await self._begin()
        for _ in range(3):
            self._draw()
            await self.buttons["Skip"]()

        self.assertEqual(self.target.decided, [])
        self.assertIsNone(self.state["reviewing"])
        self.assertEqual(self.target.ended, 1)
        self.assertEqual(len(self.state["waiting"]), 3)

    async def test_stop_ends_it_leaving_the_rest_waiting(self) -> None:
        await self._begin()
        await self.buttons["Stop"]()

        self.assertIsNone(self.state["reviewing"])
        self.assertEqual(self.target.ended, 1)
        self.assertEqual(self.target.decided, [])

    async def test_before_is_offered_only_where_there_is_a_file_to_be_replaced(self) -> None:
        await self._begin()
        self.ui.toggle.return_value.props.return_value.classes.return_value \
            .set_enabled.assert_called_with(True)

        self.state["reviewing"]["at"] = 2
        self._draw()
        self.ui.toggle.return_value.props.return_value.classes.return_value \
            .set_enabled.assert_called_with(False)

    async def test_turning_to_before_shows_it_on_the_frontend_and_draws_nothing_again(
            self) -> None:
        await self._begin()
        self.redraw.reset_mock()
        turn = self.ui.toggle.call_args.kwargs["on_change"]

        await turn(SimpleNamespace(value="before", sender=mock.Mock()))

        self.assertEqual(self.target.shown[-1], ("a", "before"))
        self.assertEqual(self.state["reviewing"]["showing"], "before")
        self.redraw.assert_not_called()

    async def test_a_turn_the_frontend_would_not_show_turns_back(self) -> None:
        await self._begin()
        turn = self.ui.toggle.call_args.kwargs["on_change"]
        sender = mock.Mock()

        with mock.patch.object(self.target, "show_proposal",
                               side_effect=RuntimeError("gone")):
            await turn(SimpleNamespace(value="before", sender=sender))

        self.assertEqual(self.state["reviewing"]["showing"], "after")
        sender.set_value.assert_called_once_with("after")

    async def test_the_frontend_turning_before_and_after_turns_the_phone(self) -> None:
        await self._begin()

        await remote_record.heard(self.state, {"running": True, "preview": {
            "proposal": "a", "showing": "before"}}, lambda: self.target, self.redraw)

        self.assertEqual(self.state["reviewing"]["showing"], "before")

    async def test_one_decided_at_the_frontend_moves_the_phone_on(self) -> None:
        await self._begin()
        self.target.rows = self.target.rows[1:]

        await remote_record.heard(self.state, {"running": True, "preview": None},
                                  lambda: self.target, self.redraw)

        self.assertEqual(self.state["reviewing"]["at"], 1)
        self.assertEqual(self.target.shown[-1], ("b", "after"))

    async def test_one_decided_while_the_frontend_moved_to_another_moves_on_too(
            self) -> None:
        await self._begin()
        self.target.rows = self.target.rows[1:]

        await remote_record.heard(self.state, {"running": True, "preview": {
            "proposal": "z", "showing": "after"}}, lambda: self.target, self.redraw)

        self.assertEqual(self.state["reviewing"]["at"], 1)

    async def test_a_preview_ended_with_the_recording_still_waiting_is_left_alone(
            self) -> None:
        await self._begin()

        await remote_record.heard(self.state, {"running": True, "preview": None},
                                  lambda: self.target, self.redraw)
        self.state["reviewing"]["acting"] = True
        self.target.rows = self.target.rows[1:]
        await remote_record.heard(self.state, {"running": True, "preview": None},
                                  lambda: self.target, self.redraw)

        self.assertEqual(self.state["reviewing"]["at"], 0)
        self.assertEqual(self.target.shown, [("a", "after")])

    async def test_the_frontend_closing_ends_the_review(self) -> None:
        await self._begin()

        await remote_record.heard(self.state, {"running": False}, lambda: self.target,
                                  self.redraw)

        self.assertIsNone(self.state["reviewing"])


class RunCardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.enterContext(mock.patch.object(remote_record, "ui"))
        self.buttons: dict[str, Any] = {}
        self.enterContext(mock.patch.object(
            remote_record.panel, "remote_action",
            lambda label, act=None, **_kwargs: self.buttons.setdefault(label, act)))
        self.redraw = mock.Mock()

    def _card(self, run: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = {"run": run, "run_heard": 3}
        remote_record.run_card(state, mock.Mock(), self.redraw)
        return state

    def test_a_run_going_offers_pause_and_one_paused_resume_each_with_stop(self) -> None:
        self._card({"state": "running", "done": 2, "of": 24, "game": {"name": "A"}})
        self.assertEqual(list(self.buttons), ["Pause", "Stop"])

        self.buttons.clear()
        self._card({"state": "paused", "done": 2, "of": 24, "game": {"name": "A"}})
        self.assertEqual(list(self.buttons), ["Resume", "Stop"])

    async def test_a_change_heard_while_stop_was_asked_is_the_later_word(self) -> None:
        state = self._card({"state": "running", "done": 2, "of": 24})

        def stopped() -> dict[str, Any]:
            state.update(run={}, run_heard=4)
            return {"state": "running", "done": 2, "of": 24}

        client = mock.Mock(stop_capture=stopped)
        with mock.patch.object(remote_record.offload, "io", mock.AsyncMock(side_effect=_io)):
            self.buttons.clear()
            remote_record.run_card(state, lambda: client, self.redraw)
            await self.buttons["Stop"]()

        self.assertEqual(state["run"], {})


class RecordingsTableTests(unittest.TestCase):
    """A recording's table up is the run's card, wherever the Remote draws what is up."""

    def _drawn(self, play: dict[str, Any]) -> tuple[Any, Any]:
        from manager.remote import remote

        with mock.patch.object(remote, "ui"), mock.patch.object(remote, "_hero"), \
                mock.patch.object(remote, "panel") as panel, \
                mock.patch.object(remote.remote_record, "run_card") as card:
            remote._playing(play, {"run": {"state": "running", "of": 3}, "frontend": None},
                            mock.Mock(), mock.Mock())
        return card, panel

    def test_quit_table_gives_way_to_the_runs_card(self) -> None:
        card, panel = self._drawn({"launching": True, "source": "capture"})
        card.assert_called_once()
        panel.remote_action.assert_not_called()

        card, panel = self._drawn({"launching": True, "source": "frontend"})
        card.assert_not_called()
        self.assertEqual(panel.remote_action.call_args.args[0], "Quit Table")


class SheetPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_line_says_what_the_game_lacks_and_each_choice_records(self) -> None:
        client = mock.Mock()
        client.plan_capture.return_value = _plan(playfield_video=(1, 0, False),
                                                 backglass=(0, 1, False))
        client.start_capture.return_value = {"id": "j1"}
        lacks, started = mock.Mock(), mock.AsyncMock()
        with mock.patch("manager.kit.on_page.ui"), \
                mock.patch.object(remote_record, "ui") as ui, \
                mock.patch.object(remote_record.panel, "remote_action"), \
                mock.patch.object(remote_record.offload, "io",
                                  mock.AsyncMock(side_effect=_io)), \
                mock.patch.object(remote_record.busy, "fill") as fill:
            remote_record.sheet_entry({"id": "g1"}, {"capture": {"available": True}},
                                      lambda: client, lacks, started)
            await fill.call_args.args[1]()
            choices = {call.args[0]: call.kwargs["on_click"]
                       for call in ui.menu_item.call_args_list}
            await choices["Missing Only"]()

        lacks.set_text.assert_called_once_with("No Playfield Video")
        self.assertEqual(list(choices), ["Missing Only", "Replace"])
        client.start_capture.assert_called_once_with({"games": ["g1"], "existing": "fill"})
        started.assert_awaited_once()


class SheetTests(unittest.TestCase):
    def test_not_drawn_where_the_target_does_not_record(self) -> None:
        with mock.patch.object(remote_record.panel, "remote_action") as button:
            remote_record.sheet_entry({"id": "g1"}, {"capture": None}, mock.Mock(),
                                      mock.Mock(), mock.AsyncMock())
        button.assert_not_called()

    def test_dimmed_with_the_reason_where_it_cannot_record(self) -> None:
        with mock.patch.object(remote_record, "ui") as ui, \
                mock.patch.object(remote_record.panel, "remote_action") as button, \
                mock.patch.object(remote_record.busy, "fill") as fill:
            remote_record.sheet_entry(
                {"id": "g1"}, {"capture": {"available": False,
                                           "reason": "Recording isn't supported on macOS yet"}},
                mock.Mock(), mock.Mock(), mock.AsyncMock())

        button.return_value.set_enabled.assert_called_once_with(False)
        self.assertEqual(ui.label.call_args.args[0], "Recording isn't supported on macOS yet")
        fill.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Recordings kept for a decision, reviewed a game at a time: what each game's page holds,
the way on from it, and which door opens it."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import contextmanager
from typing import Any
from unittest import mock

from manager.library import record
from manager.settings import recording

NOT_SHOWN = {"key": "capture.screen.not_shown", "fix": "none", "remedy": None,
             "params": {"app": "Visual Pinball X", "window": "scoreview"}}


def _waiting(proposal_id: str, game_id: str, kind: str, table_id: str = "",
             name: str = "") -> dict[str, Any]:
    return {"id": proposal_id, "game_id": game_id, "table_id": table_id, "kind": kind,
            "name": name or f"Game {game_id}", "file": f"{kind}.mp4", "size": 10,
            "url": f"/api/v1/capture/proposals/{proposal_id}/file", "replaces": None}


class Library:
    def __init__(self, *rows: dict[str, Any]) -> None:
        self.waiting = {"count": len(rows), "bytes": 10 * len(rows), "proposals": list(rows)}

    def capture_proposals(self) -> dict[str, Any]:
        return self.waiting


def _io(call: Any, *args: Any, **kwargs: Any) -> Any:
    return call(*args, **kwargs)


class Box:
    """The dialog: awaited until something submits it."""

    def __init__(self) -> None:
        self.answered: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    def submit(self, value: Any) -> None:
        if not self.answered.done():
            self.answered.set_result(value)

    def __await__(self) -> Any:
        return self.answered.__await__()


class Button:
    def __init__(self, label: str, on_click: Any) -> None:
        self.label, self.on_click, self.visible = label, on_click, True

    def set_visibility(self, visible: bool) -> None:
        self.visible = visible


class PagesTests(unittest.TestCase):
    def test_a_game_at_a_time_a_table_apart_and_each_window_by_window(self) -> None:
        rows = [_waiting("a", "g1", "backglass_video"), _waiting("b", "g2", "playfield"),
                _waiting("c", "g1", "playfield"), _waiting("e", "g1", "audio"),
                _waiting("d", "g1", "playfield", table_id="t1")]

        self.assertEqual([[row["id"] for row in page] for page in record.grouped(rows)],
                         [["c", "a", "e"], ["b"], ["d"]])

    def test_skip_until_every_file_is_decided_then_next_and_done_from_the_last(
            self) -> None:
        self.assertEqual(record.onward(0, 3, settled=False), record.SKIP)
        self.assertEqual(record.onward(1, 3, settled=True), record.NEXT)
        self.assertEqual(record.onward(2, 3, settled=False), record.DONE)
        self.assertEqual(record.onward(0, 1, settled=False), record.DONE)


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(mock.patch.object(record, "ui"))
        self.enterContext(mock.patch.object(record.offload, "io",
                                            mock.AsyncMock(side_effect=_io)))
        self.shown: list[tuple[str, Any]] = []
        self.enterContext(mock.patch.object(
            record, "proposal",
            lambda _library, row, decided: self.shown.append((row["id"], decided))))
        self.buttons: dict[str, Button] = {}
        self.enterContext(mock.patch.object(record.frame, "opened", self._opened))
        self.enterContext(mock.patch.object(record.frame, "footer"))
        self.enterContext(mock.patch.object(record.frame, "quiet", self._button))
        self.enterContext(mock.patch.object(record.frame, "answer", self._button))
        self.titles: list[str] = []
        self.ui.label.side_effect = self._label
        self.then = mock.AsyncMock()
        self.act = self.enterContext(mock.patch.object(record.undo, "act"))

    @contextmanager
    def _opened(self, title: str, **_kwargs: Any) -> Any:
        self.box = Box()
        yield self.box

    def _button(self, label: str, on_click: Any, **_kwargs: Any) -> Button:
        self.buttons[label] = Button(label, on_click)
        return self.buttons[label]

    def _label(self, text: str = "") -> Any:
        made = mock.MagicMock()
        made.classes.return_value = made
        made.set_text.side_effect = self.titles.append
        return made

    def _open(self) -> list[str]:
        return [label for label, button in self.buttons.items() if button.visible]

    async def _start(self, library: Library, proposed: list[str] | None) -> asyncio.Task:
        task = asyncio.create_task(record.review(library, self.then, proposed))
        for _ in range(5):
            await asyncio.sleep(0)
        return task

    async def test_a_runs_games_go_by_one_at_a_time_and_skip_decides_nothing(self) -> None:
        library = Library(_waiting("a", "g1", "playfield_video", name="Attack from Mars"),
                          _waiting("b", "g1", "backglass_video"),
                          _waiting("x", "g9", "playfield_video"),
                          _waiting("c", "g2", "playfield_video", name="Black Knight"))
        task = await self._start(library, ["a", "b", "c"])

        self.assertEqual(self.titles[0], "Recorded for “Attack from Mars”")
        self.assertIn("1 of 2", self.titles)
        self.assertEqual([one for one, _ in self.shown], ["a", "b"])
        self.assertEqual(self._open(), ["Stop", "Skip"])

        self.buttons["Skip"].on_click()
        self.assertIn("Recorded for “Black Knight”", self.titles)
        self.assertEqual([one for one, _ in self.shown][2:], ["c"])
        self.assertEqual(self._open(), ["Done"])

        self.buttons["Done"].on_click()
        await task
        self.then.assert_not_awaited()

    async def test_next_once_every_file_is_decided_and_then_runs_where_one_was_used(
            self) -> None:
        library = Library(_waiting("a", "g1", "playfield_video"),
                          _waiting("b", "g1", "backglass_video"),
                          _waiting("c", "g2", "playfield_video"))
        task = await self._start(library, None)

        decide = dict(self.shown)
        decide["a"](True)
        self.assertEqual(self._open(), ["Stop", "Skip"])
        decide["b"](False)
        self.assertEqual(self._open(), ["Stop", "Next"])

        self.buttons["Stop"].on_click()
        await task
        self.assertEqual([one for one, _ in self.shown], ["a", "b"])
        self.then.assert_awaited_once()

    async def test_closing_with_files_undecided_leaves_a_notice_that_reviews_them(
            self) -> None:
        library = Library(_waiting("a", "g1", "playfield_video"),
                          _waiting("b", "g1", "backglass_video"),
                          _waiting("c", "g2", "playfield_video"))
        task = await self._start(library, None)
        dict(self.shown)["a"](True)

        self.buttons["Stop"].on_click()
        await task

        said = self.act.call_args
        self.assertEqual(said.args[:2], ("2 recordings still waiting", "Review"))
        with mock.patch.object(record, "review", mock.AsyncMock()) as again:
            await said.args[2]()
        assert again.await_args is not None
        self.assertEqual(again.await_args.args[2], ["b", "c"])

    async def test_closing_with_every_file_decided_leaves_no_notice(self) -> None:
        task = await self._start(Library(_waiting("a", "g1", "playfield_video")), ["a"])
        dict(self.shown)["a"](False)

        self.buttons["Done"].on_click()
        await task

        self.act.assert_not_called()

    async def test_one_games_review_is_done_alone_with_no_progress(self) -> None:
        task = await self._start(Library(_waiting("a", "g1", "playfield_video")), ["a"])

        self.assertEqual(self._open(), ["Done"])
        self.assertFalse(any(" of " in said for said in self.titles))
        self.ui.linear_progress.assert_not_called()
        self.buttons["Done"].on_click()
        await task
        self.assertEqual(self.act.call_args.args[0], "1 recording still waiting")

    async def test_nothing_waiting_opens_nothing(self) -> None:
        task = await self._start(Library(_waiting("x", "g9", "playfield_video")), ["a"])

        await task
        self.assertEqual(self.buttons, {})


# The reference cab: no screen shows the DMD, so every game fails its DMD video.
def _game(proposed: list[str] | None = None) -> dict[str, Any]:
    return {"state": "recorded", "placed": [{"kind": "playfield"}],
            "failed": [{"kind": "scoreview_video", "reason": NOT_SHOWN}],
            "proposed": [{"kind": "playfield_video", "id": one} for one in proposed or []],
            "reason": None}


class SlotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(mock.patch.object(record, "ui"))
        self.enterContext(mock.patch.object(record.offload, "io",
                                            mock.AsyncMock(side_effect=_io)))
        self.actions: dict[str, Any] = {}

        def action(label: str, run: Any, **_kwargs: Any) -> Any:
            self.actions[label] = run
            return lambda: None

        self.enterContext(mock.patch.object(record.panel, "action", action))

    def test_only_this_slots_recordings_are_found(self) -> None:
        listing = Library(_waiting("a", "g1", "playfield_video"),
                          _waiting("b", "g1", "playfield_video", table_id="t1"),
                          _waiting("c", "g1", "backglass_video"),
                          _waiting("d", "g2", "playfield_video")).waiting

        found = record.waiting_in(listing, "g1", "", "playfield_video")

        self.assertEqual([row["id"] for row in found], ["a"])

    async def test_a_slot_says_a_recording_waits_with_its_size(self) -> None:
        rows = [_waiting("a", "g1", "playfield_video")]
        record.waiting_line(Library(*rows), rows, mock.AsyncMock())

        self.assertEqual(self.ui.label.call_args.args[0], "Recording waiting, 10 B")
        self.assertEqual(list(self.actions), ["Review", "Discard"])

    async def test_discard_throws_the_slots_recordings_away_and_redraws(self) -> None:
        rows = [_waiting("a", "g1", "playfield_video"), _waiting("b", "g1", "playfield_video")]
        library = mock.Mock()
        then = mock.AsyncMock()
        record.waiting_line(library, rows, then)

        await self.actions["Discard"]()

        self.assertEqual([call.args for call in library.use_proposal.call_args_list],
                         [("a", False), ("b", False)])
        then.assert_awaited_once()

    async def test_review_opens_this_slots_recordings_then_reads_the_game_again(
            self) -> None:
        rows = [_waiting("a", "g1", "playfield_video")]
        library = mock.Mock()
        then = mock.AsyncMock()
        record.waiting_line(library, rows, then)

        with mock.patch.object(record, "review", mock.AsyncMock()) as opened:
            await self.actions["Review"]()

        assert opened.await_args is not None
        self.assertEqual(opened.await_args.args[2], ["a"])
        library.forget_media.assert_not_called()

        await opened.await_args.args[1]()

        library.forget_media.assert_called_once_with("g1")
        then.assert_awaited_once()


class ChooserTests(unittest.TestCase):
    """The recording beside what it would replace: which column holds which."""

    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(mock.patch.object(record, "ui"))
        self.enterContext(mock.patch.object(record.mediaview, "preview"))
        self.enterContext(mock.patch.object(record.panel, "line"))
        self.calls: list[str] = []
        self.ui.label.side_effect = (
            lambda text="": self.calls.append(text) or mock.MagicMock())  # type: ignore[func-returns-value]

        def action(label: str, *_args: Any, **_kwargs: Any) -> Any:
            self.calls.append(f"[{label}]")
            return lambda: None

        self.enterContext(mock.patch.object(record.panel, "action", action))

    def test_the_buttons_come_after_the_new_recording_and_never_after_the_current(
            self) -> None:
        row = {**_waiting("a", "g1", "playfield_video"),
               "replaces": {"path": "/x/pf.mp4", "source": "user", "goes": True}}

        record.proposal(mock.Mock(), row, mock.Mock(), titled=False)

        self.assertEqual(self.calls, ["Current", "New recording", "10 B",
                                      "[Use This]", "[Discard]"])

    def test_an_empty_slot_says_there_is_nothing_there(self) -> None:
        record.proposal(mock.Mock(), _waiting("a", "g1", "playfield_video"), mock.Mock(),
                        titled=False)

        self.assertEqual(self.calls[:3], ["Current", "Nothing there yet", "New recording"])


class NoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(mock.patch.object(record, "ui"))
        self.act = self.enterContext(mock.patch.object(record.undo, "act"))
        self.state = {"show_recorded": mock.AsyncMock()}

    async def _said(self, tables: list[dict[str, Any]], state: str = "done") -> Any:
        await record.say_run(Library(), {"tables": tables, "run": {
            "state": state, "done": 1, "of": len(tables),
            "reason": {"key": "capture.run.space", "params": {"device": "Cab 1"}}}},
            self.state, mock.AsyncMock())
        return self.act.call_args

    async def test_a_run_that_left_recordings_waiting_offers_review_of_its_own(
            self) -> None:
        with mock.patch.object(record, "review", mock.AsyncMock()) as review:
            said = await self._said([_game(["a1", "a2"]), _game(), _game(["b1"])])
            await said.args[2]()

        self.assertEqual(said.args[:2], (
            "Recorded 3 games, 3 wait for a decision", "Review"))
        self.assertEqual(said.kwargs["caption"],
                         "DMD Video: Visual Pinball X doesn't show the DMD on a screen of "
                         "its own")
        assert review.await_args is not None
        self.assertEqual(review.await_args.args[2], ["a1", "a2", "b1"])

    async def test_show_where_nothing_waits_and_no_review_while_the_run_is_paused(
            self) -> None:
        said = await self._said([_game(), _game()])
        self.assertEqual(said.args[1], "Show")

        said = await self._said([_game(["a1"])], state="paused")
        self.assertEqual((said.args[0], said.args[1]),
                         ("Recording paused at 2 of 1", "Show"))


class SettingsDoorTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_rereads_the_games_it_used_and_the_count(self) -> None:
        library = mock.Mock()
        refill = mock.Mock()
        waiting = Library(_waiting("a", "g1", "playfield_video"),
                          _waiting("b", "g2", "playfield_video")).waiting
        actions: dict[str, Any] = {}

        def action(label: str, run: Any, **_kwargs: Any) -> Any:
            actions[label] = run
            return lambda: None

        with mock.patch.object(recording, "ui") as ui, \
                mock.patch("manager.kit.on_page.ui"), \
                mock.patch.object(recording.panel, "action", action), \
                mock.patch.object(recording.run, "io_bound",
                                  mock.AsyncMock(side_effect=_io)), \
                mock.patch.object(record, "review", mock.AsyncMock()) as review:
            label, draw = recording._waiting(library, refill, waiting)
            draw()
            await actions["Review"]()
            assert review.await_args is not None
            await review.await_args.args[1]()

        self.assertEqual(label, "Waiting for a Decision")
        self.assertEqual(list(actions), ["Review", "Discard All"])
        self.assertEqual([call.args[0] for call in ui.label.call_args_list][1:],
                         ["Game g1 - Playfield Video", "Game g2 - Playfield Video"])
        library.reread_media.assert_called_once_with({"g1", "g2"})
        refill.assert_called_once()


if __name__ == "__main__":
    unittest.main()

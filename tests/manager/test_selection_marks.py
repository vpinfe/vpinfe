"""The marks of a selection: what a mark reads as across the rows, what a change writes to
them, and what Undo is handed back."""

from __future__ import annotations

import unittest
from collections import Counter
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from manager.workbench import selection_marks as marks


def _table(table_id: str, game_id: str = "g1", **fields: object) -> dict:
    return {"id": table_id, "game_id": game_id, **fields}


def _game(game_id: str, **fields: object) -> dict:
    return {"id": game_id, **fields}


class ReadTests(unittest.TestCase):
    def test_a_table_reads_its_own_marks(self) -> None:
        row = _table("t", rating=4, hidden=True, delete_nvram_on_close=True,
                     user={"tags": ["a", "b"]})

        self.assertEqual((4, True, True, ["a", "b"]),
                         (marks.rating_of(row), marks.hidden_of(row), marks.nvram_of(row),
                          marks.tags_of(row)))

    def test_a_row_with_nothing_set_reads_as_off_and_empty(self) -> None:
        row = _table("t")

        self.assertEqual((0, False, False, False, []),
                         (marks.rating_of(row), marks.hidden_of(row), marks.nvram_of(row),
                          marks.favorite_of(row), marks.tags_of(row)))

    def test_a_game_reads_favorite_and_private(self) -> None:
        row = _game("g", private=True, user={"favorite": True})

        self.assertEqual((True, True), (marks.favorite_of(row), marks.private_of(row)))

    def test_rows_that_agree_read_as_one_value_and_rows_that_differ_as_several(self) -> None:
        rows = [_table("a", rating=3), _table("b", rating=3), _table("c", rating=1)]

        self.assertEqual(Counter({3: 2, 1: 1}), marks.shared(rows, marks.rating_of))
        self.assertEqual(Counter({3: 2}), marks.shared(rows[:2], marks.rating_of))

    def test_a_launcher_is_named_only_where_the_table_chose_one_that_exists(self) -> None:
        context = {"launchers": {"launchers": [{"launcher_id": "vpx"}],
                                 "mappings": {"a": "vpx", "b": "gone"}}}

        self.assertEqual(["vpx", "", ""],
                         [marks.named_launcher(context, _table(one)) for one in "abc"])


class EditTests(unittest.TestCase):
    def test_a_table_names_its_game_and_itself(self) -> None:
        self.assertEqual({"game_id": "g1", "table_id": "t", "rating": 2},
                         marks.TABLES.edit(_table("t"), {"rating": 2}))

    def test_a_game_names_only_itself(self) -> None:
        self.assertEqual({"game_id": "g", "favorite": True},
                         marks.GAMES.edit(_game("g"), {"favorite": True}))


class ChangeTests(unittest.IsolatedAsyncioTestCase):
    async def _change(self, kind: marks.Kind, rows: list[dict], name: str, new, old) -> tuple:
        sent = AsyncMock()
        with patch.object(marks, "apply", sent):
            await marks.change({kind.rows: rows}, kind, name, new, old)
        return sent.await_args.args[2:] if sent.await_args else ((), ())

    async def test_each_row_is_put_back_to_what_it_held_itself(self) -> None:
        rows = [_table("a", rating=1), _table("b", rating=5)]

        forward, back = await self._change(marks.TABLES, rows, "rating",
                                           lambda _row: 3, marks.rating_of)

        self.assertEqual([3, 3], [edit["rating"] for edit in forward])
        self.assertEqual([1, 5], [edit["rating"] for edit in back])

    async def test_a_row_already_holding_the_value_is_left_out_of_both(self) -> None:
        rows = [_table("a", rating=3), _table("b", rating=5)]

        forward, back = await self._change(marks.TABLES, rows, "rating",
                                           lambda _row: 3, marks.rating_of)

        self.assertEqual(["b"], [edit["table_id"] for edit in forward])
        self.assertEqual(["b"], [edit["table_id"] for edit in back])

    async def test_nothing_to_change_writes_nothing(self) -> None:
        sent = AsyncMock()
        with patch.object(marks, "apply", sent):
            await marks.change({"games": [_game("g", private=True)]}, marks.GAMES, "private",
                               lambda _row: True, marks.private_of)

        sent.assert_not_awaited()

    async def test_adding_a_tag_keeps_what_each_row_carries(self) -> None:
        rows = [_table("a", user={"tags": ["x"]}), _table("b", user={"tags": ["x", "y"]})]

        forward, back = await self._change(
            marks.TABLES, rows, "tags",
            lambda row: [*marks.tags_of(row), *(["y"] if "y" not in marks.tags_of(row) else [])],
            marks.tags_of)

        self.assertEqual([["x", "y"]], [edit["tags"] for edit in forward])
        self.assertEqual([["x"]], [edit["tags"] for edit in back])


class _Library:
    def __init__(self, written: list[dict], failed: list[dict] | None = None,
                 state: str = "done") -> None:
        self.sent: list[list[dict]] = []
        self.forgot = 0
        self._result = {"written": written, "failed": failed or []}
        self._state = state

    def put_marks(self, edits: list[dict]) -> dict:
        self.sent.append(edits)
        return {"id": "job-1"}

    def job(self, _job_id: str) -> dict:
        return {"state": self._state, "result": self._result, "error": "boom"}

    def marks_written(self) -> None:
        self.forgot += 1


class WriteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async def inline(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        for target in (patch.object(marks.run, "io_bound", inline),
                       patch.object(marks, "JOB_POLL_S", 0)):
            target.start()
            self.addCleanup(target.stop)

    async def test_the_edits_go_as_one_job_and_the_caches_are_dropped(self) -> None:
        library = _Library([{"game": "g", "table": "t"}])
        edits = [{"game_id": "g", "table_id": "t", "rating": 2}]

        written, failed = await marks.write({"library": library, "state": {}}, edits)

        self.assertEqual([edits], library.sent)
        self.assertEqual(([("g", "t")], []), (written, failed))
        self.assertEqual(1, library.forgot)

    async def test_a_row_that_was_not_written_comes_back_with_its_reason(self) -> None:
        library = _Library([], [{"game": "g", "table": "t", "reason": "read-only"}])

        written, failed = await marks.write({"library": library, "state": {}}, [{}])

        self.assertEqual(([], [{"reason": "read-only"}]), (written, failed))

    async def test_a_job_that_failed_raises_with_what_it_said(self) -> None:
        library = _Library([], state="failed")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await marks.write({"library": library, "state": {}}, [{}])
        self.assertEqual(1, library.forgot)


class ApplyTests(unittest.IsolatedAsyncioTestCase):
    """`apply` is called under a page; its body is what is under test."""

    async def asyncSetUp(self) -> None:
        self.rebuilt = 0
        self.offered: list[tuple] = []
        self.notified: list[dict] = []
        self.written: list[list[dict]] = []

        async def drawn_again(_context, _kind) -> None:
            self.rebuilt += 1

        self.context: dict[str, Any] = {}
        redraw = patch.object(marks, "_drawn_again", drawn_again)
        redraw.start()
        self.addCleanup(redraw.stop)

        def offer(said, reverse, **kwargs):
            self.offered.append((said, reverse, kwargs))

        def notify(message, **kwargs):
            self.notified.append({"message": message, **kwargs})

        for target in (patch.object(marks.undo, "offer", offer),
                       patch.object(marks.ui, "notify", notify)):
            target.start()
            self.addCleanup(target.stop)

    def _writes(self, written: list[tuple[str, str]], failed: list[dict] | None = None):
        """Every write answers `written`; only the first answers `failed`."""
        async def write(_context, edits):
            self.written.append(edits)
            return written, (failed or []) if len(self.written) == 1 else []

        return patch.object(marks, "write", write)

    async def test_the_rows_written_are_counted_and_undo_puts_back_only_those(self) -> None:
        forward = [{"game_id": "g", "table_id": "a", "rating": 3},
                   {"game_id": "g", "table_id": "b", "rating": 3}]
        back = [{"game_id": "g", "table_id": "a", "rating": 1},
                {"game_id": "g", "table_id": "b", "rating": 5}]

        with self._writes([("g", "b")], [{"reason": "locked"}]):
            await cast(Any, marks.apply).__wrapped__(self.context, marks.TABLES, forward, back)
            said, reverse, kwargs = self.offered[0]
            await reverse.__wrapped__()

        self.assertEqual(marks.t("manager.selection.changed_tables", count=1), said)
        self.assertTrue(kwargs["warn"])
        self.assertEqual([forward, [back[1]]], self.written)
        self.assertEqual(2, self.rebuilt)

    async def test_nothing_written_says_why_and_offers_no_undo(self) -> None:
        with self._writes([], [{"reason": "locked"}]):
            await cast(Any, marks.apply).__wrapped__(self.context, marks.GAMES,
                                          [{"game_id": "g", "rating": 1}],
                                          [{"game_id": "g", "rating": 0}])

        self.assertEqual([], self.offered)
        self.assertEqual("locked", self.notified[0]["caption"])
        self.assertEqual("negative", self.notified[0]["type"])

    async def test_a_job_that_failed_says_so_and_draws_the_panel_again_from_what_is_stored(
            self) -> None:
        async def broken(_context, _edits):
            raise RuntimeError("boom")

        with patch.object(marks, "write", broken):
            await cast(Any, marks.apply).__wrapped__(self.context, marks.GAMES, [{}], [{}])

        self.assertEqual("boom", self.notified[0]["caption"])
        self.assertEqual((1, []), (self.rebuilt, self.offered))

    async def test_an_undo_that_misses_a_row_says_how_many(self) -> None:
        forward = [{"game_id": "g", "table_id": "a", "rating": 3}]
        back = [{"game_id": "g", "table_id": "a", "rating": 1}]

        with self._writes([("g", "a")]):
            await cast(Any, marks.apply).__wrapped__(self.context, marks.TABLES, forward, back)
            _, reverse, _ = self.offered[0]
        async def misses(_context, _edits):
            return [], [{"reason": "locked"}]

        with patch.object(marks, "write", misses), \
                self.assertRaisesRegex(RuntimeError, marks.t("manager.selection.not_written",
                                                              count=1)):
            await reverse.__wrapped__()


class _Mark:
    def __init__(self) -> None:
        self.refilled = 0

    def refill(self) -> None:
        self.refilled += 1


class _Rows:
    games = [{"id": "g", "rating": 4}]

    def load_tables(self) -> list[dict]:
        return [{"id": "a", "rating": 4}, {"id": "other", "rating": 1}]

    def launchers(self) -> dict:
        return {"launchers": [{"launcher_id": "vpx"}]}


class DrawnAgainTests(unittest.IsolatedAsyncioTestCase):
    """After a write the rows are read again in place and every mark is drawn from them."""

    async def asyncSetUp(self) -> None:
        async def inline(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        self.saved = 0

        async def saved() -> None:
            self.saved += 1

        self.tags = 0
        self.mark = _Mark()
        self.context: dict[str, Any] = {
            "saved": saved, "library": _Rows(), "tables": [{"id": "a", "rating": 1}],
            "games": [{"id": "g", "rating": 0}], "marks": {"rating": self.mark},
            "settle_tags": self._settled}
        patched = patch.object(marks.offload, "io", inline)
        patched.start()
        self.addCleanup(patched.stop)

    def _settled(self) -> None:
        self.tags += 1

    async def test_the_selection_s_rows_are_read_again_and_its_marks_drawn_from_them(self) -> None:
        rows = self.context["tables"]

        await marks._drawn_again(self.context, marks.TABLES)

        self.assertIs(rows, self.context["tables"])
        self.assertEqual([{"id": "a", "rating": 4}], rows)
        self.assertEqual((1, 1, 1), (self.saved, self.mark.refilled, self.tags))
        self.assertEqual([{"launcher_id": "vpx", "is_default": False}],
                         self.context["launchers"]["launchers"])

    async def test_games_are_read_from_the_library_s_games(self) -> None:
        await marks._drawn_again(self.context, marks.GAMES)

        self.assertEqual([{"id": "g", "rating": 4}], self.context["games"])
        self.assertEqual(1, self.mark.refilled)

    async def test_a_selection_gone_from_the_library_is_let_be(self) -> None:
        self.context["tables"] = [{"id": "gone", "rating": 2}]

        await marks._drawn_again(self.context, marks.TABLES)

        self.assertEqual([{"id": "gone", "rating": 2}], self.context["tables"])
        self.assertEqual((1, 0, 0), (self.saved, self.mark.refilled, self.tags))


if __name__ == "__main__":
    unittest.main()

"""A selection of tables in the panel: the programs it names, what a write to it keeps for
Undo, and the hover on a setting the tables hold differently."""

from __future__ import annotations

import unittest
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from nicegui import run

from common.i18n import t
from manager.kit import panel
from manager.workbench import all_settings, app_settings
from tests.support.workbench_patch import patch_workbench

KEY = "Player.BallTrail"
OTHER = "Player.FXAA"
GROUPS = [SimpleNamespace(key="more", label="More", curated=[], summarized=False, settings=[
    SimpleNamespace(key=KEY, label="Ball Trail", default="0", description="", help=""),
    SimpleNamespace(key=OTHER, label="FXAA", default="0", description="", help="")])]


class _Tables:
    """Each table's settings as the API answers for them, what was written where, and the
    job that writing several tables makes."""

    def __init__(self, **values: dict) -> None:
        self.values = values
        self.written: list[tuple[list[str], dict]] = []
        self.failed: list[dict] = []
        self.refused: set[str] = set()
        self.skipped: set[str] = set()
        self.refreshed = 0
        self._last: list[str] = []

    def launcher_config(self, _launcher: str, table: str, _scope: str) -> dict:
        return {"values": self.values[table], "shared_with_game": False}

    def write_launcher_config_tables(self, launcher: str, values: dict,
                                     tables: list[str]) -> dict:
        if launcher in self.refused:
            raise RuntimeError("refused")
        self.written.append((list(tables), values))
        self._last = list(tables)
        return {"id": "job-1"} if len(tables) > 1 else {}

    def job(self, _job_id: str) -> dict:
        lost = {one["table"] for one in self.failed} | self.skipped
        return {"state": "done", "result": {
            "failed": self.failed, "written": [one for one in self._last if one not in lost]}}

    def settings_written(self) -> None:
        self.refreshed += 1


def _targets(tables: _Tables, **launchers: str) -> list[dict]:
    return [{"table": {"id": table}, "launcher_id": launchers.get(table, "l1")}
            for table in tables.values]


def _own(value: str) -> dict:
    return {KEY: {"value": value, "set_here": True, "scope": "entry"}}


class ProgramsTests(unittest.TestCase):
    CONTEXT = {"launchers": {"launchers": [
        {"launcher_id": "vpx", "app": "vpx", "app_name": "Visual Pinball X"},
        {"launcher_id": "fp", "app": "fp", "app_name": "Future Pinball"}]}}

    def test_tables_are_grouped_by_program_with_the_most_first(self) -> None:
        tables = [{"id": "a", "launcher": "fp"}, {"id": "b", "launcher": "vpx"},
                  {"id": "c", "launcher": "vpx"}]

        found = app_settings._programs(self.CONTEXT, tables)

        self.assertEqual([([one["id"] for one in held], app_settings._program_name(launcher))
                          for launcher, held in found],
                         [(["b", "c"], "Visual Pinball X"), (["a"], "Future Pinball")])

    def test_a_table_with_no_launcher_has_no_program(self) -> None:
        found = app_settings._programs(self.CONTEXT, [{"id": "a", "launcher": "gone"}])

        self.assertEqual([(launcher, [one["id"] for one in held]) for launcher, held in found],
                         [(None, ["a"])])
        self.assertEqual(app_settings._program_name(None),
                         t("manager.app_settings.no_program"))


class WriteSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async def inline(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        for target in (patch.object(app_settings.offload, "io", inline),
                       patch.object(app_settings, "JOB_POLL_S", 0)):
            target.start()
            self.addCleanup(target.stop)

    async def _write(self, tables: _Tables, value: str) -> dict:
        return await app_settings.write_selection(
            tables, {}, _targets(tables), GROUPS, {KEY: value})

    async def test_undo_holds_each_table_s_own_value_and_a_blank_where_it_held_none(self) -> None:
        tables = _Tables(a=_own("1"), b={KEY: {"value": "0"}}, c=_own("2"))

        wrote = await self._write(tables, "3")

        self.assertEqual(sorted(wrote["undo"]), [
            ("a", "l1", {KEY: "1"}), ("b", "l1", {KEY: ""}), ("c", "l1", {KEY: "2"})])
        self.assertFalse(wrote["blank"])

    async def test_the_tables_go_in_one_write_and_the_job_is_waited_out(self) -> None:
        tables = _Tables(a=_own("1"), b={KEY: {"value": "0"}}, c=_own("2"))

        await self._write(tables, "3")

        self.assertEqual(tables.written, [(["a", "b", "c"], {KEY: "3"})])
        self.assertEqual(tables.refreshed, 1)

    async def test_a_table_already_holding_the_value_is_not_written_or_undone(self) -> None:
        tables = _Tables(a=_own("3"), b={KEY: {"value": "0"}}, c={KEY: {"value": "0"}})

        wrote = await self._write(tables, "3")

        self.assertEqual(tables.written, [(["b", "c"], {KEY: "3"})])
        self.assertEqual([table for table, _, _ in wrote["undo"]], ["b", "c"])

    async def test_clearing_undoes_only_the_tables_that_set_one(self) -> None:
        tables = _Tables(a=_own("1"), b={KEY: {"value": "0"}})

        wrote = await self._write(tables, "")

        self.assertTrue(wrote["blank"])
        self.assertEqual(tables.written, [(["a"], {KEY: ""})])
        self.assertEqual(wrote["undo"], [("a", "l1", {KEY: "1"})])

    async def test_a_table_the_job_could_not_write_is_named_and_left_out_of_undo(self) -> None:
        tables = _Tables(a=_own("1"), b=_own("2"))
        tables.failed = [{"table": "b", "reason": "read-only"}]

        wrote = await self._write(tables, "3")

        self.assertEqual(wrote["failed"], tables.failed)
        self.assertEqual([table for table, _, _ in wrote["undo"]], ["a"])

    async def test_a_launcher_that_cannot_be_written_is_named_and_the_others_keep_their_undo(
            self) -> None:
        tables = _Tables(a=_own("1"), b=_own("2"))
        tables.refused = {"l2"}

        wrote = await app_settings.write_selection(
            tables, {}, _targets(tables, b="l2"), GROUPS, {KEY: "3"})

        self.assertEqual([one["table"] for one in wrote["failed"]], ["b"])
        self.assertEqual(wrote["undo"], [("a", "l1", {KEY: "1"})])

    async def test_a_table_the_job_never_reached_is_failed_and_left_out_of_undo(self) -> None:
        tables = _Tables(a=_own("1"), b=_own("2"))
        tables.skipped = {"b"}

        wrote = await self._write(tables, "3")

        self.assertEqual([one["table"] for one in wrote["failed"]], ["b"])
        self.assertEqual([table for table, _, _ in wrote["undo"]], ["a"])

    async def test_a_table_is_sent_only_the_settings_it_changes(self) -> None:
        tables = _Tables(a={KEY: {"value": "3", "set_here": True}}, b={})

        wrote = await app_settings.write_selection(
            tables, {}, _targets(tables), GROUPS, {KEY: "3", OTHER: "5"})

        self.assertEqual(sorted(tables.written), [
            (["a"], {OTHER: "5"}), (["b"], {KEY: "3", OTHER: "5"})])
        self.assertEqual(sorted(wrote["undo"]), [
            ("a", "l1", {OTHER: ""}), ("b", "l1", {KEY: "", OTHER: ""})])

    async def test_nothing_to_change_holds_nothing_to_undo(self) -> None:
        tables = _Tables(a={KEY: {"value": "0"}}, b={KEY: {"value": "0"}})

        self.assertEqual((await self._write(tables, ""))["undo"], [])
        self.assertEqual(tables.written, [])


class VariesByValueTests(unittest.TestCase):
    def _hover(self, values: list[str], **kwargs) -> str:
        tables = [{"id": str(index)} for index in range(len(values))]
        held = {"varies": True, "each": list(zip(tables, values, strict=True))}
        field = SimpleNamespace(key=KEY, label="Ball Trail", default="0", kind="bool",
                                options=())
        with patch_workbench("_said_value", side_effect=lambda _f, v: v), \
                patch.object(panel, "state") as state:
            drawn = all_settings._varies([(None, held, field)],  # type: ignore[list-item]
                                      tables, "Visual Pinball X", **kwargs)
        assert drawn is not None
        return state.call_args.kwargs["hint"]

    def test_by_value_counts_the_tables_holding_each_and_the_commonest_first(self) -> None:
        hover = self._hover(["Off", "On", "Off", "Off", "On"], by_value=True)

        self.assertEqual(hover.splitlines(), [
            t("manager.workbench.value_count", said="Off", count=3),
            t("manager.workbench.value_count", said="On", count=2)])

    def test_otherwise_each_table_is_named_with_its_value(self) -> None:
        hover = self._hover(["Off", "On"])

        self.assertEqual(len(hover.splitlines()), 2)
        self.assertNotIn(t("manager.workbench.value_count", said="Off", count=1), hover)


class PutBackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async def inline(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        for target in (patch.object(app_settings.offload, "io", inline),
                       patch.object(app_settings, "JOB_POLL_S", 0)):
            target.start()
            self.addCleanup(target.stop)
        self.drawn = 0

    async def _rebuild(self) -> None:
        self.drawn += 1

    async def test_a_table_that_cannot_be_put_back_is_said_and_the_panel_still_draws(self) -> None:
        tables = _Tables(a=_own("1"), b=_own("2"))
        tables.failed = [{"table": "b", "reason": "locked"}]
        inner = {"library": tables, "state": {}, "rebuild": self._rebuild}

        with self.assertRaisesRegex(RuntimeError, "locked"):
            await app_settings._put_back(
                inner, [("a", "l1", {KEY: "1"}), ("b", "l1", {KEY: "1"})])

        self.assertEqual(self.drawn, 1)


class _Launcher(_Tables):
    """The same tables, with the launcher's own values (the launcher) and the table rows
    the library lists."""

    def __init__(self, rows: list[dict], held: dict | None = None, **values: dict) -> None:
        super().__init__(**values)
        self.rows = rows
        self.held = held or {}
        self.order: list[str] = []
        self.launcher_written: list[tuple[str, dict]] = []
        self.unwritable = False

    def load_tables(self) -> list[dict]:
        return self.rows

    def launcher_config(self, launcher: str, table: str = "", scope: str = "entry") -> dict:
        if scope == "launcher":
            return {"values": self.held}
        return super().launcher_config(launcher, table, scope)

    def write_launcher_config(self, launcher: str, values: dict, **_kwargs) -> dict:
        if self.unwritable:
            raise RuntimeError("read-only")
        self.order.append("launcher")
        self.launcher_written.append((launcher, values))
        return {}

    def write_launcher_config_tables(self, launcher: str, values: dict,
                                     tables: list[str]) -> dict:
        self.order.append("tables")
        return super().write_launcher_config_tables(launcher, values, tables)


def _row(table: str, keys: tuple[str, ...] = (), launcher: str = "l1") -> dict:
    return {"id": table, "launcher": launcher, "launcher_settings_keys": list(keys)}


class MoveToAllTests(unittest.IsolatedAsyncioTestCase):
    FIELD = GROUPS[0].settings[0]

    async def asyncSetUp(self) -> None:
        async def inline(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        self.asked: list[dict] = []
        self.answer = True
        self.offered: list[tuple] = []
        self.said: list[tuple] = []
        self.drawn = 0

        async def ask(question: str, **kwargs) -> bool:
            self.asked.append({"question": question, **kwargs})
            return self.answer

        async def keeping(_client, rebuild, written: str) -> None:
            await rebuild()

        for target in (patch.object(app_settings.offload, "io", inline),
                       patch.object(app_settings, "JOB_POLL_S", 0),
                       patch.object(app_settings.confirm, "ask", ask),
                       patch.object(app_settings.undo, "offer",
                                    lambda said, reverse, **_k: self.offered.append(
                                        (said, reverse))),
                       patch_workbench("_keeping_place", keeping),
                       patch.object(app_settings, "ui", SimpleNamespace(
                           context=SimpleNamespace(client=None),
                           notify=lambda *a, **k: self.said.append((a, k))))):
            target.start()
            self.addCleanup(target.stop)

    async def _rebuild(self) -> None:
        self.drawn += 1

    def _inner(self, library: _Launcher, **more) -> dict:
        shared = [{**one, "values": library.values[one["table"]["id"]]}
                  for one in _targets(library)]
        return {"library": library, "state": {}, "rebuild": self._rebuild,
                "tables": [{"id": one} for one in library.values],
                "shared": shared, "config_groups": GROUPS, **more}

    def _move(self, inner: dict, value: str = "1") -> Awaitable[Any]:
        move = cast(Any, app_settings._move_to_all).__wrapped__
        return move(inner, [(self.FIELD, value)], "l1", self._rebuild)

    async def test_it_writes_all_tables_first_then_clears_each_table_that_sets_its_own(
            self) -> None:
        library = _Launcher(
            [_row("a", (KEY,)), _row("b", (KEY,)), _row("c"), _row("d"), _row("e", (OTHER,))],
            a=_own("1"), b=_own("1"), c={KEY: {"value": "0"}})

        await self._move(self._inner(library))

        self.assertEqual(library.launcher_written, [("l1", {KEY: "1"})])
        self.assertEqual(library.order, ["launcher", "tables"])
        self.assertEqual(library.written, [(["a", "b"], {KEY: ""})])

    async def test_the_count_is_of_the_selected_tables_that_set_their_own(self) -> None:
        library = _Launcher([_row("a", (KEY,)), _row("b", (KEY,)), _row("c", (KEY,))],
                            a=_own("1"), b={KEY: {"value": "1", "scope": "folder"}},
                            c={KEY: {"value": "0"}})

        await self._move(self._inner(library))

        self.assertEqual(self.asked[0]["detail"],
                         [t("manager.app_settings.move_all.some", count=1)])

    async def test_the_second_sentence_is_left_out_where_all_tables_holds_the_value(
            self) -> None:
        library = _Launcher([_row("a", (KEY,)), _row("b", (KEY,)), _row("c")],
                            held={KEY: {"value": "1", "set_here": True}},
                            a=_own("1"), b=_own("1"))

        await self._move(self._inner(library))

        self.assertEqual(self.asked[0]["detail"],
                         [t("manager.app_settings.move_all.stop", count=2)])

    async def test_all_tables_is_read_again_after_the_answer_for_undo(self) -> None:
        library = _Launcher([_row("a", (KEY,))], held={KEY: {"value": "7", "set_here": True}},
                            a=_own("1"))
        ask = app_settings.confirm.ask

        async def changed(question: str, **kwargs) -> bool:
            library.held = {KEY: {"value": "9", "set_here": True}}
            return await ask(question, **kwargs)

        with patch.object(app_settings.confirm, "ask", changed):
            await self._move(self._inner(library))
        library.launcher_written.clear()
        await self.offered[0][1]()

        self.assertEqual(library.launcher_written, [("l1", {KEY: "9"})])

    async def test_a_clearing_that_throws_puts_all_tables_back_and_offers_no_undo(self) -> None:
        library = _Launcher([_row("a", (KEY,))], held={KEY: {"value": "7", "set_here": True}},
                            a=_own("1"))

        with patch.object(app_settings, "write_selection", side_effect=RuntimeError("gone")):
            await self._move(self._inner(library))

        self.assertEqual(library.launcher_written, [("l1", {KEY: "1"}), ("l1", {KEY: "7"})])
        self.assertEqual(self.offered, [])
        self.assertEqual(self.said[0][1]["type"], "negative")

    async def test_tables_that_could_not_be_cleared_put_all_tables_back(self) -> None:
        library = _Launcher([_row("a", (KEY,))], a=_own("1"))
        library.refused = {"l1"}

        await self._move(self._inner(library))

        self.assertEqual(library.launcher_written, [("l1", {KEY: "1"}), ("l1", {KEY: ""})])
        self.assertEqual(self.offered, [])

    async def test_undo_leaves_the_tables_alone_where_all_tables_cannot_be_put_back(
            self) -> None:
        library = _Launcher([_row("a", (KEY,))], a=_own("1"))

        await self._move(self._inner(library))
        library.written.clear()
        library.unwritable = True
        with self.assertRaises(RuntimeError):
            await self.offered[0][1]()

        self.assertEqual(library.written, [])

    async def test_the_question_counts_the_tables_that_stop_and_those_that_follow(self) -> None:
        selected = ("a", "b")
        library = _Launcher(
            [_row("a", (KEY,)), _row("b", (KEY,)), _row("c"), _row("d", (OTHER,)),
             _row("e", (KEY,)), _row("f", launcher="l2")],
            a=_own("1"), b=_own("1"))
        self.assertEqual(tuple(library.values), selected)

        await self._move(self._inner(library))

        self.assertEqual(self.asked[0]["question"],
                         t("manager.app_settings.move_all.ask", setting="Ball Trail"))
        self.assertEqual(self.asked[0]["detail"], [
            t("manager.app_settings.move_all.stop", count=2),
            t("manager.app_settings.move_all.follow", count=2)])

    async def test_no_second_sentence_when_every_other_table_sets_its_own(self) -> None:
        library = _Launcher([_row("a", (KEY,)), _row("b", (KEY,))], a=_own("1"), b=_own("1"))

        await self._move(self._inner(library))

        self.assertEqual(self.asked[0]["detail"],
                         [t("manager.app_settings.move_all.stop", count=2)])

    async def test_declining_writes_nothing(self) -> None:
        library = _Launcher([_row("a", (KEY,))], a=_own("1"))
        self.answer = False

        await self._move(self._inner(library))

        self.assertEqual(library.order, [])
        self.assertEqual(self.offered, [])

    async def test_a_launcher_that_cannot_be_written_leaves_every_table_as_it_was(self) -> None:
        library = _Launcher([_row("a", (KEY,))], a=_own("1"))
        library.unwritable = True

        await self._move(self._inner(library))

        self.assertEqual(library.written, [])
        self.assertEqual(self.offered, [])
        self.assertEqual(self.said[0][1]["type"], "negative")
        self.assertEqual(self.drawn, 1)

    async def test_undo_puts_back_all_tables_and_then_each_table_s_own_value(self) -> None:
        library = _Launcher([_row("a", (KEY,)), _row("b", (KEY,))], held={
            KEY: {"value": "7", "set_here": True}}, a=_own("1"), b=_own("2"))

        await self._move(self._inner(library))
        library.order.clear()
        library.written.clear()
        library.launcher_written.clear()
        said, reverse = self.offered[0]
        await reverse()

        self.assertEqual(said, t("manager.app_settings.move_done", setting="Ball Trail"))
        self.assertEqual(library.launcher_written, [("l1", {KEY: "7"})])
        self.assertEqual(library.order[0], "launcher")
        self.assertEqual(sorted(library.written), [(["a"], {KEY: "1"}), (["b"], {KEY: "2"})])

    async def test_undo_clears_all_tables_where_it_held_none(self) -> None:
        library = _Launcher([_row("a", (KEY,))], a=_own("1"))

        await self._move(self._inner(library))
        library.launcher_written.clear()
        await self.offered[0][1]()

        self.assertEqual(library.launcher_written, [("l1", {KEY: ""})])


class MoveToAllOfferTests(unittest.TestCase):
    FIELD = GROUPS[0].settings[0]

    def _offer(self, held: dict, *, launchers: tuple[str, ...] = ("l1",),
               playing: bool = False, offered: frozenset[str] = frozenset({KEY})):
        inner = {"shared": [{"launcher_id": one} for one in launchers], "playing": playing}
        move = app_settings.MoveToAll(
            inner, offered, lambda: None)  # type: ignore[arg-type,return-value]
        with patch.object(app_settings.panel, "action") as action:
            drawn = move(held, self.FIELD)
        return drawn, action

    def test_it_sits_beside_one_value_that_a_table_sets_itself(self) -> None:
        drawn, action = self._offer({"set_here": True, "value": "1"})

        self.assertIsNotNone(drawn)
        self.assertEqual(action.call_args.args[0], t("manager.app_settings.move_all"))
        self.assertTrue(action.call_args.kwargs["enabled"])
        self.assertEqual(action.call_args.kwargs["hint"], t("manager.app_settings.move_all.help"))

    def test_a_value_that_varies_is_not_offered(self) -> None:
        self.assertIsNone(self._offer({"set_here": True, "varies": True, "value": ""})[0])

    def test_a_value_no_table_sets_itself_is_not_offered(self) -> None:
        self.assertIsNone(self._offer({"set_here": False, "value": "1"})[0])

    def test_a_value_set_to_nothing_is_not_offered(self) -> None:
        self.assertIsNone(self._offer({"set_here": True, "value": ""})[0])

    def test_the_move_asks_with_the_launcher_and_the_values_it_was_offered_for(self) -> None:
        _, action = self._offer({"set_here": True, "value": "1"})

        move = action.call_args.args[1]
        self.assertIs(move.func, app_settings._move_to_all)
        self.assertEqual([(field.key, value) for field, value in move.args[1]], [(KEY, "1")])
        self.assertEqual(move.args[2], "l1")

    def test_a_pair_is_offered_only_when_each_of_its_rows_that_is_set_can_move(self) -> None:
        inner = {"shared": [{"launcher_id": "l1"}], "playing": False}
        move = app_settings.MoveToAll(inner, frozenset({KEY, OTHER}),
                                      lambda: None)  # type: ignore[arg-type,return-value]
        second = GROUPS[0].settings[1]
        with patch.object(app_settings.panel, "action") as action:
            both = move({"set_here": True, "value": "1"}, self.FIELD,
                        ({"set_here": True, "value": "2"}, second))
            half = move({"set_here": True, "value": "1"}, self.FIELD,
                        ({"set_here": True, "varies": True, "value": ""}, second))

        self.assertIsNotNone(both)
        self.assertEqual([(field.key, value) for field, value in action.call_args.args[1].args[1]],
                         [(KEY, "1"), (OTHER, "2")])
        self.assertIsNone(half)

    def test_a_setting_all_tables_cannot_hold_is_not_offered(self) -> None:
        self.assertIsNone(self._offer({"set_here": True, "value": "1"},
                                      offered=frozenset())[0])

    def test_more_than_one_launcher_dims_it_and_says_how_many(self) -> None:
        _, action = self._offer({"set_here": True, "value": "1"}, launchers=("l1", "l2"))

        self.assertFalse(action.call_args.kwargs["enabled"])
        self.assertEqual(action.call_args.kwargs["hint"],
                         t("manager.app_settings.move_all.launchers", count=2))

    def test_a_table_that_plays_dims_it(self) -> None:
        _, action = self._offer({"set_here": True, "value": "1"}, playing=True)

        self.assertFalse(action.call_args.kwargs["enabled"])
        self.assertEqual(action.call_args.kwargs["hint"], t(all_settings.PLAYING_NOTE))


class ConfigAppliedTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_coroutine_write_is_awaited_where_it_stands(self) -> None:
        async def write(values: dict) -> dict:
            return {"cut": [values]}

        self.assertEqual(await all_settings.config_applied(write, {KEY: "1"}),
                         {"cut": [{KEY: "1"}]})

    async def test_a_plain_write_runs_off_the_loop(self) -> None:
        async def inline(callback, *args):
            return callback(*args)

        with patch.object(run, "io_bound", inline):
            found = await all_settings.config_applied(lambda values: {"cut": [values]},
                                                   {KEY: "1"})

        self.assertEqual(found, {"cut": [{KEY: "1"}]})


if __name__ == "__main__":
    unittest.main()

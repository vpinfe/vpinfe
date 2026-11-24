"""A grid's saved layout, put back on the grid: the order a person left the columns in,
and the widths the grid declares wherever the person has not set one."""

from __future__ import annotations

import unittest
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from manager.kit import grid

COLUMNS = [{"field": "name", "width": 240, "pinned": "left"}, {"field": "year", "width": 90},
           {"field": "rating", "width": 130}, {"field": "plays", "width": 130}]
SELECTION = "ag-Grid-SelectionColumn"


def _kept(*order: str, **stored: Any) -> grid.Layout:
    return grid.Layout.read("test.grid", {"order": list(order), **stored})


def _by_column(state: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["colId"]: entry for entry in state}


class _Answer:
    def __init__(self, value: Any) -> None:
        self.value = value

    def __await__(self) -> Generator[Any, None, Any]:
        yield from ()
        return self.value


class _Grid:
    """Records what is sent to it, and answers `getColumnState` with `state`, which
    starts as the declared widths with the selection column first, and the `context`
    option with `context`."""

    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.handlers: dict[str, Any] = {}
        self.context: dict[str, Any] = {}
        self.state = [{"colId": SELECTION, "width": 50, "pinned": "left"}] + [
            {"colId": definition["field"], "width": definition["width"],
             "pinned": definition.get("pinned")} for definition in COLUMNS]

    def run_grid_method(self, name: str, *args: Any) -> _Answer:
        self.sent.append((name, *args))
        if name == "getColumnState":
            return _Answer([dict(entry) for entry in self.state])
        return _Answer(self.context if (name, *args) == ("getGridOption", "context")
                       else None)

    def on(self, event: str, handler: Any, **_: Any) -> None:
        self.handlers[event] = handler

    def width(self, col_id: str, width: int) -> None:
        next(entry for entry in self.state if entry["colId"] == col_id)["width"] = width

    def fit(self, col_id: str, width: int) -> None:
        """What the browser does when it fits a column to the grid, with no one asking."""
        self.width(col_id, width)
        self.context["fit"] = {"col": col_id, "at": width, "own": True}

    def drag(self, col_id: str, width: int) -> None:
        """A person's drag, which the browser's fit then leaves to them."""
        self.width(col_id, width)
        if self.context.get("fit", {}).get("col") == col_id:
            self.context["fit"]["own"] = False

    async def fire(self, event: str, **args: Any) -> None:
        await self.handlers[event](SimpleNamespace(args=args))


class AppliedStateTests(unittest.TestCase):
    def test_a_moved_column_goes_back_where_it_was_put(self) -> None:
        state = grid.applied_state(COLUMNS, _kept("plays", "name", "year", "rating"))

        self.assertEqual(["plays", "name", "year", "rating"],
                         [entry["colId"] for entry in state])

    def test_a_column_the_save_does_not_name_comes_after_the_ones_it_does(self) -> None:
        state = grid.applied_state(COLUMNS, _kept("rating", "name"))

        self.assertEqual(["rating", "name", "year", "plays"],
                         [entry["colId"] for entry in state])

    def test_a_saved_column_the_grid_no_longer_has_is_left_out(self) -> None:
        state = grid.applied_state(COLUMNS, _kept("gone", "year"))

        self.assertNotIn("gone", [entry["colId"] for entry in state])

    def test_nothing_saved_is_the_declared_order(self) -> None:
        state = grid.applied_state(COLUMNS, _kept())

        self.assertEqual(["name", "year", "rating", "plays"],
                         [entry["colId"] for entry in state])

    def test_a_declared_width_reaches_a_grid_with_one_column_resized(self) -> None:
        declared = [dict(definition) for definition in COLUMNS]
        declared[1]["width"] = 110

        state = _by_column(grid.applied_state(declared, _kept(widths={"name": 200})))

        self.assertEqual(200, state["name"]["width"])
        self.assertEqual(110, state["year"]["width"])

    def test_a_column_the_person_did_not_pin_takes_its_declared_pin(self) -> None:
        state = _by_column(grid.applied_state(COLUMNS, _kept(pins={"year": "left"})))

        self.assertEqual("left", state["year"]["pinned"])
        self.assertEqual("left", state["name"]["pinned"])
        self.assertIsNone(state["plays"]["pinned"])

    def test_a_filling_column_takes_the_leftover_width_until_a_width_is_saved(self) -> None:
        declared = [dict(definition) for definition in COLUMNS]
        declared[2]["flex"] = 1

        state = _by_column(grid.applied_state(declared, _kept()))

        self.assertEqual(1, state["rating"]["flex"])
        self.assertNotIn("width", state["rating"])
        self.assertEqual(90, state["year"]["width"])
        self.assertNotIn("flex", state["year"])

    def test_a_width_saved_for_a_filling_column_ends_the_fill(self) -> None:
        declared = [dict(definition) for definition in COLUMNS]
        declared[2]["flex"] = 1

        state = _by_column(grid.applied_state(declared, _kept(widths={"rating": 170})))

        self.assertIsNone(state["rating"]["flex"])
        self.assertEqual(170, state["rating"]["width"])

    def test_a_filling_column_declares_its_width_as_its_floor(self) -> None:
        filled = grid.column("note", "Note", 200, flex=1, help="x")

        self.assertEqual(200, filled["minWidth"])
        self.assertNotIn("minWidth", grid.column("note", "Note", 200, help="x"))

    def test_a_layout_saved_as_a_column_list_is_not_read(self) -> None:
        kept = grid.Layout.read("test.grid", {"columns": [{"colId": "plays", "width": 99}]})

        self.assertEqual({}, kept.stored())


class ApplyLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def _applied(self, stored: dict[str, Any]) -> dict[str, Any]:
        table = _Grid()
        with patch("manager.kit.api.ApiClient"), \
                patch("manager.kit.grid.offload.io", new=AsyncMock(return_value=stored)):
            await grid.apply_layout(table, "test.grid", COLUMNS)  # type: ignore[arg-type]
        [(_, applied)] = [sent for sent in table.sent if sent[0] == "applyColumnState"]
        return applied

    async def test_the_order_is_applied_with_nothing_saved(self) -> None:
        """A view with no layout of its own shows the declared order, not the one the
        last view left on the grid."""
        applied = await self._applied({})

        self.assertTrue(applied["applyOrder"])

    async def test_a_saved_order_is_applied(self) -> None:
        applied = await self._applied({"order": ["plays", "name"]})

        self.assertTrue(applied["applyOrder"])
        self.assertEqual(["plays", "name", "year", "rating"],
                         [entry["colId"] for entry in applied["state"]])


class SaveTests(unittest.IsolatedAsyncioTestCase):
    """What a person's gesture writes, starting from a grid drawn at its definitions."""

    async def asyncSetUp(self) -> None:
        self.table = _Grid()
        self.written: list[dict[str, Any]] = []
        self.read: Any = {}

        async def put(_put: Any, where: str, value: dict[str, Any]) -> None:
            self.written.append(value)

        async def read(_read: Any, where: str) -> Any:
            if isinstance(self.read, Exception):
                raise self.read
            return self.read

        for patcher in (patch("manager.kit.api.ApiClient"),
                        patch("manager.kit.grid.offload.io", new=read),
                        patch("manager.kit.grid.run.io_bound", new=put)):
            patcher.start()
            self.addCleanup(patcher.stop)
        grid._save_on_change(self.table, "test.grid", None)  # type: ignore[arg-type]

    async def _drawn(self) -> None:
        await grid.apply_layout(self.table, "test.grid", COLUMNS)  # type: ignore[arg-type]

    async def test_a_resize_saves_the_width_of_that_column_alone(self) -> None:
        await self._drawn()
        self.table.width("name", 200)

        await self.table.fire("columnResized", colId="name")

        self.assertEqual([{"widths": {"name": 200}}], self.written)

    async def test_a_resize_naming_no_column_saves_the_one_that_changed(self) -> None:
        await self._drawn()
        self.table.width("rating", 150)

        await self.table.fire("columnResized")

        self.assertEqual([{"widths": {"rating": 150}}], self.written)

    async def test_a_move_saves_the_order_and_no_widths(self) -> None:
        await self._drawn()
        self.table.state.insert(1, self.table.state.pop(4))

        await self.table.fire("columnMoved", colId="plays")

        self.assertEqual([{"order": ["plays", "name", "year", "rating"]}], self.written)

    async def test_a_pin_saves_that_pin(self) -> None:
        await self._drawn()

        await grid.pin(self.table, "year", "left")

        self.assertEqual([{"pins": {"year": "left"}}], self.written)

    async def test_a_width_the_grid_fitted_is_not_saved_with_another_column_s(self) -> None:
        await self._drawn()
        self.table.fit("name", 180)
        self.table.drag("rating", 150)

        await self.table.fire("columnResized", colId="rating")

        self.assertEqual([{"widths": {"rating": 150}}], self.written)

    async def test_a_width_the_grid_fitted_is_not_saved_with_a_pin(self) -> None:
        await self._drawn()
        self.table.fit("name", 180)

        await grid.pin(self.table, "year", "left")

        self.assertEqual([{"pins": {"year": "left"}}], self.written)

    async def test_a_fitted_column_a_person_then_drags_is_theirs(self) -> None:
        await self._drawn()
        self.table.fit("name", 180)
        self.table.drag("name", 170)

        await self.table.fire("columnResized")

        self.assertEqual([{"widths": {"name": 170}}], self.written)

    async def test_a_filling_column_shrinking_is_not_saved_with_another_column_s(self) -> None:
        grid._save_on_change(self.table, "test.grid", None,  # type: ignore[arg-type]
                             frozenset({"rating"}))
        await self._drawn()
        self.table.width("rating", 100)
        self.table.width("year", 150)

        await self.table.fire("columnResized", colId="year")

        self.assertEqual([{"widths": {"year": 150}}], self.written)

    async def test_a_filling_column_a_person_drags_is_theirs(self) -> None:
        grid._save_on_change(self.table, "test.grid", None,  # type: ignore[arg-type]
                             frozenset({"rating"}))
        await self._drawn()
        self.table.width("rating", 100)

        await self.table.fire("columnResized", colId="rating")

        self.assertEqual([{"widths": {"rating": 100}}], self.written)

    async def test_an_unchanged_layout_is_not_written_again(self) -> None:
        self.read = {"widths": {"name": 200}}
        self.table.width("name", 200)
        await self._drawn()

        await self.table.fire("columnResized", colId="name")

        self.assertEqual([], self.written)

    async def test_a_layout_that_could_not_be_read_is_not_written_over(self) -> None:
        self.read = OSError("unreadable")
        with self.assertLogs("vpinfe.manager.kit.grid", "WARNING"):
            await self._drawn()
        self.table.width("name", 200)

        await self.table.fire("columnResized", colId="name")

        self.assertEqual([], self.written)


if __name__ == "__main__":
    unittest.main()

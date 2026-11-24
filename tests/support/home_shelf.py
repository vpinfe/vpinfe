"""Discovery's front page drawn through the Library and a client that answers, for the
tests of one shelf."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from nicegui import core, run, ui

from common.online import vps_home, vps_marks
from manager.kit.data import Library
from manager.library import discovery
from manager.library import discovery_home as home

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()

KEPT = ["tableFiles", "b2sFiles"]


def table(rid: str, by: int, fmt: str = "VPX") -> dict[str, Any]:
    return {"id": rid, "by": [by], "added": "2020-01-01T00:00:00Z", "held": False,
            "mod": False, "version": "", "format": fmt}


def entry(vps_id: str, name: str, tables: list[dict[str, Any]], *, in_library: bool = False,
           manufacturer: str = "", year: int | None = None) -> dict[str, Any]:
    return {"vps_id": vps_id, "name": name, "in_library": in_library, "img_url": "",
            "manufacturer": manufacturer, "year": year, "type": "", "themes": [], "url": "",
            "listed": ["tableFiles"], "held": [], "releases": {"tableFiles": tables}}


PEOPLE: list[dict[str, Any]] = [{"name": name, "aliases": [], "releases": 4}
                                for name in ("Ann", "Bo", "Cy")]


def passes(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Whether a grid row stays under the filter models a view sets: a choice filter keeps
    the values it names (a blank cell is the value ""), a number filter its notBlank."""
    for field, model in filters.items():
        cell = row.get(field)
        if "values" in model:
            if ("" if cell is None else cell) not in model["values"]:
                return False
        elif model.get("type") == "notBlank":
            if cell is None:
                return False
        else:
            raise AssertionError(f"a filter this test does not read: {model}")
    return True


def grid_order(rows: list[dict[str, Any]], sort: tuple[dict[str, Any], ...]) -> list[str]:
    ordered = list(rows)
    for one in sorted(sort, key=lambda each: -each["sortIndex"]):
        ordered.sort(key=lambda row: (str(row[one["colId"]]).casefold()
                                      if isinstance(row[one["colId"]], str) else row[one["colId"]]),
                     reverse=one["sort"] == "desc")
    return [row["id"] for row in ordered]


def held_game(game_id: str, name: str, vps_id: str = "", tables: int = 1) -> dict[str, Any]:
    """One game of the library as `library_ops.library_games` answers it."""
    return {"game_id": game_id, "name": name, "vps_id": vps_id, "tables": tables}


def mark(state: str, scope: str, one: str) -> dict[str, str]:
    return {"state": state, "scope": scope, "id": one, "detail": "", "since": "x"}


def words_of(body: ui.element) -> list[str]:
    return [str(one.text) for one in body.descendants()
            if isinstance(one, ui.label) and not one.is_deleted]


async def settle() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


class ShelfHarness(unittest.IsolatedAsyncioTestCase):
    """Home drawn by `_fill_home`, through the Library and a client that answers. A subclass
    names the `found` it starts from as `FOUND`."""

    FOUND: dict[str, Any] = {}
    UNMATCHED: list[dict[str, Any]] = []

    def setUp(self) -> None:
        self.client = mock.Mock()
        self.client.library_policy.return_value = {"hidden_asset_kinds": [],
                                                   "hidden_media_kinds": []}
        self.client.catalog_discovery.return_value = self.FOUND
        self.client.discovery_marks.return_value = []
        self.client.preferences.return_value = {}
        self.client.discovery_home.side_effect = self._answer
        self.library = Library(self.client)
        self.library.load_kept_kinds()
        self.found = self.FOUND
        self.games: list[dict[str, Any]] = []
        self.journal: list[dict[str, Any]] = []
        self.unmatched = list(self.UNMATCHED)
        self.state: dict[str, Any] = {}
        self.selected: list[Any] = []
        self.spied: dict[str, Any] = {}
        self.refresh: Any = None

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        for patcher in (
                mock.patch.object(run, "io_bound", inline),
                mock.patch.object(vps_home.arrivals, "was", return_value=""),
                mock.patch.object(home.remembered, "get", return_value=None),
                mock.patch.object(discovery.deeplink, "sync")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _answer(self, days: int) -> dict[str, Any]:
        return vps_home.home(self.found, KEPT, days,
                             marks=vps_marks.Marks(self.client.discovery_marks()),
                             formats=("VPX",), library=self.games, opened=self.journal,
                             unmatched_games=self.unmatched)

    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

    async def _select(self, row: Any) -> None:
        self.selected.append(row)

    async def _draw(self, rerender: Any = None) -> ui.element:
        real = home.fill

        def spy(*args: Any, **more: Any) -> None:
            self.spied.update(more)
            self.refresh = real(*args, **more)

        with _PAGE:
            body = ui.column()
            with mock.patch.object(home, "fill", spy):
                await discovery._fill_home(self.library, body, self.state, self._select,
                                           rerender or mock.Mock())
        await settle()
        return body

    async def _redraw(self) -> None:
        """What an apply from the pane calls once Home is drawn."""
        with _PAGE:
            await self.state["redraw_discovery"]()
        await settle()

    @staticmethod
    def _see_alls(body: ui.element) -> int:
        return sum(1 for one in body.descendants() if not one.is_deleted
                   and str(getattr(one, "text", "")) == "See All")

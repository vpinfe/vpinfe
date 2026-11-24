"""What is on screen while its replacement is read is marked busy from the act that
replaces it until the replacement is drawn.

Two acts read before they draw and leave the old region up meanwhile: a rail click to a
view that reads first, and a pane build for the next subject - a grid row, or a rebuild
after a write.
"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock, patch

from nicegui import core, ui

from manager import page
from manager.kit import busy
from manager.workbench import builds
from tests.support.workbench_patch import patch_workbench

_PAGE = ui.element()
OLD, NEW = "what was there", "what replaced it"


def _region(saying: str = OLD) -> ui.column:
    with _PAGE:
        region = ui.column()
    with region:
        ui.label(saying)
    return region


def _busy(region: ui.element) -> bool:
    return region.props.get("aria-busy") == "true"


def _says(region: ui.element) -> list[str]:
    return [one.text for one in region.descendants() if isinstance(one, ui.label)]


def _spinners(region: ui.element) -> int:
    return sum(isinstance(one, ui.spinner) for one in region.descendants())


def _over(region: ui.element) -> bool:
    rows = [one for one in region.descendants() if "manager-busy" in one.classes]
    return bool(rows) and all("manager-busy--over" in one.classes for one in rows)


def _redraws(region: ui.element) -> Callable[[], None]:
    def render() -> None:
        region.clear()
        with region:
            ui.label(NEW)
    return render


async def _until(done: Callable[[], bool]) -> None:
    for _turn in range(200):
        if done():
            return
        await asyncio.sleep(0)
    raise AssertionError("never happened")


class ARailClick(unittest.IsolatedAsyncioTestCase):
    """`page.read_then_render`, which every way of arriving at a view goes through."""

    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(core, "loop", asyncio.get_running_loop()))
        self.read = asyncio.Event()

    async def _arrive(self, view: str, library: Any) -> None:
        content = _region()
        render, light = Mock(side_effect=_redraws(content)), Mock()

        page.read_then_render({"view": view}, library, content, render, Mock(), light)

        light.assert_called_once_with()
        self.assertEqual((True, True, [OLD]),
                         (_busy(content), _over(content), _says(content)),
                         "the old view is up, and busy under the spinner, while the new "
                         "one reads")
        render.assert_not_called()
        self.read.set()
        await _until(lambda: render.called and not _busy(content))
        self.assertEqual([NEW], _says(content))
        self.assertEqual(0, _spinners(content))

    async def test_a_view_that_reads_every_time(self) -> None:
        async def io(*_args: Any) -> list[str]:
            await self.read.wait()
            return []

        with patch.object(page.offload, "io", new=io):
            await self._arrive("settings", Mock())

    async def test_a_list_not_read_yet(self) -> None:
        library = Mock()
        library.has_games_grid.return_value = False

        async def io_bound(*_args: Any) -> None:
            await self.read.wait()

        with patch.object(page.run, "io_bound", new=io_bound):
            await self._arrive("games", library)

    async def test_devices_are_read_again_into_the_list_the_page_holds(self) -> None:
        held = [{"device_id": "forgotten"}]
        library = Mock()
        library.devices.return_value = [{"device_id": "kept"}]
        content = _region()
        drawn: list[list[dict]] = []

        async def io(call: Callable[..., Any], *args: Any) -> Any:
            return call(*args)

        with patch.object(page.offload, "io", new=io):
            page.read_then_render({"view": "devices", "devices": held}, library, content,
                                  lambda: drawn.append(list(held)), Mock(), Mock())
            await _until(lambda: bool(drawn))

        self.assertEqual([[{"device_id": "kept"}]], drawn)

    async def test_settings_redraws_the_rail_from_the_extensions_it_just_read(self) -> None:
        state: dict[str, Any] = {"view": "settings", "extensions": [{"name": "stale"}]}
        order: list[Any] = []
        client = Mock()
        client.extensions.return_value = [{"name": "fresh"}]
        content = _region()

        async def io(call: Callable[..., Any], *args: Any) -> Any:
            return call(*args)

        with patch.object(page.offload, "io", new=io), \
                patch.object(page, "ApiClient", return_value=client):
            page.read_then_render(
                state, Mock(), content, lambda: order.append("render"), Mock(), Mock(),
                lambda: order.append(list(state["extensions"])))
            await _until(lambda: "render" in order)

        self.assertEqual([[{"name": "fresh"}], "render"], order)

    def test_a_view_that_holds_what_it_needs_draws_at_once(self) -> None:
        content = _region()
        library = Mock()
        library.has_games_grid.return_value = True

        page.read_then_render({"view": "games"}, library, content, _redraws(content),
                              Mock(), Mock())

        self.assertEqual((False, [NEW]), (_busy(content), _says(content)))


BUILDS: tuple[tuple[Callable[..., Any], str], ...] = (
          (builds.build, "_draw"), (builds.build_collection, "_draw_collection"),
          (builds.build_tag, "_draw_tag"), (builds.build_file, "_draw_file"),
          (builds.build_launcher, "_draw_launcher"),
          (builds.build_device, "_draw_device"), (builds.build_theme, "_draw_theme"))


class APaneBuild(unittest.IsolatedAsyncioTestCase):
    """Every `workbench.build*`, which a grid row and a rebuild after a write both call."""

    def _gated(self, pane: ui.element, gate: asyncio.Event,
               drawn: list[str]) -> Callable[..., Any]:
        async def draw(*_args: Any) -> None:
            await gate.wait()
            _redraws(pane)()
            drawn.append(NEW)
        return draw

    async def test_the_old_pane_is_busy_from_the_call_until_the_next_is_drawn(self) -> None:
        for build, draw in BUILDS:
            with self.subTest(build.__name__):
                pane, gate, drawn = _region(), asyncio.Event(), list[str]()
                with patch_workbench(draw, new=self._gated(pane, gate, drawn)):
                    building = asyncio.create_task(
                        build(pane, _region(), Mock(), "next", {}))
                    await asyncio.sleep(0)

                    self.assertEqual((True, True, [OLD]),
                                     (_busy(pane), _over(pane), _says(pane)))
                    gate.set()
                    await building

                self.assertEqual((False, [NEW]), (_busy(pane), _says(pane)))

    async def test_a_build_waiting_its_turn_keeps_the_pane_busy(self) -> None:
        pane, state, drawn = _region(), dict[str, Any](), list[str]()
        first, second = asyncio.Event(), asyncio.Event()
        gates = iter((first, second))

        async def draw(*_args: Any) -> None:
            await self._gated(pane, next(gates), drawn)()

        with patch_workbench("_draw", new=draw):
            one = asyncio.create_task(builds.build(pane, _region(), Mock(), "a", state))
            await asyncio.sleep(0)
            two = asyncio.create_task(builds.build(pane, _region(), Mock(), "b", state))
            first.set()
            await one

            self.assertEqual((True, [NEW]), (_busy(pane), drawn))
            self.assertLessEqual(_spinners(pane), 1)
            second.set()
            await two

        self.assertEqual((False, [NEW, NEW]), (_busy(pane), drawn))


class Treatments(unittest.TestCase):
    def test_a_region_being_filled_keeps_its_spinner_after_what_it_has(self) -> None:
        region = _region()

        with busy.held(region):
            self.assertFalse(_over(region))

    def test_content_that_stays_up_takes_the_spinner_over_it(self) -> None:
        region = _region()

        with busy.held(region, over=True):
            self.assertTrue(_over(region))


class OverlappingWork(unittest.TestCase):
    def test_a_region_is_let_go_when_the_last_hold_ends(self) -> None:
        region = _region()

        with busy.held(region):
            with busy.held(region):
                self.assertEqual(1, _spinners(region))
            self.assertTrue(_busy(region))
            self.assertEqual(1, _spinners(region))

        self.assertEqual((False, 0), (_busy(region), _spinners(region)))

    def test_a_hold_after_a_clear_draws_its_spinner_again(self) -> None:
        region = _region()

        with busy.held(region):
            region.clear()
            with busy.held(region):
                self.assertEqual(1, _spinners(region))
            self.assertTrue(_busy(region))

        self.assertFalse(_busy(region))


class Started(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch.object(core, "loop", asyncio.get_running_loop()))

    async def test_busy_at_once_and_idle_once_the_work_ends(self) -> None:
        region, gate, ran = _region(), asyncio.Event(), []

        async def work() -> None:
            await gate.wait()
            ran.append(True)

        busy.start(region, work)

        self.assertEqual((True, []), (_busy(region), ran))
        gate.set()
        await _until(lambda: not _busy(region))
        self.assertEqual(([True], 0), (ran, _spinners(region)))

    async def test_idle_after_the_work_raises(self) -> None:
        region = _region()

        async def work() -> None:
            raise RuntimeError("the read failed")

        with patch.object(core.app, "handle_exception"):
            busy.start(region, work)
            await _until(lambda: not _busy(region))

        self.assertEqual(0, _spinners(region))


if __name__ == "__main__":
    unittest.main()

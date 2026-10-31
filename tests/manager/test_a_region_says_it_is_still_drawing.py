"""A region still being drawn is marked busy until its work ends, however it ends."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock, patch

from nicegui import ui

from manager.kit import busy
from manager.settings import settings
from manager.workbench import core, rail
from tests.support.workbench_patch import patch_workbench

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _busy(region: ui.element) -> bool:
    return region.props.get("aria-busy") == "true"


def _spinners(region: ui.element) -> list[ui.element]:
    return [one for one in region.descendants() if isinstance(one, ui.spinner)]


def _region() -> ui.column:
    with _PAGE:
        return ui.column()


class Held(unittest.IsolatedAsyncioTestCase):
    async def test_busy_while_the_work_runs_and_idle_after(self) -> None:
        region = _region()
        seen: list[tuple[bool, int]] = []

        with region, busy.held(region):
            await asyncio.sleep(0)
            seen.append((_busy(region), len(_spinners(region))))

        self.assertEqual([(True, 1)], seen)
        self.assertEqual((False, []), (_busy(region), _spinners(region)))

    async def test_idle_after_the_work_raises(self) -> None:
        region = _region()

        with self.assertRaises(RuntimeError), region, busy.held(region):
            ui.label("half drawn")
            raise RuntimeError("the read failed")

        self.assertEqual((False, []), (_busy(region), _spinners(region)))

    async def test_idle_after_the_work_cleared_the_region_itself(self) -> None:
        region = _region()

        with region, busy.held(region):
            region.clear()
            ui.label("drawn")

        self.assertFalse(_busy(region))
        self.assertEqual(["drawn"], [one.text for one in region.descendants()
                                     if isinstance(one, ui.label)])

    async def test_a_region_deleted_while_it_waits_is_let_go_quietly(self) -> None:
        region = _region()

        with patch("nicegui.helpers.warn_once") as warned, busy.held(region):
            region.delete()

        self.assertTrue(region.is_deleted)
        warned.assert_not_called()

    async def test_overlapping_work_on_a_deleted_region_is_let_go_quietly(self) -> None:
        region = _region()

        with patch("nicegui.helpers.warn_once") as warned:
            with busy.held(region), busy.held(region):
                region.delete()

        warned.assert_not_called()

    def test_the_spinner_is_not_read_out(self) -> None:
        region = _region()

        with busy.held(region):
            row = next(iter(region))

            self.assertEqual("true", row.props.get("aria-hidden"))
            self.assertIn("manager-busy", row.classes)


class UntilGone(unittest.TestCase):
    def test_busy_with_its_spinner_until_it_is_deleted(self) -> None:
        region = _region()

        busy.until_gone(region)

        self.assertEqual((True, 1), (_busy(region), len(_spinners(region))))
        region.delete()
        self.assertTrue(region.is_deleted)


class Filled(unittest.IsolatedAsyncioTestCase):
    def _fill(self, region: ui.element, work: Callable[[], Any]) -> Callable[[], Any]:
        """`busy.fill`, handing back what it scheduled rather than waiting on a browser."""
        with patch.object(busy.ui, "timer") as timer:
            busy.fill(region, work)
        return timer.call_args.args[1]

    async def test_busy_at_once_before_the_work_has_started(self) -> None:
        region = _region()
        work = Mock()

        self._fill(region, work)

        work.assert_not_called()
        self.assertTrue(_busy(region))

    async def test_idle_once_the_work_has_drawn(self) -> None:
        region = _region()
        seen: list[bool] = []

        async def work() -> None:
            seen.append(_busy(region))
            with region:
                ui.label("drawn")

        await self._fill(region, work)()

        self.assertEqual([True], seen)
        self.assertEqual((False, []), (_busy(region), _spinners(region)))

    async def test_idle_after_the_work_raises(self) -> None:
        region = _region()

        async def work() -> None:
            raise RuntimeError("the read failed")

        with self.assertRaises(RuntimeError):
            await self._fill(region, work)()

        self.assertEqual((False, []), (_busy(region), _spinners(region)))


class TheSidePane(unittest.IsolatedAsyncioTestCase):
    """The open section's build, in the region beside the rail."""

    async def _open(self, build: Callable[[dict[str, Any]], Any]) -> None:
        probe = core.Section("probe", lambda _context: "Probe", build,
                                  subjects=frozenset({"probe"}))
        self.enterContext(patch_workbench("SECTIONS", (probe,)))
        self.enterContext(patch_workbench("sections_for", return_value=(probe,)))
        with _PAGE:
            await rail._rail({"redraws": []}, "probe", {"section": "probe"})

    async def test_busy_while_the_section_builds_and_idle_after(self) -> None:
        seen: list[ui.element] = []

        async def build(_context: dict[str, Any]) -> None:
            region = ui.context.slot.parent
            seen.append(region)
            self.assertTrue(_busy(region))
            await asyncio.sleep(0)
            ui.label("drawn")

        await self._open(build)

        self.assertEqual((False, []), (_busy(seen[0]), _spinners(seen[0])))

    async def test_idle_after_the_section_raises(self) -> None:
        seen: list[ui.element] = []

        async def build(_context: dict[str, Any]) -> None:
            seen.append(ui.context.slot.parent)
            raise RuntimeError("the read failed")

        with self.assertRaises(RuntimeError):
            await self._open(build)

        self.assertEqual((False, []), (_busy(seen[0]), _spinners(seen[0])))


class SettingsPages(unittest.IsolatedAsyncioTestCase):
    """The page beside the Settings rail, drawn once the browser has the shell."""

    async def test_busy_from_the_start_until_the_page_is_drawn(self) -> None:
        seen: list[bool] = []

        async def page(_library: Any, _redraw: Any, body: ui.element, *_rest: Any) -> None:
            seen.append(_busy(body))

        self.enterContext(patch.object(settings, "_draw_system_page", new=page))
        with _PAGE, patch.object(busy.ui, "timer") as timer, patch.object(ui, "run_javascript"):
            settings.build_system(Mock(), {}, Mock(), {"features": []})
        scheduled = timer.call_args.args[1]
        body = [one for one in _PAGE.descendants()
                if "manager-settings-body" in one.classes][-1]
        self.assertTrue(_busy(body))

        await scheduled()

        self.assertEqual([True], seen)
        self.assertEqual((False, []), (_busy(body), _spinners(body)))


if __name__ == "__main__":
    unittest.main()

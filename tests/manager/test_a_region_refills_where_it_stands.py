"""A region draws its part of the page again inside the same container: what is around it
is not touched, and nothing marks the page busy."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from nicegui import ui

from manager.kit import panel

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _texts(box: ui.element) -> list[str]:
    return [one.text for one in box.descendants() if isinstance(one, ui.label)]


class Drawn:
    """A column with a label before the region, the region, and a label after it."""

    def __init__(self) -> None:
        self.word = "one"
        self.builds = 0
        self.region = panel.region(self._build)
        with _PAGE:
            self.column = ui.column()
            with self.column:
                self.before = ui.label("before")
                self.region()
                self.after = ui.label("after")

    def _build(self) -> None:
        self.builds += 1
        ui.label(self.word)

    @property
    def box(self) -> ui.element:
        box = self.region._box
        assert box is not None
        return box


class ARegionRefillsWhereItStandsTests(unittest.TestCase):
    def setUp(self) -> None:
        patch = mock.patch.object(_PAGE.client, "run_javascript")
        self.run_js = patch.start()
        self.addCleanup(patch.stop)
        attached = mock.patch.object(_PAGE.client, "tab_id", "tab")
        attached.start()
        self.addCleanup(attached.stop)

    def test_it_draws_its_part_once_when_it_is_drawn(self) -> None:
        drawn = Drawn()

        self.assertEqual(["before", "one", "after"], _texts(drawn.column))
        self.assertEqual(1, drawn.builds)

    def test_a_refill_draws_the_part_again_in_the_same_container(self) -> None:
        drawn = Drawn()
        box = drawn.box
        drawn.word = "two"

        drawn.region.refill()

        self.assertIs(box, drawn.box)
        self.assertEqual(["before", "two", "after"], _texts(drawn.column))
        self.assertEqual(2, drawn.builds)

    def test_what_is_around_it_is_the_same_element_after(self) -> None:
        drawn = Drawn()
        box = drawn.box

        drawn.region.refill()

        self.assertEqual([drawn.before, box, drawn.after],
                         list(drawn.column.default_slot.children))

    def test_a_refill_asks_the_page_to_give_focus_back_to_its_part(self) -> None:
        drawn = Drawn()

        drawn.region.refill()

        self.run_js.assert_called_once()
        self.assertIn(f"giveBack('{drawn.box.html_id}')", self.run_js.call_args.args[0])

    def test_a_page_with_no_browser_on_it_is_asked_for_nothing(self) -> None:
        drawn = Drawn()

        with mock.patch.object(_PAGE.client, "tab_id", None):
            drawn.region.refill()

        self.run_js.assert_not_called()

    def test_the_first_draw_asks_the_page_for_nothing(self) -> None:
        Drawn()

        self.run_js.assert_not_called()

    def test_a_refill_marks_nothing_busy(self) -> None:
        drawn = Drawn()

        drawn.region.refill()

        self.assertNotIn("aria-busy", drawn.box.props)
        self.assertFalse(any("busy" in one.classes for one in drawn.column.descendants()))

    def test_a_container_that_is_gone_is_not_drawn_into(self) -> None:
        drawn = Drawn()
        drawn.box.delete()

        drawn.region.refill()

        self.assertEqual(1, drawn.builds)

    def test_a_region_that_was_never_drawn_is_let_be(self) -> None:
        built: list[int] = []

        panel.region(lambda: built.append(1)).refill()

        self.assertEqual([], built)


class Waiting:
    """A region whose build reads before it draws."""

    def __init__(self) -> None:
        self.word = "one"
        self.gate: asyncio.Event | None = None
        self.region = panel.region(self._build)
        with _PAGE:
            self.column = ui.column()
            with self.column:
                self.before = ui.label("before")
                self.region()
                self.after = ui.label("after")

    async def _build(self) -> None:
        word = self.word
        if self.gate is not None:
            await self.gate.wait()
        ui.label(word)

    def shown(self) -> list[str]:
        return [one.text for one in self.column.descendants()
                if isinstance(one, ui.label) and one.visible
                and all(up.visible for up in _ups(one, self.column))]


def _ups(one: ui.element, stop: ui.element) -> list[ui.element]:
    ups = []
    while one.parent_slot is not None and one.parent_slot.parent is not stop:
        one = one.parent_slot.parent
        ups.append(one)
    return ups


class AWaitingRegionSwapsInOneStepTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patch = mock.patch.object(_PAGE.client, "run_javascript")
        self.run_js = patch.start()
        self.addCleanup(patch.stop)
        attached = mock.patch.object(_PAGE.client, "tab_id", "tab")
        attached.start()
        self.addCleanup(attached.stop)

    async def test_the_old_part_stays_until_the_new_one_is_drawn(self) -> None:
        drawn = Waiting()
        await drawn.region.refilled()
        drawn.word = "two"
        drawn.gate = asyncio.Event()

        task = asyncio.ensure_future(drawn.region.refilled())
        await asyncio.sleep(0)
        during = drawn.shown()
        drawn.gate.set()
        await task

        self.assertEqual(["before", "one", "after"], during)
        self.assertEqual(["before", "two", "after"], drawn.shown())

    async def test_a_swap_asks_the_page_for_focus_back_once_the_new_part_shows(self) -> None:
        drawn = Waiting()

        await drawn.region.refilled()

        box = drawn.region._box
        assert box is not None
        self.run_js.assert_called_once()
        self.assertIn(f"giveBack('{box.html_id}')", self.run_js.call_args.args[0])

    async def test_a_swap_that_is_dropped_asks_for_nothing(self) -> None:
        drawn = Waiting()
        drawn.gate = asyncio.Event()
        first = asyncio.ensure_future(drawn.region.refilled())
        await asyncio.sleep(0)
        second = asyncio.ensure_future(drawn.region.refilled())
        await asyncio.sleep(0)
        drawn.gate.set()
        await asyncio.gather(first, second)

        self.run_js.assert_called_once()

    async def test_what_is_around_it_is_untouched(self) -> None:
        drawn = Waiting()
        await drawn.region.refilled()

        await drawn.region.refilled()

        self.assertEqual([drawn.before, drawn.region._box, drawn.after],
                         list(drawn.column.default_slot.children))

    async def test_the_newer_refill_wins(self) -> None:
        drawn = Waiting()
        await drawn.region.refilled()
        drawn.gate = asyncio.Event()
        drawn.word = "slow"
        first = asyncio.ensure_future(drawn.region.refilled())
        await asyncio.sleep(0)
        drawn.word = "latest"
        second = asyncio.ensure_future(drawn.region.refilled())
        await asyncio.sleep(0)
        drawn.gate.set()
        await asyncio.gather(first, second)

        self.assertEqual(["before", "latest", "after"], drawn.shown())

    async def test_a_refill_that_finishes_before_an_earlier_one_leaves_the_part_whole(
            self) -> None:
        drawn = Waiting()
        await drawn.region.refilled()
        slow, quick = asyncio.Event(), asyncio.Event()
        calls = [("slow", slow), ("quick", quick)]

        async def build() -> None:
            word, gate = calls.pop(0)
            await gate.wait()
            ui.label(word)

        drawn.region._build = build
        first = asyncio.ensure_future(drawn.region.refilled())
        await asyncio.sleep(0)
        second = asyncio.ensure_future(drawn.region.refilled())
        await asyncio.sleep(0)
        quick.set()
        await second
        slow.set()
        await first

        self.assertEqual(["before", "quick", "after"], drawn.shown())

    async def test_a_build_that_fails_leaves_what_was_drawn(self) -> None:
        drawn = Waiting()
        await drawn.region.refilled()

        async def broken() -> None:
            raise OSError("gone")

        drawn.region._build = broken
        with self.assertLogs("vpinfe.manager.kit.panel", level="ERROR"):
            await drawn.region.refilled()

        self.assertEqual(["before", "one", "after"], drawn.shown())

    async def test_a_first_draw_that_fails_raises_and_leaves_no_empty_part(self) -> None:
        drawn = Waiting()

        async def broken() -> None:
            raise OSError("gone")

        drawn.region._build = broken
        with self.assertRaises(OSError):
            await drawn.region.refilled()

        box = drawn.region._box
        assert box is not None
        self.assertEqual([], list(box.default_slot.children))


if __name__ == "__main__":
    unittest.main()

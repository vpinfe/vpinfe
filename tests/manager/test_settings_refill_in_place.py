"""A settings write draws again only the part of the page that reads it."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from manager.kit import panel
from manager.settings import settings


class KindSwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = MagicMock()
        self.library.library_policy.return_value = {}
        self.library.put_library_policy.return_value = {}
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch.object(
            settings.offload, "io",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch("manager.kit.on_page.ui"))
        self.enterContext(patch.object(settings, "ui"))
        self.enterContext(patch.object(panel, "facts"))
        self.enterContext(patch.object(panel, "lede"))
        self.switch = self.enterContext(patch.object(panel, "switch"))

    async def _flip(self, refill: Any) -> None:
        await settings._fill_kinds(self.library, refill, MagicMock(), "note", "library",
                                   "hidden_media_kinds", lambda _library: {"wheel": "Wheel"})
        [(_, on_change), *_] = [call.args for call in self.switch.call_args_list]
        await on_change(SimpleNamespace(value=False))

    async def test_a_switch_is_written_to_the_library_s_list(self) -> None:
        await self._flip(None)

        self.library.put_library_policy.assert_called_once_with(
            {"hidden_media_kinds": ["wheel"]})

    async def test_a_page_where_something_reads_the_list_draws_that_part_again(self) -> None:
        refill = Mock()

        await self._flip(refill)

        refill.assert_called_once()

    async def test_a_second_switch_thrown_before_the_first_is_written_keeps_both(self) -> None:
        both = {"wheel": "Wheel", "backglass": "Backglass"}
        written: list[dict] = []
        release = asyncio.Event()

        async def slow(call: Any, *args: Any, **kwargs: Any) -> Any:
            written.append(args[0])
            if len(written) == 1:
                await release.wait()
            return {}

        cast(Any, settings.run.io_bound).side_effect = slow
        await settings._fill_kinds(self.library, None, MagicMock(), "note", "library",
                                   "hidden_media_kinds", lambda _library: both)
        # Drawn in label order: Backglass, then Wheel.
        [(_, backglass), (_, wheel)] = [call.args for call in self.switch.call_args_list]
        first = asyncio.ensure_future(wheel(SimpleNamespace(value=False)))
        await asyncio.sleep(0)
        second = asyncio.ensure_future(backglass(SimpleNamespace(value=False)))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)

        self.assertEqual(written[-1], {"hidden_media_kinds": ["backglass", "wheel"]})

    async def test_a_refused_write_draws_nothing_again(self) -> None:
        refill = Mock()
        self.library.put_library_policy.side_effect = OSError("refused")

        await self._flip(refill)

        refill.assert_not_called()


class SettledTests(unittest.IsolatedAsyncioTestCase):
    def _page(self, read: tuple[Any, ...],
              state: dict[str, Any] | None = None) -> tuple[Any, Mock]:
        refilled = Mock()
        with patch.object(panel, "region",
                          new=lambda draw, swapped: SimpleNamespace(refill=refilled)):
            page = settings.SchemaPage(MagicMock(), "k", ("tools",), state, [])
        page.read = read
        return page, refilled

    async def test_a_write_that_leaves_the_marks_as_they_were_draws_nothing(self) -> None:
        page, refilled = self._page(("same",))

        with patch.object(settings, "_marked", new=AsyncMock(return_value=("same",))):
            await page.settled()

        refilled.assert_not_called()

    async def test_a_write_that_moves_a_mark_draws_the_page_again(self) -> None:
        page, refilled = self._page(("before",))

        with patch.object(settings, "_marked", new=AsyncMock(return_value=("after",))):
            await page.settled()

        refilled.assert_called_once()

    async def test_a_drop_on_the_old_rows_is_ignored_until_the_new_ones_are_shown(self) -> None:
        old = SimpleNamespace(stale=True)
        state = {"theme_sources": old}
        page, _ = self._page((), state)

        found: dict[str, list] = {"lists": [], "themes": []}
        library = MagicMock()
        library.theme_sources.return_value = found
        with patch.object(settings.offload, "io",
                          new=AsyncMock(side_effect=lambda call, *a: call(*a))), \
                patch.object(settings.theme_sources, "rows", return_value=[]):
            await settings._theme_sources(library, state, Mock())
        fresh = state["theme_sources"]

        self.assertIsNot(fresh, old)
        self.assertTrue(fresh.stale)
        page._shown()
        self.assertFalse(fresh.stale)

    async def test_the_rail_s_marks_are_read_again_before_the_page_is_compared(self) -> None:
        recheck = AsyncMock()
        page, _ = self._page(("same",), {"recheck_trouble": recheck})

        with patch.object(settings, "_marked", new=AsyncMock(return_value=("same",))):
            await page.settled()

        recheck.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

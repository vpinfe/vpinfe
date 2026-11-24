"""Settings > Themes, the sources block: what a change writes, and what it leaves alone."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import httpapi
from common.config import paths
from common.online import theme_source_checks as checks
from manager.frontend import theme_sources
from manager.kit import offload, panel

LISTS, THEMES = theme_sources.LISTS, theme_sources.THEMES

ONE = "https://example.test/one/themes.json"
TWO = "https://example.test/two/themes.json"
THREE = "https://example.test/three/themes.json"
THEME = "https://github.com/owner/neon"


def source(address: str, **fields: Any) -> dict[str, Any]:
    return {"address": address, "name": address, "state": "ready", "level": "", **fields}


class Written:
    """A Library that only records what the block asked of it."""

    def __init__(self, sources: dict[str, list[dict[str, Any]]]) -> None:
        self.sources = sources
        self.writes: list[dict[str, Any]] = []
        self.reads = 0

    def put_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        self.writes.append(changes)
        for group, addresses in changes["themes"].items():
            self.sources[group] = [source(one) for one in addresses]
        return {}

    def theme_sources(self) -> dict[str, Any]:
        return self.sources

    def themes(self, refresh: bool = False) -> dict[str, Any]:
        self.reads += 1
        return {}


class BlockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = Written({LISTS: [source(ONE), source(TWO), source(THREE)],
                                THEMES: []})
        self.rerender = MagicMock()
        self.block = theme_sources.Block(self.library, self.library.sources,  # type: ignore[arg-type]
                                         self.rerender)
        self.enterContext(patch.object(
            offload, "io", new=AsyncMock(side_effect=lambda call, *args: call(*args))))
        self.enterContext(patch("manager.kit.on_page.ui"))
        self.enterContext(patch.object(theme_sources, "ui"))
        self.offered = self.enterContext(patch.object(theme_sources.undo, "offer"))

    async def test_a_drop_writes_the_order_it_made_and_reads_the_themes_again(self) -> None:
        await self.block.moved(LISTS, {"from": 0, "to": 2})

        self.assertEqual(self.library.writes, [{"themes": {LISTS: [TWO, THREE, ONE]}}])
        self.assertEqual(self.library.reads, 1)
        self.rerender.assert_called_once()

    async def test_a_step_off_either_end_writes_nothing(self) -> None:
        await self.block.step(LISTS, ONE, -1)
        await self.block.step(LISTS, THREE, 1)

        self.assertEqual(self.library.writes, [])

    async def test_a_drop_that_names_no_row_writes_nothing(self) -> None:
        for event in ({}, {"from": "x", "to": 1}, {"from": 5, "to": 0}, {"from": 1, "to": 1}):
            await self.block.moved(LISTS, event)

        self.assertEqual(self.library.writes, [])

    async def test_removing_the_last_list_leaves_none_and_offers_it_back(self) -> None:
        for one in (ONE, TWO, THREE):
            block = theme_sources.Block(self.library, self.library.sources,  # type: ignore[arg-type]
                                        self.rerender)
            await block.remove(LISTS, source(one, name=one))

        self.assertEqual(self.library.writes[-1], {"themes": {LISTS: []}})
        self.assertEqual(self.offered.call_count, 3)

        self.library.sources[LISTS] = []
        self.library.writes.clear()
        await self.offered.call_args.args[1]()

        self.assertEqual(self.library.writes, [{"themes": {LISTS: [THREE]}}])

    async def test_the_stock_list_is_put_back_once(self) -> None:
        self.library.sources[LISTS] = []

        await theme_sources.add_default(self.library, self.rerender)  # type: ignore[arg-type]
        await theme_sources.add_default(self.library, self.rerender)  # type: ignore[arg-type]

        self.assertEqual(self.library.writes,
                         [{"themes": {LISTS: [theme_sources.default_list()]}}])
        self.rerender.assert_called_once()


    async def test_two_removals_in_one_window_both_stand(self) -> None:
        await asyncio.gather(self.block.remove(LISTS, source(ONE, name="one")),
                             self.block.remove(LISTS, source(TWO, name="two")))

        self.assertEqual(self.library.writes[-1], {"themes": {LISTS: [THREE]}})

    async def test_a_drop_after_a_write_waits_for_the_page_to_be_drawn_again(self) -> None:
        await self.block.remove(LISTS, source(ONE, name="one"))
        self.library.writes.clear()

        await self.block.moved(LISTS, {"from": 0, "to": 1})

        self.assertEqual(self.library.writes, [])

    async def test_a_drop_whose_write_fails_draws_the_rows_again(self) -> None:
        self.library.put_config = MagicMock(side_effect=OSError("read-only"))  # type: ignore[method-assign]

        await self.block.moved(LISTS, {"from": 0, "to": 2})

        self.rerender.assert_called_once()
        self.assertEqual(self.block.addresses[LISTS], [ONE, TWO, THREE])

    async def test_undo_puts_back_only_the_one_removed_at_its_place(self) -> None:
        await self.block.remove(LISTS, source(ONE, name="one"))
        self.library.sources[LISTS] = [source(TWO), source(THREE), source(THEME)]
        self.library.writes.clear()

        await self.offered.call_args.args[1]()

        self.assertEqual(self.library.writes,
                         [{"themes": {LISTS: [ONE, TWO, THREE, THEME]}}])

    async def test_undo_that_cannot_write_says_so_instead_of_undone(self) -> None:
        await self.block.remove(LISTS, source(ONE, name="one"))
        self.library.put_config = MagicMock(side_effect=OSError("read-only"))  # type: ignore[method-assign]

        with self.assertRaises(OSError):
            await self.offered.call_args.args[1]()


class RowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.block = theme_sources.Block(
            Written({}), {}, MagicMock())  # type: ignore[arg-type]
        self.ui = self.enterContext(patch.object(theme_sources, "ui"))
        self.actions = self.enterContext(patch.object(panel, "action"))

    def _strip(self, sources: dict[str, Any]) -> list[str]:
        self.actions.reset_mock()
        entries = theme_sources.rows(self.block, sources)
        entries[1][1]()
        return [call.args[0] for call in self.actions.call_args_list]

    def test_one_row_holds_every_source_and_one_strip_adds(self) -> None:
        entries = theme_sources.rows(self.block, {LISTS: [source(ONE)], THEMES: [source(THEME)]})

        self.assertEqual([label for label, _value in entries[:1]],
                         [theme_sources.t("manager.theme_sources.label")])
        self.assertIs(entries[1][0], panel.ASIDE)
        self.assertEqual(len(entries), 2)

    def test_the_stock_list_is_offered_back_only_while_it_is_missing(self) -> None:
        add, back = (theme_sources.t("manager.theme_sources.add"),
                     theme_sources.t("manager.theme_sources.add_default"))
        stock = theme_sources.default_list()

        self.assertEqual(self._strip({LISTS: [source(ONE)], THEMES: []}), [add, back])
        self.assertEqual(self._strip({LISTS: [], THEMES: []}), [add, back])
        self.assertEqual(self._strip({LISTS: [source(stock)], THEMES: []}), [add])

    def test_no_sources_says_so(self) -> None:
        theme_sources.rows(self.block, {LISTS: [], THEMES: []})[0][1]()

        self.ui.label.assert_called_once_with(theme_sources.t("manager.theme_sources.none"))

    def test_an_unread_source_reads_when_the_rows_are_drawn(self) -> None:
        sources = {LISTS: [source(ONE, state=checks.NOT_CHECKED)], THEMES: []}
        waiting = theme_sources.Block(Written({}), sources, MagicMock(),  # type: ignore[arg-type]
                                      frozenset({ONE}))

        theme_sources.rows(self.block, sources)[0][1]()
        self.ui.timer.assert_not_called()
        theme_sources.rows(waiting, sources)[0][1]()
        self.ui.timer.assert_called_once()

    def test_the_stock_list_is_named_and_says_where_it_lives(self) -> None:
        stock = source(theme_sources.default_list(), name="vpinfe/themes",
                       display="vpinfe/themes", count=12)

        self.assertEqual(theme_sources._title(LISTS, stock), "VPinFE Theme List")
        self.assertEqual(theme_sources._detail(LISTS, stock), "12 themes · vpinfe/themes")

    def test_another_list_keeps_its_own_name_and_says_only_its_count(self) -> None:
        other = source(ONE, name="example/one", display="example/one", count=12)

        self.assertEqual(theme_sources._title(LISTS, other), "example/one")
        self.assertEqual(theme_sources._detail(LISTS, other), "12 themes")

    def test_a_single_theme_says_its_version_where_and_pin(self) -> None:
        one = source(THEME, name="Neon", display="owner/neon", version="1.2", pinned="dev")

        self.assertEqual(theme_sources._detail(THEMES, one), "owner/neon · 1.2 · Pinned to dev")
        unread = source(THEME, name="owner/neon", display="owner/neon")
        self.assertEqual(theme_sources._detail(THEMES, unread), "")

    def test_a_name_somebody_gave_replaces_the_one_a_source_has(self) -> None:
        stock = source(theme_sources.default_list(), name="vpinfe/themes",
                       display="vpinfe/themes", count=12, label="Mine")
        other = source(ONE, name="example/one", display="example/one", count=3, label="Dave's")

        self.assertEqual(theme_sources._title(LISTS, stock), "Mine")
        self.assertEqual(theme_sources._title(LISTS, other), "Dave's")

    def test_a_renamed_list_says_where_it_lives(self) -> None:
        other = source(ONE, name="example/one", display="example/one", count=3, label="Dave's")

        self.assertEqual(theme_sources._detail(LISTS, other), "3 themes · example/one")

    def test_a_name_that_is_what_the_source_is_called_anyway_is_not_kept(self) -> None:
        self.assertEqual(theme_sources._kept("  ", "Neon"), "")
        self.assertEqual(theme_sources._kept("Neon", "Neon"), "")
        self.assertEqual(theme_sources._kept(" My   Neon ", "Neon"), "My Neon")
        self.assertEqual(theme_sources._kept("Neon  Pack", "Neon  Pack"), "")
        long = "x" * 200
        self.assertEqual(theme_sources._kept(long[:theme_sources.online.NAME_LIMIT], long), "")

    def test_the_suggestion_is_the_themes_own_name_or_the_lists_repository(self) -> None:
        self.assertEqual(theme_sources._suggested(
            {"kind": checks.THEME, "address": THEME, "name": "Neon"}), "Neon")
        self.assertEqual(theme_sources._suggested(
            {"kind": checks.LIST, "address": ONE, "name": "example/one"}), "example/one")
        self.assertEqual(theme_sources._suggested(
            {"kind": checks.LIST, "address": theme_sources.default_list(), "name": "x/y"}),
            "VPinFE Theme List")


class ChipTests(unittest.TestCase):
    def test_every_state_that_carries_a_level_has_its_word(self) -> None:
        for state in (checks.NOT_CHECKED, checks.NOT_ANSWERING, checks.NOT_FOUND,
                      checks.NOT_A_THEME, checks.SOME_UNREADABLE, checks.NOT_USED,
                      checks.NEEDS_VPINFE):
            if checks.level(state):
                word = theme_sources._word({"state": state, "needs": "3.0"})
                self.assertFalse(word.startswith("manager."), state)


class EmptiedListTests(unittest.TestCase):
    """An emptied `registries` is stored as empty, not read back as the default."""

    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(paths, "VPINFE_INI_PATH", root / "vpinfe.ini"))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _lists(self) -> list[str]:
        found = self.client.get("/themes/sources").json()
        return [one["address"] for one in found[LISTS]]

    def test_an_emptied_list_stays_empty(self) -> None:
        self.assertEqual(self._lists(), [theme_sources.default_list()])

        put = self.client.put("/config", json={"settings": {"themes": {LISTS: []}}})

        self.assertFalse(put.is_error, put.text)
        self.assertEqual(self._lists(), [])

        body = {"settings": {"themes": {LISTS: [theme_sources.default_list()]}}}
        put = self.client.put("/config", json=body)

        self.assertFalse(put.is_error, put.text)
        self.assertEqual(self._lists(), [theme_sources.default_list()])


class NamedSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(paths, "VPINFE_INI_PATH", root / "vpinfe.ini"))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _label(self) -> str:
        return str(self.client.get("/themes/sources").json()[LISTS][0]["label"])

    def test_a_name_is_given_shown_and_taken_back_without_touching_the_address(self) -> None:
        stock = theme_sources.default_list()

        put = self.client.put("/themes/sources/name", json={"address": stock, "name": "Mine, 100%"})

        self.assertFalse(put.is_error, put.text)
        self.assertEqual(self._label(), "Mine, 100%")
        self.client.put("/themes/sources/name", json={"address": stock, "name": ""})
        self.assertEqual(self._label(), "")
        listed = self.client.get("/themes/sources").json()[LISTS]
        self.assertEqual([one["address"] for one in listed], [stock])


if __name__ == "__main__":
    unittest.main()

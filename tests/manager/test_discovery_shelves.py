"""The arrangement of Discovery's Home shelves: what is stored, how Home honors it, and the
Settings list that writes it."""

from __future__ import annotations

import ast
import asyncio
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from nicegui import ui

from common.i18n import t
from manager.library import discovery as discovery_page
from manager.library import discovery_home as home
from manager.library import discovery_search
from manager.library import discovery_shelves as shelves
from manager.settings import shelves as settings_shelves
from tests.manager import test_discovery_opened as opened
from tests.manager import test_discovery_unmatched as unmatched
from tests.support import trees
from tests.support.home_shelf import _PAGE, settle, words_of

NEW = "New on VPS"
DONT_HAVE = "Games You Don't Have"
FAMILY = "Other Versions of Your Games"
OPENED = "Opened but Not Imported"
AUTHORS = "Authors You Collect"
VIEWS = "Your Views"
HEADINGS = [NEW, DONT_HAVE, FAMILY, OPENED, AUTHORS, VIEWS]
EMPTY = "Every shelf is off"


def _first(*ids: str) -> list[str]:
    """The built order with `ids` moved to the top, as the Settings list writes it."""
    return [*ids, *[one for one in shelves.IDS if one not in ids]]


def _links(body: ui.element) -> list[str]:
    return [str(one.text) for one in body.descendants()
            if isinstance(one, ui.link) and not one.is_deleted]


class TheStoredArrangement(unittest.TestCase):
    def test_nothing_stored_is_every_shelf_in_the_built_order(self) -> None:
        self.assertEqual(list(shelves.IDS), shelves.order(None))
        self.assertEqual(list(shelves.IDS), shelves.shown({}))

    def test_a_stored_order_is_the_order_drawn(self) -> None:
        stored = [*reversed(shelves.IDS)]

        self.assertEqual(stored, shelves.shown({shelves.ORDER_KEY: stored}))

    def test_an_id_that_is_no_shelf_is_ignored_in_the_order_and_in_the_hidden_list(self) -> None:
        stored = [*reversed(shelves.IDS)]
        shown = shelves.shown({shelves.ORDER_KEY: ["nonesuch", *stored[:3], "nonesuch",
                                                   *stored[3:], stored[0]]})

        self.assertEqual(stored, shown)
        self.assertEqual(stored[1:], shelves.shown({shelves.ORDER_KEY: stored,
                                                    shelves.HIDDEN_KEY: [stored[0], "nonesuch"]}))

    def test_a_shelf_the_order_lacks_follows_the_shelf_it_follows_by_default(self) -> None:
        stored = [one for one in shelves.IDS if one != "health"][::-1]

        placed = shelves.order(stored)

        at = placed.index(shelves.IDS[shelves.IDS.index(shelves.HEALTH) - 1])
        self.assertEqual(shelves.HEALTH, placed[at + 1])
        self.assertEqual(stored, [one for one in placed if one != shelves.HEALTH])

    def test_a_first_shelf_the_order_lacks_goes_first(self) -> None:
        stored = [one for one in shelves.IDS if one != shelves.UPDATES][::-1]

        self.assertEqual(shelves.UPDATES, shelves.order(stored)[0])

    def test_a_hidden_shelf_is_left_out_and_the_rest_keep_their_order(self) -> None:
        shown = shelves.shown({shelves.HIDDEN_KEY: ["new", "opened"]})

        self.assertEqual([one for one in shelves.IDS if one not in ("new", "opened")], shown)

    def test_a_hidden_shelf_stays_hidden_wherever_the_order_puts_it(self) -> None:
        shown = shelves.shown({shelves.ORDER_KEY: _first("opened"),
                               shelves.HIDDEN_KEY: ["opened"]})

        self.assertNotIn("opened", shown)
        self.assertEqual("updates", shown[0])

    def test_every_shelf_hidden_is_no_shelf(self) -> None:
        self.assertEqual([], shelves.shown({shelves.HIDDEN_KEY: list(shelves.IDS)}))

    def test_the_comma_string_an_ini_holds_reads_as_the_list(self) -> None:
        self.assertEqual(["views", "new"], shelves.listed(" views, new,, "))
        self.assertEqual([one for one in shelves.IDS if one != "new"],
                         shelves.shown({shelves.HIDDEN_KEY: "new"}))

    def test_a_value_that_is_no_list_is_the_default_arrangement(self) -> None:
        for odd in (None, 7, {"a": 1}, mock.Mock()):
            with self.subTest(odd=odd):
                self.assertEqual(list(shelves.IDS), shelves.shown({shelves.ORDER_KEY: odd,
                                                                   shelves.HIDDEN_KEY: odd}))
        self.assertEqual(list(shelves.IDS), shelves.shown(None))
        self.assertEqual(list(shelves.IDS), shelves.shown(mock.Mock()))

    def test_every_shelf_has_a_name_and_a_description_in_the_catalog(self) -> None:
        for shelf in shelves.SHELVES:
            with self.subTest(shelf=shelf.id):
                self.assertNotEqual(shelf.name, t(shelf.name))
                self.assertNotEqual(shelf.description, t(shelf.description))


class HomeLeavesOutTheUpdatesShelfWhileUpdatesAreOff(unittest.IsolatedAsyncioTestCase):
    async def test_the_shelf_goes_and_the_others_keep_their_places(self) -> None:
        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        for shown in (True, False):
            with self.subTest(shown=shown), \
                    mock.patch.object(discovery_page.offload, "io", inline):
                library = mock.Mock()
                library.manager_policy.return_value = {}
                library.updates_shown.return_value = shown

                drawn = await discovery_page._home_shelves(library)

                self.assertEqual(shown, shelves.UPDATES in drawn)
                self.assertEqual([one for one in shelves.IDS if shown or one != shelves.UPDATES],
                                 drawn)


class EveryShelfHasADraw(unittest.TestCase):
    def test_the_draws_name_exactly_the_registry_ids(self) -> None:
        tree = trees.tree_for(home.__file__)
        [named] = [node.value for node in ast.walk(tree)
                   if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                   and node.target.id == "draws" and isinstance(node.value, ast.Dict)]

        ids = [getattr(shelves, key.attr) for key in named.keys
               if isinstance(key, ast.Attribute)]

        self.assertEqual(len(named.keys), len(ids))
        self.assertEqual(sorted(shelves.IDS), sorted(ids))


class HomeHonorsTheArrangement(opened.OpenedShelf):
    def _arrange(self, **stored: Any) -> None:
        self.client.manager_policy.return_value = stored

    @staticmethod
    def _headings(words: list[str]) -> list[str]:
        return sorted((one for one in HEADINGS if one in words), key=words.index)

    async def test_with_nothing_stored_every_shelf_is_drawn_in_the_built_order(self) -> None:
        words = words_of(await self._draw())

        self.assertEqual(HEADINGS, self._headings(words))
        self.assertNotIn(EMPTY, words)

    async def test_a_hidden_shelf_is_not_drawn_and_the_others_are(self) -> None:
        self._arrange(hidden_discovery_shelves=["opened", "new"])

        words = words_of(await self._draw())

        self.assertEqual([DONT_HAVE, FAMILY, AUTHORS, VIEWS], self._headings(words))

    async def test_the_stored_order_is_the_order_drawn(self) -> None:
        self._arrange(discovery_shelf_order=_first("views", "authors", "opened"))

        words = words_of(await self._draw())

        self.assertEqual([VIEWS, AUTHORS, OPENED, NEW, DONT_HAVE, FAMILY],
                         self._headings(words))

    async def test_a_shelf_can_be_moved_above_the_updates(self) -> None:
        self._arrange(discovery_shelf_order=_first("opened"))

        words = words_of(await self._draw())

        self.assertEqual(OPENED, self._headings(words)[0])

    async def test_a_setting_that_cannot_be_read_draws_every_shelf(self) -> None:
        self.client.manager_policy.side_effect = RuntimeError("down")

        words = words_of(await self._draw())

        self.assertEqual(HEADINGS, self._headings(words))

    async def test_every_shelf_hidden_says_so_and_points_at_the_settings(self) -> None:
        self._arrange(hidden_discovery_shelves=list(shelves.IDS))

        body = await self._draw()

        self.assertEqual([], self._headings(words_of(body)))
        self.assertIn(EMPTY, words_of(body))
        link = next(one for one in body.descendants()
                    if isinstance(one, ui.link) and one.text == "Choose Shelves")
        self.assertEqual("/manager?view=settings&page=library.discovery", link.props["href"])

    async def test_the_search_box_is_still_fed_when_your_views_is_hidden(self) -> None:
        self._arrange(hidden_discovery_shelves=["views"])
        box = mock.Mock(gone=False)
        self.state[discovery_search.KEY] = box
        real = home.background_tasks.create
        learning: list[asyncio.Task[Any]] = []

        def spy(*args: Any, **more: Any) -> asyncio.Task[Any]:
            learning.append(real(*args, **more))
            return learning[-1]

        with mock.patch.object(home.background_tasks, "create", spy):
            words = words_of(await self._draw())
        await asyncio.gather(*learning)

        box.feed.assert_called_once()
        self.assertEqual(1, [one.get_name() for one in learning].count("discovery-learn"))
        self.assertNotIn(VIEWS, words)
        self.assertIn(OPENED, words)

    async def test_the_search_box_is_fed_once_the_catalog_is_in_when_your_views_is_shown(
            self) -> None:
        box = mock.Mock(gone=False)
        self.state[discovery_search.KEY] = box

        words = words_of(await self._draw())
        await settle()

        box.feed.assert_called_once()
        self.assertIn(VIEWS, words)

    async def test_reading_the_shelves_again_leaves_a_hidden_shelf_hidden(self) -> None:
        self._arrange(hidden_discovery_shelves=["new"])
        body = await self._draw()

        await self.refresh()
        await settle()

        words = words_of(body)
        self.assertNotIn(NEW, words)
        self.assertIn(OPENED, words)

    async def test_a_failed_reread_with_the_new_shelf_hidden_leaves_the_rest_standing(
            self) -> None:
        self._arrange(hidden_discovery_shelves=["new"])
        body = await self._draw()
        self.client.discovery_home.side_effect = RuntimeError("down")

        await self.refresh()
        await settle()

        self.assertIn(OPENED, words_of(body))


class HomeHonorsTheArrangementOfGamesToMatch(unmatched.UnmatchedShelf):
    async def test_the_shelf_is_drawn_and_a_switch_takes_it_off_home(self) -> None:
        self.assertIn("Games to Match", words_of(await self._draw()))

        self.client.manager_policy.return_value = {"hidden_discovery_shelves": ["match"]}

        self.assertNotIn("Games to Match", words_of(await self._draw()))


class TheSettingsList(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = mock.Mock()
        self.library.manager_policy.return_value = {}
        self.redraw = mock.Mock()
        self.refresh = mock.Mock()

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        self.enterContext(mock.patch.object(settings_shelves.offload, "io", inline))
        self.enterContext(mock.patch("manager.kit.on_page.ui"))

    def _block(self, **stored: Any) -> settings_shelves.Shelves:
        block = settings_shelves.Shelves(self.library, stored, self.redraw, self.refresh)
        return block

    def _written(self) -> dict[str, list[str]]:
        return self.library.put_manager_policy.call_args.args[0]

    def _drawn(self, block: settings_shelves.Shelves,
               draw: Callable[[settings_shelves.Shelves], None] = settings_shelves.draw,
               ) -> ui.element:
        with mock.patch.object(settings_shelves.ui, "run_javascript"), _PAGE:
            body = ui.column()
            with body:
                draw(block)
        return body

    async def test_every_shelf_is_a_row_with_its_name_its_description_and_a_switch(self) -> None:
        body = self._drawn(self._block())

        words = words_of(body)
        switches = [one for one in body.descendants() if isinstance(one, ui.switch)]
        self.assertEqual(len(shelves.SHELVES), len(switches))
        for shelf in shelves.SHELVES:
            with self.subTest(shelf=shelf.id):
                self.assertIn(t(shelf.name), words)
                self.assertIn(t(shelf.description), words)
        self.assertTrue(all(one.value for one in switches))

    async def test_the_rows_follow_the_stored_order_and_a_hidden_shelf_is_off(self) -> None:
        body = self._drawn(self._block(discovery_shelf_order=_first("views"),
                                       hidden_discovery_shelves=["views"]))

        words = words_of(body)
        switches = [one for one in body.descendants() if isinstance(one, ui.switch)]
        self.assertLess(words.index(t("manager.discovery.views")),
                        words.index(t("manager.discovery.updates")))
        self.assertEqual([False] + [True] * (len(switches) - 1), [one.value for one in switches])

    async def test_the_updates_row_is_off_and_says_why_while_updates_are_off(self) -> None:
        block = settings_shelves.Shelves(self.library, {}, self.redraw, self.refresh,
                                         updates=False)

        body = self._drawn(block)

        words = words_of(body)
        switches = [one for one in body.descendants() if isinstance(one, ui.switch)]
        self.assertIn(t("manager.settings.shelf.updates.held"), words)
        self.assertNotIn(t(shelves.BY_ID[shelves.UPDATES].description), words)
        self.assertEqual([False] + [True] * (len(switches) - 1), [one.value for one in switches])
        self.assertEqual([True] + [False] * (len(switches) - 1),
                         [not one.enabled for one in switches])

    async def test_reset_is_offered_only_once_something_is_changed(self) -> None:
        def buttons(block: settings_shelves.Shelves) -> list[str]:
            return [str(one.text) for one in self._drawn(
                block, settings_shelves.draw_reset).descendants()
                    if isinstance(one, ui.button) and not one.is_deleted]

        self.assertNotIn("Reset", buttons(self._block()))
        self.assertIn("Reset", buttons(self._block(hidden_discovery_shelves=["new"])))
        self.assertIn("Reset", buttons(self._block(discovery_shelf_order=_first("views"))))

    async def test_the_rows_hold_no_reset_button(self) -> None:
        body = self._drawn(self._block(hidden_discovery_shelves=["new"]))

        self.assertEqual([], [one for one in body.descendants() if isinstance(one, ui.button)
                              and one.text == "Reset"])

    async def test_each_switch_is_named_for_its_shelf(self) -> None:
        body = self._drawn(self._block())

        tips = [one for one in body.descendants() if isinstance(one, ui.tooltip)]
        named = [[str(tip.text) for tip in tips if tip.props["target"] == f"#{one.html_id}"]
                 for one in body.descendants() if isinstance(one, ui.switch)]
        self.assertEqual([[t(shelf.name)] for shelf in shelves.SHELVES], named)

    async def test_switching_a_shelf_off_writes_it_to_the_hidden_list(self) -> None:
        block = self._block()

        await block.flip("opened", False)

        self.assertEqual({shelves.HIDDEN_KEY: ["opened"]}, self._written())
        self.refresh.assert_called_once()
        self.redraw.assert_not_called()

    async def test_the_switch_on_a_row_hides_the_shelf_when_it_turns_off(self) -> None:
        block = self._block()

        await settings_shelves._flipper(block, "opened")(mock.Mock(value=False))

        self.assertEqual({shelves.HIDDEN_KEY: ["opened"]}, self._written())

    async def test_switching_a_shelf_back_on_takes_it_off_the_list(self) -> None:
        block = self._block(hidden_discovery_shelves=["opened", "new"])

        await block.flip("opened", True)

        self.assertEqual({shelves.HIDDEN_KEY: ["new"]}, self._written())

    async def test_a_stored_name_that_is_no_shelf_is_not_a_change_and_is_not_written_back(
            self) -> None:
        block = self._block(hidden_discovery_shelves=["later_shelf"])
        self.assertTrue(block.unchanged)
        self.assertEqual([], [one for one in self._drawn(
            block, settings_shelves.draw_reset).descendants() if isinstance(one, ui.button)])

        await block.flip("opened", False)

        self.assertEqual(["opened"], self._written()[shelves.HIDDEN_KEY])

    async def test_a_drop_writes_the_whole_order(self) -> None:
        block = self._block()

        await block.moved({"from": len(shelves.IDS) - 1, "to": 0})

        moved = [shelves.IDS[-1], *shelves.IDS[:-1]]
        self.assertEqual(moved, self._written()[shelves.ORDER_KEY])
        self.assertEqual(moved, block.order)
        self.redraw.assert_called_once()
        self.refresh.assert_called_once()

    async def test_a_drop_nowhere_or_out_of_range_writes_nothing(self) -> None:
        block = self._block()

        for event in ({"from": 2, "to": 2}, {"from": 0, "to": 99}, {"from": "x"}, {}):
            with self.subTest(event=event):
                await block.moved(event)

        self.library.put_manager_policy.assert_not_called()

    async def test_move_down_on_the_last_row_writes_nothing(self) -> None:
        block = self._block()

        await block.step(shelves.IDS[-1], 1)

        self.library.put_manager_policy.assert_not_called()

    async def test_a_refused_write_leaves_the_list_as_it_was_and_draws_it_again(self) -> None:
        self.library.put_manager_policy.side_effect = RuntimeError("no")
        block = self._block()

        with mock.patch.object(settings_shelves.ui, "notify") as notify:
            await block.moved({"from": 1, "to": 0})
            await block.flip("new", False)

        self.assertEqual(list(shelves.IDS), block.order)
        self.assertEqual([], block.off)
        self.assertEqual(2, notify.call_count)
        self.assertEqual(2, self.redraw.call_count)
        self.assertEqual(2, self.refresh.call_count)

    async def test_reset_stores_both_as_empty_and_returns_to_the_built_arrangement(self) -> None:
        block = self._block(discovery_shelf_order=["views"], hidden_discovery_shelves=["new"])

        await block.reset()

        self.assertEqual({shelves.ORDER_KEY: [], shelves.HIDDEN_KEY: []}, self._written())
        self.assertTrue(block.unchanged)
        self.redraw.assert_called_once()
        self.refresh.assert_called_once()

    async def test_the_group_is_a_heading_a_line_and_the_list_from_the_settings_read(
            self) -> None:
        state: dict[str, Any] = {}
        regions: list[mock.Mock] = []

        def region(build: Callable[[], Any]) -> mock.Mock:
            regions.append(mock.Mock(build=build))
            return regions[-1]

        with mock.patch.object(settings_shelves.panel, "region", new=region):
            entries = settings_shelves.entries(
                self.library, state, {"manager": {"hidden_discovery_shelves": ["new"]}})

        self.assertEqual(3, len(entries))
        self.assertIs(settings_shelves.panel.HEADING, entries[0][0])
        self.assertIs(settings_shelves.panel.FULL, entries[2][0])
        self.assertEqual(["new"], state[settings_shelves.KEY].off)
        self.library.manager_policy.assert_not_called()
        block = state[settings_shelves.KEY]
        self.assertEqual((regions[0].refill, regions[1].refill), (block.redraw, block.refresh))

    async def test_a_drop_reaches_the_block_the_page_drew(self) -> None:
        block = self._block()

        await settings_shelves.moved({settings_shelves.KEY: block},
                                     mock.Mock(args={"from": 1, "to": 0}))

        self.assertEqual("new", block.order[0])

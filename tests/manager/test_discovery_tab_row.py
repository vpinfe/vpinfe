"""Discovery's tab row: the tabs, the search box at its right end, and what each tab hands
the box."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from nicegui import core, ui

from manager.library import discovery, discovery_ask, discovery_search

_PAGE = ui.element()

PEOPLE = [{"name": "Alpha", "aliases": [], "releases": 4}]
GAMES = [{"id": "g1", "name": "Alien", "manufacturer": "", "themes": []}]


def _drawn(state: dict[str, Any], rerender: Any = None) -> ui.element:
    """The page `build` draws for `state`, the tab's own body left empty."""
    with _PAGE:
        holder = ui.column()
        with holder, mock.patch.object(discovery.busy, "fill"):
            discovery.build(mock.Mock(), state, mock.AsyncMock(), rerender or mock.Mock())
    return holder


def _tabs(holder: ui.element) -> ui.tabs:
    return next(one for one in holder.descendants() if isinstance(one, ui.tabs))


class TabRowTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(discovery.deeplink, "sync")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_home_browse_and_releases_are_tabs_and_the_one_open_is_lit(self) -> None:
        holder = _drawn({"discovery_tab": discovery.BROWSE})

        tabs = _tabs(holder)
        names = [one.props["name"] for one in holder.descendants() if isinstance(one, ui.tab)]
        self.assertEqual([discovery.HOME, discovery.BROWSE, discovery.RELEASES], names)
        self.assertEqual(discovery.BROWSE, tabs.value)

    def test_no_tab_is_lit_while_the_marks_grid_shows(self) -> None:
        holder = _drawn({"discovery_tab": discovery.MARKS})

        self.assertIsNone(_tabs(holder).value)

    def test_a_page_of_an_author_lights_browse(self) -> None:
        holder = _drawn({"subject": {"type": "author", "name": "Alpha"}})

        self.assertEqual(discovery.BROWSE, _tabs(holder).value)

    def test_choosing_a_tab_leaves_a_page_and_draws_the_tab(self) -> None:
        state: dict[str, Any] = {"discovery_tab": discovery.HOME,
                                 "subject": {"type": "author", "name": "Alpha"}}
        rerender = mock.Mock()
        tabs = _tabs(_drawn(state, rerender))

        tabs.set_value(discovery.RELEASES)

        self.assertEqual((discovery.RELEASES, None), (state["discovery_tab"], state["subject"]))
        rerender.assert_called_once()

    def test_the_box_is_on_the_row_beside_the_tabs_on_every_tab(self) -> None:
        for tab in (discovery.HOME, discovery.BROWSE, discovery.RELEASES, discovery.MARKS):
            with self.subTest(tab=tab):
                state: dict[str, Any] = {"discovery_tab": tab}
                holder = _drawn(state)

                row = next(one for one in holder.descendants()
                           if "manager-tabs-row" in one.classes)
                box = state[discovery_search.KEY]
                self.assertIsInstance(box, discovery_search.SearchBox)
                self.assertIn(box.input, list(row.descendants()))
                self.assertIn("manager-search", box.input.classes)
                self.assertNotIn("w-64", box.input.classes)

    def test_the_search_a_page_opened_the_tab_with_is_in_the_box(self) -> None:
        for tab in (discovery.BROWSE, discovery.RELEASES):
            with self.subTest(tab=tab):
                state: dict[str, Any] = {
                    "discovery_tab": tab,
                    "opening": {"name": "Bally", "filters": {}, "search": "alien", "tab": tab}}

                _drawn(state)

                self.assertEqual("alien", state[discovery_search.KEY].input.value)

    def _opened(self, tab: str = discovery.BROWSE) -> dict[str, Any]:
        return {"discovery_tab": tab, "opening": {
            "name": "Bally", "filters": {}, "search": "alien", "tab": tab}}

    def test_an_edit_to_the_box_is_what_a_redraw_of_the_tab_opens_on(self) -> None:
        for tab in (discovery.BROWSE, discovery.RELEASES):
            with self.subTest(tab=tab):
                state = self._opened(tab)
                _drawn(state)

                state[discovery_search.KEY].input.set_value("bally")
                _drawn(state)

                self.assertEqual("bally", state["opening"]["search"])
                self.assertEqual("bally", state[discovery_search.KEY].input.value)

    def test_a_box_cleared_is_not_filled_again_by_a_redraw(self) -> None:
        state = self._opened()
        _drawn(state)

        state[discovery_search.KEY].input.set_value("")
        _drawn(state)

        self.assertNotIn("search", state["opening"])
        self.assertFalse(state[discovery_search.KEY].input.value)

    def test_an_edit_survives_a_visit_to_home_and_back(self) -> None:
        state = self._opened()
        _drawn(state)
        state[discovery_search.KEY].input.set_value("bally")

        state["discovery_tab"] = discovery.HOME
        _drawn(state)
        state["discovery_tab"] = discovery.BROWSE
        _drawn(state)

        self.assertEqual("bally", state[discovery_search.KEY].input.value)

    def test_a_box_edited_after_the_view_was_left_keeps_nothing(self) -> None:
        state = self._opened()
        _drawn(state)
        box = state[discovery_search.KEY]
        discovery_ask.drop_opening(state, discovery.BROWSE)

        box.input.set_value("bally")
        _drawn(state)

        self.assertNotIn("opening", state)
        self.assertFalse(state[discovery_search.KEY].input.value)

    def test_a_search_belongs_to_the_tab_the_opening_is_for(self) -> None:
        state: dict[str, Any] = {
            "discovery_tab": discovery.HOME,
            "opening": {"name": "Bally", "filters": {}, "search": "alien",
                        "tab": discovery.BROWSE}}

        _drawn(state)

        self.assertFalse(state[discovery_search.KEY].input.value)

    def test_a_subject_page_opens_with_an_empty_box_whatever_opening_is_kept(self) -> None:
        state: dict[str, Any] = {
            "subject": {"type": "maker", "name": "Bally"},
            "opening": {"name": "Bally", "filters": {}, "search": "alien",
                        "tab": discovery.BROWSE}}

        _drawn(state)

        self.assertFalse(state[discovery_search.KEY].input.value)

    def test_a_tab_drawn_again_replaces_the_box_it_kept(self) -> None:
        state: dict[str, Any] = {"discovery_tab": discovery.HOME}
        _drawn(state)
        first = state[discovery_search.KEY]

        _drawn(state)

        self.assertIsNot(first, state[discovery_search.KEY])


class SearchBoxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

    def _box(self) -> discovery_search.SearchBox:
        with _PAGE:
            return discovery_search.SearchBox()

    async def test_a_box_not_yet_fed_suggests_nothing(self) -> None:
        box = self._box()

        found = box._find("alpha")

        self.assertEqual([], [one for group in found.values() for one in group])

    async def test_a_box_fed_suggests_authors_and_games(self) -> None:
        box = self._box()
        box.feed(PEOPLE, GAMES, mock.AsyncMock())

        self.assertEqual(["Alpha"], [one["name"] for one in box._find("alpha")["author"]])
        self.assertEqual(["Alien"], [one["name"] for one in box._find("alien")["game"]])

    async def test_lists_fed_are_read_again_as_they_change(self) -> None:
        box = self._box()
        people: list[dict[str, Any]] = []
        box.feed(people, [], mock.AsyncMock())
        people[:] = PEOPLE

        self.assertEqual(["Alpha"], [one["name"] for one in box._find("alpha")["author"]])

    async def test_a_pick_goes_to_the_tab_that_fed_it(self) -> None:
        box = self._box()
        choose = mock.AsyncMock()
        box.feed(PEOPLE, GAMES, choose)

        await box._pick("author", PEOPLE[0])

        choose.assert_awaited_once_with("author", PEOPLE[0])

    async def test_a_pick_before_a_feed_does_nothing(self) -> None:
        await self._box()._pick("author", PEOPLE[0])

    async def test_the_text_typed_narrows_the_tab_that_asked_to(self) -> None:
        box = self._box()
        narrowed: list[str] = []
        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=narrowed.append)

        box.input.set_value("alien")

        self.assertEqual(["alien"], narrowed)

    async def test_text_typed_before_the_feed_narrows_the_tab_and_is_suggested_from(self) -> None:
        box = self._box()
        box.input.set_value("alien")
        narrowed: list[str] = []

        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=narrowed.append)

        self.assertEqual(["alien"], narrowed)
        self.assertEqual([("game", "Alien")],
                         [(group, one["name"]) for group, one in box.suggest.items])

    async def test_text_the_box_opens_with_narrows_the_tab_and_suggests_nothing(self) -> None:
        with _PAGE:
            box = discovery_search.SearchBox("alien")
        narrowed: list[str] = []

        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=narrowed.append)

        self.assertEqual(["alien"], narrowed)
        self.assertEqual([], box.suggest.items)
        self.assertFalse(box.suggest.list.visible)

    async def test_typing_after_the_box_opened_with_text_suggests_again(self) -> None:
        with _PAGE:
            box = discovery_search.SearchBox("alien")
        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=lambda _text: None)

        box.input.set_value("alien x")
        box.input.set_value("alien")

        self.assertEqual([("game", "Alien")],
                         [(group, one["name"]) for group, one in box.suggest.items])

    async def test_an_empty_box_fed_narrows_nothing_yet(self) -> None:
        box = self._box()
        narrowed: list[str] = []

        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=narrowed.append)

        self.assertEqual([], narrowed)
        self.assertEqual([], box.suggest.items)

    async def test_a_feed_without_narrow_takes_the_one_before_off(self) -> None:
        box = self._box()
        narrowed: list[str] = []
        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=narrowed.append)

        box.feed(PEOPLE, GAMES, mock.AsyncMock())
        box.input.set_value("alien")

        self.assertEqual([], narrowed)

    async def test_a_feed_with_a_new_narrow_replaces_the_old_one(self) -> None:
        box = self._box()
        old: list[str] = []
        new: list[str] = []
        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=old.append)
        box.feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=new.append)

        box.input.set_value("alien")

        self.assertEqual(([], ["alien"]), (old, new))

    async def test_feeding_the_row_of_a_page_with_none_is_a_no_op(self) -> None:
        discovery_search.feeder({})(PEOPLE, GAMES, mock.AsyncMock())

    async def test_feeding_the_row_reaches_its_box(self) -> None:
        box = self._box()
        choose = mock.AsyncMock()

        discovery_search.feeder({discovery_search.KEY: box})(PEOPLE, GAMES, choose)

        self.assertEqual((PEOPLE, GAMES, choose), (box.people, box.games, box.choose))

    async def test_a_tab_finishing_late_feeds_the_box_it_was_drawn_with(self) -> None:
        first, second = self._box(), self._box()
        state: dict[str, Any] = {discovery_search.KEY: first}
        feed = discovery_search.feeder(state)
        state[discovery_search.KEY] = second

        feed(PEOPLE, GAMES, mock.AsyncMock())

        self.assertEqual(PEOPLE, first.people)
        self.assertEqual([], second.people)

    async def test_a_box_drawn_over_is_not_fed(self) -> None:
        box = self._box()
        narrowed: list[str] = []
        box.input.set_value("alien")
        state: dict[str, Any] = {discovery_search.KEY: box}
        feed = discovery_search.feeder(state)
        box.root.delete()

        feed(PEOPLE, GAMES, mock.AsyncMock(), narrow=narrowed.append)

        self.assertEqual(([], []), (box.people, narrowed))

"""Discovery's front page: which tab opens, which window is remembered, what a tile says,
and the page drawn and driven the way the Manager draws it."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest import mock
from unittest.mock import patch

from nicegui import background_tasks, core, events, run, ui

from common.i18n import number
from common.online import vps_home, vps_marks
from manager.kit import dropdown, views
from manager.kit.data import Library
from manager.library import (
    discovery,
    discovery_ask,
    discovery_marks,
    discovery_marks_grid,
    discovery_releases,
    discovery_search,
    games,
)
from manager.library import discovery_home as home
from manager.overview import needs

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


class TabTests(unittest.TestCase):
    def test_home_is_the_front_page(self) -> None:
        self.assertEqual(discovery.tab_of({}), discovery.HOME)

    def test_an_address_naming_a_subject_or_a_game_opens_browse(self) -> None:
        self.assertEqual(discovery.tab_of({"subject": {"type": "author", "name": "A"}}),
                         discovery.BROWSE)
        self.assertEqual(discovery.tab_of({"entry": "g1"}), discovery.BROWSE)

    def test_the_tab_chosen_wins_over_a_game_in_the_address(self) -> None:
        self.assertEqual(discovery.tab_of({"discovery_tab": discovery.HOME, "entry": "g1"}),
                         discovery.HOME)
        self.assertEqual(discovery.tab_of({"discovery_tab": discovery.RELEASES}),
                         discovery.RELEASES)

    def test_the_marks_grid_is_a_tab_to_reach_but_has_no_segment(self) -> None:
        self.assertEqual(discovery.tab_of({"discovery_tab": discovery.MARKS}), discovery.MARKS)

    def test_a_tab_that_is_not_one_falls_back_to_the_front_page(self) -> None:
        self.assertEqual(discovery.tab_of({"discovery_tab": "games"}), discovery.HOME)

    def test_a_shelf_opens_the_grid_on_the_tab_it_names(self) -> None:
        state: dict[str, Any] = {"discovery_tab": discovery.HOME}
        opening = {"name": "New in the Past Week", "filters": {}}
        with patch.object(discovery.deeplink, "sync"):
            discovery.open_page(state, [], lambda: None, None, opening=opening,
                                tab=discovery.RELEASES)

        self.assertEqual(discovery.tab_of(state), discovery.RELEASES)
        self.assertEqual(discovery_ask.opening_on(state, discovery.RELEASES),
                         {**opening, "tab": discovery.RELEASES})
        self.assertIsNone(discovery_ask.opening_on(state, discovery.BROWSE))


class WindowTests(unittest.TestCase):
    def test_the_windows_are_a_week_two_and_a_month(self) -> None:
        self.assertEqual(sorted(home.WINDOWS), [7, 14, 30])

    def test_an_unknown_window_is_a_week(self) -> None:
        self.assertEqual(home.window_days(14), 14)
        self.assertEqual(home.window_days(365), 7)
        self.assertEqual(home.window_days(None), 7)

    def test_the_window_remembered_is_the_one_read_back(self) -> None:
        with patch.object(home.remembered, "get", return_value=30):
            self.assertEqual(home.remembered_days(), 30)
        with patch.object(home.remembered, "get", return_value=None):
            self.assertEqual(home.remembered_days(), 7)


class SeeAllTests(unittest.TestCase):
    def test_the_window_of_a_shelf_is_the_added_filter_on_releases(self) -> None:
        filters = discovery_releases.arrival_filters({"since": 14}, counted=True)

        self.assertEqual(filters, {"added": {"days": 14}})

    def test_the_kind_and_the_library_are_asked_beside_the_window(self) -> None:
        filters = discovery_releases.arrival_filters(
            {"since": 7, "kind": "b2sFiles", "owned": "yes"}, counted=True)

        self.assertEqual(filters, {"added": {"days": 7}, "kind": {"values": ["b2sFiles"]},
                                   "in_library": {"values": ["yes"]}})

    def test_in_library_is_left_out_before_the_library_is_counted(self) -> None:
        filters = discovery_releases.arrival_filters({"since": 7, "owned": "yes"},
                                                     counted=False)

        self.assertEqual(filters, {"added": {"days": 7}})


class TileTests(unittest.TestCase):
    def test_a_re_dated_release_says_what_it_was(self) -> None:
        self.assertEqual(home.version_line({"version": "1.0.8", "was": "1.0.7"}),
                         "1.0.8, was 1.0.7")

    def test_a_release_that_never_changed_says_only_its_version(self) -> None:
        self.assertEqual(home.version_line({"version": "1.0.8", "was": ""}), "1.0.8")

    def test_a_release_with_no_version_says_nothing(self) -> None:
        self.assertEqual(home.version_line({"version": "", "was": "1.0"}), "")


class ViewCountTests(unittest.TestCase):
    ROWS = [{"in_library": "yes", "gap": "missing"}, {"in_library": "yes", "gap": ""},
            {"in_library": "no", "gap": ""}]

    def test_a_view_of_plain_value_lists_is_counted(self) -> None:
        one = views.View(id="v", name="Gaps", filters={
            "in_library": {"values": ["yes"]}, "gap": {"values": ["missing"]}})

        self.assertEqual(home.view_count(one, self.ROWS, compared=True), 1)

    def test_a_filter_it_cannot_read_leaves_the_count_out(self) -> None:
        one = views.View(id="v", name="Text", filters={"name": {"type": "contains"}})

        self.assertIsNone(home.view_count(one, self.ROWS, compared=True))

    def test_a_filtered_view_has_no_count_before_the_library_is_compared(self) -> None:
        one = views.View(id="v", name="Gaps", filters={"gap": {"values": ["missing"]}})
        everything = views.View(id="w", name="All", filters={})

        self.assertIsNone(home.view_count(one, self.ROWS, compared=False))
        self.assertEqual(home.view_count(everything, self.ROWS, compared=False), 3)


class ListCellTests(unittest.TestCase):
    def test_a_list_column_counts_a_row_that_holds_any_value_picked(self) -> None:
        rows: list[dict[str, Any]] = [
            {"themes": ["Space", "Horror"]}, {"themes": ["Sports"]}, {"themes": []}]
        one = views.View(id="v", name="Space", filters={"themes": {"values": ["Space"]}})

        self.assertEqual(home.view_count(one, rows, compared=True), 1)

    def test_a_row_matching_any_of_several_values_is_counted_once(self) -> None:
        rows: list[dict[str, Any]] = [{"themes": ["Space", "Horror"]}, {"themes": ["Sports"]}]
        one = views.View(id="v", name="Two", filters={
            "themes": {"values": ["Space", "Horror", "Sports"]}})

        self.assertEqual(home.view_count(one, rows, compared=True), 2)


def _day(ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _release(rid: str, ago: int, **more: Any) -> dict[str, Any]:
    return {"id": rid, "by": [0], "added": _day(ago), "held": False, "mod": False,
            "version": "", **more}


PEOPLE = [{"name": "Alpha", "aliases": [], "releases": 4}]
FOUND: dict[str, Any] = {"counted": "2026-10-01T10:00:00Z", "people": PEOPLE, "rows": [
    {"vps_id": "g1", "name": "Alien", "in_library": True, "img_url": "", "manufacturer": "",
     "year": None, "type": "", "themes": ["Space"], "url": "",
     "listed": ["tableFiles", "b2sFiles", "romFiles"], "held": ["tableFiles"],
     "releases": {
         "tableFiles": [_release("t1", 0, held=True, version="1.2")],
         "b2sFiles": [_release("b1", 3)],
         "romFiles": [_release("r1", 0)],
         "ruleFiles": [_release("x1", 0)]}},
    {"vps_id": "g2", "name": "Batman", "in_library": False, "img_url": "", "manufacturer": "",
     "year": None, "type": "", "themes": [], "url": "", "listed": ["tableFiles"], "held": [],
     "releases": {"tableFiles": [{**_release("t2", 2, mod=True), "format": "FP"},
                                    {**_release("t9", 20), "format": "FP"}]}},
]}
KEPT = ["tableFiles", "b2sFiles", "romFiles", "ruleFiles"]


def _words(body: ui.element) -> list[str]:
    return [str(one.text) for one in body.descendants()
            if isinstance(one, ui.label) and not one.is_deleted]


def _options(choice: dropdown.Select) -> list[str]:
    """Each option as the open list reads it: the name, then its count where it has one."""
    counts = cast(dict[Any, int], choice._counts)
    return [f"{label} {number(counts[value])}" if value in counts else str(label)
            for value, label in cast(dict[Any, Any], choice.options).items()]


def _labels(body: ui.element) -> list[str]:
    return [label for one in body.descendants()
            if isinstance(one, dropdown.Select) and not one.is_deleted for label in _options(one)]


async def _press(button: ui.button) -> None:
    started: list[Any] = []
    with mock.patch.object(background_tasks, "create_or_defer",
                           lambda awaitable, **_: started.append(awaitable)):
        for listener in button._event_listeners.values():
            if listener.type == "click" and listener.handler is not None:
                listener.handler(events.ClickEventArguments(sender=button,
                                                            client=button.client))
    await asyncio.gather(*started)


async def _settle() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


class SeeAllCountsTests(unittest.TestCase):
    """The number on a chip is the number of rows See All opens."""

    @staticmethod
    def _opened(filters: dict[str, Any], days: int) -> int:
        cutoff = vps_home.since(days)
        values = filters["kind"]["values"]
        owned = filters.get("in_library", {}).get("values") == ["yes"]
        return sum(1 for row in FOUND["rows"]
                   for listing, releases in row["releases"].items() if listing in values
                   for one in releases
                   if one["added"] >= cutoff and (row["in_library"] or not owned))

    def test_every_chip_opens_as_many_releases_as_it_counts(self) -> None:
        with mock.patch.object(vps_home.arrivals, "was", return_value=""):
            shelf = vps_home.home(FOUND, KEPT, 30, formats=("VPX",))["new"]
        for chip in (vps_home.ALL, vps_home.YOURS, "tableFiles", "b2sFiles"):
            with self.subTest(chip=chip):
                filters = home.new_filters(30, chip, KEPT, counted=True)

                self.assertEqual(self._opened(filters, 30), shelf[chip]["count"])

    def test_rule_sheets_are_left_out_of_all_as_the_shelf_leaves_them_out(self) -> None:
        filters = home.new_filters(7, vps_home.ALL, KEPT, counted=True)

        self.assertNotIn("ruleFiles", filters["kind"]["values"])
        self.assertEqual(filters["added"], {"days": 7})

    def test_a_chip_for_a_kind_opens_that_kind_alone(self) -> None:
        filters = home.new_filters(14, "b2sFiles", KEPT, counted=True)

        self.assertEqual(filters["kind"], {"values": ["b2sFiles"]})

    def test_your_games_asks_for_the_library_once_it_is_counted(self) -> None:
        self.assertEqual(home.new_filters(7, vps_home.YOURS, KEPT, counted=True)["in_library"],
                         {"values": ["yes"]})
        self.assertNotIn("in_library",
                         home.new_filters(7, vps_home.YOURS, KEPT, counted=False))


ALIEN_UPDATE: dict[str, Any] = {
    "game_id": "game-g1", "vps_id": "g1", "game": "Alien", "img_url": "",
    "moves": [{"source": "vps", "kind": "tableFiles", "release_id": "t1", "vps_id": "g1",
               "file": "Alien.vpx", "then": "1.0", "now": "1.2"},
              {"source": "vps", "kind": "b2sFiles", "release_id": "b1", "vps_id": "g1",
               "file": "Alien.directb2s", "then": "2.0", "now": "2.1"}]}


def _picture(kind: str, md5: str = "abc") -> dict[str, Any]:
    return {"source": "vpinmediadb", "kind": kind, "release_id": "", "vps_id": "g1",
            "file": "Alien Folder", "then": "", "now": "", "md5": md5}


PICTURE_UPDATE: dict[str, Any] = {
    "game_id": "game-g1", "vps_id": "g1", "game": "Alien", "img_url": "",
    "moves": [_picture("wheel"), _picture("backglass")]}


class MoveTests(unittest.TestCase):
    def test_a_games_replaced_pictures_share_one_line_naming_them(self) -> None:
        self.assertEqual(["Wheel and Backglass pictures"],
                         home.tile_lines(PICTURE_UPDATE["moves"]))
        self.assertEqual(["Wheel pictures"], home.tile_lines([_picture("wheel")]))

    def test_three_pictures_are_listed_with_commas(self) -> None:
        self.assertEqual(["Wheel, Backglass and Flyer pictures"], home.tile_lines(
            [_picture("wheel"), _picture("backglass"), _picture("flyer")]))

    def test_a_video_or_a_sound_is_named_without_calling_it_a_picture(self) -> None:
        self.assertEqual(["Wheel and Playfield Video"],
                         home.tile_lines([_picture("wheel"), _picture("playfield_video")]))

    def test_the_pictures_line_follows_the_lines_of_the_files(self) -> None:
        self.assertEqual(["Table 1.0 to 1.2", "Backglass 2.0 to 2.1", "Wheel pictures"],
                         home.tile_lines([*ALIEN_UPDATE["moves"], _picture("wheel")]))

    def test_a_tied_release_says_the_version_it_moved_from_and_to(self) -> None:
        self.assertEqual(home.move_line(ALIEN_UPDATE["moves"][0]), "Table 1.0 to 1.2")

    def test_a_source_that_names_only_its_version_says_it_is_available(self) -> None:
        move = {**ALIEN_UPDATE["moves"][0], "then": ""}

        self.assertEqual(home.move_line(move), "Table 1.2 available")


def _offered(game_id: str, vps_id: str, name: str) -> dict:
    return {"game_id": game_id, "vps_id": vps_id, "name": name, "available": 1}


class DrivenHome(unittest.IsolatedAsyncioTestCase):
    """Home drawn by `_fill_home`, through the Library and a client that answers."""

    def setUp(self) -> None:
        self.prefs: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.client = mock.Mock()
        self.client.library_policy.return_value = {"hidden_asset_kinds": [],
                                                   "hidden_media_kinds": []}
        self.client.catalog_discovery.return_value = FOUND
        self.client.discovery_marks.return_value = []
        self.client.preferences.side_effect = lambda scope: self.prefs.get(scope, {})
        self.client.put_preferences.side_effect = \
            lambda scope, value: self.writes.append((scope, value))
        self.client.discovery_home.side_effect = self._answer
        self.library = Library(self.client)
        self.library.load_kept_kinds()
        self.state: dict[str, Any] = {}
        self.rerender = mock.Mock()
        self.selected: list[Any] = []
        self.io: list[str] = []
        self.remembered: list[int] = []
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.slow = False
        self.unreadable = False
        self.media: dict[str, list[dict]] = {}
        self.updates: list[dict[str, Any]] = []
        self.overrides: dict[str, Any] = {}

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            self.io.append(getattr(call, "__name__", str(call)))
            return call(*args, **more)

        for patcher in (
                mock.patch.object(run, "io_bound", inline),
                mock.patch.object(vps_home.arrivals, "was",
                                  side_effect=lambda _s, item: "1.0" if item == "t1" else ""),
                mock.patch.object(home.remembered, "get", return_value=None),
                mock.patch.object(home.remembered, "put",
                                  side_effect=lambda _k, days: self.remembered.append(days)),
                mock.patch.object(discovery.deeplink, "sync")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _answer(self, days: int) -> dict[str, Any]:
        if self.unreadable and days != 7:
            raise RuntimeError("no answer")
        holders = {row["vps_id"]: [f"game-{row['vps_id']}"] for row in FOUND["rows"]}
        return vps_home.home(FOUND, KEPT, days, media=self.media, holders=holders,
                             marks=vps_marks.Marks(self.client.discovery_marks()),
                             updates=self.updates, formats=("VPX",))

    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

    async def _select(self, row: Any) -> None:
        self.selected.append(row)
        self.state["entry"] = row["id"]

    async def _draw(self) -> ui.element:
        spied: dict[str, Any] = {}
        real = home.fill

        def spy(*args: Any, **more: Any) -> None:
            spied.update(more)
            real(*args, **{**more, **self.overrides})

        self.spied = spied
        with _PAGE:
            body = ui.column()
            with mock.patch.object(home, "fill", spy):
                await discovery._fill_home(self.library, body, self.state, self._select,
                                           self.rerender)
        await _settle()
        return body

    async def test_every_shelf_is_drawn_from_one_read(self) -> None:
        body = await self._draw()

        words = _words(body)
        self.client.discovery_home.assert_called_once_with(7)
        for word in ("New on VPS", "Alien", "Batman", "Missing from Your Games",
                     "Authors You Collect", "Your Views"):
            self.assertIn(word, words)
        self.assertEqual([one for one in _labels(body) if one.startswith(("All", "Your"))],
                         ["All 4", "Your Games 3"])

    async def test_no_update_draws_no_updates_shelf(self) -> None:
        self.assertNotIn("Updates for Your Games", _words(await self._draw()))

    async def test_updates_are_the_first_shelf_with_a_line_for_what_moved(self) -> None:
        self.updates = [ALIEN_UPDATE]

        words = _words(await self._draw())

        self.assertEqual("Updates for Your Games", words[0])
        self.assertLess(words.index("Updates for Your Games"), words.index("New on VPS"))
        self.assertIn("Table 1.0 to 1.2", words)
        self.assertIn("Backglass 2.0 to 2.1", words)

    def _update_tile(self, body: ui.element) -> ui.element:
        return next(one for one in body.descendants()
                    if "manager-shelf-tile" in one.classes and "Alien" in _words(one))

    @staticmethod
    async def _press(tile: ui.element) -> None:
        for listener in list(tile._event_listeners.values()):
            if listener.type == "click":
                assert listener.handler is not None
                answer = listener.handler(mock.Mock())
                if asyncio.iscoroutine(answer):
                    await answer

    async def test_a_tile_opens_the_entry_its_game_is_matched_to(self) -> None:
        self.updates = [ALIEN_UPDATE]

        await self._press(self._update_tile(await self._draw()))

        self.assertEqual([{"id": "g1"}], self.selected)

    async def test_a_tile_of_a_game_matched_to_no_entry_opens_it_in_the_games_list(self) -> None:
        self.updates = [{**ALIEN_UPDATE, "vps_id": "", "moves": ALIEN_UPDATE["moves"][1:]}]
        opened: list[needs.Opens] = []

        async def open_on(opens: needs.Opens) -> None:
            opened.append(opens)

        self.state["open_on"] = open_on
        await self._press(self._update_tile(await self._draw()))

        [opens] = opened
        self.assertEqual(("games", ("game-g1",)), (opens.view, opens.keys[games.UPDATES_KEY]))
        self.assertEqual([], self.selected)

    def _picture_tile(self, body: ui.element) -> ui.element:
        return next(one for one in body.descendants()
                    if "manager-shelf-tile" in one.classes and "Alien" in _words(one))

    async def test_a_tile_of_replaced_pictures_opens_the_pane_on_the_first_ones_section(
            self) -> None:
        self.updates = [PICTURE_UPDATE]
        body = await self._draw()
        self.assertIn("Wheel and Backglass pictures", _words(body))

        self.state["section"] = "discovery_game"
        await self._press(self._picture_tile(body))

        self.assertEqual([{"id": "g1"}], self.selected)
        self.assertEqual("discovery_wheel", self.state["section"])

    async def test_a_tile_that_also_holds_a_table_update_opens_the_pane_as_it_was(self) -> None:
        self.updates = [{**ALIEN_UPDATE, "moves": [*ALIEN_UPDATE["moves"], _picture("wheel")]}]
        self.state["section"] = "discovery_game"

        await self._press(self._update_tile(await self._draw()))

        self.assertEqual([{"id": "g1"}], self.selected)
        self.assertEqual("discovery_game", self.state["section"])

    async def test_home_hands_the_entry_the_count_it_read(self) -> None:
        self.updates = [ALIEN_UPDATE]
        self.state["update_count"] = mock.Mock()

        await self._draw()

        self.state["update_count"].from_home.assert_called_once_with(1)

    async def test_see_all_on_updates_opens_games_on_every_game_the_shelf_counts(self) -> None:
        self.updates = [ALIEN_UPDATE]
        await self._draw()
        opened: list[needs.Opens] = []

        async def open_on(opens: needs.Opens) -> None:
            opened.append(opens)

        self.state["open_on"] = open_on
        await self.spied["see_updates"](["game-g1"])

        [opens] = opened
        self.assertEqual(("games", ("game-g1",)), (opens.view, opens.keys[games.UPDATES_KEY]))
        shown = games.stamp_updates([{"id": "game-g1"}, {"id": "other"}],
                                    opens.keys[games.UPDATES_KEY])
        self.assertEqual([[games.updates_label()], []],
                         [row[games.UPDATES_COLUMN] for row in shown])

    async def test_an_ignored_game_has_no_update_tile(self) -> None:
        self.updates = [ALIEN_UPDATE]
        self.client.discovery_marks.return_value = [
            {"state": "ignored", "scope": "game", "id": "g1", "detail": "", "since": "x"}]

        self.assertNotIn("Updates for Your Games", _words(await self._draw()))

    async def test_nothing_wanted_draws_no_wanted_shelf(self) -> None:
        self.assertNotIn("Wanted", _words(await self._draw()))

    async def test_what_is_wanted_is_a_shelf_of_its_own(self) -> None:
        self.client.discovery_marks.return_value = [
            {"state": "wanted", "scope": "game", "id": "g2", "detail": "", "since": "x"}]

        words = _words(await self._draw())

        self.assertIn("Wanted", words)
        self.assertEqual(words.count("Batman"), 2)

    async def test_an_ignored_game_is_on_no_shelf(self) -> None:
        self.client.discovery_marks.return_value = [
            {"state": "ignored", "scope": "game", "id": "g1", "detail": "", "since": "x"}]

        words = _words(await self._draw())

        self.assertNotIn("Alien", words)
        self.assertIn("Batman", words)

    async def test_your_views_lists_the_saved_views_of_releases_and_says_where_each_opens(
            self) -> None:
        self.prefs["discovery_releases.views"] = {"views": [
            views.to_record(views.View(id="r1", name="Mine On Releases"))], "active": ""}

        words = _words(await self._draw())

        self.assertIn("Mine On Releases", words)
        self.assertEqual(words.count("Releases"), 2)
        self.assertIn("All Games", words)

    async def test_a_releases_view_chosen_opens_the_releases_tab_on_it(self) -> None:
        self.prefs["discovery_releases.views"] = {"views": [
            views.to_record(views.View(id="r1", name="Mine On Releases"))], "active": ""}
        await self._draw()

        with _PAGE:
            await self.spied["open_releases_view"]("r1")

        scope, value = self.writes[-1]
        self.assertEqual((scope, value["active"]), ("discovery_releases.views", "r1"))
        self.assertEqual(self.state["discovery_tab"], discovery.RELEASES)
        self.rerender.assert_called()

    async def test_an_ignored_thing_is_a_tile_in_your_views_with_its_count(self) -> None:
        self.client.discovery_marks.return_value = [
            {"state": "ignored", "scope": "game", "id": "g1", "detail": "", "since": "x"}]

        words = _words(await self._draw())

        self.assertIn("Ignored", words)
        self.assertIn("1 item", words)

    async def test_the_ignored_tile_says_it_opens_the_marks_grid(self) -> None:
        self.client.discovery_marks.return_value = [
            {"state": "ignored", "scope": "game", "id": "g1", "detail": "", "since": "x"}]

        body = await self._draw()

        tile = next(one for one in body.descendants() if "manager-shelf-tile" in one.classes
                    and "Ignored" in _words(one))
        self.assertEqual(_words(tile)[:2], ["Marks", "Ignored"])

    async def test_with_nothing_ignored_there_is_no_ignored_tile(self) -> None:
        self.assertNotIn("Ignored", _words(await self._draw()))

    async def test_the_marks_grid_is_opened_on_the_view_named(self) -> None:
        await self._draw()

        with _PAGE:
            await self.spied["open_marks"](discovery_marks_grid.WANTED_VIEW)

        scope, value = self.writes[-1]
        self.assertEqual((scope, value["active"]),
                         ("discovery_marks.views", "builtin:manager.discovery.mark.wanted"))
        self.assertEqual(self.state["discovery_tab"], discovery.MARKS)

    async def test_the_wanted_shelf_has_a_see_all(self) -> None:
        self.client.discovery_marks.return_value = [
            {"state": "wanted", "scope": "game", "id": "g1", "detail": "", "since": "x"}]

        body = await self._draw()

        shelf = next(one for one in body.descendants() if "manager-heading-row" in one.classes
                     and "Wanted" in _words(one))
        self.assertEqual(self._see_alls(shelf), 1)

    async def test_a_re_dated_release_says_what_it_was(self) -> None:
        self.assertIn("1.2, was 1.0", _words(await self._draw()))

    async def test_the_saved_views_are_read_before_they_are_listed(self) -> None:
        self.prefs["discovery.views"] = {"views": [
            views.to_record(views.View(id="v1", name="Mine"))], "active": ""}

        body = await self._draw()

        self.assertIn("discovery.views", [c.args[0] for c in self.client.preferences.mock_calls])
        self.assertIn("Mine", _words(body))

    async def test_a_view_chosen_is_written_off_the_loop_with_the_views_kept(self) -> None:
        self.prefs["discovery.views"] = {"views": [
            views.to_record(views.View(id="v1", name="Mine"))], "active": ""}
        await self._draw()

        with _PAGE:
            await self.spied["open_view"]("v1")

        scope, value = self.writes[-1]
        self.assertEqual((scope, value["active"], [one["id"] for one in value["views"]]),
                         ("discovery.views", "v1", ["v1"]))
        self.assertIn("remember", self.io)
        self.assertEqual(self.state["discovery_tab"], discovery.BROWSE)
        self.rerender.assert_called()

    async def test_the_counts_on_your_views_follow_the_catalog(self) -> None:
        self.prefs["discovery.views"] = {"views": [views.to_record(views.View(
            id="v1", name="Space", filters={"themes": {"values": ["Space"]}}))], "active": ""}

        body = await self._draw()

        self.assertIn("1 game", _words(body))

    async def test_search_finds_a_game_once_the_catalog_has_loaded(self) -> None:
        await self._draw()

        people, rows = await self.spied["catalog"]()

        self.assertEqual([one["id"] for one in rows], ["g1", "g2"])
        self.assertEqual([one["name"] for one in people], ["Alpha"])

    async def test_home_draws_no_search_box_of_its_own(self) -> None:
        body = await self._draw()

        self.assertEqual([], [one for one in body.descendants() if isinstance(one, ui.input)])

    async def test_the_catalog_read_feeds_the_box_on_the_tab_row(self) -> None:
        with _PAGE:
            box = discovery_search.SearchBox()
        self.state[discovery_search.KEY] = box
        await self._draw()

        found = box._find("alpha")

        self.assertEqual(["Alpha"], [one["name"] for one in found["author"]])
        self.assertEqual(["Alien"], [one["name"] for one in box._find("alien")["game"]])

    async def test_a_pick_from_the_box_on_home_opens_a_game_or_an_authors_page(self) -> None:
        with _PAGE:
            box = discovery_search.SearchBox()
        self.state[discovery_search.KEY] = box
        await self._draw()

        with _PAGE:
            await box._pick("game", {"id": "g1", "name": "Alien"})
            await box._pick("author", {"name": "Alpha", "count": 4})

        self.assertEqual([{"id": "g1"}], self.selected)
        self.assertEqual({"type": "author", "name": "Alpha"}, self.state["subject"])

    async def test_a_game_opened_from_home_leaves_home_where_it_is(self) -> None:
        """The pane opening sets the entry; a redraw after it, such as Add to Library or a
        comparison ending, is still Home."""
        with _PAGE, mock.patch.object(discovery.busy, "fill") as filled:
            discovery.build(self.library, self.state, self._select, self.rerender)
        self.assertEqual(self.state["discovery_tab"], discovery.HOME)

        await self._select({"id": "g1"})
        with _PAGE:
            await filled.call_args.args[1]()
        await _settle()
        with _PAGE:
            await self.state["redraw_discovery"]()

        self.assertEqual(discovery.tab_of(self.state), discovery.HOME)

    async def _redrawn_on(self, tab: str) -> mock.Mock:
        """The redraw a tab leaves for the pane and its menus to call, run, with the count
        on the entry watched."""
        self.state["update_count"] = counting = mock.Mock(recount=mock.AsyncMock())
        self.state["discovery_tab"] = tab
        with _PAGE, mock.patch.object(discovery.busy, "fill") as filled:
            discovery.build(self.library, self.state, self._select, self.rerender)
        with _PAGE:
            await filled.call_args.args[1]()
        await _settle()
        counting.recount.reset_mock()
        with _PAGE, mock.patch.object(ui.aggrid, "run_grid_method", create=True):
            await self.state["redraw_discovery"]()
        return counting

    async def _drawn_on(self, tab: str, *, typing: str | None = None
                        ) -> tuple[ui.element, discovery_search.SearchBox]:
        """The body a tab draws, and the box on the tab row above it. With `typing`, that
        text is then typed into the box and `self.grid_calls` is what the grid was told."""
        self.state["discovery_tab"] = tab
        with _PAGE, mock.patch.object(discovery.busy, "fill") as filled:
            discovery.build(self.library, self.state, self._select, self.rerender)
        with _PAGE, mock.patch.object(ui.aggrid, "run_grid_method", create=True) as grid:
            await filled.call_args.args[1]()
            await _settle()
            box = self.state[discovery_search.KEY]
            if typing is not None:
                grid.reset_mock()
                box.input.set_value(typing)
            self.grid_calls = grid.call_args_list
        return filled.call_args.args[0], box

    async def test_text_typed_in_the_box_narrows_the_grid_of_each_tab(self) -> None:
        for tab in (discovery.BROWSE, discovery.RELEASES, discovery.MARKS):
            with self.subTest(tab=tab):
                await self._drawn_on(tab, typing="x")

                self.assertIn(mock.call("setGridOption", "quickFilterText", "x"),
                              self.grid_calls)

    async def test_a_tab_read_after_the_page_was_drawn_again_feeds_the_old_box_only(self) -> None:
        with _PAGE:
            old = discovery_search.SearchBox()
            new = discovery_search.SearchBox()
        self.state[discovery_search.KEY] = old
        await self._draw()
        self.state[discovery_search.KEY] = new

        await self.spied["catalog"]()

        self.assertEqual(["Alpha"], [one["name"] for one in old.people])
        self.assertEqual([], new.people)

    async def test_every_tab_feeds_the_box_on_its_row_and_draws_none_in_its_bar(self) -> None:
        for tab in (discovery.BROWSE, discovery.RELEASES, discovery.MARKS):
            with self.subTest(tab=tab):
                body, box = await self._drawn_on(tab)

                self.assertEqual([], [one for one in body.descendants()
                                      if isinstance(one, ui.input)])
                self.assertEqual(["Alpha"], [one["name"] for one in box._find("alpha")["author"]])
                self.assertIsNotNone(box.choose)

    async def test_an_authors_page_feeds_the_box_on_the_tab_row(self) -> None:
        self.state["subject"] = {"type": "author", "name": "Alpha"}

        _body, box = await self._drawn_on(discovery.BROWSE)

        self.assertEqual(["Alien"], [one["name"] for one in box._find("alien")["game"]])

    async def test_a_redraw_of_browse_counts_the_updates_again(self) -> None:
        counting = await self._redrawn_on(discovery.BROWSE)

        counting.recount.assert_awaited_once()

    async def test_a_redraw_of_releases_counts_the_updates_again(self) -> None:
        counting = await self._redrawn_on(discovery.RELEASES)

        counting.recount.assert_awaited_once()

    async def test_a_redraw_of_a_subject_page_counts_the_updates_again(self) -> None:
        self.state["subject"] = {"type": "author", "name": "Alpha"}

        counting = await self._redrawn_on(discovery.BROWSE)

        counting.recount.assert_awaited_once()

    async def test_a_redraw_of_home_counts_from_its_own_read_and_asks_nothing_more(self) -> None:
        self.state["update_count"] = counting = mock.Mock(recount=mock.AsyncMock())
        await self._draw()
        counting.reset_mock()

        with _PAGE:
            await self.state["redraw_discovery"]()

        counting.recount.assert_not_called()
        self.rerender.assert_called()

    async def test_an_address_that_names_a_game_opens_browse(self) -> None:
        self.state["entry"] = "g1"
        with _PAGE, mock.patch.object(discovery.busy, "fill"):
            discovery.build(self.library, self.state, self._select, self.rerender)

        self.assertEqual(self.state["discovery_tab"], discovery.BROWSE)

    async def test_see_all_on_new_opens_releases_with_the_filters_the_shelf_counts(self) -> None:
        await self._draw()

        self.spied["see_new"](7, vps_home.YOURS)

        opening = discovery_ask.opening_on(self.state, discovery.RELEASES)
        assert opening is not None
        self.assertEqual(opening["name"], "New in the Past Week")
        self.assertEqual(opening["filters"], home.new_filters(
            7, vps_home.YOURS, discovery.kept_listings(self.library), counted=True))
        self.assertEqual(discovery.tab_of(self.state), discovery.RELEASES)

    async def test_see_all_on_missing_opens_games_on_every_game_the_tab_counts(self) -> None:
        self.media = {"backglass": [_offered("i9", "zz", "Zed")]}
        await self._draw()
        tab = vps_home.home(FOUND, KEPT, 7, media=self.media, holders={
            row["vps_id"]: [f"game-{row['vps_id']}"] for row in FOUND["rows"]},
            formats=("VPX",))["missing"]["b2sFiles"]
        opened: list[needs.Opens] = []

        async def open_on(opens: needs.Opens) -> None:
            opened.append(opens)

        self.state["open_on"] = open_on
        await self.spied["see_missing"](tab["game_ids"])

        [opens] = opened
        ids = opens.keys[games.LACKING_KEY]
        self.assertEqual(("games", tab["count"]), (opens.view, len(ids)))
        library = [{"id": one} for one in ("i9", "game-g1", "game-g2", "other")]
        shown = [row for row in games.stamp_lacking(library, ids) if row[games.LACKING_COLUMN]]
        self.assertEqual(tab["count"], len(shown))

    async def test_a_gap_a_source_can_fill_is_a_tab_with_its_own_see_all(self) -> None:
        self.media = {"playfield": [_offered("i1", "g1", "Alien")]}
        body = await self._draw()
        self.assertIn("Playfields 1", _labels(body))
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and "Playfields 1" in _options(one))
        self.assertEqual(self._see_alls(body), 2)

        kinds.set_value("playfield")
        await _settle()

        self.assertEqual(self._see_alls(body), 2)
        self.assertIn("Alien", _words(body))
        self.assertIn("1 from a source", _words(body))
        self.assertNotIn("1 on VPS", _words(body))

    async def test_a_kind_is_one_tab_whichever_way_it_is_known(self) -> None:
        self.media = {"wheel": [_offered("i9", "zz", "Zed")]}
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and any(opt.startswith("Wheels") for opt in _options(one)))

        self.assertEqual(sum(1 for opt in _options(kinds) if opt.startswith("Wheels")), 1)
        self.assertNotIn("wheel", kinds.options)

    async def test_the_backglass_file_and_the_backglass_picture_are_told_apart(self) -> None:
        self.media = {"backglass": [_offered("i9", "zz", "Zed")]}
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and {"b2sFiles", "backglass"} <= set(cast(dict[Any, Any], one.options)))

        self.assertEqual(sorted(opt for opt in _options(kinds) if opt.startswith("Backglass")),
                         ["Backglasses 1", "Backglasses 1"])
        self.assertIn("backglass", kinds.options)
        self.assertEqual(kinds._shown, {"b2sFiles": "Backglasses (Asset)",
                                        "backglass": "Backglasses (Media)"})
        self.assertEqual(self._see_alls(body), 2)

    async def test_the_missing_from_list_is_assets_then_media_each_by_name(self) -> None:
        self.media = {"wheel": [_offered("i9", "zz", "Zed")],
                      "backglass": [_offered("i8", "yy", "Why")]}
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and {"b2sFiles", "backglass"} <= set(cast(dict[Any, Any], one.options)))

        shown = [str(one["label"]) for one in kinds._props["options"]]
        headings = [i for i, one in enumerate(kinds._props["options"]) if one.get("section")]
        self.assertEqual(["Assets", "Media"], [shown[i] for i in headings])
        assets, media = shown[headings[0] + 1:headings[1]], shown[headings[1] + 1:]
        self.assertEqual(assets, sorted(assets, key=str.casefold))
        self.assertEqual(media, sorted(media, key=str.casefold))
        self.assertTrue(all(text.startswith(("Backglasses", "ROMs", "Points"))
                            for text in assets))
        self.assertIn("Backglasses", media)

    async def test_the_two_playfield_videos_keep_a_tab_each_under_their_own_name(self) -> None:
        self.media = {"playfield_fss": [_offered("i1", "g1", "Alien")],
                      "playfield_video": [_offered("i2", "g1", "Alien")]}
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and "Full-Screen Playfield 1" in _options(one))

        self.assertIn("Playfield Video 1", _options(kinds))

    async def test_every_game_a_source_can_fill_is_drawn_on_its_tab(self) -> None:
        self.media = {"wheel": [_offered(f"i{n}", f"v{n}", f"Game {n:02d}")
                                for n in range(vps_home.GAPS_SHOWN * 5)]}
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and f"Wheels {len(self.media['wheel'])}" in _options(one))

        kinds.set_value("wheelArtFiles")
        await _settle()

        names = _words(body)
        self.assertTrue(all(f"Game {n:02d}" in names for n in range(len(self.media["wheel"]))))

    def _gap(self, *, take: bool = True) -> dict:
        return {**_offered("i1", "g1", "Alien"), "kind": "wheel", "source": "vpinmediadb",
                "size": "", "picture": "https://example.test/wheel.png", "take": take}

    async def _wheels(self) -> ui.element:
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and "Wheels 1" in _options(one))
        kinds.set_value("wheelArtFiles")
        await _settle()
        return body

    @staticmethod
    def _takes(body: ui.element) -> list[ui.button]:
        return [one for one in body.descendants() if isinstance(one, ui.button)
                and not one.is_deleted and one.props.get("aria-label") == "Take"]

    async def test_a_gap_a_file_can_fill_has_a_take_that_fetches_it(self) -> None:
        self.media = {"wheel": [self._gap()]}
        self.client.fetch_media.return_value = {}
        body = await self._wheels()
        [button] = self._takes(body)

        with mock.patch.object(ui, "notify") as notified:
            await _press(button)

        self.client.fetch_media.assert_called_once_with("i1", "", "wheel", "vpinmediadb",
                                                        "g1", "")
        notified.assert_called_once()
        self.assertEqual(notified.call_args.kwargs["type"], "positive")

    async def test_a_take_redraws_in_place_and_leaves_the_tab_that_was_picked(self) -> None:
        other = {**self._gap(), **_offered("i2", "g2", "Batman")}
        self.media = {"wheel": [self._gap(), other]}
        self.client.fetch_media.side_effect = lambda *_a: self.media.update(wheel=[other]) or {}
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and "Wheels 2" in _options(one))
        kinds.set_value("wheelArtFiles")
        await _settle()
        [first, _second] = self._takes(body)

        with mock.patch.object(ui, "notify"):
            await _press(first)

        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and not one.is_deleted and "Wheels 1" in _options(one))
        self.assertEqual(kinds.value, "wheelArtFiles")
        self.assertEqual(len(self._takes(body)), 1)
        self.rerender.assert_not_called()

    async def test_a_gap_that_cannot_be_taken_as_it_is_has_no_take(self) -> None:
        self.media = {"wheel": [self._gap(take=False)]}

        body = await self._wheels()

        self.assertEqual(self._takes(body), [])
        self.assertIn("Alien", _words(body))

    async def test_the_tile_shows_the_picture_the_source_offers(self) -> None:
        self.media = {"wheel": [self._gap()]}

        body = await self._wheels()

        shown = [str(getattr(one, "content", "")) for one in body.descendants()
                 if isinstance(one, ui.html) and not one.is_deleted]
        self.assertTrue(any("https://example.test/wheel.png" in one for one in shown))

    @staticmethod
    def _see_alls(body: ui.element) -> int:
        return sum(1 for one in body.descendants() if not one.is_deleted
                   and str(getattr(one, "text", "")) == "See All")

    def _picker(self, body: ui.element) -> dropdown.Select:
        return next(one for one in body.descendants() if isinstance(one, dropdown.Select))

    async def test_a_longer_window_is_remembered_once_it_has_answered(self) -> None:
        body = await self._draw()
        picker = self._picker(body)

        picker.set_value(30)
        await _settle()

        self.assertEqual(self.remembered, [30])
        self.assertIn("All 5", _labels(body))
        self.assertIs(picker, self._picker(body))
        self.assertFalse(picker.is_deleted)

    async def test_a_window_that_cannot_be_read_leaves_the_shelf_and_the_window(self) -> None:
        body = await self._draw()
        picker = self._picker(body)
        self.unreadable = True

        with mock.patch.object(ui, "notify") as notified:
            picker.set_value(30)
            await _settle()

        self.assertEqual((self.remembered, picker.value), ([], 7))
        self.assertIn("All 4", _labels(body))
        notified.assert_called_once()

    async def test_an_answer_that_arrives_after_a_later_pick_is_dropped(self) -> None:
        await self._draw()
        gates: dict[int, asyncio.Future[None]] = {14: asyncio.Future(), 30: asyncio.Future()}

        async def reread(days: int) -> dict[str, Any]:
            await gates[days]
            return vps_home.home(FOUND, KEPT, days, formats=("VPX",))

        # Drive the shelf's own picker with answers released out of order.
        with _PAGE:
            body2 = ui.column()
            home.fill(body2, vps_home.home(FOUND, KEPT, 7, formats=("VPX",)), library=self.library,
                      scope="discovery", view_presets={}, catalog=self.spied["catalog"],
                      reread=reread, on_select=self._select, see_new=mock.Mock(),
                      see_missing=mock.Mock(), see_updates=mock.Mock(),
                      see_dont_have=mock.Mock(), see_family=mock.Mock(), see_health=mock.Mock(),
                      see_unmatched=mock.Mock(), match_game=mock.AsyncMock(),
                      see_picture=mock.Mock(), open_view=mock.Mock(),
                      open_releases_view=mock.Mock(), open_marks=mock.Mock(),
                      open_subject=mock.Mock(),
                      marking=discovery_marks.Marking(self.library, mock.AsyncMock()))
        picker = self._picker(body2)
        picker.set_value(14)
        await _settle()
        picker.set_value(30)
        await _settle()
        gates[30].set_result(None)
        await _settle()
        gates[14].set_result(None)
        await _settle()

        self.assertEqual(self.remembered, [30])
        self.assertEqual(picker.value, 30)
        self.assertIn("All 5", _labels(body2))

    async def test_a_chip_narrows_the_tiles_to_its_kind(self) -> None:
        body = await self._draw()
        kinds = next(one for one in body.descendants() if isinstance(one, dropdown.Select)
                      and "All 4" in _options(one))
        self.assertIn("Batman", _words(body))

        kinds.set_value("b2sFiles")
        await _settle()

        self.assertNotIn("Batman", _words(body))
        self.assertIn("Alien", _words(body))

    @staticmethod
    def _heading_of(body: ui.element, word: str) -> ui.element:
        return next(one for one in body.descendants() if "manager-heading-row" in one.classes
                    and word in _words(one))

    @staticmethod
    def _parent(one: ui.element) -> ui.element:
        assert one.parent_slot is not None
        return one.parent_slot.parent

    @staticmethod
    def _selects(within: ui.element) -> list[dropdown.Select]:
        return [one for one in within.descendants() if isinstance(one, dropdown.Select)]

    SHELVES = ["Updates for Your Games", "New on VPS", "Wanted", "Missing from Your Games",
               "Games You Don't Have", "Authors You Collect", "Your Views"]

    def _a_game_the_library_lacks(self) -> None:
        """Batman has a table in the format the shelf counts, so Games You Don't Have draws."""
        release = FOUND["rows"][1]["releases"]["tableFiles"][0]
        release["format"] = "VPX"
        self.addCleanup(release.update, format="FP")

    async def test_a_heading_sits_on_the_edge_and_the_tiles_on_the_indent(self) -> None:
        self._a_game_the_library_lacks()
        self.media = {"playfield": [_offered("i1", "g1", "Alien")]}
        self.updates = [ALIEN_UPDATE]
        self.client.discovery_marks.return_value = [
            {"state": "wanted", "scope": "game", "id": "g1", "detail": "", "since": "x"}]
        body = await self._draw()

        rows = [one for one in body.descendants() if "manager-heading-row" in one.classes]
        shelves = [one for one in body.descendants() if "manager-shelf" in one.classes]

        self.assertEqual([_words(row)[0] for row in rows], self.SHELVES)
        for row in rows:
            self.assertNotIn("px-3", row.classes, _words(row))
        self.assertEqual(len(shelves), len(self.SHELVES))
        for shelf in shelves:
            self.assertIn("px-3", shelf.classes)

    def _end_of(self, heading: ui.element) -> ui.element:
        """The group at a heading row's right end, which a space in the row pushes there."""
        kids = [one for one in heading.default_slot.children if not one.is_deleted]
        self.assertIsInstance(kids[-2], ui.space, _words(heading))
        self.assertIn("pr-3", kids[-1].classes)
        return kids[-1]

    @staticmethod
    def _see_all_link(within: ui.element) -> ui.link:
        return next(one for one in within.descendants() if isinstance(one, ui.link)
                    and not one.is_deleted and one.text == "See All")

    @staticmethod
    def _follow(link: ui.link) -> None:
        for listener in link._event_listeners.values():
            if listener.type == "click" and listener.handler is not None:
                events.handle_event(listener.handler, events.ClickEventArguments(
                    sender=link, client=link.client))

    async def test_the_controls_and_see_all_of_a_shelf_are_pushed_to_the_right_end(self) -> None:
        self._a_game_the_library_lacks()
        self.media = {"playfield": [_offered("i1", "g1", "Alien")]}
        body = await self._draw()

        for word in ("New on VPS", "Missing from Your Games", "Games You Don't Have"):
            with self.subTest(shelf=word):
                heading = self._heading_of(body, word)
                end = self._end_of(heading)

                self.assertIn(self._see_all_link(heading), end.descendants())
        new = self._heading_of(body, "New on VPS")
        for control in self._selects(new):
            self.assertIn(control, self._end_of(new).descendants())

    async def test_see_all_on_new_opens_the_kind_picked_and_follows_a_later_pick(self) -> None:
        self.overrides = {"see_new": mock.Mock()}
        body = await self._draw()
        heading = self._heading_of(body, "New on VPS")
        _window, kinds = self._selects(heading)

        kinds.set_value("b2sFiles")
        await _settle()
        self._follow(self._see_all_link(heading))
        kinds.set_value("tableFiles")
        await _settle()
        self._follow(self._see_all_link(heading))
        kinds.set_value(vps_home.YOURS)
        await _settle()
        self._follow(self._see_all_link(heading))

        self.assertEqual(self.overrides["see_new"].call_args_list,
                         [mock.call(7, "b2sFiles"), mock.call(7, "tableFiles"),
                          mock.call(7, vps_home.YOURS)])

    async def test_see_all_on_missing_opens_the_kind_picked_and_goes_with_a_kind_that_has_none(
            self) -> None:
        self.media = {"playfield": [_offered("i1", "g1", "Alien")],
                      "playfield_video": [_offered("", "g2", "Batman")]}
        self.overrides = {"see_missing": mock.Mock()}
        body = await self._draw()
        tabs = vps_home.home(FOUND, KEPT, 7, media=self.media, holders={
            row["vps_id"]: [f"game-{row['vps_id']}"] for row in FOUND["rows"]},
            formats=("VPX",))["missing"]
        self.assertNotEqual(tabs["b2sFiles"]["game_ids"], tabs["playfield"]["game_ids"])
        self.assertEqual(tabs["playfield_video"]["game_ids"], [])
        heading = self._heading_of(body, "Missing from Your Games")
        [kinds] = self._selects(heading)

        self._follow(self._see_all_link(heading))
        kinds.set_value("playfield")
        await _settle()
        self._follow(self._see_all_link(heading))
        kinds.set_value("playfield_video")
        await _settle()
        without = self._see_alls(heading)
        kinds.set_value("playfield")
        await _settle()

        self.assertEqual(self.overrides["see_missing"].call_args_list,
                         [mock.call(tabs["b2sFiles"]["game_ids"]),
                          mock.call(tabs["playfield"]["game_ids"])])
        self.assertEqual(without, 0)
        self.assertEqual(self._see_alls(heading), 1)

    async def test_new_on_vps_has_its_window_and_kinds_and_see_all_at_the_right_end(self) -> None:
        body = await self._draw()

        heading = self._heading_of(body, "New on VPS")
        window, kinds = self._selects(heading)

        self.assertEqual(_options(window), ["Past Week", "Past 2 Weeks", "Past Month"])
        self.assertEqual(_options(kinds), ["All 4", "Your Games 3", "Tables 2", "Backglasses 1"])
        self.assertEqual(self._see_alls(heading), 1)
        end = self._parent(self._parent(kinds))
        self.assertIn("pr-3", end.classes)
        self.assertIs(self._parent(window), end)

    async def test_missing_from_your_games_has_its_kinds_and_see_all_at_the_right_end(
            self) -> None:
        self.media = {"playfield": [_offered("i1", "g1", "Alien")],
                      "wheel": [_offered("i2", "g2", "Batman")]}
        body = await self._draw()

        heading = self._heading_of(body, "Missing from Your Games")
        [kinds] = self._selects(heading)

        self.assertIn("Playfields 1", _options(kinds))
        self.assertIn("Wheels 1", _options(kinds))
        self.assertEqual(self._see_alls(heading), 1)
        self.assertIn("pr-3", self._parent(kinds).classes)

    async def test_the_kind_picked_on_new_keeps_its_dropdown_where_it_stands(self) -> None:
        body = await self._draw()
        heading = self._heading_of(body, "New on VPS")
        _window, kinds = self._selects(heading)

        kinds.set_value("b2sFiles")
        await _settle()

        self.assertFalse(kinds.is_deleted)
        self.assertIn(kinds, self._selects(self._heading_of(body, "New on VPS")))
        self.assertEqual(self._see_alls(heading), 1)

    async def test_the_kind_picked_on_missing_keeps_its_dropdown_where_it_stands(self) -> None:
        self.media = {"playfield": [_offered("i1", "g1", "Alien")],
                      "wheel": [_offered("i2", "g2", "Batman")]}
        body = await self._draw()
        heading = self._heading_of(body, "Missing from Your Games")
        [kinds] = self._selects(heading)

        kinds.set_value("wheelArtFiles")
        await _settle()

        self.assertFalse(kinds.is_deleted)
        self.assertEqual(self._selects(self._heading_of(body, "Missing from Your Games")), [kinds])
        self.assertEqual(kinds.value, "wheelArtFiles")
        self.assertIn("Batman", _words(body))
        self.assertEqual(self._see_alls(heading), 1)

    async def test_an_author_is_a_name_and_one_line_of_counts(self) -> None:
        body = await self._draw()

        tiles = [one for one in body.descendants() if "manager-shelf-tile--plain" in one.classes
                 and any(word.endswith("in your library") for word in _words(one))]

        self.assertTrue(tiles)
        for card in tiles:
            lines = [child for child in card.default_slot.children if not child.is_deleted]
            self.assertEqual(len(lines), 2, _words(card))
        counts = [one for one in tiles[0].descendants() if isinstance(one, ui.row)]
        self.assertEqual(_words(counts[0])[0], "1 in your library")
        self.assertEqual(_words(counts[0])[1:], ["·", "4 new"])


if __name__ == "__main__":
    unittest.main()

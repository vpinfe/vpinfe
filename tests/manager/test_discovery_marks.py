"""Wanted and Ignored on Discovery: the Status column, the menu entries each state offers,
the notice that says what was set and offers it back, and the pane's status line."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, cast
from unittest import mock

from nicegui import core, run, ui

from common.online import vps_marks
from manager.kit import views
from manager.kit.data import Library
from manager.library import discovery, discovery_marks, discovery_marks_grid, discovery_releases

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()

IGNORED, WANTED = vps_marks.IGNORED, vps_marks.WANTED
G1 = ("game", "g1", "")


def _mark(state: str, scope: str, one: str, detail: str = "") -> dict[str, str]:
    return {"state": state, "scope": scope, "id": one, "detail": detail, "since": "x"}


def _entry(vps_id: str, name: str, **over: Any) -> dict[str, Any]:
    return {"vps_id": vps_id, "name": name, "manufacturer": "", "year": 1990, "type": "SS",
            "themes": [], "url": "", "listed": ["tableFiles", "b2sFiles"],
            "in_library": True, "held": ["tableFiles"], **over}


FOUND = {"counted": "2026-10-01T10:00:00Z",
         "rows": [_entry("g1", "Alien"), _entry("g2", "Batman"), _entry("g3", "Cactus")]}


def _rows(*marked: dict[str, str]) -> list[dict[str, Any]]:
    return discovery.rows(FOUND, None, vps_marks.Marks(marked))


def _labels(entries: list[Any]) -> list[str]:
    return [one.label for one in entries]


def _library() -> Any:
    return mock.Mock(spec=Library)


class RowsTests(unittest.TestCase):
    def test_a_game_wears_the_state_it_holds_and_the_rest_wear_nothing(self) -> None:
        rows = _rows(_mark(IGNORED, "game", "g1"), _mark(WANTED, "game", "g2"))

        self.assertEqual([one["mark"] for one in rows], [IGNORED, WANTED, ""])

    def test_a_mark_on_a_release_or_a_gap_is_not_the_games(self) -> None:
        rows = _rows(_mark(IGNORED, "release", "g1"), _mark(IGNORED, "gap", "g1", "b2sFiles"))

        self.assertEqual({one["mark"] for one in rows}, {""})


class ReleaseRowsTests(unittest.TestCase):
    GAMES = {"g1": {"id": "g1", "name": "Alien", "said": "", "themes": [], "in_library": "yes"},
             "g2": {"id": "g2", "name": "Batman", "said": "", "themes": [],
                    "in_library": "yes"}}

    def _rows(self, found: vps_marks.Marks) -> dict[str, dict[str, Any]]:
        release = {"id": "t1", "by": [], "mod": False, "features": [], "format": "",
                   "version": "1.2", "added": "2026-10-01T00:00:00Z", "held": False}
        listed = {"g1": [("tableFiles", release)], "g2": [("tableFiles", {**release, "id": "t2"})]}
        return {one["id"]: one for one in discovery_releases.rows(
            listed, self.GAMES, [], counted=True, marks=found)}

    def test_a_release_carries_its_own_id_apart_from_its_row(self) -> None:
        self.assertEqual(self._rows(vps_marks.Marks())["g1:t1"]["release_id"], "t1")

    def test_a_release_of_an_ignored_game_wears_ignored_without_being_ignored(self) -> None:
        row = self._rows(vps_marks.Marks([_mark(IGNORED, "game", "g1")]))["g1:t1"]

        self.assertEqual((row["mark"], row[discovery_marks.OWN]), (IGNORED, ""))

    def test_a_wanted_release_wears_wanted_and_its_neighbour_wears_nothing(self) -> None:
        rows = self._rows(vps_marks.Marks([_mark(WANTED, "release", "t1")]))

        self.assertEqual((rows["g1:t1"]["mark"], rows["g2:t2"]["mark"]), (WANTED, ""))

    def test_an_ignored_release_is_ignored_in_its_own_right(self) -> None:
        row = self._rows(vps_marks.Marks([_mark(IGNORED, "release", "t1")]))["g1:t1"]

        self.assertEqual((row["mark"], row[discovery_marks.OWN]), (IGNORED, IGNORED))


class ColumnTests(unittest.TestCase):
    def test_it_is_a_status_column_filtered_by_ignored_or_wanted(self) -> None:
        column = discovery_marks.column()

        self.assertEqual((column["field"], column["headerName"]), ("mark", "Status"))
        self.assertEqual([one["value"] for one in column["filterParams"]["choices"]],
                         [WANTED, IGNORED])

    def test_both_grids_carry_it_and_the_everything_views_show_it(self) -> None:
        self.assertIn("mark", [one["field"] for one in discovery.COLUMNS])
        for built in (discovery.VIEWS, discovery_releases.VIEWS):
            everything = built["manager.view.everything"]
            assert isinstance(everything, views.Preset)
            self.assertIn("mark", everything.columns)


class GameVerbTests(unittest.TestCase):
    def _labels(self, *marked: dict[str, str], wanting: bool = True) -> list[str]:
        return _labels(discovery_marks.game_verbs(
            _library(), _rows(*marked), mock.AsyncMock(), wanting=wanting))

    def test_an_unmarked_game_can_be_wanted_or_ignored(self) -> None:
        self.assertEqual(self._labels()[:2], ["Add to Wanted", "Ignore Game"])

    def test_an_ignored_game_offers_stop_ignoring_in_place_of_ignore(self) -> None:
        rows = [one for one in _rows(_mark(IGNORED, "game", "g1")) if one["id"] == "g1"]

        offered = _labels(discovery_marks.game_verbs(_library(), rows, mock.AsyncMock()))

        self.assertEqual(offered, ["Add to Wanted", "Stop Ignoring"])

    def test_a_wanted_game_offers_remove_from_wanted_in_place_of_add(self) -> None:
        rows = [one for one in _rows(_mark(WANTED, "game", "g2")) if one["id"] == "g2"]

        offered = _labels(discovery_marks.game_verbs(_library(), rows, mock.AsyncMock()))

        self.assertEqual(offered, ["Remove from Wanted", "Ignore Game"])

    def test_a_selection_of_both_offers_each_entry_once(self) -> None:
        offered = self._labels(_mark(IGNORED, "game", "g1"), _mark(WANTED, "game", "g2"))

        self.assertEqual(sorted(offered), sorted(set(offered)))
        self.assertEqual(set(offered), {"Add to Wanted", "Remove from Wanted", "Ignore Game",
                                        "Stop Ignoring"})

    def test_the_releases_tab_leaves_wanting_to_the_releases(self) -> None:
        self.assertEqual(self._labels(wanting=False), ["Ignore Game"])


RELEASE = {"id": "g1:t1", "release_id": "t1", "game_id": "g1", "name": "Alien",
           "kind": "b2sFiles", "version": "1.2", "mark": "", discovery_marks.OWN: ""}


def _kind_word(listing: str) -> str:
    return {"b2sFiles": "Backglass"}[listing]


class ReleaseVerbTests(unittest.TestCase):
    def _verbs(self, rows: list[dict[str, Any]], *, lacks: bool = True,
               gap_ignored: bool = False) -> list[str]:
        library = mock.Mock(spec=Library)
        library.marks.return_value = [_mark(IGNORED, "gap", "g1", "b2sFiles")] \
            if gap_ignored else []
        return _labels(discovery_marks.release_verbs(
            library, rows, mock.AsyncMock(), kind_word=_kind_word, lacks=lambda _one: lacks))

    def test_a_release_of_a_kind_the_game_lacks_offers_ignoring_that_kind(self) -> None:
        self.assertEqual(self._verbs([RELEASE]),
                         ["Add to Wanted", "Ignore Release", "Ignore Missing Backglass"])

    def test_a_release_the_library_holds_is_not_offered_for_wanting(self) -> None:
        held = {**RELEASE, "held": "have"}

        self.assertEqual(self._verbs([held]),
                         ["Ignore Release", "Ignore Missing Backglass"])
        self.assertEqual(self._verbs([held, {**RELEASE, "release_id": "t2"}])[0],
                         "Add to Wanted")

    def test_a_kind_already_ignored_offers_stopping_it(self) -> None:
        self.assertEqual(self._verbs([RELEASE], gap_ignored=True)[-1],
                         "Stop Ignoring Missing Backglass")

    def test_a_game_that_has_the_kind_offers_no_gap_entry(self) -> None:
        self.assertEqual(self._verbs([RELEASE], lacks=False),
                         ["Add to Wanted", "Ignore Release"])

    def test_several_releases_offer_no_gap_entry(self) -> None:
        self.assertNotIn("Ignore Missing Backglass",
                         self._verbs([RELEASE, {**RELEASE, "release_id": "t2"}]))

    def test_a_release_is_called_by_its_kind_version_and_game(self) -> None:
        one = discovery_marks.release_subjects([RELEASE], _kind_word)[0]

        self.assertEqual(one.name, "Backglass 1.2 of Alien")
        self.assertEqual(discovery_marks.release_name("Alien", "Backglass", ""),
                         "Backglass of Alien")


def _item(scope: str, **over: Any) -> dict[str, Any]:
    return {"scope": scope, "id": "x", "detail": "", "state": IGNORED,
            "since": "2026-10-18T00:00:00Z", "vps_id": "g1", "game": "Alien", "img_url": "",
            "manufacturer": "Gottlieb",
            "year": 1980, "in_library": True, "listing": "", "media": "",
            "version": "", "authors": [], "held": False, **over}


WORDS = {"b2sFiles": "Backglass", "romFiles": "ROM", "backglass": "Backglass"}


class MarksGridRowTests(unittest.TestCase):
    def _row(self, item: dict[str, Any]) -> dict[str, Any]:
        return discovery_marks_grid.rows([item], WORDS)[0]

    def test_a_row_is_keyed_as_the_store_keys_the_mark(self) -> None:
        row = self._row(_item("gap", id="g1", detail="romFiles", listing="romFiles"))

        self.assertEqual((row["id"], row["mark_scope"], row["mark_id"], row["mark_detail"]),
                         ("gap|g1|romFiles", "gap", "g1", "romFiles"))

    def test_the_kind_column_holds_the_scope_token_and_the_status_the_state(self) -> None:
        row = self._row(_item("version", detail="1.2", version="1.2"))

        self.assertEqual((row["kind"], row[discovery_marks.FIELD]), ("version", IGNORED))

    def test_the_detail_says_which_release_kind_or_version(self) -> None:
        self.assertEqual(self._row(_item("game"))["detail"], "")
        self.assertEqual(self._row(_item("gap", detail="romFiles", listing="romFiles"))["detail"],
                         "ROM")
        self.assertEqual(self._row(_item("release", listing="b2sFiles", version="1.2",
                                         authors=["A", "B", "C"]))["detail"],
                         "Backglass 1.2 \u00b7 A, B")
        self.assertEqual(self._row(_item("version", version="2.0"))["detail"], "2.0")
        self.assertEqual(self._row(_item("updates", media="backglass"))["detail"], "Backglass")

    def test_a_row_is_called_as_a_notice_names_it(self) -> None:
        self.assertEqual(self._row(_item("game"))["called"], "Alien")
        self.assertEqual(self._row(_item("release", listing="b2sFiles", version="1.2"))["called"],
                         "Backglass 1.2 of Alien")

    def test_a_row_says_who_made_the_game_and_when(self) -> None:
        self.assertEqual(self._row(_item("game"))["said"], "Gottlieb 1980")

    def test_subjects_carry_the_mark_keys_and_the_state(self) -> None:
        row = self._row(_item("gap", id="g1", detail="romFiles", listing="romFiles"))

        one = discovery_marks_grid.subjects([row])[0]

        self.assertEqual((one.scope, one.id, one.detail, one.state),
                         ("gap", "g1", "romFiles", IGNORED))


class MarksGridViewTests(unittest.TestCase):
    def test_the_ignored_and_wanted_views_filter_on_the_status_column(self) -> None:
        for view, state in ((discovery_marks_grid.IGNORED_VIEW, IGNORED),
                            (discovery_marks_grid.WANTED_VIEW, WANTED)):
            with self.subTest(view):
                preset = discovery_marks_grid.VIEWS[view]
                assert isinstance(preset, views.Preset)
                self.assertEqual(preset.filters, {discovery_marks.FIELD: {"values": [state]}})

    def test_every_view_lists_the_newest_mark_first(self) -> None:
        for preset in discovery_marks_grid.VIEWS.values():
            assert isinstance(preset, views.Preset)
            self.assertEqual(preset.sort[0]["colId"], "since")

    def test_the_kind_filter_offers_every_scope(self) -> None:
        column = next(one for one in discovery_marks_grid.columns() if one["field"] == "kind")

        for word in ("Game", "Release", "Missing", "Skipped Version", "Updates"):
            self.assertIn(word, json.dumps(column))


class MixedSubjectsTests(unittest.TestCase):
    def test_a_selection_of_several_kinds_is_counted_as_items(self) -> None:
        mixed = [discovery_marks.Subject("game", "g1", "Alien"),
                 discovery_marks.Subject("gap", "g1", "ROM of Alien", detail="romFiles")]

        self.assertEqual(discovery_marks._called(mixed), "2 items")

    def test_stopping_is_offered_for_anything_ignored_and_wanting_for_what_can_be(self) -> None:
        subjects = [discovery_marks.Subject("gap", "g1", "ROM of Alien", IGNORED, "romFiles"),
                    discovery_marks.Subject("game", "g2", "Batman", IGNORED)]

        offered = _labels(discovery_marks.listed_verbs(_library(), subjects, mock.AsyncMock()))

        self.assertEqual(offered, ["Stop Ignoring", "Add to Wanted"])

    def test_a_held_release_row_of_the_marks_grid_is_not_offered_for_wanting(self) -> None:
        row = discovery_marks_grid.rows(
            [_item("release", listing="b2sFiles", version="1.2", held=True)], WORDS)[0]
        subjects = discovery_marks_grid.subjects([row])

        self.assertEqual(_labels(discovery_marks.listed_verbs(
            _library(), subjects, mock.AsyncMock())), ["Stop Ignoring"])

    def test_a_held_release_that_is_wanted_can_still_be_taken_off(self) -> None:
        subjects = [discovery_marks.Subject("release", "t1", "Backglass of Alien", WANTED,
                                            held=True)]

        offered = _labels(discovery_marks.listed_verbs(_library(), subjects, mock.AsyncMock()))

        self.assertEqual(offered, ["Remove from Wanted"])

    def test_a_wanted_release_offers_taking_it_off(self) -> None:
        subjects = [discovery_marks.Subject("release", "t1", "Backglass of Alien", WANTED)]

        offered = _labels(discovery_marks.listed_verbs(_library(), subjects, mock.AsyncMock()))

        self.assertEqual(offered, ["Remove from Wanted"])

    def test_a_skipped_version_offers_only_stopping(self) -> None:
        subjects = [discovery_marks.Subject("version", "t1", "Alien 1.2", IGNORED, "1.2")]

        offered = _labels(discovery_marks.listed_verbs(_library(), subjects, mock.AsyncMock()))

        self.assertEqual(offered, ["Stop Ignoring"])


class MarksGridIntroTests(unittest.IsolatedAsyncioTestCase):
    """The line a grid with nothing marked says, drawn by `discovery_marks_grid.fill`."""

    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)
        self.marks: list[dict[str, str]] = []
        self.client = mock.Mock()
        self.client.library_policy.return_value = {"hidden_asset_kinds": [],
                                                   "hidden_media_kinds": []}
        self.client.catalog_discovery.return_value = FOUND
        self.client.discovery_marks.side_effect = lambda: self.marks
        self.client.preferences.return_value = {}
        self.library = Library(self.client)
        self.library.load_kept_kinds()

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        for patcher in (mock.patch.object(run, "io_bound", inline),
                        mock.patch.object(discovery_marks.offload, "io", inline)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _draw(self) -> tuple[ui.element, dict[str, Any]]:
        state: dict[str, Any] = {}
        with _PAGE:
            body = ui.column()

        async def found() -> Any:
            return FOUND

        await discovery_marks.refresh(self.library)
        await discovery_marks_grid.fill(
            body, library=self.library, state=state, feed=mock.Mock(), found=found, words={},
            games=[], people=[], on_select=mock.AsyncMock(), open_page=mock.AsyncMock())
        return body, state

    @staticmethod
    def _intro_shown(body: ui.element) -> bool:
        line = discovery_marks_grid.t("manager.discovery.marks.empty")

        def seen(one: ui.element) -> bool:
            chain = [one]
            while chain[-1] is not body:
                chain.append(cast(Any, chain[-1].parent_slot).parent)
            return all(each.visible for each in chain)

        return any(seen(one) for one in body.descendants()
                   if isinstance(one, ui.label) and str(one.text) == line)

    async def test_nothing_marked_says_so(self) -> None:
        body, _state = await self._draw()

        self.assertTrue(self._intro_shown(body))

    async def test_a_mark_made_elsewhere_takes_the_line_away(self) -> None:
        body, state = await self._draw()
        self.marks.append(_mark(IGNORED, "game", "g1"))

        with _PAGE:
            await state["redraw_discovery"]()

        self.assertFalse(self._intro_shown(body))

    async def test_taking_back_the_last_mark_brings_the_line_back(self) -> None:
        self.marks.append(_mark(IGNORED, "game", "g1"))
        body, state = await self._draw()
        self.assertFalse(self._intro_shown(body))
        self.marks.clear()

        with _PAGE:
            await state["redraw_discovery"]()

        self.assertTrue(self._intro_shown(body))


class LacksKindTests(unittest.TestCase):
    """Ignore Missing {Kind} is offered only where Home has a tab for the kind and the game
    is in the library."""

    FOUND = {"counted": "2026-10-01T10:00:00Z", "rows": [
        _entry("in", "Alien", listed=["tableFiles", "b2sFiles", "romFiles", "pupPackFiles"]),
        _entry("out", "Batman", in_library=False,
               listed=["tableFiles", "b2sFiles", "romFiles", "pupPackFiles"], held=[])]}

    def _lacks(self, game: str, kind: str) -> bool:
        games = discovery.rows(self.FOUND, None, vps_marks.Marks())
        return discovery.lacks_kind(games, {"game_id": game, "kind": kind})

    def test_a_kind_home_has_a_tab_for_that_a_library_game_lacks(self) -> None:
        for kind in ("b2sFiles", "romFiles"):
            with self.subTest(kind):
                self.assertTrue(self._lacks("in", kind))

    def test_a_game_outside_the_library_is_not_missing_anything(self) -> None:
        self.assertFalse(self._lacks("out", "b2sFiles"))

    def test_a_kind_without_a_tab_on_home_offers_nothing(self) -> None:
        for kind in ("tableFiles", "pupPackFiles"):
            with self.subTest(kind):
                self.assertFalse(self._lacks("in", kind))

    def test_a_kind_the_game_holds_offers_nothing(self) -> None:
        self.assertFalse(self._lacks("in", "tableFiles"))


class StopIgnoringTests(unittest.TestCase):
    def _labels(self, release: str, game: str, shelf: str = discovery_marks.NEW) -> list[str]:
        library = mock.Mock(spec=Library)
        library.marks.return_value = [_mark(IGNORED, scope, one) for scope, one, state
                                      in (("release", "t1", release), ("game", "g1", game))
                                      if state]
        item = {"id": "t1", "vps_id": "g1", "game": "Alien", "version": "1.2", "scope": "release"}
        return _labels(discovery_marks.Marking(library, mock.AsyncMock()).verbs(
            shelf, item, "b2sFiles", "Backglass"))

    def test_a_menu_that_can_stop_both_names_which_is_which(self) -> None:
        for shelf in (discovery_marks.NEW, discovery_marks.WANT):
            with self.subTest(shelf):
                self.assertEqual(self._labels("x", "x", shelf),
                                 ["Add to Wanted", "Stop Ignoring Release", "Stop Ignoring Game"])

    def test_a_menu_that_can_stop_one_keeps_the_plain_word(self) -> None:
        self.assertEqual(self._labels("x", ""),
                         ["Add to Wanted", "Stop Ignoring", "Ignore Game"])
        self.assertEqual(self._labels("", "x"),
                         ["Add to Wanted", "Ignore Release", "Stop Ignoring"])

    def test_the_releases_tab_names_them_where_its_two_lists_meet(self) -> None:
        library = mock.Mock(spec=Library)
        library.marks.return_value = []
        both = [{**RELEASE, "mark": IGNORED, discovery_marks.OWN: IGNORED}]
        games = [{"id": "g1", "name": "Alien", "mark": IGNORED}]

        made = discovery_marks.distinguish([
            *discovery_marks.release_verbs(library, both, mock.AsyncMock(),
                                           kind_word=_kind_word, lacks=lambda _one: False),
            *discovery_marks.game_verbs(library, games, mock.AsyncMock(), wanting=False)])

        self.assertEqual(_labels(made), ["Add to Wanted", "Stop Ignoring Release",
                                         "Stop Ignoring Game"])


class PaneReleaseTests(unittest.TestCase):
    def _menus(self, record: dict[str, Any]) -> tuple[int, int]:
        library = mock.Mock(spec=Library)
        library.marks.return_value = []
        with _PAGE:
            row = ui.element("div")
            discovery_marks.release_marks({"library": library, "state": {}}, row, "Alien",
                                          "Backglass", record)
            menus = sum(isinstance(one, ui.context_menu) for one in row.descendants())
        return menus, library.marks.call_count

    def test_a_release_with_a_file_id_gets_a_menu(self) -> None:
        self.assertEqual(self._menus({"vps_file_id": "f1", "version": "1.2"})[0], 1)

    def test_a_release_with_no_file_id_gets_none_and_nothing_is_read(self) -> None:
        for record in ({"version": "1.2"}, {"vps_file_id": ""}):
            with self.subTest(record):
                self.assertEqual(self._menus(record), (0, 0))


class HomeMenuTests(unittest.TestCase):
    def _verbs(self, shelf: str, item: dict[str, Any], *marked: dict[str, str],
               listing: str = "b2sFiles") -> list[str]:
        library = mock.Mock(spec=Library)
        library.marks.return_value = list(marked)
        return _labels(discovery_marks.Marking(library, mock.AsyncMock()).verbs(
            shelf, item, listing, "Backglass"))

    NEW = {"id": "t1", "vps_id": "g1", "game": "Alien", "version": "1.2"}
    LACKING = {"vps_id": "g1", "name": "Alien"}

    def test_a_new_release_can_be_wanted_or_ignored_and_so_can_its_game(self) -> None:
        self.assertEqual(self._verbs(discovery_marks.NEW, self.NEW),
                         ["Add to Wanted", "Ignore Release", "Ignore Game"])

    def test_a_new_release_the_library_holds_cannot_be_wanted(self) -> None:
        held = {**self.NEW, "held": True}

        self.assertEqual(self._verbs(discovery_marks.NEW, held),
                         ["Ignore Release", "Ignore Game"])

    def test_a_game_lacking_a_kind_can_stop_hearing_of_that_kind_or_be_ignored(self) -> None:
        self.assertEqual(self._verbs(discovery_marks.MISSING, self.LACKING),
                         ["Ignore Missing Backglass", "Ignore Game"])

    def test_a_wanted_release_can_be_taken_off_or_ignored(self) -> None:
        item = {**self.NEW, "scope": "release"}

        self.assertEqual(self._verbs(discovery_marks.WANT, item, _mark(WANTED, "release", "t1")),
                         ["Remove from Wanted", "Ignore Release", "Ignore Game"])

    def test_a_wanted_game_can_be_taken_off_or_ignored(self) -> None:
        item = {"scope": "game", "id": "g1", "vps_id": "g1", "game": "Alien"}

        self.assertEqual(self._verbs(discovery_marks.WANT, item, _mark(WANTED, "game", "g1")),
                         ["Remove from Wanted", "Ignore Game"])

    def test_a_release_is_wanted_by_its_own_mark(self) -> None:
        library = mock.Mock(spec=Library)
        library.marks.return_value = [_mark(WANTED, "release", "t1")]
        marking = discovery_marks.Marking(library, mock.AsyncMock())

        self.assertTrue(marking.wanted_release("t1"))
        self.assertFalse(marking.wanted_release("t2"))


class UpdateMenuTests(unittest.TestCase):
    MOVE = {"source": "vps", "kind": "tableFiles", "release_id": "t1", "vps_id": "g1",
            "file": "Alien.vpx", "then": "1.0", "now": "1.2"}
    UNTIED = {"source": "vps", "kind": "b2sFiles", "release_id": "", "vps_id": "",
              "file": "Alien", "then": "", "now": ""}

    def _verbs(self, moves: list[dict[str, Any]], *marked: dict[str, str]) -> list[str]:
        library = mock.Mock(spec=Library)
        library.marks.return_value = list(marked)
        item = {"game_id": "x", "vps_id": "g1", "game": "Alien", "moves": moves}
        return _labels(discovery_marks.Marking(library, mock.AsyncMock()).update_verbs(
            item, lambda _listing: "Table"))

    def test_a_moved_release_can_be_skipped_for_this_version_or_ignored_for_good(self) -> None:
        self.assertEqual(self._verbs([self.MOVE]),
                         ["Skip This Version", "Ignore Updates to This Table"])

    def test_a_tile_with_no_release_or_picture_has_nothing_to_skip(self) -> None:
        self.assertEqual(self._verbs([self.UNTIED]), [])

    PICTURE = {"source": "vpinmediadb", "kind": "wheel", "release_id": "", "vps_id": "g1",
               "file": "Alien", "then": "", "now": "", "md5": "abc"}

    def test_a_replaced_picture_can_be_skipped_or_ignored_like_a_release(self) -> None:
        self.assertEqual(self._verbs([self.PICTURE]),
                         ["Skip This Version", "Ignore Updates to This Table"])

    def test_a_picture_is_marked_under_the_id_and_hash_the_pane_writes(self) -> None:
        library = mock.Mock(spec=Library)
        library.marks.return_value = []
        item = {"game_id": "x", "vps_id": "g1", "game": "Alien",
                "moves": [self.MOVE, self.PICTURE]}

        with mock.patch.object(discovery_marks, "apply") as applied:
            skip, ignore = discovery_marks.Marking(library, mock.AsyncMock()).update_verbs(
                item, lambda _listing: "Table")
            assert skip.run is not None and ignore.run is not None
            skip.run()
            ignore.run()

        skipped = [(one.scope, one.id, one.detail) for one in applied.call_args_list[0].args[2]]
        ignored = [(one.scope, one.id, one.detail) for one in applied.call_args_list[1].args[2]]
        self.assertEqual([("version", "t1", "1.2"), ("version", "g1:wheel", "abc")], skipped)
        self.assertEqual([("updates", "t1", ""), ("updates", "g1:wheel", "")], ignored)

    def test_a_picture_already_skipped_is_not_offered_to_skip_again(self) -> None:
        self.assertEqual(
            self._verbs([self.PICTURE], _mark(IGNORED, "version", "g1:wheel", "abc")),
            ["Ignore Updates to This Table"])

    def test_a_version_already_skipped_is_not_offered_again(self) -> None:
        self.assertEqual(
            self._verbs([self.MOVE], _mark(IGNORED, "version", "t1", "1.2")),
            ["Ignore Updates to This Table"])


class ApplyTests(unittest.IsolatedAsyncioTestCase):
    """A mark set through the Library, and the notice that offers it back."""

    def setUp(self) -> None:
        self.held: dict[tuple[str, str, str], str] = {}
        self.client = mock.Mock()

        def put(state: str, scope: str, one: str, detail: str = "") -> dict[str, Any]:
            before = self.held.get((scope, one, detail), "")
            self.held[(scope, one, detail)] = state
            return {"replaced": before, "marks": []}

        def delete(scope: str, one: str, detail: str = "") -> dict[str, Any]:
            return {"replaced": self.held.pop((scope, one, detail), ""), "marks": []}

        self.client.put_discovery_mark.side_effect = put
        self.client.delete_discovery_mark.side_effect = delete
        self.library = Library(self.client)
        self.offers: list[tuple[Any, ...]] = []
        self.notices: list[dict[str, Any]] = []

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        for patcher in (
                mock.patch.object(discovery_marks.offload, "io", inline),
                mock.patch.object(discovery_marks.undo, "offer",
                                  lambda *args, **_more: self.offers.append(args)),
                mock.patch.object(discovery_marks.ui, "notify",
                                  lambda *args, **more: self.notices.append(
                                      {"text": args[0], **more}))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.after = mock.AsyncMock()

    def _alien(self, state: str = "") -> list[discovery_marks.Subject]:
        return [discovery_marks.Subject("game", "g1", "Alien", state)]

    async def test_ignoring_says_what_was_ignored_and_puts_it_right_after(self) -> None:
        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, self._alien(), self.after)

        self.assertEqual(self.offers[0][0], "Ignored Alien")
        self.after.assert_awaited_once()
        self.assertEqual(self.held, {G1: IGNORED})

    async def test_skipping_a_version_says_skipped_and_marks_that_version_of_the_release(
            self) -> None:
        one = [discovery_marks.Subject("version", "t1", "Table 1.2 of Alien", "", "1.2")]

        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, one, self.after,
                                        name="Table 1.2 of Alien", skipped=True)

        self.assertEqual(self.offers[0][0], "Skipped Table 1.2 of Alien")
        self.assertEqual(self.held, {("version", "t1", "1.2"): IGNORED})

    async def test_ignoring_a_wanted_game_says_it_left_wanted_and_undo_wants_it_again(self) -> None:
        self.held[G1] = WANTED

        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, self._alien(WANTED), self.after)
            self.assertEqual(self.offers[0][0], "Ignored Alien, taken off Wanted")
            await self.offers[0][1]()

        self.assertEqual(self.held[G1], WANTED)
        self.assertEqual(self.after.await_count, 2)

    async def test_undoing_an_ignore_of_an_unmarked_game_leaves_it_unmarked(self) -> None:
        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, self._alien(), self.after)
            await self.offers[0][1]()

        self.assertEqual(self.held, {})

    async def test_wanting_an_ignored_game_says_it_is_no_longer_ignored(self) -> None:
        self.held[G1] = IGNORED

        with _PAGE:
            await discovery_marks.apply(self.library, WANTED, self._alien(IGNORED), self.after)

        self.assertEqual(self.offers[0][0], "Added Alien to Wanted, no longer ignored")

    async def test_stopping_an_ignore_says_so_and_undo_ignores_again(self) -> None:
        self.held[G1] = IGNORED

        with _PAGE:
            await discovery_marks.apply(self.library, "", self._alien(IGNORED), self.after)
            self.assertEqual(self.offers[0][0], "Stopped ignoring Alien")
            await self.offers[0][1]()

        self.assertEqual(self.held[G1], IGNORED)

    async def test_taking_a_game_off_wanted_says_so(self) -> None:
        self.held[G1] = WANTED

        with _PAGE:
            await discovery_marks.apply(self.library, "", self._alien(WANTED), self.after)

        self.assertEqual(self.offers[0][0], "Removed Alien from Wanted")

    async def test_several_are_called_by_how_many_they_are(self) -> None:
        many = [discovery_marks.Subject("game", one, one) for one in ("g1", "g2", "g3")]

        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, many, self.after)

        self.assertEqual(self.offers[0][0], "Ignored 3 games")

    async def test_a_refusal_says_why_and_offers_nothing_to_undo(self) -> None:
        self.client.put_discovery_mark.side_effect = RuntimeError("no room")

        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, self._alien(), self.after)

        self.assertEqual(self.offers, [])
        self.assertEqual(self.notices[0]["type"], "negative")
        self.after.assert_not_awaited()

    async def test_a_change_that_fails_part_way_keeps_what_it_did_and_offers_it_back(self) -> None:
        calls = []

        def put(state: str, scope: str, one: str, detail: str = "") -> dict[str, Any]:
            calls.append(one)
            if one == "g2":
                raise RuntimeError("no room")
            self.held[(scope, one, detail)] = state
            return {"replaced": "", "marks": []}

        self.client.put_discovery_mark.side_effect = put
        many = [discovery_marks.Subject("game", one, one) for one in ("g1", "g2", "g3")]

        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, many, self.after)

            self.assertEqual(calls, ["g1", "g2"])
            self.assertEqual(self.offers[0][0], "Ignored g1")
            self.assertEqual(self.notices[0]["type"], "negative")
            await self.offers[0][1]()

        self.assertEqual([call.args for call in self.client.delete_discovery_mark.call_args_list],
                         [("game", "g1", "")], "only what was done is put back")
        self.assertEqual(self.held, {})

    async def test_an_undo_that_cannot_put_one_back_puts_the_rest_back_and_says_so(self) -> None:
        many = [discovery_marks.Subject("game", one, one) for one in ("g1", "g2", "g3")]
        with _PAGE:
            await discovery_marks.apply(self.library, IGNORED, many, self.after)
        reached = self.client.delete_discovery_mark.side_effect

        def delete(scope: str, one: str, detail: str = "") -> dict[str, Any]:
            if one == "g2":
                raise RuntimeError("no room")
            return reached(scope, one, detail)

        self.client.delete_discovery_mark.side_effect = delete
        self.after.reset_mock()

        with _PAGE, self.assertRaisesRegex(RuntimeError, "no room"):
            await self.offers[0][1]()

        self.assertEqual(self.held, {("game", "g2", ""): IGNORED})
        self.after.assert_awaited_once()


class PaneStatusTests(unittest.TestCase):
    def _words(self, row: dict[str, Any]) -> tuple[bool, list[str]]:
        with _PAGE:
            holder = ui.column()
            with holder:
                drawn = discovery_marks.ignored_status(_library(), row, mock.AsyncMock())
            words = [str(one.text) for one in holder.descendants()
                     if isinstance(one, ui.label)]
            words += [str(one.text) for one in holder.descendants()
                      if isinstance(one, ui.button)]
        return drawn, words

    def test_an_ignored_entry_says_ignored_with_the_way_to_stop(self) -> None:
        drawn, words = self._words({"id": "g1", "name": "Alien", "mark": IGNORED})

        self.assertTrue(drawn)
        self.assertIn("Ignored", words)
        self.assertIn("Stop Ignoring", words)

    def test_an_entry_that_is_not_ignored_draws_nothing_and_leaves_add_to_library(self) -> None:
        for mark in ("", WANTED):
            with self.subTest(mark):
                drawn, words = self._words({"id": "g1", "name": "Alien", "mark": mark})

                self.assertEqual((drawn, words), (False, []))


class LibraryMarksTests(unittest.TestCase):
    def test_a_change_holds_the_marks_the_core_answers_and_says_what_it_replaced(self) -> None:
        client = mock.Mock()
        client.put_discovery_mark.return_value = {
            "replaced": "wanted", "marks": [_mark(IGNORED, "game", "g1")]}
        library = Library(client)

        replaced = library.mark_discovery(IGNORED, "game", "g1")

        self.assertEqual(replaced, "wanted")
        self.assertEqual(library.marks(), [_mark(IGNORED, "game", "g1")])

    def test_nothing_is_marked_before_the_first_read(self) -> None:
        library = Library(mock.Mock())

        self.assertEqual(library.marks(), [])


if __name__ == "__main__":
    unittest.main()

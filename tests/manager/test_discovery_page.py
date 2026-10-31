"""The Discovery page: what a catalog entry becomes as a row, and what can be done to one."""

from __future__ import annotations

import time
import unittest
from typing import Any
from unittest import mock

from nicegui import ui

from common.online import vps_kinds
from common.runtime.jobs import KIND_VPS_ROLLUP
from manager import page
from manager.kit import deeplink, grid
from manager.kit.data import Library
from manager.library import (
    discovery,
    discovery_ask,
    discovery_get,
    discovery_list,
    import_dialog,
    vps_records,
)

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _entry(vps_id: str = "v1", **over) -> dict:
    return {"vps_id": vps_id, "name": "Attack from Mars", "manufacturer": "Bally",
            "year": 1995, "type": "SS", "themes": ["Space"], "url": "https://vps/v1",
            "listed": ["tableFiles", "b2sFiles", "romFiles"], "in_library": True,
            "held": ["tableFiles", "b2sFiles"], **over}


def _found(*entries: dict, counted: str = "2026-10-01T10:00:00Z") -> dict:
    return {"counted": counted, "rows": list(entries)}


def _cells(row: dict) -> dict:
    return {field: row[field] for _listed, field, _word in discovery.KINDS}


class Rows(unittest.TestCase):
    def test_a_file_tied_to_a_release_reads_matched_and_one_tied_to_none_unmatched(self) -> None:
        tied = {"tableFiles": [{"held": True}], "b2sFiles": [{"held": False}]}
        cells = _cells(discovery.rows(_found(_entry(releases=tied)))[0])

        self.assertEqual(("matched", "unmatched", "missing"),
                         (cells["kind_table"], cells["kind_backglass"], cells["kind_rom"]))

    def test_a_file_of_a_kind_vps_lists_nothing_of_reads_unmatched(self) -> None:
        cells = _cells(discovery.rows(_found(_entry(listed=["tableFiles"])))[0])

        self.assertEqual(("unmatched", "unmatched"), (cells["kind_table"], cells["kind_backglass"]))

    def test_a_kind_the_entry_does_not_list_is_blank(self) -> None:
        cells = _cells(discovery.rows(_found(_entry()))[0])

        self.assertEqual("", cells["kind_pov"])
        self.assertEqual("", cells["kind_topper"])

    def test_a_game_not_in_the_library_is_missing_on_every_kind_it_lists(self) -> None:
        row = discovery.rows(_found(_entry(in_library=False, held=[])))[0]

        cells = _cells(row)
        self.assertEqual("no", row["in_library"])
        self.assertEqual({"missing"}, {cells["kind_table"], cells["kind_backglass"],
                                       cells["kind_rom"]})
        self.assertEqual("", cells["kind_pov"])

    def test_nothing_is_said_while_the_library_has_never_been_counted(self) -> None:
        row = discovery.rows(_found(_entry(in_library=None, held=[]), counted=""))[0]

        self.assertEqual("", row["in_library"])
        self.assertEqual({""}, set(_cells(row).values()))
        self.assertEqual("", row["gap"])

    def test_the_gap_column_says_missing_only_where_something_is(self) -> None:
        complete = _entry("a", listed=["tableFiles"], held=["tableFiles"])
        short = _entry("b")

        gaps = {row["id"]: row["gap"] for row in discovery.rows(_found(complete, short))}

        self.assertEqual({"a": "", "b": "missing"}, gaps)

    def test_the_id_is_the_entry_the_row_stands_for(self) -> None:
        self.assertEqual("v9", discovery.rows(_found(_entry("v9")))[0]["id"])


class Cell(unittest.TestCase):
    def test_the_game_cell_says_who_made_it_and_when(self) -> None:
        row = discovery.rows(_found(_entry(img_url="https://vps/a.webp")))[0]

        self.assertEqual(("Bally 1995", "https://vps/a.webp"), (row["said"], row["art"]))

    def test_a_game_with_no_year_says_only_who_made_it(self) -> None:
        row = discovery.rows(_found(_entry(year=None)))[0]

        self.assertEqual("Bally", row["said"])


class KeptKinds(unittest.TestCase):
    def test_a_kind_the_library_does_not_keep_is_blank_and_no_gap(self) -> None:
        row = discovery.rows(_found(_entry()), kept={"tableFiles", "b2sFiles"})[0]

        self.assertEqual("", row["kind_rom"])
        self.assertEqual("", row["gap"])

    def test_every_kind_counts_where_none_is_named(self) -> None:
        self.assertEqual("missing", discovery.rows(_found(_entry()))[0]["gap"])


class Views(unittest.TestCase):
    def test_no_built_in_view_turns_on_what_the_game_cell_already_says(self) -> None:
        for name, preset in discovery.VIEWS.items():
            with self.subTest(name):
                assert not isinstance(preset, list)
                self.assertFalse(set(preset.columns) & {"manufacturer", "year", "game_type",
                                                        "themes"})

    def test_the_picker_still_offers_them(self) -> None:
        fields = {one["field"] for one in discovery.COLUMNS}

        self.assertLessEqual({"manufacturer", "year", "game_type", "themes"}, fields)


class Unnamed(unittest.TestCase):
    def test_an_entry_with_no_name_is_not_a_row(self) -> None:
        shown = discovery.rows(_found(_entry("a"), _entry("b", name=""), _entry("c", name="  ")))

        self.assertEqual(["a"], [row["id"] for row in shown])


class Summary(unittest.TestCase):
    def test_a_counted_library_says_how_many_of_them_it_holds(self) -> None:
        shown = discovery.rows(_found(_entry("a"), _entry("b", in_library=False, held=[])))

        self.assertEqual("2 games, 1 in your library", discovery.summary(shown, True))

    def test_an_uncounted_one_says_only_how_many_games_there_are(self) -> None:
        shown = discovery.rows(_found(_entry(in_library=None), counted=""))

        self.assertEqual("1 game", discovery.summary(shown, False))


class Acts(unittest.TestCase):
    def test_add_is_offered_for_a_game_not_in_the_library(self) -> None:
        row = discovery.rows(_found(_entry(in_library=False, held=[])))[0]

        add = discovery.acts(mock.Mock(spec=Library), [row], mock.AsyncMock())[0]

        self.assertIsNotNone(add.run)

    def test_add_is_refused_with_a_reason_where_every_game_is_already_in(self) -> None:
        row = discovery.rows(_found(_entry()))[0]

        add = discovery.acts(mock.Mock(spec=Library), [row], mock.AsyncMock())[0]

        self.assertIsNone(add.run)
        self.assertTrue(add.hint)


    def test_add_waits_for_a_comparison(self) -> None:
        row = discovery.rows(_found(_entry(in_library=None, held=[]), counted=""))[0]

        add = discovery.acts(mock.Mock(spec=Library), [row], mock.AsyncMock())[0]

        self.assertIsNone(add.run)
        self.assertEqual("", add.hint)


class PaneActs(unittest.TestCase):
    def _verbs(self, row: dict, game_id: str = "") -> dict:
        return {one.label: one for one in discovery.pane_acts(
            mock.Mock(spec=Library), row, game_id, mock.AsyncMock())}

    def test_a_game_in_the_library_opens_in_games_and_cannot_be_added(self) -> None:
        row = discovery.rows(_found(_entry()))[0]

        verbs = self._verbs(row, "g1")

        self.assertEqual("/manager?" + deeplink.query({"view": "games", "game": "g1"}),
                         verbs["Open in Games"].to)
        self.assertIsNone(verbs["Add to Library"].run)
        self.assertEqual("https://vps/v1", verbs["View on VPS"].to)

    def test_a_game_not_in_the_library_says_why_it_cannot_be_opened(self) -> None:
        row = discovery.rows(_found(_entry(in_library=False, held=[])))[0]

        verbs = self._verbs(row)

        self.assertIsNone(verbs["Open in Games"].run)
        self.assertFalse(verbs["Open in Games"].to)
        self.assertEqual("Not in your library", verbs["Open in Games"].hint)
        self.assertIsNotNone(verbs["Add to Library"].run)

    def test_a_game_in_the_library_with_no_game_found_does_not_say_it_is_absent(self) -> None:
        row = discovery.rows(_found(_entry()))[0]

        verbs = self._verbs(row)

        self.assertIsNone(verbs["Open in Games"].run)
        self.assertNotEqual("Not in your library", verbs["Open in Games"].hint)

    def test_before_a_comparison_opening_is_dimmed_with_no_reason_given(self) -> None:
        row = discovery.rows(_found(_entry(in_library=None, held=[]), counted=""))[0]

        self.assertEqual("", self._verbs(row)["Open in Games"].hint)

    def test_an_entry_with_no_page_has_no_view_on_vps(self) -> None:
        row = discovery.rows(_found(_entry(url="")))[0]

        self.assertNotIn("View on VPS", self._verbs(row, "g1"))


def _library(*, hidden_assets: tuple[str, ...] = (), hidden_media: tuple[str, ...] = ()) -> Library:
    client = mock.Mock()
    client.library_policy.return_value = {"hidden_asset_kinds": list(hidden_assets),
                                          "hidden_media_kinds": list(hidden_media)}
    library = Library(client)
    library.load_kept_kinds()
    return library


def _held(**files: list[dict]) -> dict:
    kinds = [{"kind": listed_as, "held": bool(files.get(listed_as)),
              "files": files.get(listed_as, [])}
             for listed_as in ("b2sFiles", "romFiles", "wheelArtFiles")]
    return {"kinds": kinds, "tables": files.get("tableFiles", [])}


class Standing(unittest.TestCase):
    def test_the_live_state_decides_which_kinds_are_held(self) -> None:
        row = discovery.rows(_found(_entry(held=[])))[0]
        held = _held(tableFiles=[{"name": "a.vpx", "vps_file_id": ""}],
                     romFiles=[{"name": "rom", "vps_file_id": ""}])

        said = discovery.standing(_library(), row, held)

        self.assertEqual({"tableFiles": "have", "b2sFiles": "missing", "romFiles": "have"}, said)

    def test_the_row_answers_where_the_state_cannot_be_read(self) -> None:
        row = discovery.rows(_found(_entry()))[0]

        said = discovery.standing(_library(), row, None)

        self.assertEqual({"tableFiles": "have", "b2sFiles": "have", "romFiles": "missing"}, said)

    def test_a_game_not_in_the_library_has_no_state_on_any_kind(self) -> None:
        row = discovery.rows(_found(_entry(in_library=False, held=[])))[0]

        self.assertEqual({}, discovery.standing(_library(), row, None))

    def test_a_library_never_counted_has_no_state_on_any_kind(self) -> None:
        row = discovery.rows(_found(_entry(in_library=None, held=[]), counted=""))[0]

        self.assertEqual({}, discovery.standing(_library(), row, None))

    def test_a_kind_the_library_does_not_keep_is_not_counted(self) -> None:
        row = discovery.rows(_found(_entry()))[0]

        said = discovery.standing(_library(hidden_assets=("rom",)), row, None)

        self.assertNotIn("romFiles", said)


class Kept(unittest.TestCase):
    def test_the_table_is_always_kept(self) -> None:
        self.assertTrue(discovery.kept(_library(hidden_assets=("rom",)), "tableFiles"))

    def test_a_kind_switched_off_in_the_library_is_not(self) -> None:
        library = _library(hidden_assets=("rom",), hidden_media=("wheel",))

        self.assertFalse(discovery.kept(library, "romFiles"))
        self.assertFalse(discovery.kept(library, "wheelArtFiles"))
        self.assertTrue(discovery.kept(library, "b2sFiles"))

    def test_a_kind_that_is_two_of_ours_is_kept_while_either_is(self) -> None:
        self.assertTrue(discovery.kept(_library(hidden_assets=("altcolor_serum",)),
                                       "altColorFiles"))
        self.assertFalse(discovery.kept(
            _library(hidden_assets=("altcolor_serum", "altcolor_vni")), "altColorFiles"))


class Records(unittest.TestCase):
    def _records(self, count: int) -> list[dict]:
        return [{"vps_file_id": f"r{index}", "updated_at": f"2024-01-{index + 1:02d}T00:00:00Z"}
                for index in range(count)]

    def test_the_newest_come_first(self) -> None:
        ordered = vps_records.newest_first(self._records(3))

        self.assertEqual(["r2", "r1", "r0"], [one["vps_file_id"] for one in ordered])


class ChoosingBy(unittest.TestCase):
    def test_a_mod_and_its_features_share_a_line_without_saying_mod_twice(self) -> None:
        record = {"features": ["MOD", "VR"], "mod_of": {"vps_file_id": "b", "version": "1.0",
                                                          "authors": ["Larouillas"]}}

        self.assertEqual("Mod of 1.0 · Larouillas · VR",
                         vps_records.what_it_is(record, features=True))

    def test_a_list_that_hides_features_still_says_a_file_is_a_mod(self) -> None:
        record = {"features": ["MOD", "VR"]}

        self.assertEqual("Mod", vps_records.what_it_is(record, features=False))

    def test_a_vpx_file_does_not_name_its_format_and_another_does(self) -> None:
        self.assertEqual("", vps_records.what_it_is({"format": "VPX"}, features=True))
        self.assertEqual("Future Pinball",
                         vps_records.what_it_is({"format": "FP"}, features=True))

    def test_a_file_is_linked_by_the_site_it_is_on(self) -> None:
        record = {"url": "https://www.vpforums.org/file"}

        self.assertEqual(("VPForums", "https://www.vpforums.org/file"), vps_records.site_of(record))

    def test_a_page_is_broken_only_where_vps_marks_that_address(self) -> None:
        links = [{"url": "https://a.example/1", "broken": True}, {"url": "https://b.example/2"}]

        self.assertFalse(vps_records.page_is_broken({"url": "https://b.example/2", "urls": links}))
        self.assertTrue(vps_records.page_is_broken({"url": "https://a.example/1", "urls": links}))
        self.assertFalse(vps_records.page_is_broken({"url": "", "urls": []}))

    def test_a_file_with_no_link_names_no_site(self) -> None:
        self.assertEqual(("", ""), vps_records.site_of({"url": ""}))


class TypeWords(unittest.TestCase):
    def test_the_letters_are_spelled_out(self) -> None:
        self.assertEqual(["Solid State", "Electro-Mechanical", "Pure Mechanical"],
                         [discovery.type_word(code) for code in ("SS", "EM", "PM")])

    def test_a_code_it_does_not_know_is_shown_as_it_is(self) -> None:
        self.assertEqual("XY", discovery.type_word("XY"))


class Address(unittest.TestCase):
    def test_the_entry_is_named_in_the_address_on_this_page_only(self) -> None:
        on_page = deeplink.query({"view": "discovery", "entry": "v1"})
        elsewhere = deeplink.query({"view": "games", "entry": "v1"})

        self.assertIn("entry=v1", on_page)
        self.assertNotIn("entry", elsewhere)

    def test_a_subject_is_named_in_the_address_by_its_kind(self) -> None:
        for kind in deeplink.SUBJECTS:
            with self.subTest(kind=kind):
                state = {"view": "discovery", "subject": {"type": kind, "name": "Bally"}}
                self.assertEqual(f"view=discovery&{kind}=Bally", deeplink.query(state))

    def test_a_subject_is_named_on_this_page_only(self) -> None:
        state = {"view": "games", "subject": {"type": "author", "name": "VPW"}}
        self.assertNotIn("author", deeplink.query(state))

    def test_an_address_opens_the_page_for_the_subject_it_names(self) -> None:
        state: dict = {"view": "discovery"}
        deeplink.apply(state, {"view": "discovery", "maker": " Bally "},
                       views=["discovery"], sections=[])
        self.assertEqual(state["subject"], {"type": "maker", "name": "Bally"})


class ReleasesKept(unittest.TestCase):
    def test_the_releases_a_library_does_not_collect_are_left_out(self) -> None:
        found = {"rows": [{"vps_id": "a", "releases": {"tableFiles": [1], "romFiles": [2]}},
                          {"vps_id": "b", "releases": {"romFiles": [3]}}]}
        self.assertEqual(discovery.releases_of(found, ["tableFiles"]), {"a": {"tableFiles": [1]}})


BALLY = {"type": "maker", "name": "Bally"}
SPACE = {"type": "theme", "name": "Space"}
WILDMAN = {"type": "author", "name": "Wildman"}


def _browse_columns(*, many_makers: bool = False) -> list[dict]:
    makers = 301 if many_makers else 2
    built = [{"manufacturer": f"Maker {n}", "game_type": "SS"} for n in range(makers)]
    return grid.with_derived_facets(discovery.COLUMNS, built, discovery.CATALOG_FACET_CEILING)


class OpenInGrid(unittest.TestCase):
    def test_a_makers_page_opens_browse_on_its_manufacturer(self) -> None:
        tab, opening = discovery.opening_for(
            BALLY, {"maker": "Bally"}, _browse_columns(), counted=True)

        self.assertEqual((discovery.BROWSE, "Bally"), (tab, opening["name"]))
        self.assertEqual({"manufacturer": {"values": ["Bally"]}}, opening["filters"])

    def test_a_manufacturer_filtered_by_typing_is_matched_on_the_whole_name(self) -> None:
        _tab, opening = discovery.opening_for(
            BALLY, {"maker": "Bally"}, _browse_columns(many_makers=True), counted=True)

        self.assertEqual({"manufacturer": {"filterType": "text", "type": "equals",
                                           "filter": "Bally"}}, opening["filters"])

    def test_a_themes_page_opens_browse_on_its_theme(self) -> None:
        tab, opening = discovery.opening_for(
            SPACE, {"theme": "Space"}, _browse_columns(), counted=True)

        self.assertEqual(discovery.BROWSE, tab)
        self.assertEqual({"themes": {"values": ["Space"]}}, opening["filters"])

    def test_a_makers_kind_in_library_and_missing_set_the_columns_that_say_them(self) -> None:
        question = {"maker": "Bally", "kind": "b2sFiles", "owned": "yes", "held": "missing"}

        _tab, opening = discovery.opening_for(BALLY, question, _browse_columns(),
                                              counted=True)

        self.assertEqual({"manufacturer": {"values": ["Bally"]},
                          "in_library": {"values": ["yes"]},
                          "kind_backglass": {"values": ["missing"]}}, opening["filters"])
        self.assertEqual("Bally Backglasses", opening["name"])

    def test_missing_without_a_kind_is_the_anything_missing_column(self) -> None:
        _tab, opening = discovery.opening_for(
            BALLY, {"maker": "Bally", "held": "missing"}, _browse_columns(),
            counted=True)

        self.assertEqual({"values": ["missing"]}, opening["filters"]["gap"])
        self.assertNotIn("held", opening["filters"])

    def test_a_kind_alone_filters_to_the_games_that_list_it(self) -> None:
        _tab, opening = discovery.opening_for(
            BALLY, {"maker": "Bally", "kind": "tableFiles"}, _browse_columns(),
            counted=True)

        self.assertEqual({"values": ["matched", "unmatched", "missing"]},
                         opening["filters"]["kind_table"])
        self.assertEqual("Bally Tables", opening["name"])

    def test_before_a_count_only_the_maker_filters(self) -> None:
        question = {"maker": "Bally", "kind": "b2sFiles", "owned": "yes", "held": "missing"}

        _tab, opening = discovery.opening_for(BALLY, question, _browse_columns(),
                                              counted=False)

        self.assertEqual({"manufacturer": {"values": ["Bally"]}}, opening["filters"])
        self.assertEqual("Bally", opening["name"])

    def test_an_authors_page_opens_releases_with_the_filters_on_its_columns(self) -> None:
        question = {"by": ["Wildman"], "kind": "b2sFiles", "owned": "yes"}

        tab, opening = discovery.opening_for(WILDMAN, question, [], counted=True)

        self.assertEqual((discovery.RELEASES, "Wildman Backglasses"), (tab, opening["name"]))
        self.assertEqual({"authors": {"values": ["Wildman"]}, "kind": {"values": ["b2sFiles"]},
                          "in_library": {"values": ["yes"]}}, opening["filters"])

    def test_the_words_typed_open_the_grid_holding_them(self) -> None:
        for subject, question in ((WILDMAN, {"by": ["Wildman"]}), (BALLY, {"maker": "Bally"})):
            with self.subTest(subject=subject["type"]):
                _tab, opening = discovery.opening_for(
                    subject, question, _browse_columns(), counted=True, search="alien storm")

                self.assertEqual("alien storm", opening["search"])

    def test_nothing_typed_opens_the_grid_with_no_search(self) -> None:
        for search in ("", "  "):
            _tab, opening = discovery.opening_for(
                BALLY, {"maker": "Bally"}, _browse_columns(), counted=True, search=search)

            self.assertNotIn("search", opening)

    def test_an_authors_playable_formats_go_with_the_formats_the_releases_are_filed_under(
            self) -> None:
        question = {"by": ["Wildman"], "kind": "tableFiles", "format": ["VPX"]}
        releases = {"a": {"tableFiles": [{"format": "VPX"}, {"format": "FP"}, {}]}}

        _tab, opening = discovery.opening_for(WILDMAN, question, [], counted=True,
                                              releases=releases)

        self.assertEqual({"values": ["VPX", ""]}, opening["filters"]["format"])

    def test_the_formats_the_releases_are_filed_under_are_read_from_every_listing(self) -> None:
        releases = {"a": {"tableFiles": [{"format": "VPX"}, {"format": ""}, {}],
                          "b2sFiles": [{"format": "FP"}]}, "b": {}}

        self.assertEqual({"VPX", "", "FP"}, discovery.formats_filed(releases))

    def test_the_view_is_named_for_the_page_alone_where_no_kind_was_chosen(self) -> None:
        _tab, opening = discovery.opening_for(WILDMAN, {"by": ["Wildman"]}, [],
                                              counted=True)

        self.assertEqual("Wildman", opening["name"])

    def test_a_page_opens_on_the_tab_it_names_and_the_chosen_tab_is_remembered(self) -> None:
        state: dict[str, Any] = {"view": "discovery", "subject": WILDMAN}
        opening = {"name": "Wildman", "filters": {}}

        with mock.patch.object(discovery.ui, "run_javascript"):
            discovery.open_page(state, [], lambda: None, None, opening=opening,
                                tab=discovery.RELEASES)

        self.assertEqual((None, {**opening, "tab": discovery.RELEASES}, discovery.RELEASES),
                         (state["subject"], state["opening"], state["discovery_tab"]))
        self.assertEqual(discovery.RELEASES, discovery.tab_of(state))

    def test_a_pick_in_the_search_opens_a_page_under_the_name_vps_credits(self) -> None:
        people = [{"name": "Wildman", "aliases": ["Wild Man"], "releases": 3}]
        state: dict[str, Any] = {"view": "discovery"}

        with mock.patch.object(discovery.ui, "run_javascript"):
            discovery.open_page(state, people, lambda: None,
                                {"type": "author", "name": "Wild Man"})

        self.assertEqual({"type": "author", "name": "Wildman"}, state["subject"])
        self.assertNotIn("opening", state)

    def test_a_page_that_opens_no_view_leaves_the_one_a_tab_was_sent_to(self) -> None:
        state: dict[str, Any] = {"view": "discovery",
                                 "opening": {"name": "Wildman", "filters": {}, "tab": "releases"}}

        with mock.patch.object(discovery.ui, "run_javascript"):
            discovery.open_page(state, [], lambda: None, None)

        self.assertEqual("Wildman", state["opening"]["name"])

    def test_a_view_a_tab_was_sent_to_is_kept_for_that_tab_until_another_is_picked(self) -> None:
        state: dict[str, Any] = {"opening": {"name": "Wildman", "filters": {}, "tab": "releases"}}

        self.assertIsNone(discovery_ask.opening_on(state, discovery.BROWSE))
        self.assertIsNotNone(discovery_ask.opening_on(state, discovery.RELEASES))
        discovery_ask.drop_opening(state, discovery.BROWSE)
        self.assertIn("opening", state)
        discovery_ask.drop_opening(state, discovery.RELEASES)
        self.assertNotIn("opening", state)


class MediaBands(unittest.TestCase):
    def test_the_wheel_and_the_topper_come_first_whatever_order_they_are_given_in(self) -> None:
        bands = discovery.media_bands(list(discovery.KINDS), ["playfield", "topper", "wheel"])

        self.assertEqual(["wheel", "topper", "playfield", "rule_sheet"],
                         [kind for kind, _one in bands])

    def test_a_kind_vps_lists_keeps_its_vps_row_and_one_only_a_source_has_gets_a_word(
            self) -> None:
        bands = dict(discovery.media_bands(list(discovery.KINDS), ["playfield"]))

        self.assertEqual("wheelArtFiles", bands["wheel"][0])
        self.assertEqual("ruleFiles", bands["rule_sheet"][0])
        self.assertEqual("", bands["playfield"][0])
        self.assertEqual("media.kind.playfield.label", bands["playfield"][2])

    def test_a_kind_neither_vps_nor_a_source_has_is_not_a_band(self) -> None:
        bands = discovery.media_bands([("tableFiles", "kind_table", "word.table")], [])

        self.assertEqual([], bands)


class Picks(unittest.TestCase):
    def _offer(self, **over) -> dict:
        return {"source": "vpinmediadb", "kind": "wheel", "size": "4K", "name": "wheel.png",
                "url": "https://example.test/wheel.png", **over}

    def test_a_pick_is_named_by_where_it_is_from_what_it_is_and_which_size(self) -> None:
        pick = discovery_get.pick_of(self._offer())

        self.assertEqual(("vpinmediadb", "wheel", "4K"), (pick.source, pick.kind, pick.size))
        self.assertEqual(discovery_get.key_of("vpinmediadb", "wheel", "4K"), pick.key)

    def test_an_offer_with_no_size_is_a_pick_with_none(self) -> None:
        self.assertEqual("", discovery_get.pick_of(self._offer(size=None)).size)

    def test_a_tray_belongs_to_the_entry_and_outlives_a_redraw(self) -> None:
        state: dict = {}

        mine = discovery_get.tray_of(state, "v1")

        self.assertIs(mine, discovery_get.tray_of(state, "v1"))
        self.assertIsNot(mine, discovery_get.tray_of(state, "v2"))

    def test_the_wheel_and_the_topper_are_drawn_before_the_rest(self) -> None:
        order = sorted(["playfield", "topper", "wheel", "backglass", "nothing_we_know"],
                       key=discovery_get.media_order)

        self.assertEqual(["wheel", "topper", "backglass", "playfield", "nothing_we_know"], order)


class Moving(unittest.TestCase):
    def _pick(self, size: str, kind: str = "wheel") -> discovery_get.Pick:
        return discovery_get.pick_of({"source": "vpinmediadb", "kind": kind, "size": size})

    def test_a_file_on_its_way_counts_and_one_that_failed_does_not(self) -> None:
        tray, going, queued, failed = (discovery_get.Tray(), self._pick("4K"),
                                       self._pick("4K", "topper"), self._pick("4K", "backglass"))

        tray.move(going.key, discovery_get.DOWNLOADING)
        tray.move(queued.key, discovery_get.QUEUED)
        tray.move(failed.key, discovery_get.FAILED, "no connection")

        self.assertEqual(2, tray.downloading())
        self.assertTrue(tray.running)

    def test_a_tray_with_no_move_is_not_running(self) -> None:
        tray = discovery_get.Tray()

        self.assertFalse(tray.running)
        self.assertEqual(0, tray.downloading())

    def test_forgetting_a_move_takes_the_file_off_the_tray(self) -> None:
        tray, pick = discovery_get.Tray(), self._pick("4K")
        tray.move(pick.key, discovery_get.FAILED, "no connection")

        tray.forget(pick.key)

        self.assertEqual({}, tray.moves)
        self.assertIsNone(discovery_get.standing_of(tray, pick.key))

    def test_a_failed_file_stands_as_failed_with_its_reason_and_a_way_to_try_again(self) -> None:
        tray, pick = discovery_get.Tray(), self._pick("4K")
        tray.move(pick.key, discovery_get.FAILED, "no connection")

        standing = discovery_get.standing_of(tray, pick.key)

        assert standing is not None
        self.assertEqual(("bad", "no connection", True),
                         (standing.level, standing.hint, standing.retry))

    def test_a_file_on_its_way_stands_as_going(self) -> None:
        tray, pick = discovery_get.Tray(), self._pick("4K")
        tray.move(pick.key, discovery_get.DOWNLOADING)

        standing = discovery_get.standing_of(tray, pick.key)

        assert standing is not None
        self.assertEqual("unknown", standing.level)

    def test_an_extension_row_that_needs_the_person_stands_with_its_buttons(self) -> None:
        tray, pick = discovery_get.Tray(), self._pick("4K")
        row = {"state": {"level": "warn", "text": "Sign in", "hint": "A login is needed"},
               "acts": [{"label": "Sign in"}]}
        tray.offered[pick.key] = discovery_get.Offered("ext", "k", "s", "name", "table", row, pick)

        standing = discovery_get.standing_of(tray, pick.key)

        assert standing is not None
        self.assertEqual(("warn", "Sign in", False), (standing.level, standing.word,
                                                      standing.retry))
        self.assertEqual(({"label": "Sign in"},), standing.acts)

    def test_an_extension_row_that_is_in_stands_as_nothing(self) -> None:
        tray, pick = discovery_get.Tray(), self._pick("4K")
        row = {"state": {"level": "on", "text": "In"}}
        tray.offered[pick.key] = discovery_get.Offered("ext", "k", "s", "name", "table", row, pick)

        self.assertIsNone(discovery_get.standing_of(tray, pick.key))

    def test_progress_is_whole_percent_and_none_where_the_source_gave_none(self) -> None:
        pick = self._pick("4K")
        half = discovery_get.Offered("ext", "k", "s", "n", "table",
                                     {"state": {"level": "unknown", "progress": 0.5}}, pick)
        none = discovery_get.Offered("ext", "k", "s", "n", "table",
                                     {"state": {"level": "unknown", "progress": True}}, pick)

        self.assertEqual((50, None), (half.progress, none.progress))

    def test_a_download_on_an_extension_row_counts_while_it_is_unknown(self) -> None:
        tray, pick = discovery_get.Tray(), self._pick("4K")
        row = {"state": {"level": "unknown"}}
        tray.offered[pick.key] = discovery_get.Offered("ext", "k", "s", "n", "table", row, pick)

        self.assertEqual(1, tray.downloading())

    def test_repainting_all_redraws_every_key_and_forgets_a_holder_that_was_deleted(self) -> None:
        tray, drawn = discovery_get.Tray(), []
        live, dead = mock.MagicMock(is_deleted=False), mock.MagicMock(is_deleted=True)
        tray._painters = {"a": [(live, lambda: drawn.append("a"))],
                          "b": [(dead, lambda: drawn.append("b"))]}

        tray.repaint_all()

        self.assertEqual(["a"], drawn)
        self.assertEqual([], tray._painters["b"])

    def test_a_move_redraws_the_file_the_foot_and_the_rail(self) -> None:
        tray, drawn = discovery_get.Tray(), []
        holder = mock.MagicMock(is_deleted=False)

        def drawing(key: str) -> Any:
            return lambda: drawn.append(key)

        tray._painters = {key: [(holder, drawing(key))]
                          for key in ("k", discovery_get.FOOT, discovery_get.RAIL, "other")}

        tray.move("k", discovery_get.QUEUED)

        self.assertEqual({"k", discovery_get.FOOT, discovery_get.RAIL}, set(drawn))


class OwnPicture(unittest.TestCase):
    def _draw(self, listed_as: str, name: str, game_id: str = "g1", present: bool = True):
        context = {"library": mock.Mock(media={"g1": {
            "rule_sheet": {"present": present, "version": "v1"},
            "topper": {"present": present, "version": "v2"}}})}
        scene = mock.Mock(game_id=game_id)
        return discovery_list._own_picture(context, scene, mock.Mock(
            listed_as=listed_as, media=""), name)

    def test_a_rule_sheet_that_is_a_picture_is_drawn_from_its_file(self) -> None:
        self.assertEqual(("rule_sheet", "v1"), self._draw("ruleFiles", "300 (Gottlieb 1975).png"))

    def test_a_topper_is_drawn_from_its_own_slot(self) -> None:
        self.assertEqual(("topper", "v2"), self._draw("topperFiles", "topper.png"))

    def test_a_rule_sheet_that_is_a_document_keeps_the_glyph(self) -> None:
        self.assertIsNone(self._draw("ruleFiles", "rules.pdf"))

    def test_a_kind_held_as_an_asset_has_no_picture_of_its_own(self) -> None:
        self.assertIsNone(self._draw("b2sFiles", "table.png"))

    def test_a_game_not_in_the_library_has_nothing_to_draw_from(self) -> None:
        self.assertIsNone(self._draw("ruleFiles", "rules.png", game_id=""))

    def test_a_slot_the_library_does_not_hold_is_not_drawn(self) -> None:
        self.assertIsNone(self._draw("ruleFiles", "rules.png", present=False))


class Choosing(unittest.IsolatedAsyncioTestCase):
    async def _ask(self, name: str = "", looked: dict | None = None, current: str = ""):
        views = {("v1", "rule"): mock.Mock(looked=looked or {})}
        context = {"state": {"discovery_views": views}, "library": mock.Mock(
            media={"g1": {"rule_sheet": {"present": True, "version": "v9"}}})}
        scene = mock.Mock(game_id="g1", vps_id="v1", title="Attack from Mars")
        kind = mock.Mock(listed_as="ruleFiles", key="rule", media="", glyph="description",
                         records=[{"vps_file_id": "f1"}, {"vps_file_id": "f2", "img_url": "u2"}])
        asked = mock.AsyncMock(return_value="f1")
        with mock.patch.object(discovery_list.release_match, "ask", asked):
            picked = await discovery_list._chooser(context, kind, scene, name)(current)
        return picked, asked.call_args

    async def test_a_picture_read_from_the_source_reaches_the_picker(self) -> None:
        picked, call = await self._ask(looked={"f1": "https://host/p.png"})
        listed = call.args[1]

        self.assertEqual(["https://host/p.png", "u2"], [one["img_url"] for one in listed])

    async def test_the_files_own_row_names_it_and_draws_its_picture(self) -> None:
        _picked, call = await self._ask(name="300 (Gottlieb 1975).png")
        yours = call.args[0]

        self.assertEqual("300 (Gottlieb 1975).png", yours.file)
        self.assertIn("/api/v1/games/g1/media/rule_sheet", yours.picture)

    async def test_the_release_the_file_is_tied_to_draws_the_files_picture(self) -> None:
        _picked, call = await self._ask(name="300 (Gottlieb 1975).png", current="f1")
        tied, other = call.args[1]

        self.assertIn("/api/v1/games/g1/media/rule_sheet", tied["img_url"])
        self.assertEqual("u2", other["img_url"])

    async def test_a_file_that_is_not_a_picture_has_none_to_draw(self) -> None:
        _picked, call = await self._ask(name="rules.pdf")

        self.assertEqual("", call.args[0].picture)


class Listings(unittest.TestCase):
    def test_a_vps_kind_is_ours_by_the_kinds_behind_it(self) -> None:
        ours, held_in = discovery_get.of_listing("b2sFiles")

        self.assertEqual(("backglass",), ours)
        self.assertEqual(vps_kinds.ASSET, held_in)

    def test_a_kind_that_is_two_of_ours_names_both(self) -> None:
        ours, held_in = discovery_get.of_listing("topperFiles")

        self.assertEqual(("topper", "topper_video"), ours)
        self.assertEqual(vps_kinds.MEDIA, held_in)

    def test_the_table_is_an_asset_kind_of_its_own(self) -> None:
        self.assertEqual((("table",), vps_kinds.ASSET), discovery_get.of_listing("tableFiles"))


class Getting(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.asked: list[str] = []
        self.refused: set[str] = set()
        self.on_asked = lambda kind: None

        async def inline(callback, *args):
            return callback(*args)

        self.enterContext(mock.patch.object(discovery_get.offload, "io", inline))
        self.library = mock.Mock()
        self.library.fetch_media = self._fetch
        self.library.owned = lambda _ids: ({}, {})
        self.discovery: dict[str, Any] = {"id": "v1", "name": "Attack from Mars",
                                          "is_in_library": True}
        self.context = {"library": self.library, "state": {}, "discovery": self.discovery}
        self.tray = discovery_get.Tray()

    def _fetch(self, _game, _table, kind, _source, _vps, _size):
        self.asked.append(kind)
        self.on_asked(kind)
        if kind in self.refused:
            raise RuntimeError("the source has no file")
        return {"kind": kind}

    def _picks(self, *kinds: str) -> list[discovery_get.Pick]:
        return [discovery_get.pick_of({"source": "vpinmediadb", "kind": kind, "size": "4K"})
                for kind in kinds]

    async def _get(self, picks: list[discovery_get.Pick], game_id: str = "g1") -> None:
        with _PAGE:
            await discovery_get.get(self.context, self.tray, game_id, picks)

    async def test_a_file_the_source_refuses_fails_alone_and_the_rest_arrive(self) -> None:
        wheel, topper, playfield = self._picks("wheel", "topper", "playfield")
        self.refused = {"topper"}

        await self._get([wheel, topper, playfield])

        self.assertEqual(["wheel", "topper", "playfield"], self.asked)
        self.assertEqual({topper.key: discovery_get.FAILED},
                         {key: move.state for key, move in self.tray.moves.items()})
        self.assertEqual("the source has no file", self.tray.moves[topper.key].reason)
        self.assertFalse(self.tray.running)

    async def test_trying_again_asks_for_that_file_alone(self) -> None:
        wheel, topper = self._picks("wheel", "topper")
        self.refused = {"topper"}
        await self._get([wheel, topper])
        self.asked.clear()
        self.refused = set()

        with _PAGE:
            await discovery_get.try_again(self.context, self.tray, "g1", topper)()

        self.assertEqual(["topper"], self.asked)
        self.assertEqual({}, self.tray.moves)

    async def _drop(self, ours: tuple[str, ...], held_in: str, name: str = "rules.pdf",
                    **more: Any) -> tuple[Any, Any]:
        taken: list[Any] = []
        self.library.games = [{"id": "g1", "folder": "/games/Attack from Mars"}]
        read = mock.AsyncMock(return_value=None)
        imported = mock.AsyncMock()
        with (_PAGE,
              mock.patch.object(discovery_get.mediasource, "take_drops",
                                lambda _holder, arrived: taken.append(arrived)),
              mock.patch.object(discovery_get.uploads, "analyzed", read),
              mock.patch.object(discovery_get.uploads, "confirmed_import", imported)):
            discovery_get.use_a_file(self.context, "g1", _PAGE, ours=ours, held_in=held_in,
                                     **more)
            await taken[0](discovery_get.uploads.Drop(upload_id="u-1", name=name))
        return read, imported

    async def test_a_file_dropped_on_a_picture_or_document_kind_is_not_read_by_its_name(
            self) -> None:
        read, imported = await self._drop(("rule_sheet",), vps_kinds.MEDIA)

        read.assert_not_awaited()
        self.assertEqual("rule_sheet", imported.await_args.kwargs["media_kind"])
        self.assertEqual("/games/Attack from Mars", imported.await_args.kwargs["game_dir"])

    async def test_a_topper_file_goes_to_the_slot_its_extension_belongs_to(self) -> None:
        for name, slot in (("Attack from Mars.png", "topper"),
                           ("Attack from Mars.mp4", "topper_video"),
                           ("Attack from Mars.xyz", "topper")):
            with self.subTest(name=name):
                read, imported = await self._drop(("topper", "topper_video"),
                                                  vps_kinds.MEDIA, name)

                read.assert_not_awaited()
                self.assertEqual(slot, imported.await_args.kwargs["media_kind"])

    async def test_a_drop_off_the_row_is_offered_as_the_file_whose_page_was_opened(
            self) -> None:
        match = import_dialog.ReleaseMatch(mock.AsyncMock(), "Rule Sheet", "VPS Rule Sheet")
        _read, imported = await self._drop(("rule_sheet",), vps_kinds.MEDIA, match=match,
                                           opened=lambda: ("f1", "1.0.0"))

        release = imported.await_args.kwargs["release"]
        self.assertEqual(("f1", "1.0.0"), (release.vps_file_id, release.label))
        self.assertEqual("Because you opened this VPS Rule Sheet's page first",
                         release.reason)
        self.assertEqual("", match.vps_file_id)

    async def test_a_drop_with_no_page_opened_has_no_match_and_no_reason(self) -> None:
        match = import_dialog.ReleaseMatch(mock.AsyncMock(), "Rule Sheet", "VPS Rule Sheet")
        _read, imported = await self._drop(("rule_sheet",), vps_kinds.MEDIA, match=match,
                                           opened=lambda: ("", ""))

        release = imported.await_args.kwargs["release"]
        self.assertEqual(("", ""), (release.vps_file_id, release.reason))

    async def test_a_file_the_drop_names_is_not_replaced_by_the_opened_one(self) -> None:
        match = import_dialog.ReleaseMatch(mock.AsyncMock(), "Rule Sheet", "VPS Rule Sheet",
                                           vps_file_id="f2", label="2.0")
        _read, imported = await self._drop(("rule_sheet",), vps_kinds.MEDIA, match=match,
                                           opened=lambda: ("f1", "1.0.0"))

        release = imported.await_args.kwargs["release"]
        self.assertEqual("f2", release.vps_file_id)
        self.assertEqual("Because you dropped the file on this VPS Rule Sheet",
                         release.reason)

    async def test_a_picture_or_document_import_is_tied_to_its_release_by_path(self) -> None:
        plan = {"items": [{"kind": "media", "media_kind": "rule_sheet",
                           "action": "replace_media",
                           "destination": "/games/Attack from Mars/medias/rules.png"}]}
        with _PAGE, mock.patch.object(discovery_get.opened_pages, "forget"):
            await discovery_get._tie(self.library, "g1", "e1", "/games/Attack from Mars", "f1",
                                     ("rule_sheet",), {"imported": ["media"]}, plan)

        self.library.set_asset_source.assert_called_once_with("g1", "medias/rules.png", "f1")

    async def test_a_table_a_patch_made_is_tied_to_its_release(self) -> None:
        plan = {"items": [{"kind": "patch", "action": "apply_patch",
                           "destination": "/games/Attack from Mars/Attack from Mars VPW.vpx"}]}
        with _PAGE, mock.patch.object(discovery_get.opened_pages, "forget"):
            await discovery_get._tie(self.library, "g1", "e1", "/games/Attack from Mars", "f1",
                                     ("table",), {"imported": ["patch"],
                                                  "patched_tables": ["t7"]}, plan)

        self.library.set_table_source.assert_called_once_with("g1", "t7", "f1")

    async def test_a_file_dropped_on_a_kind_without_a_slot_is_read_first(self) -> None:
        read, imported = await self._drop(("rom",), vps_kinds.ASSET)

        read.assert_awaited_once()
        imported.assert_not_awaited()

    async def test_stop_ends_the_batch_after_the_file_in_hand(self) -> None:
        wheel, topper, playfield = self._picks("wheel", "topper", "playfield")

        def pressed(kind: str) -> None:
            self.tray.stopping = kind == "wheel"

        self.on_asked = pressed

        await self._get([wheel, topper, playfield])

        self.assertEqual(["wheel"], self.asked)
        self.assertEqual({}, self.tray.moves)
        self.assertFalse(self.tray.running)
        self.assertFalse(self.tray.stopping)

    async def test_a_file_already_on_its_way_is_not_asked_for_twice(self) -> None:
        (wheel,) = self._picks("wheel")
        self.tray.move(wheel.key, discovery_get.DOWNLOADING)

        await self._get([wheel])

        self.assertEqual([], self.asked)

    async def test_a_file_that_failed_is_asked_for_again_on_the_next_press(self) -> None:
        (wheel,) = self._picks("wheel")
        self.tray.move(wheel.key, discovery_get.FAILED, "no connection")

        await self._get([wheel])

        self.assertEqual(["wheel"], self.asked)
        self.assertEqual({}, self.tray.moves)

    async def test_a_game_in_the_library_that_cannot_be_read_gets_nothing_and_says_so(
            self) -> None:
        (wheel,) = self._picks("wheel")
        self.library.owned = mock.Mock(side_effect=RuntimeError("no listing"))

        with _PAGE, mock.patch.object(discovery_get.ui, "notify") as said:
            await discovery_get.get(self.context, self.tray, "", [wheel])

        self.assertEqual([], self.asked)
        self.assertFalse(self.tray.running)
        self.assertEqual("negative", said.call_args.kwargs["type"])

    async def test_a_game_not_in_the_library_is_made_first_and_declining_the_place_ends_it(
            self) -> None:
        self.discovery["is_in_library"] = False
        (wheel,) = self._picks("wheel")

        with mock.patch.object(discovery_get.import_dialog, "ask_where",
                               mock.AsyncMock(return_value=None)):
            await self._get([wheel], "")

        self.assertEqual([], self.asked)
        self.assertEqual({}, self.tray.moves)
        self.assertFalse(self.tray.running)

    async def test_a_game_not_in_the_library_is_made_and_then_gets_the_files(self) -> None:
        self.discovery["is_in_library"] = False
        self.library.add_game_from_vps = lambda _vps, _where: {"id": "g9"}
        (wheel,) = self._picks("wheel")

        with mock.patch.object(discovery_get.import_dialog, "ask_where",
                               mock.AsyncMock(return_value="place")):
            await self._get([wheel], "")

        self.assertEqual(["wheel"], self.asked)
        self.assertEqual("g9", self.tray.made)
        self.assertEqual({}, self.tray.moves)

    async def test_the_game_made_once_is_not_made_again_for_the_next_file(self) -> None:
        self.discovery["is_in_library"] = False
        made = mock.Mock(return_value={"id": "g9"})
        self.library.add_game_from_vps = made
        wheel, topper = self._picks("wheel", "topper")

        with mock.patch.object(discovery_get.import_dialog, "ask_where",
                               mock.AsyncMock(return_value="place")):
            await self._get([wheel], "")
            await self._get([topper], "")

        self.assertEqual(1, made.call_count)
        self.assertEqual(["wheel", "topper"], self.asked)

    async def test_an_entry_the_library_holds_but_has_not_compared_gets_its_own_game(self) -> None:
        self.discovery["is_in_library"] = False
        self.library.owned = lambda _ids: ({"v1": {"game_id": "g5"}}, {})
        made = mock.Mock(return_value={"id": "g9"})
        self.library.add_game_from_vps = made
        (wheel,) = self._picks("wheel")

        await self._get([wheel], "")

        self.assertEqual(0, made.call_count)
        self.assertEqual("g5", self.tray.made)
        self.assertEqual(["wheel"], self.asked)

    async def test_what_is_queued_is_cleared_when_the_batch_cannot_start(self) -> None:
        self.library.owned = mock.Mock(side_effect=RuntimeError("no listing"))
        (wheel,) = self._picks("wheel")

        with mock.patch.object(discovery_get.ui, "notify"):
            await self._get([wheel], "")

        self.assertEqual({}, self.tray.moves)

    async def test_what_the_batch_leaves_is_drawn_again_when_it_ends(self) -> None:
        wheel, topper = self._picks("wheel", "topper")
        self.refused = {"wheel"}
        redrawn: list[str] = []
        holder = mock.MagicMock(is_deleted=False)
        self.tray._painters = {topper.key: [(holder, lambda: redrawn.append(topper.key))]}

        await self._get([wheel, topper])

        self.assertIn(topper.key, redrawn)


class Leaving(unittest.TestCase):
    def test_the_entry_panel_section_does_not_follow_the_user_to_another_page(self) -> None:
        state = {"view": "discovery", "section": "discovery_game", "entry": "v1", "game": "",
                 "table": ""}

        with mock.patch.object(page.remembered, "put"):
            page.leave_for(state, "games")

        self.assertNotIn("section", state)
        self.assertIsNone(state["entry"])


class Comparing(unittest.IsolatedAsyncioTestCase):
    """The page says a comparison is running and reads the answer when it ends. It never
    starts one: the sources watch does."""

    def setUp(self) -> None:
        self.jobs: list[dict] = []
        self.library = mock.Mock(spec=Library)
        self.library.jobs.side_effect = lambda: list(self.jobs)
        self.redraw = mock.Mock()
        self.look: Any = None
        timer = mock.patch.object(discovery.ui, "timer",
                                  side_effect=lambda _every, call: setattr(self, "look", call))
        timer.start()
        self.addCleanup(timer.stop)
        with _PAGE:
            self.line = discovery.comparing_line(self.library, self.redraw)

    async def _look(self) -> None:
        with _PAGE:
            await self.look()

    def _job(self, state: str, finished: float = 0.0, kind: str = KIND_VPS_ROLLUP) -> dict:
        return {"kind": kind, "state": state, "finished_at": finished}

    async def test_a_running_comparison_is_said_and_does_not_redraw_the_page(self) -> None:
        self.jobs = [self._job("running")]

        await self._look()

        self.assertTrue(self.line.visible)
        self.redraw.assert_not_called()

    async def test_nothing_is_said_while_no_comparison_is_running(self) -> None:
        """A comparison that failed or was never started leaves the page quiet, not
        saying one is under way."""
        self.jobs = [self._job("failed", finished=time.time() + 5)]

        await self._look()

        self.assertFalse(self.line.visible)

    async def test_the_line_goes_when_the_comparison_ends(self) -> None:
        self.jobs = [self._job("running")]
        await self._look()
        self.jobs = [self._job("done", finished=time.time() + 5)]

        await self._look()

        self.assertFalse(self.line.visible)

    async def test_the_page_is_drawn_again_when_the_comparison_ends(self) -> None:
        self.jobs = [self._job("running")]
        await self._look()
        self.jobs = [self._job("done", finished=time.time() + 5)]

        await self._look()

        self.library.forget_catalog.assert_called_once_with()
        self.redraw.assert_called_once_with()

    async def test_a_comparison_that_ended_before_the_page_was_drawn_is_not_news(self) -> None:
        """Else a page that stays uncounted would draw itself again for ever."""
        self.jobs = [self._job("done", finished=time.time() - 3600)]

        await self._look()

        self.redraw.assert_not_called()

    async def test_another_kind_of_job_is_not_a_comparison(self) -> None:
        self.jobs = [self._job("done", finished=time.time() + 5, kind="library.scan")]

        await self._look()

        self.redraw.assert_not_called()

    async def test_a_jobs_list_that_cannot_be_read_is_left_for_the_next_look(self) -> None:
        self.library.jobs.side_effect = OSError("down")

        await self._look()

        self.redraw.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Community: the lists extensions declare, drawn by core."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

from nicegui import ui

from common.extensions import host
from common.i18n import t
from manager import page
from manager.extensions import ext_lists
from manager.kit import busy, grid, renderers, verbs, views
from manager.kit.api import ApiError
from manager.kit.data import read_state

DECLARED: dict[str, Any] = {"key": "tables", "title": "Site", "area": "community",
            "base": "/community/tables",
            "columns": [{"field": "vps_id", "header": "Table", "kind": "game", "keys": "vps",
                         "name_from": "name", "under": ["maker", "year"], "picture": "",
                         "icon": ""},
                        {"field": "plays", "header": "Plays", "kind": "number"},
                        {"field": "last", "header": "Last", "kind": "date"}],
            "views": [{"key": "plays", "name": "Most played", "columns": ["vps_id", "plays"],
                       "sort": [{"field": "plays", "desc": True}], "help": ""}],
            "relation": {"field": "vps_id", "keys": "vps_entry"}}
LOADED: dict[str, Any] = {"name": "site", "state": "loaded", "lists": [DECLARED]}


def _released() -> dict:
    """DECLARED, its first column a table."""
    first, *rest = DECLARED["columns"]
    return {**DECLARED, "columns": [{**first, "kind": "table"}, *rest],
            "relation": {"field": "vps_id", "keys": "vps_release"}}


class TheNav(unittest.TestCase):
    def test_a_running_extension_s_list_sits_under_community(self) -> None:
        items = ext_lists.nav_items([LOADED, {**LOADED, "name": "off", "state": "failed"}])
        groups = dict(page.nav_for(None, items))

        self.assertEqual(["community:site:tables"],
                         [one[0] for one in groups[page.NAV_COMMUNITY]])

    def test_a_list_is_drawn_with_its_icon_or_the_extension_icon(self) -> None:
        items = ext_lists.nav_items([{**LOADED, "lists": [
            {**DECLARED, "icon": "leaderboard"}, {**DECLARED, "key": "plain"}]}])

        self.assertEqual(["leaderboard", ext_lists.ICON],
                         [one[2] for one in items["community"]])

    def test_community_sits_between_frontend_and_system(self) -> None:
        parents = [parent for parent, _items in page.NAV_GROUPS]

        self.assertEqual([page.NAV_FRONTEND, page.NAV_COMMUNITY, page.NAV_SYSTEM],
                         parents[-3:])

    def test_with_no_list_there_is_no_community(self) -> None:
        self.assertNotIn(page.NAV_COMMUNITY, dict(page.nav_for(None)))

    def test_a_list_in_system_sits_before_about(self) -> None:
        system = {**DECLARED, "area": "system"}
        items = ext_lists.nav_items([{**LOADED, "lists": [system]}])
        keys = [one[0] for one in dict(page.nav_for(None, items))[page.NAV_SYSTEM]]

        self.assertEqual(["community:site:tables", "about"], keys[-2:])

    def test_a_list_in_library_follows_core_s_own_and_is_held_to_its_feature(self) -> None:
        library = {**DECLARED, "area": "library"}
        items = ext_lists.nav_items([{**LOADED, "lists": [library]}])

        shown = [one[0] for one in dict(page.nav_for(None, items))[page.NAV_PARENT]]
        bare = [one[0] for one in dict(page.nav_for(["frontend"], items)).get(
            page.NAV_PARENT, ())]

        self.assertEqual("community:site:tables", shown[-1])
        self.assertNotIn("community:site:tables", bare)

    def test_every_page_core_names_is_a_title_a_list_may_not_take(self) -> None:
        named = {key for _parent, items in page.NAV_GROUPS for key, *_rest in items}
        named |= {parent[0] for parent, _items in page.NAV_GROUPS if parent}

        self.assertLessEqual(named, set(host.CORE_SECTIONS))

    def test_a_view_finds_its_list(self) -> None:
        self.assertEqual((LOADED, DECLARED),
                         ext_lists.find("community:site:tables", [LOADED]))


class RankedOrders(unittest.TestCase):
    RANKED = {**DECLARED, "views": [{**DECLARED["views"][0], "ranks": True},
                                    {"key": "all", "name": "All", "columns": ["vps_id"],
                                     "sort": [], "help": ""}]}

    def test_each_ranked_view_is_named_for_its_page_and_itself(self) -> None:
        offered = ext_lists.ranked_orders([{**LOADED, "lists": [self.RANKED]},
                                           {**LOADED, "name": "off", "state": "failed",
                                            "lists": [self.RANKED]}])

        self.assertEqual({"site/tables/plays": "Site: Most played"}, offered)

    def test_a_list_that_relates_to_nothing_offers_no_order(self) -> None:
        loose = {**self.RANKED, "relation": None}

        self.assertEqual({}, ext_lists.ranked_orders([{**LOADED, "lists": [loose]}]))

    def test_the_smart_collection_in_a_view_s_order_is_the_one_it_opens(self) -> None:
        collections = [{"name": "Mine", "type": "manual", "order_by": "site/tables/plays"},
                       {"name": "Titles", "type": "filter", "order_by": "title"},
                       {"name": "Site: Most played", "type": "filter",
                        "order_by": "site/tables/plays"}]

        self.assertEqual("Site: Most played",
                         ext_lists.collection_ordered_by(collections, "site/tables/plays"))
        self.assertEqual("", ext_lists.collection_ordered_by(collections[:2],
                                                              "site/tables/plays"))

    def test_a_stored_order_no_running_extension_offers_names_the_extension(self) -> None:
        self.assertEqual("Site, not running",
                         ext_lists.ranked_label({"extension": "site", "display_name": "Site",
                                                 "offered": False}))


class TheGrid(unittest.TestCase):
    def test_the_first_column_is_scanned_by_and_in_library_is_the_second(self) -> None:
        shown = ext_lists.columns(DECLARED)

        self.assertEqual(["vps_id", ext_lists.HELD, "plays", "last"],
                         [one["field"] for one in shown])
        self.assertIn("manager-cell-identifier", shown[0]["cellClass"])
        self.assertEqual("numericColumn", shown[2]["type"])

    def test_a_view_keeps_its_sort_and_yours_keeps_only_what_is_held(self) -> None:
        presets = ext_lists.presets(DECLARED)

        self.assertEqual(({"colId": "plays", "sort": "desc", "sortIndex": 0},),
                         presets["plays"].sort)
        self.assertEqual({ext_lists.HELD: {"values": [True]}},
                         presets["manager.community.yours"].filters)

    def test_yours_is_the_first_view_and_sorts_as_the_next_one_does(self) -> None:
        presets = ext_lists.presets(DECLARED)

        self.assertEqual("manager.community.yours", next(iter(presets)))
        self.assertEqual(presets["plays"].sort, presets["manager.community.yours"].sort)
        self.assertEqual(presets["plays"].columns, presets["manager.community.yours"].columns)

    def test_a_view_is_kept_by_its_key_and_shown_by_the_name_it_was_sent(self) -> None:
        (_yours, plays) = views.builtins(ext_lists.presets(DECLARED))

        self.assertEqual((views.builtin_id("plays"), "Most played"), (plays.id, plays.name))

    def test_a_held_row_links_its_name_to_the_game(self) -> None:
        rows = ext_lists.rows([{"name": "AFM", "vps_id": "vps-afm", "last": ""},
                               {"name": "TAF", "vps_id": "vps-taf", "last": ""}], DECLARED,
                              {"vps-afm": {"game_id": "afm", "table_id": "",
                                           "name": "Attack from Mars"}})

        self.assertEqual([(True, "/manager?view=games&game=afm"), (False, "")],
                         [(one[ext_lists.HELD], one["held_href"]) for one in rows])
        self.assertIn("last_ago", rows[0])

    def test_a_held_release_links_to_its_table(self) -> None:
        released = _released()
        (row,) = ext_lists.rows([{"name": "AFM", "vps_id": "rel-1", "last": ""}], released,
                                {"rel-1": {"game_id": "afm", "table_id": "t1", "name": "AFM"}})

        self.assertEqual("/manager?view=tables&game=afm&table=t1", row["held_href"])

    def test_another_version_says_which_and_links_to_the_release(self) -> None:
        released = _released()
        (row,) = ext_lists.rows(
            [{"name": "AFM", "maker": "Bally", "vps_id": "rel-2", "last": ""}], released, {},
            {"rel-2": {"game_id": "afm", "table_id": "t1", "name": "AFM", "version": "1.2",
                       "url": "https://example.test/afm"}})

        self.assertEqual((False, "https://example.test/afm"),
                         (row[ext_lists.HELD], row["held_href"]))
        self.assertEqual("Bally · Different version - you have 1.2", row[ext_lists.UNDER])

    TABLES = [{"id": "t1", "game_id": "afm", "game": "Attack from Mars",
               "manufacturer": "Bally", "year": 1995, "version": "1.2", "authors": ["Ann"],
               "media_kind": "wheel", "media_version": "v1", "default": True}]
    HELD_AFM = {"vps-afm": {"game_id": "afm", "table_id": "t1", "name": "AFM"}}

    def test_a_held_game_is_drawn_as_the_games_grid_draws_it(self) -> None:
        (row,) = ext_lists.rows([{"name": "AFM", "vps_id": "vps-afm", "last": ""}],
                                DECLARED, self.HELD_AFM, tables=self.TABLES, art=True)

        self.assertEqual(("Attack from Mars", "Bally 1995", True),
                         (row["vps_id"], row[ext_lists.UNDER], bool(row["art"])))

    def test_a_game_the_library_lacks_takes_vpsdb_s_words_and_then_the_row_s(self) -> None:
        rows = ext_lists.rows(
            [{"name": "Own", "vps_id": "a", "last": ""}, {"name": "Own", "vps_id": "b",
                                                          "last": ""}],
            DECLARED, {}, known={"a": {"name": "VPS name", "manufacturer": "Williams",
                                  "year": "1993"}})

        self.assertEqual([("VPS name", "Williams 1993", ""), ("Own", "", "")],
                         [(one["vps_id"], one[ext_lists.UNDER], one["held_href"])
                          for one in rows])

    def test_the_frame_shows_the_row_s_picture_where_the_library_has_none(self) -> None:
        declared = {**DECLARED, "columns": [{**DECLARED["columns"][0], "picture": "image"},
                                            *DECLARED["columns"][1:]]}
        rows = ext_lists.rows(
            [{"vps_id": "a", "image": "/api/x.png"}, {"vps_id": "vps-afm", "image": "/y.png"}],
            declared, self.HELD_AFM, tables=self.TABLES, art=True)

        self.assertEqual("/api/x.png", rows[0]["art"])
        self.assertIn("afm", rows[1]["art"])

    def test_no_art_field_is_written_where_the_lists_draw_none(self) -> None:
        (row,) = ext_lists.rows([{"vps_id": "vps-afm"}], DECLARED, self.HELD_AFM,
                                tables=self.TABLES)

        self.assertNotIn("art", row)

    def test_a_later_game_column_has_a_line_and_a_link_and_no_art(self) -> None:
        declared = {**DECLARED, "columns": [*DECLARED["columns"],
                                            {"field": "other", "header": "Other",
                                             "kind": "game", "keys": "vps", "name_from": "",
                                             "under": [], "picture": "", "icon": ""}]}
        (row,) = ext_lists.rows(
            [{"vps_id": "a", "other": "vps-afm"}], declared, {}, art=True,
            later={"other": ext_lists.Lookup(self.HELD_AFM, {}, {})}, tables=self.TABLES)

        self.assertEqual(("Attack from Mars", "Bally 1995", "/manager?view=games&game=afm"),
                         (row["other"], row["other_line"], row["other_href"]))
        later = ext_lists.columns(declared)[-1]
        self.assertIn("d['other_line']", later[":cellRenderer"])
        self.assertNotIn("d['art']", later[":cellRenderer"])

    CELLS = {**DECLARED, "columns": [
        DECLARED["columns"][0],
        {"field": "status", "header": "Status", "kind": "state",
         "states": {"live": {"tier": "on", "label": "Live", "why": "Taking requests"},
                    "late": {"icon": "schedule", "label": "Late", "why": ""},
                    "gone": {"label": "Gone", "why": ""}}},
        {"field": "tags", "header": "Tags", "kind": "chips"},
        {"field": "page", "header": "Page", "kind": "link", "name_from": "title"},
        {"field": "art", "header": "Art", "kind": "picture"}]}

    def _cells(self) -> dict[str, dict]:
        return {one["field"]: one for one in ext_lists.columns(self.CELLS)}

    def test_a_state_is_drawn_by_name_with_the_looks_the_extension_gave_it(self) -> None:
        shown = self._cells()["status"]

        self.assertEqual("state", shown["cellRendererParams"]["drawn"])
        self.assertEqual(
            {"live": {"label": "Live", "why": "Live - Taking requests", "tone": "on",
                      "glyph": "", "icon": ""},
             "late": {"label": "Late", "why": "Late", "tone": "", "glyph": "",
                      "icon": "schedule"},
             "gone": {"label": "Gone", "why": "Gone", "tone": "", "glyph": "", "icon": ""}},
            shown["cellRendererParams"]["states"])

    def test_a_state_an_extension_does_not_list_is_drawn_plain_there_only(self) -> None:
        self.assertTrue(self._cells()["status"]["cellRendererParams"]["plain"])
        self.assertIn("params.plain", renderers.STATE.js)

    def test_a_state_is_filtered_by_its_value_and_read_by_its_word(self) -> None:
        shown = self._cells()["status"]

        self.assertEqual(["live", "late", "gone"],
                         [one["value"] for one in shown["filterParams"]["choices"]])
        self.assertEqual(["Live", "Late", "Gone"],
                         [one["label"] for one in shown["filterParams"]["choices"]])
        self.assertIn('"live": "Live"', shown[":valueFormatter"])
        self.assertEqual(shown[":valueFormatter"], shown[":getQuickFilterText"])

    def test_chips_are_a_list_column_with_no_looks(self) -> None:
        shown = self._cells()["tags"]

        self.assertEqual("chips", shown["cellRendererParams"]["drawn"])
        self.assertEqual("", shown["cellRendererParams"]["looks"])
        self.assertIn(":filter", shown)

    def test_a_link_reads_as_the_row_field_it_says_or_as_its_header(self) -> None:
        shown = self._cells()["page"]
        bare = {**self.CELLS, "columns": [
            self.CELLS["columns"][0],
            {"field": "page", "header": "Page", "kind": "link"}]}
        plain = ext_lists.columns(bare)[-1]

        self.assertEqual(("link", "title", "Page"),
                         (shown["cellRendererParams"]["drawn"],
                          shown["cellRendererParams"]["says"],
                          shown["cellRendererParams"]["header"]))
        self.assertEqual(("", "Page"), (plain["cellRendererParams"]["says"],
                                        plain["cellRendererParams"]["header"]))

    def test_a_picture_is_drawn_and_has_nothing_to_sort_or_filter(self) -> None:
        shown = self._cells()["art"]

        self.assertEqual("image", shown["cellRendererParams"]["drawn"])
        self.assertFalse(shown["sortable"])
        self.assertFalse(shown["filter"])

    def test_the_fields_under_the_name_make_its_second_line(self) -> None:
        (row,) = ext_lists.rows([{"name": "AFM", "maker": "Bally", "year": 1995,
                                  "vps_id": "", "last": ""}], DECLARED, {})

        self.assertEqual("Bally 1995", row[ext_lists.UNDER])

    def test_in_library_is_not_a_column_of_any_view(self) -> None:
        for name, preset in ext_lists.presets(DECLARED).items():
            with self.subTest(view=name):
                self.assertNotIn(ext_lists.HELD, preset.columns)


VERBED = {**DECLARED, "verbs": [
    {"key": "open", "link": "page", "label": "Open", "tip": "", "needs_reason": ""},
    {"key": "flag", "label": "Flag", "needs": "can_flag", "needs_reason": "Already flagged",
     "danger": False, "one": False, "tip": "Mark it"},
    {"key": "drop", "label": "Drop", "danger": True, "one": True, "needs": "",
     "needs_reason": "", "tip": ""}]}


class TheVerbs(unittest.TestCase):
    def entries(self, chosen: list[dict]) -> dict[str, Any]:
        pressed: list = []
        found = ext_lists.verb_entries(VERBED, chosen, lambda verb, fit: pressed.append(fit))
        self.pressed = pressed
        return {one.label: one for one in found}

    def test_a_row_with_no_id_is_drawn_under_a_name_of_its_own_and_one_with_an_id_keeps_it(
            self) -> None:
        shown = ext_lists.rows([{"name": "A"}, {"id": 7, "name": "B"}, {"id": "", "name": "C"}],
                               VERBED, {})

        self.assertEqual(["7"], [one["id"] for one in shown if "id" in one
                                 and not one["id"].startswith(ext_lists.UNNAMED)])
        self.assertEqual(2, sum(one["id"].startswith(ext_lists.UNNAMED) for one in shown))

    def test_a_row_with_no_id_has_no_menu_and_cannot_be_ticked(self) -> None:
        shown = ext_lists.rows([{"name": "A"}, {"id": "b", "name": "B"}], VERBED, {})

        self.assertEqual([(True, True), (None, None)],
                         [(one.get(grid.NO_MENU), one.get(grid.NOT_SELECTABLE))
                          for one in shown])

    def test_a_link_goes_to_the_rows_own_address_and_is_for_one_row(self) -> None:
        entries = self.entries([{"id": "a", "page": "https://example.test/a"}])

        self.assertEqual(("https://example.test/a", True),
                         (entries["Open"].to, entries["Open"].one))

    def test_a_link_with_no_address_says_so_and_a_link_over_several_rows_is_dimmed(
            self) -> None:
        none = self.entries([{"id": "a", "page": ""}])["Open"]
        several = self.entries([{"id": "a", "page": "https://example.test/a"},
                                {"id": "b", "page": "https://example.test/b"}])["Open"]

        self.assertEqual(("", "No address"), (none.to, none.hint))
        self.assertEqual(("", True), (several.to, several.one))

    def test_a_link_whose_value_is_not_a_web_address_opens_nothing(self) -> None:
        for value in ("javascript:alert(1)", "ftp://example.test/a", "example.test/a"):
            entry = self.entries([{"id": "a", "page": value}])["Open"]

            self.assertEqual(("", "No address"), (entry.to, entry.hint), value)

    def test_a_verb_that_needs_a_field_acts_on_the_rows_it_is_true_on_only(self) -> None:
        entries = self.entries([{"id": "a", "can_flag": True}, {"id": "b", "can_flag": False}])

        entries["Flag"].run()

        self.assertEqual([[{"id": "a", "can_flag": True}]], self.pressed)

    def test_a_verb_that_needs_a_field_true_on_none_is_dimmed_with_the_extensions_reason(
            self) -> None:
        flag = self.entries([{"id": "b", "can_flag": False}])["Flag"]

        self.assertEqual((None, "Already flagged"), (flag.run, flag.hint))

    def test_a_danger_and_a_one_at_a_time_carry_through_to_the_menu(self) -> None:
        drop = self.entries([{"id": "a"}])["Drop"]

        self.assertEqual((True, True), (drop.danger, drop.one))


class PressingAVerb(unittest.IsolatedAsyncioTestCase):
    """`run_verb` is called under the page; the test calls the handler it wraps."""

    async def press(self, verb: dict, *, asked: bool = True, answer: Any = None,
                    fails: Exception | None = None) -> tuple[Mock, Mock, Mock, Mock, Mock]:
        again, post, notify, navigate = Mock(), Mock(return_value=answer), Mock(), Mock()
        if fails:
            post.side_effect = fails

        async def io(call: Callable, *args: Any) -> Any:
            return call(*args)

        ask = Mock(side_effect=lambda *a, **k: _done(asked))

        async def renewed() -> None:
            again()

        with patch.object(ext_lists.confirm, "ask", ask), \
                patch.object(ext_lists.offload, "io", io), \
                patch.object(ext_lists, "ApiClient", return_value=Mock(ext_post=post)), \
                patch.object(ext_lists.ui, "notify", notify), \
                patch.object(ext_lists.link_tab, "open_link", navigate):
            await cast(Any, ext_lists.run_verb).__wrapped__(
                {"name": "site"}, VERBED, verb, [{"id": "a"}, {"id": 2}], renewed)
        return ask, post, notify, navigate, again

    async def test_a_danger_asks_first_in_its_own_label_and_does_nothing_when_refused(
            self) -> None:
        drop = VERBED["verbs"][2]

        ask, post, _notify, _navigate, again = await self.press(drop, asked=False)

        self.assertEqual(("Drop", verbs.ACCEPT),
                         (ask.call_args.kwargs["confirm"], ask.call_args.kwargs["icon"]))
        post.assert_not_called()
        again.assert_not_called()

    async def test_a_confirmed_danger_posts_the_ids_and_reads_the_list_again(self) -> None:
        drop = VERBED["verbs"][2]

        _ask, post, _notify, _navigate, again = await self.press(drop, answer={})

        post.assert_called_once_with("/ext/site/community/tables/verbs/drop", {"ids": ["a", "2"]})
        again.assert_called_once()

    async def test_a_verb_that_is_not_a_danger_does_not_ask(self) -> None:
        ask, post, *_ = await self.press(VERBED["verbs"][1], answer={})

        ask.assert_not_called()
        post.assert_called_once()

    async def test_the_address_and_message_a_verb_answers_are_opened_and_said(self) -> None:
        _ask, _post, notify, navigate, again = await self.press(
            VERBED["verbs"][1], answer={"message": "Flagged 2", "url": "https://example.test/x"})

        navigate.assert_called_once_with("https://example.test/x")
        notify.assert_called_once_with("Flagged 2")
        again.assert_called_once()

    async def test_a_verb_that_fails_says_why_and_still_reads_the_list_again(self) -> None:
        _ask, _post, notify, navigate, again = await self.press(
            VERBED["verbs"][1], fails=ApiError("refused"))

        self.assertEqual("negative", notify.call_args.kwargs["type"])
        navigate.assert_not_called()
        again.assert_called_once()


async def _done(value: Any) -> Any:
    return value


def _answering(*rows: dict) -> Callable[[], dict]:
    return lambda: {"rows": list(rows)}


def _down() -> dict:
    raise ApiError("https://site.example did not answer")


NOTED = {"name": "site",
         "actions": [{"key": "note", "label": "Add a note", "base": "/actions/note"}]}


class OpeningADialogOverRows(unittest.IsolatedAsyncioTestCase):
    """A verb or an act that names a dialog opens it through `ext_action.open_action`."""

    async def open(self, answers: list[Any], ids: list[str], *, each: bool = False,
                   action: str = "note", who: dict[str, str] | None = None) -> tuple[bool, Mock]:
        opened = Mock(side_effect=[_done(one) for one in answers])
        with patch.object(ext_lists.ext_action, "open_action", opened):
            ran = await cast(Any, ext_lists.open_over).__wrapped__(
                NOTED, action, ids, each=each, who=who)
        return ran, opened

    async def test_one_dialog_covers_every_row_it_was_opened_over(self) -> None:
        ran, opened = await self.open([True], ["a", "b"])

        self.assertTrue(ran)
        opened.assert_called_once_with("site", NOTED["actions"][0], rows=["a", "b"], who=None)

    async def test_the_game_it_is_opened_for_goes_to_every_dialog_of_a_walk(self) -> None:
        who = {"game_id": "", "vps_id": "abcdefghij", "name": "Taxi"}

        _, opened = await self.open([True, True], ["a", "b"], each=True, who=who)

        self.assertEqual([who, who], [call.kwargs["who"] for call in opened.call_args_list])

    async def test_a_cancelled_dialog_ran_nothing(self) -> None:
        ran, _ = await self.open([False], ["a"])

        self.assertFalse(ran)

    async def test_each_walks_the_rows_one_dialog_at_a_time_saying_where_it_is(self) -> None:
        ran, opened = await self.open([True, ext_lists.walks.SKIPPED, True], ["a", "b", "c"],
                                      each=True)

        self.assertTrue(ran)
        self.assertEqual([["a"], ["b"], ["c"]],
                         [call.kwargs["rows"] for call in opened.call_args_list])
        self.assertEqual(["1 of 3", "2 of 3", "3 of 3"],
                         [call.kwargs["place"] for call in opened.call_args_list])
        self.assertTrue(all(call.kwargs["walking"] for call in opened.call_args_list))

    async def test_stop_ends_the_walk_where_it_is(self) -> None:
        ran, opened = await self.open([True, ext_lists.walks.STOPPED], ["a", "b", "c"],
                                      each=True)

        self.assertTrue(ran)
        self.assertEqual(2, opened.call_count)

    async def test_a_walk_that_skipped_every_row_ran_nothing(self) -> None:
        ran, _ = await self.open([ext_lists.walks.SKIPPED, ext_lists.walks.SKIPPED],
                                 ["a", "b"], each=True)

        self.assertFalse(ran)

    async def test_one_row_says_no_position(self) -> None:
        _, opened = await self.open([True], ["a"], each=True)

        self.assertEqual("", opened.call_args.kwargs["place"])

    async def test_a_dialog_that_cannot_open_mid_walk_is_said_and_the_rows_before_it_count(
            self) -> None:
        notify = Mock()
        opened = Mock(side_effect=[_done(True), ApiError("it went"), _done(True)])
        with patch.object(ext_lists.ext_action, "open_action", opened), \
                patch.object(ext_lists.ui, "notify", notify):
            ran = await cast(Any, ext_lists.open_over).__wrapped__(NOTED, "note", ["a", "b", "c"],
                                                        each=True)

        self.assertTrue(ran)
        self.assertEqual(3, opened.call_count)
        self.assertEqual("negative", notify.call_args.kwargs["type"])

    async def test_a_dialog_the_extension_does_not_have_says_so_and_opens_nothing(self) -> None:
        notify = Mock()
        with patch.object(ext_lists.ui, "notify", notify):
            ran, opened = await self.open([], ["a"], action="missing")

        self.assertFalse(ran)
        opened.assert_not_called()
        self.assertEqual("negative", notify.call_args.kwargs["type"])

    async def test_a_verb_that_names_a_dialog_reads_the_list_again_once_it_ran(self) -> None:
        again = Mock()

        async def renewed() -> None:
            again()

        verb = {"key": "note", "action": "note", "each": True}
        opened = Mock(side_effect=[_done(True), _done(False)])
        with patch.object(ext_lists, "open_over", opened):
            await cast(Any, ext_lists.run_verb).__wrapped__(
                NOTED, VERBED, verb, [{"id": "a"}, {"id": 2}], renewed)
            await cast(Any, ext_lists.run_verb).__wrapped__(
                NOTED, VERBED, verb, [{"id": "a"}], renewed)

        again.assert_called_once()
        opened.assert_any_call(NOTED, "note", ["a", "2"], each=True)

    async def test_an_act_that_names_a_dialog_opens_it_instead_of_posting(self) -> None:
        post = Mock()
        again = Mock()

        async def renewed() -> None:
            again()

        opened = Mock(side_effect=[_done(True)])
        with patch.object(ext_lists, "open_over", opened), \
                patch.object(ext_lists, "ApiClient", return_value=Mock(ext_post=post)):
            await cast(Any, ext_lists.run_act).__wrapped__(
                NOTED, "/community/tables/about", {"key": "open", "action": "note"}, renewed)

        opened.assert_called_once_with(NOTED, "note", [])
        post.assert_not_called()
        again.assert_called_once()


class TheLastGoodRead(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.kept = Path(folder.name)
        kept = patch("common.config.paths.COMMUNITY_KEPT_DIR", self.kept)
        kept.start()
        self.addCleanup(kept.stop)

    def test_a_fresh_read_is_kept_and_said_to_be_now(self) -> None:
        said = ext_lists.read("site", "tables", _answering({"name": "AFM"}))

        self.assertEqual(([{"name": "AFM"}], False, ""),
                         (said["rows"], said["stale"], said["error"]))
        self.assertEqual(["AFM"], [one["name"] for one in
                                   ext_lists.kept("site", "tables")["rows"]])
        self.assertTrue(said["read_at"])

    def test_an_outage_answers_with_the_last_good_list_said_to_be_stale(self) -> None:
        good = ext_lists.read("site", "tables", _answering({"name": "AFM"}))

        said = ext_lists.read("site", "tables", _down)

        self.assertEqual(([{"name": "AFM"}], True, good["read_at"]),
                         (said["rows"], said["stale"], said["read_at"]))
        self.assertIn("did not answer", said["error"])
        self.assertIn("Last good read", read_state(said))

    def test_an_outage_with_nothing_kept_has_no_list_to_show(self) -> None:
        said = ext_lists.read("site", "tables", _down)

        self.assertEqual((None, True), (said["rows"], said["stale"]))
        self.assertIsNone(ext_lists.kept("site", "tables")["rows"])

    def test_with_nothing_kept_the_answer_is_still_an_answer(self) -> None:
        """The page reads it through `offload.io`, which takes None for a shutdown."""
        self.assertEqual({"rows": None, "read_at": "", "stale": False, "error": ""},
                         ext_lists.kept("site", "tables"))

    def test_a_later_good_read_replaces_the_kept_one(self) -> None:
        ext_lists.read("site", "tables", _answering({"name": "AFM"}))
        ext_lists.read("site", "tables", _answering({"name": "TAF"}, {"name": "MM"}))

        self.assertEqual(["TAF", "MM"], [one["name"] for one in
                                         ext_lists.kept("site", "tables")["rows"]])

    def test_each_list_is_kept_apart_whatever_its_key_holds(self) -> None:
        ext_lists.read("site", "tables", _answering({"name": "AFM"}))
        ext_lists.read("site", "../tables", _answering({"name": "TAF"}))

        self.assertEqual((["AFM"], ["TAF"]),
                         tuple([one["name"] for one in ext_lists.kept("site", key)["rows"]]
                               for key in ("tables", "../tables")))
        self.assertEqual(["site"], [one.name for one in self.kept.iterdir()])


PLAIN = {"key": "tables", "title": "Site", "base": "/community/tables",
         "columns": [{"field": "name", "header": "Table", "kind": "text"}]}
KEPT = {"rows": [{"name": "AFM"}], "read_at": "2026-09-25T10:00:00+00:00", "stale": False,
        "error": ""}
SWITCHED_OFF = {**LOADED, "state": host.OFF, "reason": "Switched off",
                "reason_key": host.SWITCHED_OFF}


async def _here(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


def _library() -> Mock:
    """A library that holds nothing and draws no pictures."""
    return Mock(table_rows=Mock(return_value=[]), thumbnail_look=Mock(return_value=None),
                owned_as=Mock(return_value={}))


class ItsExtensionNotRunning(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Outside the test's task, which has no page to draw into.
        self.body = ui.column()

    async def fill(self, now: dict, kept: dict, *,
                   opened: bool = False) -> tuple[list[str], list[str], Mock]:
        """`_fill` drawn into the body, or the page `build` opens when `opened`."""
        self.held_when_read: list[bool] = []

        def reading(*_args: Any) -> dict:
            self.held_when_read.append(any(one.props.get("aria-busy")
                                           for one in self.body.descendants()))
            return kept

        read = self.enterContext(patch.object(ext_lists, "read", side_effect=reading))
        self.enterContext(patch.object(ext_lists.offload, "io", new=_here))
        self.enterContext(patch.object(ext_lists, "as_it_stands", return_value=now))
        self.enterContext(patch.object(ext_lists, "kept", return_value=kept))
        self.enterContext(patch("manager.library.games.view_control",
                                return_value=(Mock(), Mock(), Mock(), Mock())))
        # The grid talks to a browser, which a unit test does not have.
        self.enterContext(patch.object(ext_lists.grid, "build",
                                       return_value=Mock(is_deleted=False)))
        self.enterContext(patch.object(ext_lists.grid, "replace_rows"))
        if opened:
            with self.body:
                with patch.object(busy.ui, "timer") as timer:
                    ext_lists.build(LOADED, PLAIN, _library())
                await timer.call_args.args[1]()
        else:
            with self.body:
                await ext_lists._fill(LOADED, PLAIN, _library(), self.body)
        # A kept list is read again after it is drawn, in a task of its own.
        await asyncio.gather(*(asyncio.all_tasks() - {asyncio.current_task()}))
        drawn = list(self.body.descendants())
        return ([str(getattr(one, "text", "")) for one in drawn],
                [str(one.props.get("icon")) for one in drawn if isinstance(one, ui.button)],
                read)

    async def test_switched_off_it_shows_what_was_kept_and_offers_no_read(self) -> None:
        said, icons, read = await self.fill(SWITCHED_OFF, KEPT)

        read.assert_not_called()
        self.assertNotIn(verbs.REFRESH, icons)
        self.assertIn(t("word.off"), said)
        self.assertTrue(any(one.startswith("Last good read") for one in said))

    async def test_stopped_it_says_so_with_its_reason(self) -> None:
        said, icons, read = await self.fill(
            {**LOADED, "state": host.DISABLED, "reason": "It threw",
             "reason_key": "extension.reason.failed_serving"}, KEPT)

        read.assert_not_called()
        self.assertNotIn(verbs.REFRESH, icons)
        self.assertIn(t("manager.community.stopped"), said)
        self.assertIn("It threw", said)

    async def test_with_nothing_kept_it_says_the_extension_is_not_running(self) -> None:
        said, _icons, read = await self.fill(SWITCHED_OFF, {**KEPT, "rows": None})

        read.assert_not_called()
        self.assertIn(t("manager.community.not_running", name="site",
                        reason="Switched off"), said)

    async def test_running_it_reads_again_and_offers_refresh(self) -> None:
        _said, icons, read = await self.fill(LOADED, KEPT)

        read.assert_called_once()
        self.assertIn(verbs.REFRESH, icons)

    async def test_with_nothing_kept_the_read_is_part_of_opening_the_page(self) -> None:
        await self.fill(LOADED, {**KEPT, "rows": None}, opened=True)

        self.assertEqual([True], self.held_when_read)

    async def test_a_kept_list_is_read_again_once_the_page_is_open(self) -> None:
        await self.fill(LOADED, KEPT, opened=True)

        self.assertEqual([False], self.held_when_read)


ABOUT = {**PLAIN, "about": "/community/tables/about"}
STANDING: dict[str, Any] = {"status": {"text": "Not sharing - Share is off", "to": "players"},
            "acts": [{"key": "send_now", "label": "Send Now"},
                     {"key": "site", "label": "Open Site", "url": "https://site.example"}]}


class WhatTheListSays(unittest.TestCase):
    def test_a_line_is_words_or_words_with_a_place(self) -> None:
        self.assertEqual({"text": "Ends Sunday", "detail": "", "to": ""},
                         ext_lists.status_of({"status": "Ends Sunday"}))
        self.assertEqual({"text": "Not sharing", "detail": "Why", "to": "players"},
                         ext_lists.status_of({"status": {"text": "Not sharing",
                                                         "detail": "Why",
                                                         "to": "players"}}))

    def test_a_place_core_does_not_know_draws_no_link(self) -> None:
        self.assertEqual("", ext_lists.status_of(
            {"status": {"text": "Go", "to": "https://site.example"}})["to"])

    def test_no_line_is_nothing_to_draw(self) -> None:
        self.assertEqual("", ext_lists.status_of({})["text"])

    def test_the_places_are_the_manager_s_own_addresses(self) -> None:
        self.assertEqual(("/manager?view=players", "/manager?view=settings&page=extensions.site"),
                         (ext_lists.PLACES["players"]("site"),
                          ext_lists.PLACES["settings"]("site")))

    def test_the_menu_is_its_acts_in_order_then_settings(self) -> None:
        pressed: list[dict] = []
        found = ext_lists.acts({**LOADED, "surfaces": {"settings": "/settings"}},
                               STANDING, pressed.append)

        self.assertEqual([("Send Now", ""), ("Open Site", "https://site.example"),
                          (t("manager.community.settings"),
                           "/manager?view=settings&page=extensions.site")],
                         [(one.label, one.to) for one in found])
        assert found[0].run is not None
        found[0].run()
        self.assertEqual([STANDING["acts"][0]], pressed)
        self.assertIsNone(found[1].run)

    def test_settings_only_where_the_extension_has_some(self) -> None:
        self.assertEqual(["Send Now", "Open Site"],
                         [one.label for one in ext_lists.acts(LOADED, STANDING, print)])

    def test_an_address_that_is_not_the_web_is_not_drawn(self) -> None:
        said = {"acts": [{"key": "x", "label": "X", "url": "javascript:alert(1)"}]}

        self.assertEqual([], ext_lists.acts(LOADED, said, print))
        self.assertEqual("", ext_lists.web("javascript:alert(1)"))


class TheLineAndItsMenu(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.body = ui.column()
        self.subject = ui.row()

    async def fill(self, now: dict, kept: dict, standing: dict) -> tuple[list[str], Mock]:
        asked = self.enterContext(patch.object(
            ext_lists, "standing", side_effect=lambda _name, about: standing if about else {}))
        self.enterContext(patch.object(ext_lists, "read", return_value=kept))
        self.enterContext(patch.object(ext_lists.offload, "io", new=_here))
        self.enterContext(patch.object(ext_lists, "as_it_stands", return_value=now))
        self.enterContext(patch.object(ext_lists, "kept", return_value=kept))
        self.enterContext(patch("manager.library.games.view_control",
                                return_value=(Mock(), Mock(), Mock(), Mock())))
        self.enterContext(patch.object(ext_lists.grid, "build",
                                       return_value=Mock(is_deleted=False)))
        self.enterContext(patch.object(ext_lists.grid, "replace_rows"))
        with self.body:
            await ext_lists._fill(LOADED, ABOUT, _library(), self.body, self.subject)
        await asyncio.gather(*(asyncio.all_tasks() - {asyncio.current_task()}))
        return [str(getattr(one, "text", "")) for one in self.body.descendants()], asked

    def menu(self) -> list[str]:
        return [str(one.props.get("icon")) for one in self.subject.descendants()
                if isinstance(one, ui.button)]

    async def test_the_line_sits_in_the_bar_and_links_to_its_place(self) -> None:
        _said, asked = await self.fill(LOADED, KEPT, STANDING)

        asked.assert_called_with("site", "/community/tables/about")
        (link,) = [one for one in self.body.descendants() if isinstance(one, ui.link)]
        self.assertEqual(("Not sharing - Share is off", "/manager?view=players"),
                         (link.text, link.props.get("href")))
        self.assertEqual([verbs.MORE], self.menu())

    async def test_a_list_that_cannot_be_read_still_says_its_line(self) -> None:
        said, _asked = await self.fill(LOADED, {**KEPT, "rows": None, "error": "down"},
                                       {"status": "Sharing as OWN", "acts": []})

        self.assertIn(t("manager.community.read_failed", name="site"), said)
        self.assertIn("Sharing as OWN", said)
        self.assertEqual([], self.menu())

    async def test_a_stopped_extension_is_not_asked(self) -> None:
        _said, asked = await self.fill(SWITCHED_OFF, KEPT, STANDING)

        asked.assert_called_with("site", "")
        self.assertEqual([], self.menu())


class AsItStands(unittest.TestCase):
    def test_the_api_s_answer_wins_over_what_the_rail_read(self) -> None:
        with patch.object(ext_lists, "ApiClient") as client:
            client.return_value.extensions.return_value = [SWITCHED_OFF]

            self.assertEqual(SWITCHED_OFF, ext_lists.as_it_stands(LOADED))

    def test_when_the_api_cannot_say_it_is_taken_as_given(self) -> None:
        with patch.object(ext_lists, "ApiClient") as client:
            client.return_value.extensions.side_effect = ApiError("down")

            self.assertEqual(LOADED, ext_lists.as_it_stands(LOADED))


if __name__ == "__main__":
    unittest.main()

"""What the wheel shows when a collection is chosen.

The frontend had a membership engine of its own until this. It read a collection's game
ids and nothing else, so a member naming a table, an exclusion, a limit and a stored
order were all invisible on the surface a player actually uses - while REST answered
correctly for the same collection. These are the cases that told the two apart.
"""

from __future__ import annotations

import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.devices import device_registry
from common.games.collection_store import (
    BUILTIN_ALL,
    LAST_COLLECTION,
    CollectionStore,
    public_name,
)
from frontend import game_state, last_game
from frontend.api import API
from frontend.library_resolver import LibraryResolver
from tests.support.library import TempTree
from tests.support.library_loader import start_library_of


def _game(gid, title, tables, manufacturer="", default=""):
    return SimpleNamespace(
        game_dir_name=title,
        full_path_game=f"/games/{title}",
        full_path_vpx_file=f"/games/{title}/{title}.vpx",
        creation_time=0,
        pup_pack_exists=False, alt_color_exists=False, alt_sound_exists=False,
        meta_config={
            "Info": {"Title": title, "Manufacturer": manufacturer, "Year": "1995",
                     "Type": "SS", "Themes": []},
            "User": {"Rating": 0, "LastRun": 0, "StartCount": 0, "RunTime": 0},
            "vpinfe": {"game_id": gid, **({"default_table": default} if default else {})},
            "tables": tables,
        },
    )


def _table(tid, filename):
    return {"id": tid, "filename": filename}


def _ini() -> SimpleNamespace:
    parser = configparser.ConfigParser()
    parser.add_section("general")
    return SimpleNamespace(config=parser, save=lambda: None)


class _Library(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.collections = CollectionStore(str(self.root / "collections.json"))
        patcher = patch("frontend.library_resolver.get_collections_manager",
                        lambda: self.collections)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.mm = _game("mm", "Medieval Madness",
                        {"vpw": _table("vpw", "MM VPW.vpx"),
                         "jp": _table("jp", "MM JP.vpx")},
                        manufacturer="Williams", default="vpw")
        self.afm = _game("afm", "Attack from Mars", {"a1": _table("a1", "AFM.vpx")},
                         manufacturer="Bally")
        self.taf = _game("taf", "The Addams Family", {"t1": _table("t1", "TAF.vpx")},
                         manufacturer="Bally")
        self.games = [self.mm, self.afm, self.taf]

    def _api(self, games=None):
        api = API.__new__(API)
        api._ini_config = _ini()
        api.library = LibraryResolver(_ini(), games=list(
            self.games if games is None else games))
        return api

    def _rows(self, api):
        return [(entry.game.meta_config["vpinfe"]["game_id"], entry.table_id)
                for entry in api.entries]


class CollectionViewTests(_Library):
    def test_the_default_view_is_the_whole_library(self) -> None:
        """`builtin:all` is what an empty collection has always meant, and the name for
        it never leaves core."""
        api = self._api()

        self.assertEqual(api.current_collection, BUILTIN_ALL)
        self.assertEqual(public_name(api.current_collection), "")
        self.assertEqual(api.get_current_collection(), "None")
        self.assertEqual(self._rows(api),
                         [("taf", "t1"), ("afm", "a1"), ("mm", "vpw")])

    def test_a_collection_naming_two_tables_of_one_game_shows_both(self) -> None:
        """The case the wheel could not express: two rows, one game, in curated order."""
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="vpw")
        self.collections.add_member("Friday Night", "afm")
        self.collections.add_member("Friday Night", "mm", table_id="jp")

        api = self._api()
        game_state.apply_collection(api, "Friday Night")

        self.assertEqual(self._rows(api),
                         [("mm", "vpw"), ("afm", "a1"), ("mm", "jp")])

    def test_the_collection_shown_is_remembered_for_last_collection(self) -> None:
        self.collections.add_collection("Friday Night", ["mm"])
        api = self._api()
        api._ini_config.config.add_section("behavior")
        api._ini_config.config.set("behavior", "startup_collection", LAST_COLLECTION)

        game_state.apply_collection(api, "Friday Night")

        self.assertEqual(last_game.opening_collection(api._ini_config), "Friday Night")

    def test_leaving_the_collection_is_remembered_as_the_whole_library(self) -> None:
        self.collections.add_collection("Friday Night", ["mm"])
        for leave in (lambda api: game_state.apply_filters(api, manufacturer="Bally"),
                      lambda api: api.reset_filters()):
            with self.subTest():
                api = self._api()
                api._ini_config.config.add_section("behavior")
                api._ini_config.config.set("behavior", "startup_collection",
                                           LAST_COLLECTION)
                game_state.apply_collection(api, "Friday Night")

                leave(api)

                self.assertEqual(last_game.opening_collection(api._ini_config), "")

    def test_a_startup_collection_that_is_gone_opens_on_all_games(self) -> None:
        for opens_on, state in (("Ghost", ""), (LAST_COLLECTION, "Ghost")):
            with self.subTest(opens_on=opens_on):
                ini = _ini()
                ini.config.add_section("behavior")
                ini.config.set("behavior", "startup_collection", opens_on)
                device_registry.get_device_registry().set_self(
                    last_collection=state or None)
                library = LibraryResolver(ini, games=list(self.games))

                with self.assertLogs("vpinfe.frontend.api", level="WARNING") as logged:
                    api = API(ini, library=library)  # type: ignore[arg-type]

                self.assertEqual(api.current_collection, BUILTIN_ALL)
                self.assertEqual(len(api.entries), 3)
                self.assertEqual(len(logged.records), 1)
                self.assertIsNone(logged.records[0].exc_info)
                self.assertEqual(
                    device_registry.get_device_registry().self_get("last_collection", ""), "")

    def test_a_curated_order_is_held_and_is_not_one_of_the_theme_sorts(self) -> None:
        """`Manual` is what the sort UI reads back, and what stops a later refresh
        sorting the curator's list alphabetically."""
        self.collections.add_collection("Friday Night", ["mm", "taf"])
        self.collections.set_order("Friday Night", "manual")

        api = self._api()
        game_state.apply_collection(api, "Friday Night")

        self.assertEqual(api.current_sort, "manual",
                         "the collection's own token, not a sort name of its own")
        self.assertEqual(self._rows(api), [("mm", "vpw"), ("taf", "t1")],
                         "the curator's order, not the alphabetical one")

    def test_a_limit_caps_the_wheel(self) -> None:
        self.collections.add_collection("Short", ["mm", "afm", "taf"])
        self.collections.set_limit("Short", 2)

        api = self._api()
        game_state.apply_collection(api, "Short")

        self.assertEqual(self._rows(api), [("taf", "t1"), ("afm", "a1")])

    def test_an_exclusion_is_absent_from_the_wheel(self) -> None:
        self.collections.add_filter_collection("Bally", manufacturer="Bally")
        self.collections.exclude("Bally", "taf")

        api = self._api()
        game_state.apply_collection(api, "Bally")

        self.assertEqual(self._rows(api), [("afm", "a1")])

    def test_a_filter_control_makes_a_collection_out_of_the_library(self) -> None:
        """Not a narrowing of what was on screen: Friday Night holds no Addams Family,
        and filtering to Bally from inside it shows one."""
        self.collections.add_collection("Friday Night", ["mm", "afm"])

        api = self._api()
        game_state.apply_collection(api, "Friday Night")
        game_state.apply_filters(api, manufacturer="Bally")

        self.assertEqual(api.current_collection, BUILTIN_ALL)
        self.assertEqual(self._rows(api), [("taf", "t1"), ("afm", "a1")])

    def test_games_no_build_has_parsed_yet_each_get_a_row(self) -> None:
        """A folder that has never been scanned has no game id. Keyed on that, every one
        of them would collapse into a single row - which is a fresh install's whole
        library."""
        fresh = [_game("", "Alpha", {}), _game("", "Bravo", {})]

        api = self._api(fresh)

        self.assertEqual([entry.game.game_dir_name for entry in api.entries],
                         ["Alpha", "Bravo"])


class CabinetCollectionRowsTests(_Library):
    """What the cabinet's collection menu and core's picker are handed per collection."""

    def setUp(self) -> None:
        super().setUp()
        patcher = patch("common.games.collections_service.get_collections_manager",
                        lambda: self.collections)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _row(self, rows, name):
        return next(row for row in rows if row["name"] == name)

    def test_the_count_is_what_the_collection_shows_rules_and_limit_included(self) -> None:
        self.collections.add_filter_collection("Bally", manufacturer="Bally")
        self.collections.add_collection("Short", ["mm", "afm", "taf"])
        self.collections.set_limit("Short", 2)

        rows = self._api().get_collections_metadata()

        self.assertEqual(self._row(rows, "Bally")["table_count"], 2)
        self.assertEqual(self._row(rows, "Short")["table_count"], 2)

    def test_up_to_four_wheels_of_the_games_it_holds_skipping_games_with_none(self) -> None:
        more = [_game(f"g{n}", f"Game {n}", {f"t{n}": _table(f"t{n}", f"G{n}.vpx")})
                for n in range(5)]
        for game in more:
            game.wheel_image_path = f"{game.full_path_game}/medias/wheel.png"
        self.afm.wheel_image_path = "/games/Attack from Mars/medias/wheel.png"
        self.collections.add_collection("Many", ["mm", "afm", *[g.meta_config["vpinfe"]
                                                                ["game_id"] for g in more]])
        self.collections.set_order("Many", "manual")

        rows = self._api(self.games + more).get_collections_metadata()

        self.assertEqual(self._row(rows, "Many")["game_wheel_urls"],
                         ["/media/a1/wheel", "/media/t0/wheel", "/media/t1/wheel",
                          "/media/t2/wheel"])

    def test_the_picker_leads_with_the_whole_library(self) -> None:
        self.collections.add_collection("Short", ["mm"])

        items = self._api().get_collection_picker_items()

        self.assertEqual([item["name"] for item in items], ["", "Short"])
        self.assertEqual(items[0]["table_count"], 3)
        self.assertEqual(items[0]["image_url"], "")
        self.assertEqual([item["showing"] for item in items], [True, False])

    def test_the_picker_marks_what_is_showing_whatever_the_theme_contract(self) -> None:
        self.collections.add_collection("Short", ["mm"])
        api = self._api()
        game_state.apply_collection(api, "Short")

        items = api.get_collection_picker_items()

        self.assertEqual([item["showing"] for item in items], [False, True])


class CollectionGoneTests(_Library):
    """The collection on show is deleted from the Manager while the cabinet shows it."""

    WHOLE_LIBRARY = [("taf", "t1"), ("afm", "a1"), ("mm", "vpw")]

    def setUp(self) -> None:
        super().setUp()
        start_library_of(self, self.games)

    def _refresh(self, api) -> None:
        api.library.mark_stale()
        api.get_tables()

    def test_the_next_refresh_shows_the_whole_library(self) -> None:
        self.collections.add_collection("Short", ["mm"])
        api = self._api()
        game_state.apply_collection(api, "Short")
        self.collections.delete_collection("Short")

        self._refresh(api)

        self.assertEqual(api.current_collection, BUILTIN_ALL)
        self.assertEqual(api.get_current_collection(), "None")
        self.assertEqual(self._rows(api), self.WHOLE_LIBRARY)

    def test_a_deleted_rule_does_not_come_back_as_all(self) -> None:
        self.collections.add_filter_collection("Bally", manufacturer="Bally")
        api = self._api()
        game_state.apply_collection(api, "Bally")
        self.collections.delete_collection("Bally")

        self._refresh(api)
        self._refresh(api)

        self.assertEqual(api.current_filters, game_state.default_filter_state())
        self.assertEqual(self._rows(api), self.WHOLE_LIBRARY)

    def test_all_from_the_picker_stays_all_through_a_refresh(self) -> None:
        self.collections.add_filter_collection("Bally", manufacturer="Bally")
        api = self._api()
        game_state.apply_collection(api, "Bally")

        api.get_tables(reset=True)
        self._refresh(api)

        self.assertEqual(self._rows(api), self.WHOLE_LIBRARY)


if __name__ == "__main__":
    unittest.main()

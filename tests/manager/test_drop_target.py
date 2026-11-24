"""Which target a drop means, from where it landed and which page it landed on."""

from __future__ import annotations

import unittest

from common.i18n import t
from manager import page
from manager.library import drop_target
from manager.library.drop_target import Refusal, Target, Where
from tests.support.arrivals import GAME, library

MEDIA_CELL = ("manager-drop-media",)
ASSET_CELL = ("manager-drop-asset",)

TABLES = [{"id": "t-1", "game_id": "g-1"}, {"id": "t-2", "game_id": "g-1"}]
MEDIA = [
    {"id": "g-1:wheel:", "game_id": "g-1", "kind": "wheel", "page": 1, "table": "",
     "via": None},
    {"id": "g-1:wheel:t-2", "game_id": "g-1", "kind": "wheel", "page": 1, "table": "t-2",
     "via": "table"},
    {"id": "g-1:flyer::2", "game_id": "g-1", "kind": "flyer", "page": 2, "table": "",
     "via": None},
    {"id": "g-1:topper:", "game_id": "g-1", "kind": "topper", "page": 1, "table": "",
     "via": "unused"},
    {"id": "g-1:flyer:t-9", "game_id": "g-1", "kind": "flyer", "page": 1, "table": "t-9",
     "via": "orphan"},
]
ASSETS = [
    {"id": "g-1:pov:", "game_id": "g-1", "kind": "pov", "table": ""},
    {"id": "g-1:pov:t-2", "game_id": "g-1", "kind": "pov", "table": "t-2"},
]


def _resolved(view: str, **where):
    held, _client = library(tables=TABLES, media=MEDIA, assets=ASSETS)
    return drop_target.resolve(held, view, Where(**where))


class AGridCell(unittest.TestCase):
    def test_a_media_cell_names_its_game_and_slot(self) -> None:
        self.assertEqual(
            _resolved("games", row_id="g-1", col_id="media_wheel", cell=MEDIA_CELL),
            Target(game="g-1", folder=GAME["folder"], slot="wheel"))

    def test_a_thumbnail_column_names_its_slot_as_the_media_column_does(self) -> None:
        self.assertEqual(
            _resolved("games", row_id="g-1", col_id="thumb_wheel", cell=MEDIA_CELL),
            _resolved("games", row_id="g-1", col_id="media_wheel", cell=MEDIA_CELL))

    def test_an_asset_cell_names_its_game_and_asset_kind(self) -> None:
        self.assertEqual(
            _resolved("games", row_id="g-1", col_id="asset_pov", cell=ASSET_CELL),
            Target(game="g-1", folder=GAME["folder"], asset="pov"))

    def test_a_name_cell_names_only_the_game(self) -> None:
        self.assertEqual(_resolved("games", row_id="g-1", col_id="name"),
                         Target(game="g-1", folder=GAME["folder"]))

    def test_a_column_that_merely_looks_like_a_media_one_is_not_one(self) -> None:
        self.assertEqual(_resolved("games", row_id="g-1", col_id="media_count"),
                         Target(game="g-1", folder=GAME["folder"]))

    def test_empty_space_on_a_library_view_names_the_library(self) -> None:
        for view in ("overview", "games", "tables", "media", "assets", "collections"):
            with self.subTest(view=view):
                self.assertEqual(_resolved(view), Target())


class ATablesRow(unittest.TestCase):
    def test_a_tables_row_names_its_game_and_its_table(self) -> None:
        self.assertEqual(_resolved("tables", row_id="t-2", col_id="filename"),
                         Target(game="g-1", folder=GAME["folder"], table="t-2"))

    def test_a_tables_asset_cell_names_its_table_and_asset_kind(self) -> None:
        self.assertEqual(
            _resolved("tables", row_id="t-2", col_id="asset_pov", cell=ASSET_CELL),
            Target(game="g-1", folder=GAME["folder"], table="t-2", asset="pov"))


class AMediaViewRow(unittest.TestCase):
    def test_a_missing_row_names_its_game_and_its_kind(self) -> None:
        self.assertEqual(_resolved("media", row_id="g-1:wheel:"),
                         Target(game="g-1", folder=GAME["folder"], slot="wheel"))

    def test_a_have_row_for_a_tables_own_file_names_that_table(self) -> None:
        self.assertEqual(_resolved("media", row_id="g-1:wheel:t-2"),
                         Target(game="g-1", folder=GAME["folder"], table="t-2", slot="wheel"))

    def test_a_row_for_page_two_names_page_two(self) -> None:
        self.assertEqual(_resolved("media", row_id="g-1:flyer::2"),
                         Target(game="g-1", folder=GAME["folder"], slot="flyer", page=2))

    def test_an_unused_row_names_its_slot_and_says_it_is_covered(self) -> None:
        self.assertEqual(_resolved("media", row_id="g-1:topper:"),
                         Target(game="g-1", folder=GAME["folder"], slot="topper",
                                covered=True))

    def test_an_orphan_row_is_refused_for_the_table_it_is_named_for(self) -> None:
        refused = _resolved("media", row_id="g-1:flyer:t-9")

        self.assertEqual(refused, Refusal("manager.drop.orphan"))
        self.assertEqual(refused.text(), t("manager.drop.orphan"))


class AnAssetsRow(unittest.TestCase):
    def test_a_row_names_its_game_and_kind(self) -> None:
        self.assertEqual(_resolved("assets", row_id="g-1:pov:"),
                         Target(game="g-1", folder=GAME["folder"], asset="pov"))

    def test_a_row_names_its_table_where_it_names_one(self) -> None:
        self.assertEqual(_resolved("assets", row_id="g-1:pov:t-2"),
                         Target(game="g-1", folder=GAME["folder"], table="t-2", asset="pov"))


class ACollectionsRow(unittest.TestCase):
    def test_a_row_names_that_collections_picture(self) -> None:
        self.assertEqual(_resolved("collections", row_id="Favorites"),
                         Target(picture="Favorites", picture_of="collection"))


class ARowTheLibraryDoesNotHold(unittest.TestCase):
    def test_every_grid_refuses_it_with_the_row_unknown_notice(self) -> None:
        for view, row_id in (("games", "g-9"), ("tables", "t-9"), ("media", "g-9:wheel:"),
                             ("assets", "g-9:pov:")):
            with self.subTest(view=view):
                refused = _resolved(view, row_id=row_id)

                self.assertEqual(refused, Refusal("manager.page.row_unknown"))

    def test_a_declared_game_it_does_not_hold_is_refused_the_same_way(self) -> None:
        self.assertEqual(_resolved("games", declared={"game": "g-9"}),
                         Refusal("manager.page.row_unknown"))

    def test_a_declared_table_the_game_does_not_hold_is_refused(self) -> None:
        self.assertEqual(_resolved("games", declared={"game": "g-1", "table": "t-9"}),
                         Refusal("manager.page.row_unknown"))


class ADeclaredTarget(unittest.TestCase):
    def test_it_beats_whatever_the_grid_cell_says(self) -> None:
        self.assertEqual(
            _resolved("games", row_id="g-1", col_id="media_wheel", cell=MEDIA_CELL,
                      declared={"game": "g-1", "slot": "backglass"}),
            Target(game="g-1", folder=GAME["folder"], slot="backglass"))

    def test_a_panels_header_names_the_panels_game(self) -> None:
        self.assertEqual(_resolved("games", declared={"game": "g-1"}),
                         Target(game="g-1", folder=GAME["folder"]))

    def test_a_table_lens_names_its_table(self) -> None:
        self.assertEqual(_resolved("tables", declared={"game": "g-1", "table": "t-2"}),
                         Target(game="g-1", folder=GAME["folder"], table="t-2"))

    def test_a_tile_names_its_slot_and_page(self) -> None:
        self.assertEqual(
            _resolved("games", declared={"game": "g-1", "slot": "flyer", "page": 2}),
            Target(game="g-1", folder=GAME["folder"], slot="flyer", page=2))

    def test_an_asset_row_names_its_asset_kind(self) -> None:
        self.assertEqual(_resolved("games", declared={"game": "g-1", "asset": "pov"}),
                         Target(game="g-1", folder=GAME["folder"], asset="pov"))

    def test_a_declared_picture_names_the_picture(self) -> None:
        self.assertEqual(
            _resolved("collections", declared={"picture": "Favorites", "of": "collection"}),
            Target(picture="Favorites", picture_of="collection"))

    def test_a_picture_declared_for_no_known_owner_is_refused(self) -> None:
        self.assertEqual(_resolved("collections", declared={"picture": "Favorites"}),
                         Refusal("manager.page.row_unknown"))
        self.assertEqual(
            _resolved("collections", declared={"picture": "Favorites", "of": "player "}),
            Refusal("manager.page.row_unknown"))

    def test_the_all_games_row_takes_no_picture(self) -> None:
        self.assertEqual(_resolved("collections", row_id="builtin:all"),
                         Refusal("manager.drop.not_here"))

    def test_a_declared_player_picture_names_the_player(self) -> None:
        self.assertEqual(
            _resolved("players", declared={"picture": "p-1", "of": "player"}),
            Target(picture="p-1", picture_of="player"))


class WhereAFileMayBeLet(unittest.TestCase):
    NOT_HERE = ("tags", "discovery", "players", "launchers", "themes", "settings", "devices",
                "metrics", "logs", "jobs", "backups", "about", "system", "community",
                "extensions", "setup")

    def test_a_library_drop_on_any_other_page_is_refused(self) -> None:
        for view in self.NOT_HERE:
            with self.subTest(view=view):
                self.assertEqual(_resolved(view), Refusal("manager.drop.not_here"))

    def test_a_view_an_extension_registered_is_refused_too(self) -> None:
        self.assertEqual(_resolved("some-extension"), Refusal("manager.drop.not_here"))

    def test_every_view_the_manager_registers_is_in_the_table(self) -> None:
        registered = {item[0] for _parent, items in page.NAV_GROUPS for item in items}

        self.assertLessEqual(registered, set(drop_target.VIEWS))

    def test_the_views_that_take_the_library_are_the_six_library_pages(self) -> None:
        self.assertEqual({view for view, takes in drop_target.VIEWS.items() if takes},
                         {"overview", "games", "tables", "media", "assets", "collections"})


if __name__ == "__main__":
    unittest.main()

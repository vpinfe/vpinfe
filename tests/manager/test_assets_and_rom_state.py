"""ROM state on the Tables grid, per-table kinds off the Games grid, a fixed Kind filter."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.games import asset_lens
from common.media.media_specs import media_label_map
from manager.kit import views
from manager.library import assets, games, media


def _library(asset_keys: list[str], kept_assets: set[str] | None = None) -> Mock:
    library = Mock()
    library.asset_keys.return_value = asset_keys
    library.thumbnail_look.return_value = None
    library.kept_kinds.return_value = {
        "media": set(media_label_map()),
        "asset": set(asset_keys) if kept_assets is None else kept_assets}
    return library


def _filter_values(column: dict) -> list[str]:
    return [one["value"] for one in column["filterParams"]["choices"]]


class TheRomStateColumn(unittest.TestCase):
    def test_a_rom_known_to_be_absent_reads_missing(self) -> None:
        row = games._rom_state({"rom": "afm_113b", "rom_installed": False})

        self.assertEqual("missing", row)

    def test_a_rom_that_is_there_reads_installed(self) -> None:
        self.assertEqual("installed", games._rom_state({"rom": "afm_113b", "rom_installed": True}))

    def test_a_rom_nothing_could_check_reads_unknown(self) -> None:
        self.assertEqual("unknown", games._rom_state({"rom": "afm_113b", "rom_installed": None}))

    def test_a_table_that_names_no_rom_holds_nothing(self) -> None:
        self.assertEqual("", games._rom_state({"rom": "", "rom_installed": None}))

    def test_the_table_rows_carry_it(self) -> None:
        built = games.table_rows([{"game_id": "g", "rom": "afm_113b", "rom_installed": False}])

        self.assertEqual("missing", built[0]["rom_state"])

    def test_the_filter_offers_the_three_states_and_the_blank(self) -> None:
        column = next(one for one in games.TABLE_COLUMNS if one["field"] == "rom_state")

        self.assertEqual(["installed", "missing", "unknown", ""], _filter_values(column))

    def test_launch_and_assets_both_show_it(self) -> None:
        launch = games.TABLE_VIEWS["manager.game_tables.launch"]
        assert isinstance(launch, views.Preset)

        self.assertIn("rom_state", launch.columns)


class TheGamesAssetsGridHoldsOnlyWhatAFolderHolds(unittest.TestCase):
    def test_the_five_per_table_kinds_are_not_folder_kinds(self) -> None:
        library = _library(["backglass", "ini", "pup_pack", "alt_color", "music"])

        self.assertEqual(["pup_pack", "alt_color", "music"], games.folder_asset_keys(library))

    def test_the_games_assets_view_leaves_them_out(self) -> None:
        library = _library(["backglass", "ini", "pup_pack"])
        preset = games.grid_presets([], library)[games._ASSETS]
        assert isinstance(preset, views.Preset)

        self.assertEqual(("name", "asset_pup_pack"), preset.columns)

    def test_the_games_grid_declares_no_column_for_them(self) -> None:
        library = _library(["backglass", "ini", "pup_pack"])
        fields = {column["field"] for column in games.grid_columns([], [], library)}

        self.assertIn("asset_pup_pack", fields)
        self.assertNotIn("asset_backglass", fields)
        self.assertNotIn("asset_ini", fields)


class TheKindFilterOffersTheKnownKinds(unittest.TestCase):
    def test_the_assets_filter_lists_every_kind_the_library_keeps(self) -> None:
        library = _library([], kept_assets=set(asset_lens.KINDS))
        column = next(one for one in assets._with_kind_filter(assets.COLUMNS, library)
                      if one["field"] == "label")

        self.assertEqual([asset_lens.label_of(kind) for kind in asset_lens.KINDS],
                         _filter_values(column))

    def test_a_kind_the_library_does_not_keep_is_not_offered(self) -> None:
        library = _library([], kept_assets={"backglass"})
        column = next(one for one in assets._with_kind_filter(assets.COLUMNS, library)
                      if one["field"] == "label")

        self.assertEqual([asset_lens.label_of("backglass")], _filter_values(column))

    def test_the_media_filter_lists_the_kinds_the_library_collects(self) -> None:
        library = _library([])
        library.kept_kinds.return_value = {"media": {"wheel", "topper"}, "asset": set()}
        column = next(one for one in media._with_kind_filter(media.COLUMNS, library)
                      if one["field"] == "label")

        self.assertEqual(sorted(media_label_map()[kind] for kind in ("wheel", "topper")),
                         _filter_values(column))


if __name__ == "__main__":
    unittest.main()

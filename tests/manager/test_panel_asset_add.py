"""A Missing asset row in the panel opens the same dialog the Assets page does."""

from __future__ import annotations

import unittest
from unittest import mock

from manager.kit import data, panel
from manager.library import mediasource
from manager.workbench import game_assets, game_table

CONTEXT = {"rebuild": object()}


def _opens(kind: str):
    with mock.patch.object(panel, "action") as action:
        game_table._add_action(CONTEXT, kind)
    if not action.called:
        return None
    return action.call_args.args[1].func


class PanelAssetAddTests(unittest.TestCase):
    def test_a_whole_folder_kind_opens_the_folder_dialog(self) -> None:
        for kind in ("music", "pup_pack", "alt_sound", "alt_color", "rom"):
            self.assertIs(_opens(kind), mediasource.open_folder_sources, kind)

    def test_a_file_vpx_finds_by_name_opens_the_slot_dialog(self) -> None:
        for kind in ("backglass", "b2spro", "ini", "pov", "scv", "nix"):
            self.assertIs(_opens(kind), mediasource.open_asset_sources, kind)

    def test_authors_notes_open_the_notes_dialog(self) -> None:
        self.assertIs(_opens("readme"), mediasource.open_notes_sources)

    def test_a_kind_the_assets_page_offers_nothing_for_gets_no_add(self) -> None:
        self.assertIsNone(_opens("table"))

    def test_alt_color_and_alt_sound_have_a_row_to_add_from(self) -> None:
        folder = {"alt_color": {"present": False}, "alt_sound": {"present": False}}

        self.assertEqual(folder, game_assets._only_kept(folder, data._kept_of({})["asset"]))


if __name__ == "__main__":
    unittest.main()

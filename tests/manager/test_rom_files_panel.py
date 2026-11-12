"""A game's ROM files in the panel: the Game Folder rows, the count, and the acts on one."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from common.i18n import t
from manager.kit import data
from manager.library import file_acts, rom_acts
from manager.workbench import file, game_assets, game_table

TABLES = [{"id": "t1", "filename": "Theatre of Magic.vpx"},
          {"id": "t2", "filename": "Theatre of Magic Mod.vpx"},
          {"id": "t3", "filename": "Other.vpx"}]


def _rom(name: str, users: list[str]) -> dict[str, Any]:
    return {"id": f"g1:rom:{name}", "game_id": "g1", "kind": "rom", "label": "ROM",
            "binding": "game", "present": True, "file": name,
            "path": f"pinmame/roms/{name}", "serves": len(users), "users": users,
            "size_bytes": 2048}


def _context(roms: list[dict[str, Any]], lens: str = "") -> dict[str, Any]:
    return {"library": data.Library(Mock()), "game": {"assets": {}}, "lens": lens,
            "tables": [], "rom_files": roms}


class TheAssetsCount(unittest.TestCase):
    def test_each_rom_file_is_counted(self) -> None:
        label = game_assets._assets_label(
            _context([_rom("tom_13.zip", ["t1"]), _rom("tom_10.zip", [])]))

        self.assertEqual("Assets (2)", label)

    def test_a_game_with_none_says_nothing_of_roms(self) -> None:
        self.assertEqual("Assets", game_assets._assets_label(_context([])))


class ThePickOfAFile(unittest.TestCase):
    def test_a_pick_finds_its_file_whatever_the_case_of_the_name(self) -> None:
        files = [_rom("tom_13.zip", ["t1"]), _rom("TOM_10.zip", [])]

        found = game_assets._picked(files, "rom:tom_10.zip", "")

        self.assertEqual("TOM_10.zip", found["file"] if found else None)

    def test_a_pick_of_a_file_that_is_gone_finds_nothing(self) -> None:
        self.assertIsNone(game_assets._picked([_rom("tom_13.zip", [])], "rom:gone.zip", ""))

    def test_a_pick_of_no_kind_finds_nothing(self) -> None:
        self.assertIsNone(game_assets._picked([_rom("tom_13.zip", [])], "", ""))


class AFileThatIsGoneDoesNotFallBackToAnotherRom(unittest.TestCase):
    def test_the_panel_for_a_removed_rom_finds_nothing(self) -> None:
        rows = [_rom("tom_10.zip", [])]

        self.assertIsNone(file._same_file(rows, _rom("tom_13.zip", [])))

    def test_the_panel_for_a_rom_that_is_there_finds_it(self) -> None:
        rows = [_rom("tom_10.zip", []), _rom("tom_13.zip", ["t1"])]

        found = file._same_file(rows, _rom("tom_13.zip", []))

        self.assertEqual("tom_13.zip", found["file"] if found else None)


class WhoReadsAFile(unittest.TestCase):
    def test_the_tables_served_are_the_ones_that_read_it(self) -> None:
        served = file._served(_rom("tom_13.zip", ["t1", "t3"]), [], TABLES)

        self.assertEqual(["t1", "t3"], [one["id"] for one in served])

    def test_an_unused_file_serves_none(self) -> None:
        self.assertEqual([], file._served(_rom("tom_13.zip", []), [], TABLES))

    def test_the_names_are_the_tables_files(self) -> None:
        self.assertEqual(["Theatre of Magic", "Other"],
                         rom_acts.users_of(_rom("tom_13.zip", ["t1", "t3"]), TABLES))

    def test_removing_a_used_file_names_the_tables_and_what_they_will_read(self) -> None:
        said, lines = rom_acts.removal(_rom("tom_13.zip", ["t2"]), TABLES)

        self.assertEqual(t("manager.workbench.remove_asset.rom_used"), said)
        self.assertEqual(["Theatre of Magic Mod"], lines)

    def test_removing_an_unused_file_says_no_table_uses_it(self) -> None:
        said, lines = rom_acts.removal(_rom("tom_13.zip", []), TABLES)

        self.assertEqual(t("manager.workbench.remove_asset.rom_unused"), said)
        self.assertEqual([], lines)


class TheActsOnRomFiles(unittest.TestCase):
    def _labels(self, rows: list[dict[str, Any]]) -> list[str]:
        files = [file_acts.File(row, {"id": "g1", "name": "Theatre of Magic"}) for row in rows]
        return [one.label for one in file_acts.asset_acts(Mock(), files, {}, Mock())]

    def test_a_rom_file_has_acts_whether_or_not_a_table_reads_it(self) -> None:
        self.assertTrue(file_acts.asset_has_acts(_rom("tom_13.zip", [])))
        self.assertTrue(file_acts.asset_has_acts(_rom("tom_13.zip", ["t1"])))

    def test_one_file_can_be_added_to_replaced_or_removed(self) -> None:
        self.assertEqual([t("manager.files.add"), t("manager.files.replace"),
                          t("manager.games.match_vps"), t("word.remove")],
                         self._labels([_rom("tom_13.zip", ["t1"])]))

    def test_several_files_can_be_added_to_or_removed_but_not_replaced(self) -> None:
        self.assertEqual([t("manager.files.add"), t("manager.games.match_vps"),
                          t("word.remove")],
                         self._labels([_rom("tom_13.zip", ["t1"]), _rom("tom_10.zip", [])]))

    def test_the_file_panel_offers_replace_and_remove(self) -> None:
        one = file_acts.File(_rom("tom_13.zip", ["t1"]), {"id": "g1"})

        held = file_acts.held_acts(Mock(), one, "assets", {}, Mock())

        self.assertEqual({file_acts.REPLACE, file_acts.REMOVE}, set(held))


class ReplacingAFile(unittest.IsolatedAsyncioTestCase):
    """The old file goes only when the import added a new name and left the old one alone."""

    async def _replace(self, imported: list[str], stamps: list[str]) -> Mock:
        library = Mock()
        library.tables_for.return_value = []
        library.roms_of.side_effect = [[_rom("a.zip", [])],
                                       [_rom(name, []) for name in ["a.zip", *imported]]]
        library.asset_detail.side_effect = [{"modified": stamp} for stamp in stamps]
        captured: dict[str, Any] = {}

        async def done() -> None:
            return None

        async def io(callback: Any, *args: Any) -> Any:
            return callback(*args)

        with (patch("manager.kit.on_page.ui", MagicMock()), patch.object(rom_acts, "ui", Mock()),
              patch.object(rom_acts.offload, "io", io),
              patch.object(rom_acts.run, "io_bound", io),
              patch.object(rom_acts.mediasource, "open_folder_sources",
                           lambda _c, _k, _l, after, _p: captured.update(after=after))):
            await rom_acts.replace({"library": library, "game_id": "g1"},
                                   _rom("a.zip", []), done)
            await captured["after"]()
        return library

    async def test_a_new_name_beside_an_untouched_file_drops_the_old_one(self) -> None:
        library = await self._replace(["b.zip"], ["t1", "t1"])

        library.remove_asset.assert_called_once_with("g1", "pinmame/roms/a.zip")

    async def test_the_old_name_written_again_beside_a_new_one_stays(self) -> None:
        library = await self._replace(["b.zip"], ["t1", "t2"])

        library.remove_asset.assert_not_called()

    async def test_nothing_new_removes_nothing(self) -> None:
        library = await self._replace([], ["t1", "t2"])

        library.remove_asset.assert_not_called()

    async def test_a_file_whose_age_cannot_be_read_is_kept(self) -> None:
        library = await self._replace(["b.zip"], [])

        library.remove_asset.assert_not_called()


class TheThisTableRow(unittest.TestCase):
    def test_a_rom_nothing_could_check_is_not_just_a_name(self) -> None:
        drawn = game_table._rom_state({"effective": "tom_13", "installed": None}, "tom_13")

        self.assertTrue(callable(drawn))

    def test_a_table_that_names_no_rom_draws_the_bare_name(self) -> None:
        self.assertEqual("-", game_table._rom_state({"effective": "", "installed": None}, "-"))


if __name__ == "__main__":
    unittest.main()

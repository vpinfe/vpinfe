"""A game's ROM files as rows of the asset lens, each with the tables that read it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from common.games import asset_lens
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Roms000001"
FOLDER = "Theatre of Magic (Bally 1995)"

INFO: dict[str, Any] = {
    "Info": {"Title": "Theatre of Magic"},
    "vpinfe": {"game_id": GAME_ID},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx", "rom": "tom_13"},
        "tbl0000002": {"id": "tbl0000002", "filename": f"{FOLDER} Mod.vpx", "rom": "tom_14"},
    },
}


def _audit(_bin: Any, _roms: str, name: str) -> dict | None:
    return {"catalog": True, "found": True,
            "clone_of": "tom_10" if name == "tom_14" else None}


class RomFilesInTheAssetLens(TempTree):
    def _rows(self, files: dict[str, bytes], kind: str = "") -> list[dict]:
        folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                            files={f"{FOLDER}.vpx": b"v", f"{FOLDER} Mod.vpx": b"v", **files})
        game = fake_game(folder, FOLDER, meta=INFO)
        with (patch("common.games.game_repository.catalog", return_value={GAME_ID: game}),
              patch("common.games.table_lens.pinmame_catalog.lookup", side_effect=_audit)):
            return asset_lens.listing(game=GAME_ID, kind=kind)["assets"]

    def _roms(self, files: dict[str, bytes], kind: str = "") -> dict[str, dict]:
        return {row["file"]: row for row in self._rows(files, kind) if row["kind"] == "rom"}

    def test_every_zip_in_the_roms_folder_is_a_row(self) -> None:
        roms = self._roms({"pinmame/roms/tom_13.zip": b"z", "pinmame/roms/tom_10.zip": b"z"})

        self.assertEqual({"tom_10.zip", "tom_13.zip"}, set(roms))

    def test_a_file_names_the_tables_that_read_it(self) -> None:
        roms = self._roms({"pinmame/roms/tom_13.zip": b"z", "pinmame/roms/tom_14.zip": b"z"})

        self.assertEqual(["tbl0000001"], roms["tom_13.zip"]["users"])
        self.assertEqual(["tbl0000002"], roms["tom_14.zip"]["users"])

    def test_a_clone_reads_its_parents_file_too(self) -> None:
        roms = self._roms({"pinmame/roms/tom_10.zip": b"z"})

        self.assertEqual(["tbl0000002"], roms["tom_10.zip"]["users"])
        self.assertEqual(1, roms["tom_10.zip"]["serves"])

    def test_a_file_no_table_reads_serves_none(self) -> None:
        roms = self._roms({"pinmame/roms/afm_113b.zip": b"z"})

        self.assertEqual(0, roms["afm_113b.zip"]["serves"])
        self.assertEqual([], roms["afm_113b.zip"]["users"])

    def test_the_path_is_relative_to_the_game_folder(self) -> None:
        roms = self._roms({"pinmame/roms/tom_13.zip": b"z"})

        self.assertEqual("pinmame/roms/tom_13.zip", roms["tom_13.zip"]["path"])

    def test_a_game_with_no_roms_folder_has_no_rom_rows(self) -> None:
        self.assertEqual({}, self._roms({}))

    def test_a_folder_inside_the_roms_folder_is_not_a_row(self) -> None:
        roms = self._roms({"pinmame/roms/tom_13.zip": b"z", "pinmame/roms/extra/readme.txt": b"t"})

        self.assertEqual({"tom_13.zip"}, set(roms))

    def test_a_file_that_is_not_a_zip_is_not_a_row(self) -> None:
        roms = self._roms({"pinmame/roms/tom_13.zip": b"z", "pinmame/roms/.DS_Store": b"x",
                           "pinmame/roms/tom_13.txt": b"t", "pinmame/roms/TOM_10.ZIP": b"z"})

        self.assertEqual({"tom_13.zip", "TOM_10.ZIP"}, set(roms))
        self.assertEqual(["tbl0000001"], roms["tom_13.zip"]["users"])

    def test_asking_for_another_kind_leaves_them_out(self) -> None:
        rows = self._rows({"pinmame/roms/tom_13.zip": b"z"}, kind="backglass")

        self.assertEqual([], [row for row in rows if row["kind"] == "rom"])

    def test_asking_for_roms_returns_only_roms(self) -> None:
        rows = self._rows({"pinmame/roms/tom_13.zip": b"z"}, kind="rom")

        self.assertEqual({"rom"}, {row["kind"] for row in rows})

    def test_rom_is_a_kind_the_lens_lists(self) -> None:
        self.assertIn("rom", asset_lens.KINDS)

    def test_each_pup_pack_folder_is_a_row_of_its_own(self) -> None:
        rows = self._rows({"pupvideos/tom_13/a.mp4": b"x", "pupvideos/afm_113/b.mp4": b"x"})

        packs = {row["file"]: row["path"] for row in rows if row["kind"] == "pup_pack"}

        self.assertEqual({"afm_113": "pupvideos/afm_113", "tom_13": "pupvideos/tom_13"}, packs)


if __name__ == "__main__":
    unittest.main()

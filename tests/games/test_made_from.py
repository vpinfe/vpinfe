"""A patched table names the table it was made from, and the base names what was made
from it before it goes."""

from __future__ import annotations

import hashlib
import zipfile

from common.games import table_lens
from common.games.game_metadata import made_from
from common.games.game_parser import GameParser
from common.games.info_file import MetaConfig
from common.uploads import upload_ops
from common.uploads.asset_analyzer_service import analyze_upload_session
from common.uploads.asset_import_service import build_import_plan
from httpapi.models import ImportPlanResource, TableSource
from tests.support.library import TempTree, fake_game, write_game
from tests.support.swaps import library_of

FOLDER = "Example (Bally 1990)"
BASE, MADE = "Example.vpx", "Example VPW Mod.vpx"
BASE_ID, MADE_ID = "tblBase00001", "tblMade00001"
BASE_BYTES, MADE_BYTES = b"the base table", b"the patched table"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _info(base_hash: str = _sha(BASE_BYTES)) -> dict:
    return {
        "Info": {"Title": "Example", "Manufacturer": "Bally", "Year": "1990"},
        "vpinfe": {"schema": 2, "game_id": "Game00000001"},
        "tables": {
            BASE_ID: {"id": BASE_ID, "filename": BASE, "file_hash": base_hash,
                      "added": "2024-01-01T00:00:00Z"},
            MADE_ID: {"id": MADE_ID, "filename": MADE, "file_hash": _sha(MADE_BYTES),
                      "added": "2025-01-01T00:00:00Z"},
        },
    }


class _PatchedGame(TempTree):
    """A base and the table a patch made from it, recorded by the writer the import uses."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=_info(), vpx=False,
                                 files={BASE: BASE_BYTES, MADE: MADE_BYTES})
        self.meta = MetaConfig(str(self.folder / f"{FOLDER}.info"))
        self.meta.record_patch_source(MADE, BASE, _sha(BASE_BYTES), "jojodiff")


class MadeFrom(_PatchedGame):
    def _rows(self) -> dict[str, dict]:
        (game,) = GameParser(str(self.root)).get_all_games()
        return {row["filename"]: row
                for row in table_lens.table_rows(game, {"game_dir": str(self.folder)})}

    def _rehash_base(self, base_hash: str) -> None:
        meta = MetaConfig(str(self.folder / f"{FOLDER}.info"))
        meta.data["tables"][BASE_ID]["file_hash"] = base_hash
        meta.write_config()

    def test_a_patched_table_names_the_table_it_was_made_from(self) -> None:
        rows = self._rows()

        self.assertEqual({"file": BASE, "table_id": BASE_ID, "available": True},
                         rows[MADE]["source"]["base"])
        self.assertIsNone(rows[BASE]["source"])
        base = TableSource.model_validate(rows[MADE]["source"]).base
        self.assertEqual(BASE_ID, base.table_id if base else "")

    def test_a_base_gone_from_disk_keeps_its_record_and_is_not_there(self) -> None:
        (self.folder / BASE).unlink()

        self.assertEqual({"file": BASE, "table_id": BASE_ID, "available": False},
                         self._rows()[MADE]["source"]["base"])

    def test_a_newer_file_of_the_same_name_is_not_the_base(self) -> None:
        self._rehash_base(_sha(b"a newer table"))

        self.assertEqual({"file": BASE, "table_id": "", "available": False},
                         self._rows()[MADE]["source"]["base"])

    def test_a_base_whose_hash_was_never_read_is_found_by_name(self) -> None:
        self._rehash_base("")

        self.assertEqual(BASE_ID, self._rows()[MADE]["source"]["base"]["table_id"])

    def test_the_base_names_what_was_made_from_it(self) -> None:
        entries = self.meta.game_file_settings()

        self.assertEqual([MADE], made_from(entries, BASE))
        self.assertEqual([], made_from(entries, MADE))


class ReplacingABase(_PatchedGame):
    def setUp(self) -> None:
        super().setUp()
        library_of(self, {"Game00000001": fake_game(self.folder, FOLDER, meta=_info())})

    def _plan(self) -> dict:
        session = self.root / "session"
        session.mkdir()
        with zipfile.ZipFile(session / "drop.zip", "w") as archive:
            archive.writestr("Example 2.0.vpx", b"a newer table")
        analysis, _source = analyze_upload_session(session)
        plan = upload_ops._plan_to_dict(build_import_plan(analysis, game_dir=self.folder))
        ImportPlanResource.model_validate(plan)
        (item,) = (one for one in plan["items"] if one["action"] == "replace_vpx")
        return item

    def test_replacing_the_base_names_what_was_made_from_it(self) -> None:
        self.meta.data["vpinfe"]["default_table"] = BASE_ID
        self.meta.write_config()

        item = self._plan()

        self.assertEqual((f"replaces {BASE}", [MADE]), (item["replaces"], item["made_from_it"]))

    def test_the_replace_line_names_the_default_the_import_deletes(self) -> None:
        item = self._plan()

        self.assertEqual((f"replaces {MADE}", []), (item["replaces"], item["made_from_it"]))

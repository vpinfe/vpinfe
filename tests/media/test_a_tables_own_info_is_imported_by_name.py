"""Protects: (a) an imported table file never overwrites the game's file or another table's."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from common.uploads import asset_import_service
from common.uploads.asset_analyzer_service import analyze_path
from common.uploads.asset_import_service import build_import_plan, execute_import_plan
from tests.support.library import game_info
from tests.support.saved import save_record
from tests.support.swaps import library_of

GAME = {"Info": {"VPSId": "abc", "Title": "Cactus"}, "User": {"Rating": 5}}
OWN = {"Info": {"Title": "Cactus"}, "User": {"StartCount": 3},
       "vpinfe": {"schema": 3, "table": {"id": "b"}}}


def _bundle(tmp: str) -> Path:
    path = Path(tmp) / "bundle.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Cactus VR.vpx", b"vr")
        archive.writestr("Cactus.info", json.dumps(GAME))
        archive.writestr("Cactus VR.info", json.dumps(OWN))
    return path


class ImportedByName(TestCase):
    def _import(self, tmp: str):
        folder = Path(tmp) / "Cactus (Bally 1998)"
        folder.mkdir(exist_ok=True)
        (folder / "Cactus.vpx").write_bytes(b"x")
        save_record(folder / "Cactus (Bally 1998).info", game_info("Cactus"))
        zip_path = _bundle(tmp)
        plan = build_import_plan(analyze_path(zip_path), game_dir=folder)
        with mock.patch.object(asset_import_service, "refresh_game"):
            library_of(self)
            execute_import_plan(plan, zip_path)
        return folder

    def test_the_game_file_is_the_one_without_a_table_record(self) -> None:
        with TemporaryDirectory() as tmp:
            analysis = analyze_path(_bundle(tmp))

            kept = {a.entries[0].arcname: a.own_table for a in analysis.assets
                    if a.kind == "game_info"}

        self.assertEqual({"Cactus.info": False, "Cactus VR.info": True}, kept)
        assert analysis.bundle_info is not None
        self.assertEqual("abc", analysis.bundle_info["Info"]["VPSId"])

    def test_each_file_lands_under_its_own_name(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = self._import(tmp)

            written = {path.name for path in folder.glob("*.info")}
            own = json.loads((folder / "Cactus VR.info").read_text(encoding="utf-8"))

        self.assertEqual({"Cactus (Bally 1998).info", "Cactus VR.info"}, written)
        self.assertEqual("Cactus", own["Info"]["Title"])
        self.assertNotIn("StartCount", own["User"])
        self.assertEqual({"id": "b"}, own["vpinfe"]["table"])

    def test_a_table_file_already_there_is_copied_aside_first(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Cactus (Bally 1998)"
            folder.mkdir()
            (folder / "Cactus VR.info").write_text('{"User": {"StartCount": 9}}',
                                                   encoding="utf-8")

            self._import(tmp)

            (copy,) = folder.glob("Cactus VR.info.vpinfe-*-import")
            self.assertEqual(9, json.loads(copy.read_text(encoding="utf-8"))["User"]["StartCount"])


class ImportedAsANewGame(TestCase):
    def test_the_games_file_wins_where_the_names_would_be_one(self) -> None:
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Cactus VR.vpx", b"vr")
                archive.writestr("Cactus VR.info", json.dumps(OWN))
                archive.writestr("Cactus.info", json.dumps(GAME))

            plan = build_import_plan(analyze_path(zip_path), allow_new_game=True,
                                     games_path=tmp)

            (info,) = [one for one in plan.items if one.action == "write_info"]
            self.assertFalse(info.asset.own_table)

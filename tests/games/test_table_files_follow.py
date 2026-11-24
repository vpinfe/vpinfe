"""Protects: (a) a table's own file goes where its table goes and is never left behind."""

from __future__ import annotations

import json
import os
from pathlib import Path

from common.games.export_bundle import bundle_paths, prune_info
from common.games.info_file import MetaConfig
from tests.support.library import TempTree, game_info, write_game

FOLDER = "Cactus"
SHARED = "Cactus.vpx"
VR = "Cactus VR.vpx"
VR_INFO = "Cactus VR.info"
LAST = 1700000100


class Followed(TempTree):
    def setUp(self) -> None:
        super().setUp()
        record = game_info(
            "Cactus", game_id="Follow0001", User={"Rating": 4},
            tables={"a": {"id": "a", "filename": SHARED},
                    "b": {"id": "b", "filename": VR,
                          "user": {"rating": 6, "tags": ["VR"], "start_count": 2,
                                   "last_run": LAST}}})
        self.folder = write_game(self.root, FOLDER, info=record, files={VR: b"vr"})
        MetaConfig(str(self.folder / f"{FOLDER}.info")).write_config()

    def _meta(self) -> MetaConfig:
        return MetaConfig(str(self.folder / f"{FOLDER}.info"))

    def _names(self) -> set[str]:
        return set(os.listdir(self.folder))

    def _copies(self, reason: str) -> list[str]:
        return sorted(name for name in self._names() if name.endswith(f"-{reason}"))


class Rename(Followed):
    def test_the_file_goes_with_the_new_name_and_keeps_what_was_recorded(self) -> None:
        (self.folder / VR).rename(self.folder / "Cactus Night.vpx")

        self._meta().rename_table("b", "Cactus Night.vpx")

        self.assertEqual(set(), {VR_INFO} & self._names())
        own = json.loads((self.folder / "Cactus Night.info").read_text(encoding="utf-8"))
        self.assertEqual("b", own["vpinfe"]["table"]["id"])
        self.assertEqual({"rating": 6, "tags": ["VR"]}, own["vpinfe"]["table"]["own"])
        self.assertEqual(2, self._meta().data["tables"]["b"]["user"]["start_count"])

    def test_a_rename_leaves_no_copy_of_the_old_file_behind(self) -> None:
        (self.folder / VR).rename(self.folder / "Cactus Night.vpx")

        self._meta().rename_table("b", "Cactus Night.vpx")

        self.assertEqual([], self._copies("delete"))


class Removed(Followed):
    def test_a_dropped_table_takes_its_file_and_a_copy_is_kept(self) -> None:
        kept = (self.folder / VR_INFO).read_bytes()
        (self.folder / VR).unlink()

        self._meta().drop_table("b")

        self.assertNotIn(VR_INFO, self._names())
        (copy,) = self._copies("delete")
        self.assertEqual(kept, (self.folder / copy).read_bytes())

    def test_the_game_keeps_its_own_file_and_the_table_that_remains(self) -> None:
        (self.folder / VR).unlink()

        self._meta().drop_table("b")

        self.assertTrue((self.folder / f"{FOLDER}.info").exists())
        self.assertEqual({"a"}, set(self._meta().data["tables"]))


class Exported(Followed):
    def _shipped(self, table: str) -> tuple[set[str], dict[str, dict]]:
        paths = list(bundle_paths(self.folder, table=table))
        names = {arcname for _, arcname in paths}
        pruned = {arcname: json.loads(prune_info(Path(path).read_text(encoding="utf-8"), names))
                  for path, arcname in paths if arcname.endswith(".info")}
        return names, pruned

    def test_a_table_with_a_file_ships_both_files(self) -> None:
        names, _ = self._shipped(VR)

        self.assertLessEqual({f"{FOLDER}.info", VR_INFO, VR}, names)
        self.assertNotIn(SHARED, names)

    def test_a_table_named_like_the_folder_ships_the_game_file_alone(self) -> None:
        names, _ = self._shipped(SHARED)

        self.assertIn(f"{FOLDER}.info", names)
        self.assertNotIn(VR_INFO, names)

    def test_the_game_file_lists_only_what_shipped(self) -> None:
        _, pruned = self._shipped(VR)

        listed = [row["file"] for row in
                  pruned[f"{FOLDER}.info"]["vpinfe"]["manifest"]["tables"]]
        self.assertEqual([VR], listed)

    def test_the_table_file_is_unchanged_by_the_trim(self) -> None:
        _, pruned = self._shipped(VR)

        self.assertEqual(json.loads((self.folder / VR_INFO).read_text(encoding="utf-8")),
                         pruned[VR_INFO])

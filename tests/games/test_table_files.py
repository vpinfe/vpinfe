"""Protects: (a) a table's rating, tags and plays survive the split into a file of its own."""

from __future__ import annotations

import json
import os
from pathlib import Path

from common.games.info_file import MetaConfig
from tests.support.library import TempTree, game_info, write_game

FOLDER = "Cactus"
SHARED = "Cactus.vpx"
VR = "Cactus VR.vpx"
GAME_INFO = f"{FOLDER}.info"
VR_INFO = "Cactus VR.info"
LAST = 1700000100


def _record() -> dict:
    return game_info(
        "Cactus", game_id="Split00001",
        User={"Rating": 4, "Favorite": True, "Tags": ["Wide"], "StartCount": 7,
              "LastRun": 1700000000, "RunTime": 30},
        tables={"a": {"id": "a", "filename": SHARED, "user": {"start_count": 5}},
                "b": {"id": "b", "filename": VR,
                      "user": {"rating": 6, "tags": ["VR"], "start_count": 2,
                               "last_run": LAST, "run_time_seconds": 190}}})


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class SplitCase(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=_record(), files={VR: b"vr"})

    def _meta(self) -> MetaConfig:
        return MetaConfig(str(self.folder / GAME_INFO))

    def _write(self) -> MetaConfig:
        meta = self._meta()
        meta.write_config()
        return meta

    def _copies(self, reason: str) -> list[str]:
        return sorted(name for name in os.listdir(self.folder) if name.endswith(f"-{reason}"))

    def _state(self) -> dict[str, bytes]:
        return {name: (self.folder / name).read_bytes() for name in sorted(os.listdir(self.folder))}


class Split(SplitCase):
    def test_a_table_not_named_like_the_folder_gets_a_file_beside_it(self) -> None:
        self._write()

        names = set(os.listdir(self.folder))
        self.assertIn(VR_INFO, names)
        self.assertNotIn("Cactus.vpx.info", names)

    def test_the_table_file_stands_on_its_own_in_vp_units(self) -> None:
        self._write()

        own = _json(self.folder / VR_INFO)

        self.assertEqual("Cactus", own["Info"]["Title"])
        self.assertEqual({"Rating": 6, "Favorite": 1, "Tags": ["Wide", "VR"],
                          "LastRun": "2023-11-14T22:15:00Z", "StartCount": 2, "RunTime": 190},
                         own["User"])
        self.assertEqual({"rating": 6, "tags": ["VR"]}, own["vpinfe"]["table"]["own"])

    def test_the_game_file_keeps_only_who_the_table_is(self) -> None:
        self._write()

        listed = {row["file"]: row for row in
                  _json(self.folder / GAME_INFO)["vpinfe"]["manifest"]["tables"]}

        self.assertEqual({"file": VR, "id": "b"}, listed[VR])
        self.assertEqual(5, listed[SHARED]["user"]["start_count"])

    def test_the_game_file_is_backed_up_before_it_is_rewritten(self) -> None:
        before = (self.folder / GAME_INFO).read_bytes()
        self._write()

        (copy,) = self._copies("split")
        self.assertEqual(before, (self.folder / copy).read_bytes())

    def test_reading_back_gives_the_record_that_was_written(self) -> None:
        meta = self._write()
        written = json.loads(json.dumps(meta.data))

        self.assertEqual(written, json.loads(json.dumps(self._meta().data)))
        entry = self._meta().data["tables"]["b"]
        self.assertEqual({"rating": 6, "tags": ["VR"], "start_count": 2, "last_run": LAST,
                          "run_time_seconds": 190}, entry["user"])

    def test_writing_again_changes_nothing(self) -> None:
        self._write()
        settled = self._state()

        self._write()

        self.assertEqual(settled, self._state())

    def test_a_stop_between_the_two_writes_is_finished_by_the_next(self) -> None:
        self._write()
        (self.folder / GAME_INFO).write_bytes(
            (self.folder / self._copies("split")[0]).read_bytes())
        own = (self.folder / VR_INFO).read_bytes()

        self._write()

        self.assertEqual(own, (self.folder / VR_INFO).read_bytes())
        listed = _json(self.folder / GAME_INFO)["vpinfe"]["manifest"]["tables"]
        self.assertEqual({"file": VR, "id": "b"}, next(r for r in listed if r["file"] == VR))


class Adopt(SplitCase):
    def test_a_file_another_program_wrote_is_backed_up_and_its_numbers_join_ours(self) -> None:
        foreign = {"Info": {"Title": "Cactus VR own title"},
                   "User": {"Rating": 10, "StartCount": 9, "RunTime": 500,
                            "LastRun": "2024-01-01T00:00:00Z"},
                   "VPX": {"Runs": 3}}
        (self.folder / VR_INFO).write_text(json.dumps(foreign), encoding="utf-8")

        self._write()

        (copy,) = self._copies("adopt")
        self.assertEqual(foreign, _json(self.folder / copy))
        own = _json(self.folder / VR_INFO)
        self.assertEqual("Cactus VR own title", own["Info"]["Title"])
        self.assertEqual((9, 500, "2024-01-01T00:00:00Z"),
                         (own["User"]["StartCount"], own["User"]["RunTime"],
                          own["User"]["LastRun"]))
        self.assertEqual({"Runs": 3}, own["VPX"])
        self.assertEqual(10, self._meta().data["tables"]["b"]["user"]["rating"])

    def test_adopting_twice_backs_up_once(self) -> None:
        (self.folder / VR_INFO).write_text(json.dumps({"User": {"StartCount": 1}}),
                                           encoding="utf-8")
        self._write()
        self._write()

        self.assertEqual(1, len(self._copies("adopt")))


class BackToOne(TempTree):
    def test_the_table_that_remains_keeps_its_file_and_its_own_rating_folds_into_the_game(
            self) -> None:
        record = game_info(
            "Cactus", game_id="Back000001", User={"Rating": 2, "Tags": []},
            tables={"x": {"id": "x", "filename": "Cactus 1.1.vpx"},
                    "y": {"id": "y", "filename": "Cactus 1.2.vpx",
                          "user": {"rating": 5, "tags": ["Best"]}}})
        folder = write_game(self.root, FOLDER, info=record, vpx=False,
                            files={"Cactus 1.1.vpx": b"1", "Cactus 1.2.vpx": b"2"})
        meta = MetaConfig(str(folder / GAME_INFO))
        meta.write_config()
        self.assertTrue((folder / "Cactus 1.1.info").exists())
        self.assertTrue((folder / "Cactus 1.2.info").exists())

        meta = MetaConfig(str(folder / GAME_INFO))
        meta.drop_table("x")
        (folder / "Cactus 1.1.vpx").unlink()
        meta.write_config()

        after = MetaConfig(str(folder / GAME_INFO)).data
        self.assertTrue((folder / "Cactus 1.2.info").exists())
        self.assertFalse((folder / "Cactus 1.1.info").exists())
        self.assertEqual((5, ["Best"]), (after["User"]["Rating"], after["User"]["Tags"]))


class Seed(TempTree):
    def test_a_folder_with_only_table_files_is_read_as_a_game(self) -> None:
        folder = write_game(self.root, FOLDER, vpx=False,
                            files={"One.vpx": b"1", "Two.vpx": b"2"})
        for name, stars in (("One", 6), ("Two", 8)):
            (folder / f"{name}.info").write_text(json.dumps(
                {"Info": {"Title": "Cactus"}, "User": {"Rating": stars, "Tags": ["T"]}}),
                encoding="utf-8")

        data = MetaConfig(str(folder / GAME_INFO)).data

        self.assertEqual({"One.vpx", "Two.vpx"},
                         {row["filename"] for row in data["tables"].values()})
        self.assertEqual("Cactus", data["Info"]["Title"])
        self.assertEqual((6, ["T"]), (data["User"]["Rating"], data["User"]["Tags"]))



class Guarded(SplitCase):
    def test_an_unreadable_table_file_is_left_as_it_is(self) -> None:
        self._write()
        (self.folder / VR_INFO).write_text('{"Info": {"Ti', encoding="utf-8")

        meta = self._meta()
        meta.data["User"]["Rating"] = 3
        meta.write_config()

        self.assertEqual('{"Info": {"Ti', (self.folder / VR_INFO).read_text(encoding="utf-8"))

    def test_a_one_table_games_foreign_file_does_not_change_the_game(self) -> None:
        record = game_info("Cactus", game_id="Lone000001", User={"Rating": 4},
                           tables={"a": {"id": "a", "filename": "Cactus Lone.vpx"}})
        folder = write_game(self.root, "Lone", info=record, vpx=False,
                            files={"Cactus Lone.vpx": b"x"})
        (folder / "Cactus Lone.info").write_text(json.dumps({"User": {"Rating": 2}}),
                                                 encoding="utf-8")

        meta = MetaConfig(str(folder / "Lone.info"))
        meta.write_config()

        self.assertEqual(4, MetaConfig(str(folder / "Lone.info")).data["User"]["Rating"])

    def test_a_favorite_changed_in_one_table_file_is_the_games_whatever_the_order(self) -> None:
        record = game_info("Cactus", game_id="Fav0000001", User={"Favorite": False},
                           tables={key: {"id": key, "filename": f"Cactus {key}.vpx"}
                                   for key in ("a", "b", "c")})
        folder = write_game(self.root, "Fav", info=record, vpx=False,
                            files={f"Cactus {key}.vpx": b"x" for key in ("a", "b", "c")})
        MetaConfig(str(folder / "Fav.info")).write_config()
        for key in ("a", "b", "c"):
            own = json.loads((folder / f"Cactus {key}.info").read_text(encoding="utf-8"))
            own["User"]["Favorite"] = 1 if key == "b" else 0
            (folder / f"Cactus {key}.info").write_text(json.dumps(own), encoding="utf-8")

        self.assertTrue(MetaConfig(str(folder / "Fav.info")).data["User"]["Favorite"])

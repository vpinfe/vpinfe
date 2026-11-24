"""The number of settings a table has of its own is the number of chips beside it.

The Tables grid says how many in its Settings column and which in its Own Settings
column. Both are read here for every table of a library whose tables between them hold
each shape a settings file takes, from the API through to the grid's rows.
"""

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import launchers
from manager.library import games
from tests.support.launchers_api import put_launcher
from tests.support.library import TempTree, fake_game, own_launchers, write_game

CAMERA = "[TableOverride]\nViewCabMode = 1\nViewCabFOV = 55\nViewCabLayback = 0\n"
BACKGLASS = "[Backglass]\nBackglassOutput = 0\n"
SUPERSAMPLING = "[Player]\nAAFactor = 2\n"
ALL_TABLES_ONLY = "[Player]\nPlayfieldFullScreen = 1\n"

# By game folder: each table file it holds, and what the settings file beside it says.
# A file named for the folder is the game's, and answers for a table with none of its own.
LIBRARY: dict[str, dict[str, str | None]] = {
    "No File": {"No File.vpx": None},
    "Two Settings": {"Two Settings.vpx": BACKGLASS + SUPERSAMPLING},
    "Camera Alone": {"Camera Alone.vpx": CAMERA},
    "Camera Beside One": {"Camera Beside One.vpx": CAMERA + BACKGLASS},
    "All Tables Only": {"All Tables Only.vpx": ALL_TABLES_ONLY},
    "Shared": {"Shared.vpx": CAMERA + BACKGLASS, "Shared - VR.vpx": None},
    "Own Beside Game": {"Own Beside Game.vpx": BACKGLASS,
                        "Own Beside Game - VR.vpx": CAMERA},
}


class CountIsItsChipsTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        catalog = {}
        for number, (name, tables) in enumerate(LIBRARY.items(), start=1):
            game_id = f"Count{number:07d}"
            info = {"Info": {"Name": name}, "VPinFE": {"game_id": game_id},
                    "tables": {f"t{number}{index}": {"id": f"t{number}{index}",
                                                     "filename": filename}
                               for index, filename in enumerate(tables)}}
            files: dict[str, bytes] = {}
            for filename, ini in tables.items():
                files[filename] = b"vpx"
                if ini is not None:
                    files[f"{filename[:-4]}.ini"] = ini.encode()
            folder = write_game(self.root, name, info=info, vpx=False, files=files)
            catalog[game_id] = fake_game(folder, name, meta=info)
        store = own_launchers(self, str(self.root))
        store.save([], {})
        for patcher in (patch("common.games.game_repository.catalog", return_value=catalog),
                        patch.object(launchers, "get_launcher_store", return_value=store)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        put_launcher(self.client, "vpx", json={"app": "vpx", "display_name": "VPX"})

    def _grid(self) -> dict[str, tuple[int, list[str]]]:
        got = self.client.get("/tables")
        self.assertEqual(got.status_code, 200, got.text)
        return {f"{row['game']} / {row['filename']}":
                (row["settings_count"], row[games.OWN_SETTINGS_COLUMN])
                for row in games.table_rows(got.json()["tables"])}

    def test_every_table_s_count_is_its_chips(self) -> None:
        grid = self._grid()

        self.assertEqual(len(grid), 9)
        differ = {table: said for table, said in grid.items() if said[0] != len(said[1])}
        self.assertEqual(differ, {}, "\n".join(
            f"{table}: {count} against {len(chips)} chips {chips}"
            for table, (count, chips) in differ.items()))

    def test_a_camera_is_one_chip(self) -> None:
        grid = self._grid()

        self.assertEqual(grid["Camera Beside One / Camera Beside One.vpx"],
                         (2, ["Backglass.BackglassOutput", "point_of_view"]))
        self.assertEqual(grid["Camera Alone / Camera Alone.vpx"], (1, ["point_of_view"]))

    def test_the_camera_s_chip_reads_as_the_settings_column_says_a_camera(self) -> None:
        (alone,) = [row for row in games.table_rows(self.client.get("/tables").json()["tables"])
                    if row["game"] == "Camera Alone"]

        self.assertEqual(games.own_setting_names({})["point_of_view"], "Point of View")
        self.assertEqual(games.own_setting_names({})["point_of_view"], alone["settings_said"])

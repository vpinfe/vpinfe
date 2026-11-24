"""A wheel reading another device's library launches this device's own copy."""

from __future__ import annotations

import configparser
from unittest.mock import patch

from common.games import remote_library
from frontend.api import API
from tests.support.library import TempTree, fake_game, write_game
from tests.support.library_loader import library_of, start_library_of

GAME_ID = "Aaaaaaaaa1"
NAME = "Attack from Mars"
TABLE = f"{NAME}.vpx"


class _Ini:
    def __init__(self) -> None:
        self.config = configparser.ConfigParser()
        self.config.add_section("general")
        self.config.set("general", "startup_collection", "")

    def save(self) -> None:
        pass


def _as_the_library_sends_it():
    return remote_library._entry_from_wire({
        "game": {"id": GAME_ID, "dir_name": NAME, "name": NAME},
        "table": {"id": "t1", "filename": TABLE}, "siblings": 1})


class RemoteWheelLaunchTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        meta = {"Info": {"Name": NAME, "Title": NAME}, "User": {},
                "vpinfe": {"game_id": GAME_ID}}
        self.here = fake_game(write_game(self.root, NAME, info=meta), NAME, meta=meta)
        start_library_of(self, [self.here])
        self.api = API(_Ini(), window_name="playfield")  # type: ignore[arg-type]

    def _launch(self):
        with patch.object(self.api, "entry_at", return_value=_as_the_library_sends_it()), \
                patch("frontend.api.launch.launch_game") as launch_game:
            return self.api.launch_table(0), launch_game

    def test_the_wheel_launches_this_devices_copy(self) -> None:
        result, launch_game = self._launch()

        self.assertEqual(result, {"success": True})
        self.assertIs(launch_game.call_args.args[0], self.here)
        self.assertEqual(launch_game.call_args.kwargs["table"], TABLE)

    def test_a_game_this_device_has_no_copy_of_is_refused(self) -> None:
        with library_of([]):
            result, launch_game = self._launch()

        self.assertFalse(result["success"])
        launch_game.assert_not_called()

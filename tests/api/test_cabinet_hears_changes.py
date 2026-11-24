"""A change made anywhere but the cabinet reaches the cabinet.

The whole path, against a real library: a write over HTTP, the announcement, the
broadcast to the windows, and the list they read next.
"""

from __future__ import annotations

import configparser
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import events
from common.games import game_repository
from common.games.game_parser import GameParser
from common.games.library_folders import KIND_ROOT, Location
from frontend import play_events
from frontend.api import API
from frontend.library_resolver import LibraryResolver
from tests.support.library import TempTree, write_game

GAME_ID = "Hear00000001"
FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

INFO: dict[str, Any] = {
    "Info": {"Name": "Cactus Canyon", "Manufacturer": "Bally", "Year": "1998"},
    "vpinfe": {"schema": 2, "game_id": GAME_ID},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
        "tbl0000002": {"id": "tbl0000002", "filename": VR},
    },
}


class _Bridge:
    """The cabinet's side of the socket: the windows it holds, and what it sent them."""

    def __init__(self) -> None:
        self._api_instances: dict[str, API] = {}
        self.sent: list[str] = []

    def send_event_all_with_iframe(self, message: dict) -> None:
        self.sent.append(message["type"])


def _ini() -> SimpleNamespace:
    return SimpleNamespace(config=configparser.ConfigParser(), save=lambda: None)


class CabinetHearsChangesTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, FOLDER, info=INFO, vpx=False,
                   files={DESKTOP: b"vpx", VR: b"vpx"})
        config = configparser.ConfigParser()
        config.read_dict({"Settings": {"gamerootdir": str(self.root)}, "Media": {}})
        parser = GameParser(str(self.root), config)  # type: ignore[arg-type]

        held = mock.patch.object(
            game_repository.library_folders, "configured",
            return_value=[Location(location_id="test", path=str(self.root))])
        held.start()
        self.addCleanup(held.stop)
        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        game_repository._PARSERS[(str(self.root), KIND_ROOT)] = parser
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)

        events.clear()
        play_events.reset_for_tests()
        self.addCleanup(events.clear)
        self.addCleanup(play_events.reset_for_tests)

        self.bridge = _Bridge()
        ini = _ini()
        self.window = API(ini, window_name="playfield", library=LibraryResolver(ini))  # type: ignore[arg-type]
        self.bridge._api_instances["playfield"] = self.window
        with mock.patch.object(play_events, "save_last_launched"):
            play_events.register(self.bridge, None, None)  # type: ignore[arg-type]

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _offered(self) -> set[str]:
        """The tables the wheel steps through, read the way a theme reads them."""
        self.window.get_tables()
        return {entry.table_id for entry in self.window.entries}

    def _settle(self) -> None:
        timer = play_events._change_timer
        if timer is not None:
            timer.join(timeout=5)

    def test_a_table_hidden_over_http_leaves_the_wheel_without_a_reset(self) -> None:
        shown = self._offered()
        self.assertEqual(len(shown), 1)

        response = self.client.put(f"/games/{GAME_ID}/tables/{min(shown)}/hidden",
                                   json={"hidden": True})
        self.assertEqual(response.status_code, 200, response.text)
        self._settle()

        self.assertIn("TableDataChange", self.bridge.sent)
        self.assertEqual(self._offered(), {"tbl0000001", "tbl0000002"} - shown)

    def test_hiding_the_default_moves_the_scans_path_to_what_plays_now(self) -> None:
        (shown,) = self._offered()
        (other,) = {"tbl0000001", "tbl0000002"} - {shown}

        response = self.client.put(f"/games/{GAME_ID}/tables/{shown}/hidden",
                                   json={"hidden": True})
        self.assertEqual(response.status_code, 200, response.text)

        game = game_repository.game_by_id(GAME_ID)
        assert game is not None
        playing = game.full_path_vpx_file
        self.assertEqual(Path(playing).name, INFO["tables"][other]["filename"])

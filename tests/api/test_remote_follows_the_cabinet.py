from __future__ import annotations

import configparser
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.games import game_repository
from common.games.game_parser import GameParser
from common.games.library_folders import KIND_ROOT, Location
from manager.remote import remote
from tests.support.library import TempTree, write_game


def _info(game_id: str, name: str, hidden: bool) -> dict:
    return {
        "Info": {"Title": name, "Manufacturer": "Bally", "Year": "1990"},
        "vpinfe": {"schema": 2, "game_id": game_id},
        "tables": {f"{game_id}t": {"id": f"{game_id}t", "filename": f"{name}.vpx",
                                   "hidden": hidden}},
    }


class RemoteFollowsTheCabinetTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, "Shown", info=_info("Shown0000001", "Shown", False))
        write_game(self.root, "Withheld", info=_info("Hide00000001", "Withheld", True))
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

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_a_game_with_nothing_to_offer_is_left_out(self) -> None:
        response = self.client.get("/library/entries")
        self.assertEqual(response.status_code, 200, response.text)

        listed = remote.offered_games(response.json()["entries"])

        self.assertEqual([one["id"] for one in listed], ["Shown0000001"])

"""A file written through the media routes reaches the game the library holds.

Against a real library: the held game's resolved paths are what a theme and
`/media/<table>/<kind>` serve, so a write that only reaches the disk is not seen.
"""

from __future__ import annotations

import configparser
import io
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import events
from common.extensions.games import withdraw_all
from common.games import game_repository
from common.games.game_parser import GameParser
from common.games.library_folders import KIND_ROOT, Location
from tests.support.library import TempTree, game_info, write_game

GAME_ID = "Art000000001"
FOLDER = "Medieval Madness (Williams 1997)"
WHEEL = f"(Wheel) {FOLDER}.png"


def _png() -> dict:
    return {"file": ("art.png", io.BytesIO(b"\x89PNGwheel"), "image/png")}


class TheLoadedGameKeepsUp(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, FOLDER, info=game_info("Medieval Madness", game_id=GAME_ID))
        config = configparser.ConfigParser()
        config.read_dict({"Settings": {"gamerootdir": str(self.root)}, "Media": {}})
        held = mock.patch.object(
            game_repository.library_folders, "configured",
            return_value=[Location(location_id="test", path=str(self.root))])
        held.start()
        self.addCleanup(held.stop)
        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        game_repository._PARSERS[(str(self.root), KIND_ROOT)] = GameParser(
            str(self.root), config)  # type: ignore[arg-type]
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)

        events.clear()
        self.addCleanup(events.clear)
        self.changed: list[str] = []
        events.subscribe(events.GAME_CHANGED,
                         lambda path="", **_: self.changed.append(Path(path).name))
        withdraw_all()
        self.addCleanup(withdraw_all)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _wheel(self) -> str:
        game = game_repository.game_by_id(GAME_ID)
        assert game is not None
        return Path(str(game.wheel_image_path)).name if game.wheel_image_path else ""

    def test_a_placed_wheel_is_the_one_the_game_shows(self) -> None:
        self.assertEqual(self._wheel(), "")

        response = self.client.put(f"/games/{GAME_ID}/media/wheel", files=_png())

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._wheel(), WHEEL)
        self.assertEqual(self.changed, [FOLDER])

    def test_a_removed_wheel_is_no_longer_shown(self) -> None:
        self.client.put(f"/games/{GAME_ID}/media/wheel", files=_png())
        self.changed.clear()

        response = self.client.delete(f"/games/{GAME_ID}/media/wheel")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._wheel(), "")
        self.assertEqual(self.changed, [FOLDER])

    def test_removing_nothing_announces_nothing(self) -> None:
        self.client.delete(f"/games/{GAME_ID}/media/wheel")

        self.assertEqual(self.changed, [])

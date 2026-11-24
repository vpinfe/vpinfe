"""Marking a game Private over HTTP, one at a time and over a selection.

Against a real library: the flag is written to the game's `.info`, read back on every
row that describes the game, and still there once the library has been read again.
"""

from __future__ import annotations

import configparser
import json
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import events
from common.extensions.games import ExtensionGames, withdraw_all
from common.games import game_repository
from common.games.game_parser import GameParser
from common.games.info_file import MetaConfig
from common.games.library_folders import KIND_ROOT, Location
from tests.support.library import TempTree, game_info, write_game

FIRST, SECOND, THIRD = "Priv00000001", "Priv00000002", "Priv00000003"
FOLDERS = {FIRST: "Medieval Madness (Williams 1997)",
           SECOND: "Twilight Zone (Bally 1993)",
           THIRD: "Cactus Canyon (Bally 1998)"}


class GamePrivateTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        for game_id, folder in FOLDERS.items():
            write_game(self.root, folder,
                       info=game_info(folder.split(" (")[0], game_id=game_id))
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

    def _on_disk(self, game_id: str) -> object:
        """What the `.info` holds, absent as None. The response and the row are both read
        off records a write also updates in memory, so neither shows a file was written."""
        folder = FOLDERS[game_id]
        info = json.loads((self.root / folder / f"{folder}.info").read_text("utf-8"))
        return info["vpinfe"].get("private")

    def _row(self, game_id: str) -> dict:
        (row,) = [one for one in self.client.get("/games").json()["games"]
                  if one["id"] == game_id]
        return row

    def _mark(self, ids: list[str], private: bool = True):
        return self.client.put("/library/private",
                               json={"game_ids": ids, "private": private})

    def test_a_game_nobody_marked_is_not_private_and_nothing_is_written(self) -> None:
        self.assertIs(self._row(FIRST)["private"], False)
        self.assertIs(self.client.get(f"/games/{FIRST}").json()["private"], False)
        self.assertIsNone(self._on_disk(FIRST))

    def test_one_game_marked_private_says_so_everywhere_it_is_described(self) -> None:
        response = self.client.put(f"/games/{FIRST}/private", json={"private": True})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"private": True})
        self.assertIs(self._on_disk(FIRST), True)
        self.assertIs(self.client.get(f"/games/{FIRST}").json()["private"], True)
        self.assertIs(self._row(FIRST)["private"], True)
        self.assertIs(self._row(SECOND)["private"], False)

    def test_and_marked_not_private_again(self) -> None:
        self.client.put(f"/games/{FIRST}/private", json={"private": True})

        response = self.client.put(f"/games/{FIRST}/private", json={"private": False})

        self.assertEqual(response.json(), {"private": False})
        self.assertIs(self._on_disk(FIRST), False)
        self.assertIs(self._row(FIRST)["private"], False)

    def test_a_game_that_does_not_exist_is_a_404(self) -> None:
        response = self.client.put("/games/nosuchgame/private", json={"private": True})

        self.assertEqual(response.status_code, 404)

    def test_a_value_that_is_not_a_yes_or_no_is_refused(self) -> None:
        response = self.client.put(f"/games/{FIRST}/private", json={"private": "maybe"})

        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self._on_disk(FIRST))

    def test_a_selection_is_marked_in_one_request(self) -> None:
        response = self._mark([FIRST, SECOND])

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"private": True, "games": 2, "changed": 2})
        self.assertEqual([self._on_disk(one) for one in FOLDERS], [True, True, None])
        self.assertEqual([self._row(one)["private"] for one in FOLDERS],
                         [True, True, False])

    def test_changed_counts_only_the_games_that_were_not_already_so(self) -> None:
        self._mark([FIRST])

        self.assertEqual(self._mark([FIRST, SECOND, FIRST]).json(),
                         {"private": True, "games": 2, "changed": 1})
        self.assertEqual(self._mark([FIRST, SECOND], private=False).json(),
                         {"private": False, "games": 2, "changed": 2})
        self.assertEqual([self._on_disk(one) for one in FOLDERS], [False, False, None])

    def test_an_id_the_library_does_not_hold_refuses_the_whole_selection(self) -> None:
        """Nothing written, so a selection is never left half Private."""
        response = self._mark([FIRST, "nosuchgame", SECOND])

        self.assertEqual(response.status_code, 404)
        self.assertEqual([self._on_disk(one) for one in FOLDERS], [None, None, None])

    def test_every_game_written_is_announced_as_changed(self) -> None:
        self._mark([FIRST])
        self.changed.clear()

        self._mark([FIRST, SECOND])

        self.assertEqual(self.changed, [FOLDERS[SECOND]],
                         "a game already Private is not written again")

    def test_it_survives_the_library_being_read_again(self) -> None:
        self._mark([FIRST, SECOND])
        self.client.put(f"/games/{SECOND}/private", json={"private": False})

        game_repository.refresh_games()

        self.assertEqual([self._row(one)["private"] for one in FOLDERS],
                         [True, False, False])

    def test_and_a_rebuild_of_the_record_keeps_it(self) -> None:
        self._mark([FIRST])
        folder = FOLDERS[FIRST]
        record = MetaConfig(str(self.root / folder / f"{folder}.info"))

        record.write_config_meta({"vpsdata": None, "vpxdata": {"filename": f"{folder}.vpx"}})

        self.assertIs(self._on_disk(FIRST), True)

    def test_an_extension_reads_it_on_the_rows_it_is_handed(self) -> None:
        self._mark([FIRST])
        reader = ExtensionGames("reader", ("games:read",), None)  # type: ignore[arg-type]

        listed = {one["id"]: one["private"] for one in reader.list_games()["games"]}
        self.assertEqual(listed, {FIRST: True, SECOND: False, THIRD: False})
        self.assertIs(reader.get_game(FIRST)["private"], True)

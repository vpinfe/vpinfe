"""Moving a game's file to its tables and back, over HTTP.

The disk is pinned in `tests/games/test_file_move.py`. What is pinned here is the wire: the
shape of each answer, the status a refusal gets, and that Undo works through the route that
issued the token.
"""

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.backups import folder as backup_folder
from common.games import game_repository, media_ops
from common.host import launch_state
from tests.support.library import TempTree, fake_game, game_info, write_game

GAME_ID = "Moved000002"
FOLDER = "Cactus"
NAMES = {"t1": FOLDER, "t2": "Cactus B", "t3": "Cactus C"}


class FileMoveRouteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = game_info(FOLDER, game_id=GAME_ID, tables={
            key: {"id": key, "filename": f"{name}.vpx"} for key, name in NAMES.items()})
        self.folder = write_game(self.root / "library", FOLDER, info=info, vpx=False,
                                 files={f"{name}.vpx": b"vpx" for name in NAMES.values()}
                                 | {"Cactus.directb2s": b"game"})
        game = fake_game(self.folder, FOLDER, meta=info)
        self.running = launch_state.LaunchState()
        for patcher in (
                patch.object(game_repository, "catalog", return_value={GAME_ID: game}),
                patch.object(backup_folder, "current", return_value=self.root / "backups"),
                patch.object(media_ops, "reread"),
                patch.object(launch_state, "current", side_effect=lambda: self.running)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def url(self, tail: str, family: str = "asset", kind: str = "backglass") -> str:
        return f"/games/{GAME_ID}/moves/{family}/{kind}{tail}"

    def directb2s(self) -> list[str]:
        return sorted(path.name for path in self.folder.glob("*.directb2s"))

    def test_the_state_names_the_games_file_and_each_tables_own(self) -> None:
        (self.folder / "Cactus B.directb2s").write_bytes(b"own")

        response = self.client.get(self.url(""))

        self.assertEqual(response.status_code, 200, response.text)
        found = response.json()
        self.assertEqual(found["game"]["files"], ["Cactus.directb2s"])
        own = {row["stem"]: row["own"] for row in found["tables"]}
        self.assertEqual(own["Cactus B"]["files"], ["Cactus B.directb2s"])
        self.assertIsNone(own["Cactus C"])
        self.assertTrue(found["to_game"])
        self.assertFalse(found["playing"])

    def test_a_kind_that_cannot_be_moved_is_refused(self) -> None:
        self.assertEqual(self.client.get(self.url("", kind="nonsense")).status_code, 400)
        self.assertEqual(self.client.get(self.url("", family="elsewhere")).status_code, 400)

    def test_a_file_goes_to_a_table_and_the_answer_says_who_lost_it(self) -> None:
        response = self.client.post(self.url("/to-tables"), json={"tables": ["t2"]})

        self.assertEqual(response.status_code, 200, response.text)
        found = response.json()
        self.assertEqual(self.directb2s(), ["Cactus B.directb2s"])
        self.assertEqual(found["uses"], ["Cactus B"])
        self.assertEqual(sorted(found["lost"]), ["Cactus", "Cactus C"])
        self.assertFalse(found["stays"])
        self.assertTrue(found["undo"])

    def test_a_table_named_like_the_folder_cannot_be_chosen(self) -> None:
        response = self.client.post(self.url("/to-tables"), json={"tables": ["t1"]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.directb2s(), ["Cactus.directb2s"])

    def test_an_own_file_goes_to_the_game_and_the_one_it_replaces_is_kept(self) -> None:
        (self.folder / "Cactus B.directb2s").write_bytes(b"own")

        response = self.client.post(self.url("/to-game"), json={"table": "t2"})

        self.assertEqual(response.status_code, 200, response.text)
        found = response.json()
        self.assertEqual(self.directb2s(), ["Cactus.directb2s"])
        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"own")
        self.assertEqual([one["file"] for one in found["replaced"]], ["Cactus.directb2s"])
        self.assertEqual(sorted(found["changed_for"]), ["Cactus", "Cactus C"])

    def test_a_point_of_view_does_not_go_to_the_game(self) -> None:
        (self.folder / "Cactus B.pov").write_bytes(b"pov")

        response = self.client.post(self.url("/to-game", kind="pov"), json={"table": "t2"})

        self.assertEqual(response.status_code, 400)

    def test_a_table_settings_file_does_not_go_to_the_game(self) -> None:
        (self.folder / "Cactus B.ini").write_bytes(b"[Player]")

        response = self.client.post(self.url("/to-game", kind="ini"), json={"table": "t2"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.folder / "Cactus.ini").exists())

    def test_a_table_playing_blocks_the_move_with_a_conflict(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        response = self.client.post(self.url("/to-tables"), json={"tables": ["t2"]})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.directb2s(), ["Cactus.directb2s"])

    def test_the_token_undoes_the_move_once(self) -> None:
        token = self.client.post(self.url("/to-tables"), json={"tables": ["t2"]}).json()["undo"]

        first = self.client.post(f"/games/{GAME_ID}/moves/undo", json={"undo": token})
        second = self.client.post(f"/games/{GAME_ID}/moves/undo", json={"undo": token})

        self.assertEqual((first.status_code, first.json()), (200, {"undone": True}))
        self.assertEqual(second.status_code, 404)
        self.assertEqual(self.directb2s(), ["Cactus.directb2s"])

    def test_a_moved_backglass_file_settles_the_game_s_order_and_its_undo_does_again(self) -> None:
        with patch("common.games.backglass_order.settle") as settle:
            token = self.client.post(self.url("/to-tables"),
                                     json={"tables": ["t2"]}).json()["undo"]
            self.assertEqual(settle.call_count, 1)

            self.client.post(f"/games/{GAME_ID}/moves/undo", json={"undo": token})
            self.assertEqual(settle.call_count, 2)

            (self.folder / "Cactus B.directb2s").write_bytes(b"own")
            self.client.post(self.url("/to-game"), json={"table": "t2"})

        settle.assert_called_with(GAME_ID)
        self.assertEqual(settle.call_count, 3)

    def test_a_moved_file_that_is_not_a_backglass_settles_nothing(self) -> None:
        (self.folder / "Cactus B.pov").write_bytes(b"pov")
        medias = self.folder / "medias"
        medias.mkdir()
        (medias / "(Flyer) Cactus.png").write_bytes(b"one")

        with patch("common.games.backglass_order.settle") as settle:
            self.client.post(self.url("/to-tables", family="media", kind="flyer"),
                             json={"tables": ["t2"]})
            self.client.post(self.url("/to-tables", kind="pov"), json={"tables": ["t3"]})

        settle.assert_not_called()

    def test_media_moves_by_the_same_route(self) -> None:
        medias = self.folder / "medias"
        medias.mkdir()
        (medias / "(Flyer) Cactus.png").write_bytes(b"one")
        (medias / "(Flyer 2) Cactus.png").write_bytes(b"two")

        response = self.client.post(self.url("/to-tables", family="media", kind="flyer"),
                                    json={"tables": ["t2"]})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sorted(path.name for path in medias.iterdir()),
                         ["(Flyer 2) Cactus B.png", "(Flyer) Cactus B.png"])

"""A table's own tags, and a rename that reaches them."""

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import library_ops
from common.games.game_metadata import retag_library, table_tags
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.saved import saved_record

GAME_ID = "TblTags001"
NAME = "Attack from Mars (Bally 1995)"


class TableTags(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = game_info("Attack from Mars", game_id=GAME_ID, User={"Tags": ["Wide Body"]},
                         tables={"t1": {"id": "t1", "filename": f"{NAME}.vpx"},
                                 "t2": {"id": "t2", "filename": "AFM VR.vpx"}})
        self.folder = write_game(self.root, NAME, info=info,
                                 files={"AFM VR.vpx": b"not really a vpx"})
        self.game = fake_game(self.folder, NAME, meta=info)
        for target, value in (("common.games.game_repository.catalog",
                               lambda: {GAME_ID: self.game}),
                              ("common.games.game_repository.all_games",
                               lambda reload=False: [self.game]),
                              ("common.config.paths.TAGS_PATH", self.root / "tags.json")):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _on_disk(self) -> dict:
        return saved_record(self.folder / f"{NAME}.info")

    def _put(self, table: str, tags: list[str]):
        return self.client.put(f"/games/{GAME_ID}/tables/{table}/tags", json={"tags": tags})

    def test_a_table_keeps_its_own_set(self) -> None:
        response = self._put("t2", ["VR", " VR ", "Kids"])

        self.assertEqual((200, ["VR", "Kids"]), (response.status_code,
                                                 response.json()["tags"]))
        self.assertEqual(["VR", "Kids"], self._on_disk()["tables"]["t2"]["user"]["tags"])

    def test_they_read_back_from_the_file(self) -> None:
        self._put("t2", ["VR"])

        self.assertEqual(["VR"], table_tags(self._on_disk()["tables"]["t2"]))

    def test_the_table_resource_carries_them(self) -> None:
        self._put("t2", ["VR"])
        self.game.meta_config = self._on_disk()

        found = {one["id"]: one["user"].get("tags") for one in
                 self.client.get(f"/games/{GAME_ID}/tables").json()["tables"]}

        self.assertEqual(["VR"], found["t2"])

    def test_a_rename_reaches_a_table(self) -> None:
        self._put("t2", ["vr", "Kids"])

        changed = retag_library([self.game], ["vr"], "VR")

        self.assertEqual((1, ["VR", "Kids"], ["Wide Body"]),
                         (changed, self._on_disk()["tables"]["t2"]["user"]["tags"],
                          self._on_disk()["User"]["Tags"]))

    def test_a_tag_only_a_table_carries_counts_that_tables_game(self) -> None:
        self._put("t2", ["VR"])
        self.game.meta_config = self._on_disk()

        said = {one["name"]: (one["games"], one["tables"])
                for one in library_ops.tags()["tags"]}

        self.assertEqual({"VR": (1, 1), "Wide Body": (1, 0)}, said)

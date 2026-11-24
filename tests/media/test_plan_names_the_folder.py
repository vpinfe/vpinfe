"""A new game's plan, asked again under the folder name somebody typed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.uploads import asset_import_service, upload_ops
from tests.support.library import TempTree

NEW = "New Name (Mfg 2000)"


class APlanUnderANewFolderName(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()
        (self.incoming / "Old Name (Mfg 1999).vpx").write_bytes(b"x")
        (self.incoming / "Old Name (Mfg 1999).info").write_text(json.dumps({}))
        (self.incoming / "wheel.png").write_bytes(b"x")
        games = self.root / "games"
        games.mkdir()
        roots = [{"path": str(self.root.resolve()), "name": "root", "source": "library"}]
        for patcher in (mock.patch("common.games.media_browse.roots", return_value=roots),
                        mock.patch.object(asset_import_service, "_new_games_under",
                                          return_value=str(games))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _planned(self, **asked: Any) -> Any:
        upload = upload_ops.begin_from(str(self.incoming))["id"]
        self.addCleanup(upload_ops.abort, upload)
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        return client.post(f"/uploads/{upload}/plan", json={"allow_new_game": True, **asked})

    def test_it_names_the_files_under_the_folder_name_sent(self) -> None:
        response = self._planned(new_game_dir_name=NEW)

        self.assertEqual(200, response.status_code, response.text)
        plan = response.json()
        names = {item["kind"]: Path(item["destination"]).name for item in plan["items"]}
        self.assertEqual(NEW, plan["new_game_dir_name"])
        self.assertEqual(f"(Wheel) {NEW}.png", names["media"])
        self.assertEqual(f"{NEW}.info", names["game_info"])

    def test_a_name_the_import_would_refuse_is_refused(self) -> None:
        self.assertEqual(400, self._planned(new_game_dir_name="  ").status_code)

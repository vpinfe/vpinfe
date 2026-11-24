"""An upload that adds a table to a game, asked for over the API."""

from __future__ import annotations

from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.uploads import asset_import_service, upload_ops
from tests.support.library import TempTree, game_info
from tests.support.saved import save_record, saved_record


class AddTableRouteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.game = self.root / "Foo (Bar 1999)"
        self.game.mkdir()
        (self.game / "Foo.vpx").write_bytes(b"old")
        self.info = self.game / "Foo (Bar 1999).info"
        save_record(self.info, game_info("Foo", tables={
            "t-old": {"id": "t-old", "filename": "Foo.vpx"}}))
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "Foo 1.2.vpx").write_bytes(b"new")
        (incoming / "Foo 1.2.directb2s").write_bytes(b"new-b2s")
        roots = [{"path": str(self.root.resolve()), "name": "root", "source": "library"}]
        for patched in (mock.patch("common.games.media_browse.roots", return_value=roots),
                        mock.patch.object(asset_import_service, "refresh_game"),
                        mock.patch("common.games.library_enrichment.read_one",
                                   return_value={})):
            patched.start()
            self.addCleanup(patched.stop)
        self.upload = upload_ops.begin_from(str(incoming))["id"]
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.asked = {"game_dir": str(self.game), "add_table": True}

    def test_the_plan_adds_it(self) -> None:
        self.addCleanup(upload_ops.abort, self.upload)

        response = self.client.post(f"/uploads/{self.upload}/plan", json=self.asked)

        self.assertEqual(200, response.status_code, response.text)
        actions = {item["kind"]: item["action"] for item in response.json()["items"]}
        self.assertEqual("add_table", actions["table"])

    def test_the_import_names_what_it_added(self) -> None:
        response = self.client.post(f"/uploads/{self.upload}/import", json=self.asked)

        self.assertEqual(200, response.status_code, response.text)
        added = response.json()["added_tables"]
        saved = saved_record(self.info)["tables"]
        self.assertEqual(1, len(added))
        self.assertEqual("Foo 1.2.vpx", saved[added[0]]["filename"])
        self.assertEqual(b"old", (self.game / "Foo.vpx").read_bytes())

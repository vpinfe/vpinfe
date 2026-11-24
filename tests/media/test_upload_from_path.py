"""A folder or an archive already on this machine, imported where it is."""

from __future__ import annotations

import io
import shutil
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.uploads import asset_import_service, upload_ops
from tests.support.library import TempTree


class _Here(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.game = self.root / "Foo (Bar 1999)"
        self.game.mkdir()
        (self.game / "Foo.vpx").write_bytes(b"x")
        self.incoming = self.root / "incoming"
        (self.incoming / "MyPup" / "s1").mkdir(parents=True)
        (self.incoming / "MyPup" / "screens.pup").write_bytes(b"x" * 16)
        (self.incoming / "MyPup" / "s1" / "a.mp4").write_bytes(b"x" * 16)
        (self.incoming / "wheel.png").write_bytes(b"x" * 16)
        roots = [{"path": str(self.root.resolve()), "name": "root", "source": "library"}]
        patcher = mock.patch("common.games.media_browse.roots", return_value=roots)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _begun(self) -> str:
        upload = upload_ops.begin_from(str(self.incoming))["id"]
        self.addCleanup(upload_ops.abort, upload)
        return upload


class FromPathTests(_Here):
    def test_it_is_planned_where_it_is(self) -> None:
        plan = upload_ops.plan_for(self._begun(),
                                   {"game_dir": str(self.game), "asset_kind": "pup_pack"})

        self.assertEqual(["pup_pack"], [item["kind"] for item in plan["items"]])

    def test_importing_it_leaves_the_original_where_it_was(self) -> None:
        with mock.patch.object(asset_import_service, "refresh_game"):
            upload_ops.execute(self._begun(),
                               {"game_dir": str(self.game), "asset_kind": "pup_pack"})

        self.assertTrue(any(self.game.rglob("screens.pup")))
        self.assertTrue((self.incoming / "MyPup" / "screens.pup").is_file())

    def test_ending_the_session_leaves_it_too(self) -> None:
        upload_ops.abort(self._begun())

        self.assertTrue((self.incoming / "wheel.png").is_file())

    def test_a_folder_outside_what_may_be_read_is_refused(self) -> None:
        outside = self.root.parent / f"{self.root.name}-elsewhere"
        outside.mkdir()
        self.addCleanup(outside.rmdir)

        with self.assertRaises(service_errors.RefusedError):
            upload_ops.begin_from(str(outside))

    def test_nothing_can_be_uploaded_into_it(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            upload_ops.add_file(self._begun(), "more.png", io.BytesIO(b"x"))


class FromPathRouteTests(_Here):
    def test_the_route_begins_one_over_the_folder(self) -> None:
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        response = client.post("/uploads/from_path", json={"path": str(self.incoming)})

        self.assertEqual(200, response.status_code, response.text)
        upload = response.json()["id"]
        self.addCleanup(upload_ops.abort, upload)
        self.assertEqual(3, client.get(f"/uploads/{upload}").json()["file_count"])

    def test_a_path_with_nothing_there_is_refused(self) -> None:
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        response = client.post("/uploads/from_path",
                               json={"path": str(self.root / "not-here")})

        self.assertEqual(400, response.status_code)


class FromThisDeviceRouteTests(_Here):
    """`POST /folders/uploads`: the same session over a place the library does not read."""

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.elsewhere = self.root.parent / f"{self.root.name}-elsewhere"
        (self.elsewhere / "Pup").mkdir(parents=True)
        (self.elsewhere / "Pup" / "screens.pup").write_bytes(b"x" * 16)
        self.addCleanup(shutil.rmtree, self.elsewhere, True)

    def test_a_folder_outside_every_root_is_read_where_it_is(self) -> None:
        response = self.client.post("/folders/uploads", json={"path": str(self.elsewhere)})

        self.assertEqual(200, response.status_code, response.text)
        upload = response.json()["id"]
        self.addCleanup(upload_ops.abort, upload)
        self.assertEqual(1, self.client.get(f"/uploads/{upload}").json()["file_count"])
        self.assertTrue((self.elsewhere / "Pup" / "screens.pup").is_file())

    def test_the_route_for_a_program_on_the_network_stays_bounded(self) -> None:
        response = self.client.post("/uploads/from_path", json={"path": str(self.elsewhere)})

        self.assertEqual(400, response.status_code)

    def test_a_network_caller_is_refused(self) -> None:
        with mock.patch("httpapi.folders.caller_is_local", return_value=False):
            response = self.client.post("/folders/uploads",
                                        json={"path": str(self.elsewhere)})

        self.assertEqual(403, response.status_code)

    def test_a_private_folder_is_refused(self) -> None:
        private = self.elsewhere / ".ssh"
        private.mkdir()
        (private / "key.png").write_bytes(b"x")

        with mock.patch("common.media.folder_browse.Path.home", return_value=self.elsewhere):
            response = self.client.post("/folders/uploads", json={"path": str(private)})

        self.assertEqual(400, response.status_code)

    def test_a_path_with_nothing_there_is_not_found(self) -> None:
        response = self.client.post("/folders/uploads",
                                    json={"path": str(self.elsewhere / "not-here")})

        self.assertEqual(404, response.status_code)

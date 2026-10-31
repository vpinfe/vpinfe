"""A picture in an upload that has not been imported yet, drawn by the dialog that asks
whether to import it."""

from __future__ import annotations

import io
import zipfile
from unittest import mock

from PIL import Image
from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.media import sized_media
from common.uploads import upload_ops, upload_session_service
from tests.support.library import TempTree


def _png(width: int = 800, height: int = 400) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(out, "PNG")
    return out.getvalue()


class _Staged(TempTree):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(sized_media, "ROOT", self.root / "sized")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.upload = upload_ops.begin()["id"]
        self.addCleanup(upload_ops.abort, self.upload)
        upload_ops.add_file(self.upload, "(RuleSheet) 300.png", io.BytesIO(_png()))
        upload_ops.add_file(self.upload, "table.vpx", io.BytesIO(b"x"))
        upload_ops.add_file(self.upload, "in/a folder/inside.png", io.BytesIO(_png()))


class PictureTests(_Staged):
    def test_a_loose_image_is_served_at_the_size_asked_for(self) -> None:
        served = upload_ops.picture(self.upload, "(RuleSheet) 300.png", 256)

        with Image.open(served.path) as drawn:
            self.assertEqual(256, max(drawn.size))

    def test_without_a_size_it_is_the_file_itself(self) -> None:
        served = upload_ops.picture(self.upload, "(RuleSheet) 300.png", None)

        with Image.open(served.path) as drawn:
            self.assertEqual((800, 400), drawn.size)

    def test_an_image_in_a_folder_of_the_drop_is_found_by_its_name(self) -> None:
        served = upload_ops.picture(self.upload, "inside.png", 256)

        self.assertTrue(served.path.is_file())

    def test_a_file_that_is_not_an_image_has_no_picture(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            upload_ops.picture(self.upload, "table.vpx", 256)

    def test_a_name_that_is_not_in_the_upload_has_no_picture(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            upload_ops.picture(self.upload, "nothing.png", 256)

    def test_a_name_that_climbs_out_of_the_upload_has_no_picture(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            upload_ops.picture(self.upload, "../../outside.png", 256)

    def test_an_upload_that_is_not_there_is_not_found(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            upload_ops.picture("not-an-upload", "a.png", 256)

    def test_a_size_the_api_does_not_cut_is_refused(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            upload_ops.picture(self.upload, "(RuleSheet) 300.png", 100)


class ArchivedPictureTests(TempTree):
    """A picture inside an archive is not on disk until it is asked for, and is then taken out
    once into a folder of its own that goes with the session."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(sized_media, "ROOT", self.root / "sized")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.archive = self.root / "wheels.zip"
        with zipfile.ZipFile(self.archive, "w") as held:
            held.writestr("pack/(Wheel) A.png", _png(600, 300))
            held.writestr("pack/readme.txt", b"hello")
        self.upload = upload_ops.begin_over(self.archive)["id"]
        self.addCleanup(upload_ops.abort, self.upload)

    def test_a_member_is_served_at_the_size_asked_for(self) -> None:
        served = upload_ops.picture(self.upload, "pack/(Wheel) A.png", 256)

        with Image.open(served.path) as drawn:
            self.assertEqual(256, max(drawn.size))

    def test_a_member_is_found_by_its_path_in_the_archive(self) -> None:
        served = upload_ops.picture(self.upload, "pack/(Wheel) A.png", None)

        with Image.open(served.path) as drawn:
            self.assertEqual((600, 300), drawn.size)

    def test_a_member_that_is_not_a_picture_has_none(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            upload_ops.picture(self.upload, "pack/readme.txt", 256)

    def test_a_member_the_archive_does_not_hold_has_none(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            upload_ops.picture(self.upload, "pack/(Wheel) B.png", 256)

    def test_what_was_taken_out_goes_with_the_session(self) -> None:
        served = upload_ops.picture(self.upload, "pack/(Wheel) A.png", None)
        folder = upload_session_service.picture_folder(self.upload)

        self.assertEqual(folder, served.path.parent)
        upload_ops.abort(self.upload)
        self.assertFalse(folder.exists())

    def test_a_member_of_a_dropped_folder_is_found_by_its_path(self) -> None:
        folder = self.root / "pack"
        (folder / "art").mkdir(parents=True)
        (folder / "art" / "(Wheel) A.png").write_bytes(_png(600, 300))
        upload = upload_ops.begin_over(folder)["id"]
        self.addCleanup(upload_ops.abort, upload)

        served = upload_ops.picture(upload, "art/(Wheel) A.png", 256)

        with Image.open(served.path) as drawn:
            self.assertEqual(256, max(drawn.size))

    def test_a_name_with_no_folder_is_the_file_at_the_top_of_a_dropped_folder(self) -> None:
        folder = self.root / "pack"
        (folder / "alt").mkdir(parents=True)
        (folder / "wheel.png").write_bytes(_png(600, 300))
        (folder / "alt" / "wheel.png").write_bytes(_png(100, 100))
        upload = upload_ops.begin_over(folder)["id"]
        self.addCleanup(upload_ops.abort, upload)

        served = upload_ops.picture(upload, "wheel.png", None)

        with Image.open(served.path) as drawn:
            self.assertEqual((600, 300), drawn.size)

    def test_a_damaged_member_has_no_picture_now_or_on_the_next_ask(self) -> None:
        damaged = self.root / "damaged.zip"
        payload = _png(600, 300)
        with zipfile.ZipFile(damaged, "w", zipfile.ZIP_STORED) as held:
            held.writestr("pack/(Wheel) A.png", payload)
        raw = bytearray(damaged.read_bytes())
        at = raw.index(payload) + len(payload) // 2
        raw[at] ^= 0xFF
        damaged.write_bytes(bytes(raw))
        upload = upload_ops.begin_over(damaged)["id"]
        self.addCleanup(upload_ops.abort, upload)

        for _ in range(2):
            with self.assertRaises(service_errors.NotFoundError):
                upload_ops.picture(upload, "pack/(Wheel) A.png", None)

    def test_a_browser_upload_of_an_archive_is_read_the_same_way(self) -> None:
        upload = upload_ops.begin()["id"]
        self.addCleanup(upload_ops.abort, upload)
        with open(self.archive, "rb") as handle:
            upload_ops.add_file(upload, "wheels.zip", handle)

        served = upload_ops.picture(upload, "pack/(Wheel) A.png", None)

        self.assertTrue(served.path.is_file())


class PictureRouteTests(_Staged):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_route_sends_the_picture(self) -> None:
        response = self.client.get(f"/uploads/{self.upload}/picture",
                                   params={"name": "(RuleSheet) 300.png", "size": 256})

        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.headers["content-type"].startswith("image/"))

    def test_the_route_is_a_404_for_a_file_that_is_not_a_picture(self) -> None:
        response = self.client.get(f"/uploads/{self.upload}/picture",
                                   params={"name": "table.vpx"})

        self.assertEqual(404, response.status_code)

    def test_the_route_needs_a_name(self) -> None:
        response = self.client.get(f"/uploads/{self.upload}/picture")

        self.assertEqual(422, response.status_code)

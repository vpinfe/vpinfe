"""The one picture an upload holds, for a place that takes exactly one."""

from __future__ import annotations

import io

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.uploads import upload_ops
from tests.media.test_upload_picture import _png
from tests.support.library import TempTree


class _Session(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.upload = upload_ops.begin()["id"]
        self.addCleanup(upload_ops.abort, self.upload)

    def _add(self, relpath: str, body: bytes | None = None) -> None:
        upload_ops.add_file(self.upload, relpath, io.BytesIO(body if body is not None else _png()))


class SingleImageTests(_Session):
    def test_one_loose_image_is_the_picture(self) -> None:
        self._add("Wheel.PNG")

        self.assertEqual("Wheel.PNG", upload_ops.single_image(self.upload).name)

    def test_two_files_are_refused(self) -> None:
        self._add("a.png")
        self._add("b.png")

        with self.assertRaises(service_errors.RefusedError):
            upload_ops.single_image(self.upload)

    def test_a_file_that_is_not_an_image_is_refused_by_name(self) -> None:
        self._add("table.vpx", b"x")

        with self.assertRaises(service_errors.RefusedError) as caught:
            upload_ops.single_image(self.upload)
        self.assertIn("table.vpx", str(caught.exception))

    def test_an_empty_session_is_refused(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            upload_ops.single_image(self.upload)


class SingleImageRouteTests(_Session):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_route_sends_the_file_named_picture_with_its_own_suffix(self) -> None:
        self._add("Whatever it was called.PNG")

        response = self.client.get(f"/uploads/{self.upload}/image")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(_png(), response.content)
        self.assertIn('filename="picture.png"', response.headers["content-disposition"])

    def test_the_route_is_a_400_for_two_files(self) -> None:
        self._add("a.png")
        self._add("b.png")

        self.assertEqual(400, self.client.get(f"/uploads/{self.upload}/image").status_code)

    def test_the_route_is_a_404_for_a_session_that_is_not_there(self) -> None:
        self.assertEqual(404, self.client.get("/uploads/not-an-upload/image").status_code)

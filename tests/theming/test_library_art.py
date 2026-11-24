"""The pictures of a shared library, fetched by the device that plays it.

Two real servers: the install that holds the library, which answers only a caller it was
paired with, and this device's `/library/<path>`, which a window asks instead. The
non-local caller is patched in, because a loopback request is always local.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from typing import Any
from unittest import mock

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from frontend import library_art

PICTURE = b"\x89PNG library picture"
TOKEN = {"Authorization": "Bearer paired"}


def _serve(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, int]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("a test server did not start")
        time.sleep(0.02)
    return server, thread, port


class Library:
    """Stands in for the install that holds the library."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.unsatisfiable = False
        self.app = FastAPI()

        @self.app.get("/{path:path}")
        def anything(request: Request, path: str) -> Response:
            raw = request.scope["raw_path"].decode("latin-1")
            self.asked.append({"path": raw, "query": request.url.query,
                               "headers": dict(request.headers)})
            if request.headers.get("authorization") != TOKEN["Authorization"]:
                return PlainTextResponse("sign in", status_code=401)
            if "gone" in path:
                return PlainTextResponse("no", status_code=404)
            if request.headers.get("if-none-match") == '"v1"':
                return Response(status_code=304, headers={"ETag": '"v1"'})
            if self.unsatisfiable:
                return Response(status_code=416, headers={"Content-Range": "bytes */20"})
            if request.headers.get("range"):
                return Response(PICTURE[:4], status_code=206, media_type="image/png",
                                headers={"Content-Range": f"bytes 0-3/{len(PICTURE)}"})
            return Response(PICTURE, media_type="image/png", headers={"ETag": '"v1"'})

    def __enter__(self) -> Library:
        self._server, self._thread, self.port = _serve(self.app)
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


class Device:
    """This device's HTTP app, reading its library from `library_url`."""

    def __init__(self, library_url: str) -> None:
        self.library_url = library_url
        self.app = FastAPI()
        library_art.register(self.app, lambda: self.library_url)

    def __enter__(self) -> Device:
        self._server, self._thread, self.port = _serve(self.app)
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, Any]:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/library/{path}",
                                         headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return answer.status, answer.read(), answer.headers
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read(), exc.headers


class ArtPaths(unittest.TestCase):
    def test_the_routes_the_library_draws_its_pictures_from_are_art(self) -> None:
        for path in ("api/v1/collections/Favorites/image",
                     "api/v1/collections/Hub%20%26%20Picks/image",
                     "api/v1/games/g1/media/wheel",
                     "api/v1/games/g1/tables/t1/media/wheel",
                     "media/t1/backglass", "manufacturers/bally/logo",
                     "tables/Twilight Zone (Bally 1993)/media/wheel.png"):
            with self.subTest(path=path):
                self.assertTrue(library_art.is_art(path))

    def test_anything_else_the_library_offers_is_not(self) -> None:
        for path in ("", "api/v1/library/entries", "api/v1/collections/Favorites/entries",
                     "api/v1/games/g1", "api/v1/games/g1/media/wheel/placements",
                     "api/v1/settings", "app/table", "core/common/vpinfe-core.js",
                     "api/v1/collections//image", "api/v1/games/g1/media/wheel%2Fplacements",
                     "api/v1/games/g1/media/wheel%2fdetail",
                     "api/v1/games/g1%2Fmedia%2Fx/media/wheel"):
            with self.subTest(path=path):
                self.assertFalse(library_art.is_art(path))

    def test_a_climb_out_of_a_route_is_not_art(self) -> None:
        for path in ("api/v1/games/%2e%2e/media/wheel", "api/v1/games/../media/wheel",
                     "tables/x/../../api/v1/settings", "tables/x/..%2f..%2fsecret",
                     "media/%2E/wheel", "tables/x/a\\b"):
            with self.subTest(path=path):
                self.assertFalse(library_art.is_art(path))


class FetchedForTheWindow(unittest.TestCase):
    def setUp(self) -> None:
        self.library = Library().__enter__()
        self.addCleanup(self.library.__exit__)
        self.device = Device(self.library.url).__enter__()
        self.addCleanup(self.device.__exit__)
        paired = mock.patch.object(library_art, "paired_header", return_value=TOKEN)
        paired.start()
        self.addCleanup(paired.stop)

    def test_a_picture_comes_back_as_the_library_sent_it(self) -> None:
        status, body, headers = self.device.get("api/v1/games/g1/tables/t1/media/wheel")

        self.assertEqual((status, body), (200, PICTURE))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["ETag"], '"v1"')

    def test_the_library_is_asked_as_the_install_it_was_paired_with(self) -> None:
        self.device.get("api/v1/games/g1/media/wheel")

        self.assertEqual(self.library.asked[-1]["headers"]["authorization"],
                         TOKEN["Authorization"])

    def test_the_query_and_the_encoded_name_reach_the_library_as_sent(self) -> None:
        self.device.get("api/v1/collections/Hub%20%26%20Picks/image?v=7")

        asked = self.library.asked[-1]
        self.assertEqual(asked["path"], "/api/v1/collections/Hub%20%26%20Picks/image")
        self.assertEqual(asked["query"], "v=7")

    def test_a_window_that_holds_the_picture_is_told_it_still_does(self) -> None:
        status, body, headers = self.device.get("api/v1/games/g1/media/wheel",
                                                {"If-None-Match": '"v1"'})

        self.assertEqual((status, body), (304, b""))
        self.assertEqual(headers["ETag"], '"v1"')

    def test_a_range_is_passed_on_and_answered_as_one(self) -> None:
        status, body, headers = self.device.get("tables/Twilight/media/clip.png",
                                                {"Range": "bytes=0-3"})

        self.assertEqual((status, body), (206, PICTURE[:4]))
        self.assertEqual(headers["Content-Range"], f"bytes 0-3/{len(PICTURE)}")

    def test_a_range_the_library_cannot_satisfy_is_told_so(self) -> None:
        self.library.unsatisfiable = True

        self.assertEqual(self.device.get("tables/Twilight/media/clip.png",
                                         {"Range": "bytes=9-12"})[0], 416)

    def test_a_picture_the_library_does_not_have_is_missing_here_too(self) -> None:
        self.assertEqual(self.device.get("api/v1/games/gone/media/wheel")[0], 404)

    def test_a_route_that_is_not_art_is_never_forwarded(self) -> None:
        asked = len(self.library.asked)

        for path in ("api/v1/library/entries", "api/v1/games/%2e%2e/media/wheel"):
            self.assertEqual(self.device.get(path)[0], 404, path)

        self.assertEqual(len(self.library.asked), asked)

    def test_a_caller_that_is_not_this_device_is_refused(self) -> None:
        asked = len(self.library.asked)

        with mock.patch.object(library_art.auth, "caller_is_local", return_value=False):
            status = self.device.get("api/v1/games/g1/media/wheel")[0]

        self.assertEqual(status, 403)
        self.assertEqual(len(self.library.asked), asked)


class WhenTheLibraryCannotGive(unittest.TestCase):
    def test_a_library_that_refuses_this_device_is_a_refusal(self) -> None:
        with Library() as library, Device(library.url) as device, \
                mock.patch.object(library_art, "paired_header", return_value=None):
            self.assertEqual(device.get("api/v1/games/g1/media/wheel")[0], 403)

    def test_a_library_that_cannot_be_reached_is_a_bad_gateway(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        with Device(f"http://127.0.0.1:{closed}") as device, \
                mock.patch.object(library_art, "paired_header", return_value=TOKEN):
            self.assertEqual(device.get("api/v1/games/g1/media/wheel")[0], 502)

    def test_a_device_with_a_library_of_its_own_has_nothing_to_fetch(self) -> None:
        with Device("") as device:
            self.assertEqual(device.get("api/v1/games/g1/media/wheel")[0], 404)


if __name__ == "__main__":
    unittest.main()

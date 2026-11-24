"""Art at the size it is drawn, and kept for as long as the file behind it is."""

from __future__ import annotations

import io
import os
from typing import Any, cast
from unittest.mock import patch

from PIL import Image
from starlette.testclient import TestClient

import httpapi
from common.games.collection_store import CollectionStore
from common.media import sized_media
from httpapi.responses import FOREVER
from manager.kit import art
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Sized000001"
FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
               "tbl0000002": {"id": "tbl0000002", "filename": VR}},
}


def _picture(width: int, height: int, *, mode: str = "RGB",
             fmt: str = "PNG") -> bytes:
    picture = Image.new(mode, (width, height), (200, 40, 40, 255)[:len(mode)])
    if mode == "RGBA":
        picture.paste((0, 0, 0, 0), (0, 0, width // 2, height))
    out = io.BytesIO()
    picture.save(out, format=fmt)
    return out.getvalue()


def _animation(fmt: str) -> bytes:
    """Red then blue, each frame with a see-through corner, as a wheel would have."""
    frames = []
    for color in ((255, 0, 0, 255), (0, 0, 255, 255)):
        frame = Image.new("RGBA", (600, 300), color)
        frame.paste((0, 0, 0, 0), (0, 0, 60, 60))
        frames.append(frame)
    out = io.BytesIO()
    frames[0].save(out, format=fmt, save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    return out.getvalue()


ANIMATED = {"flyer": "flyer.gif", "scoreview": "dmd.webp"}


def _opened(response) -> Image.Image:
    return Image.open(io.BytesIO(response.content))


def _rgba(picture: Image.Image, at: tuple[int, int]) -> tuple[int, ...]:
    pixel = picture.convert("RGBA").getpixel(at)
    assert isinstance(pixel, tuple)
    return pixel


class _Sized(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.copies = self.root / "sized"
        patcher = patch.object(sized_media, "ROOT", self.copies)
        patcher.start()
        self.addCleanup(patcher.stop)


class MediaSizeTests(_Sized):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(
            self.root, FOLDER, info=INFO, vpx=False,
            files={DESKTOP: b"vpx", VR: b"vpx"},
            medias={"table.png": _picture(2000, 1000),
                    f"(Playfield) {FOLDER} - VR.png": _picture(1200, 600),
                    "wheel.png": _picture(600, 600, mode="RGBA"),
                    "bg.png": _picture(100, 50),
                    "flyer.gif": _animation("GIF"),
                    "dmd.webp": _animation("WEBP"),
                    "cab.png": b"\x89PNGnot a picture",
                    "table.mp4": b"not really a video"})
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _get(self, kind: str, query: str = "", table: str = ""):
        where = f"/tables/{table}" if table else ""
        return self.client.get(f"/games/{GAME_ID}{where}/media/{kind}{query}")

    def _version(self, kind: str) -> str:
        return self.client.get(f"/games/{GAME_ID}/media").json()["media"][kind]["version"]

    # --- the size ---------------------------------------------------------

    def test_a_size_is_sent_no_longer_than_that(self) -> None:
        response = self._get("playfield", "?size=256")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/webp")
        self.assertEqual(_opened(response).size, (256, 128))

    def test_a_picture_that_is_see_through_stays_so(self) -> None:
        sized = _opened(self._get("wheel", "?size=256"))

        self.assertEqual(sized.mode, "RGBA")
        self.assertEqual(cast(tuple, sized.getpixel((0, 0)))[3], 0)

    def test_a_small_picture_is_never_made_larger(self) -> None:
        self.assertEqual(_opened(self._get("backglass", "?size=1024")).size, (100, 50))

    def test_an_animated_picture_in_a_cell_is_its_first_frame_still(self) -> None:
        for kind, name in ANIMATED.items():
            with self.subTest(file=name):
                with Image.open(self.folder / "medias" / name) as source:
                    self.assertEqual(getattr(source, "n_frames", 1), 2)

                response = self._get(kind, f"?size={art.CELL}")

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["content-type"], "image/webp")
                still = _opened(response)
                self.assertEqual(getattr(still, "n_frames", 1), 1)
                self.assertEqual(still.size, (256, 128))
                red, _, blue, _ = _rgba(still, (255, 127))
                self.assertGreater(red, 200, "the first frame")
                self.assertLess(blue, 50, "the first frame")
                self.assertEqual(_rgba(still, (0, 0))[3], 0)

    def test_an_animated_picture_looked_at_is_sent_as_it_is(self) -> None:
        for kind, name in ANIMATED.items():
            for query in ("", f"?size={art.PANEL}"):
                with self.subTest(file=name, query=query):
                    response = self._get(kind, query)

                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.content,
                                     (self.folder / "medias" / name).read_bytes())

    def test_a_picture_that_cannot_be_read_is_sent_as_it_is(self) -> None:
        with self.assertLogs(sized_media.logger, "WARNING") as logged:
            response = self._get("cab", "?size=256")
            again = self._get("cab", "?size=256")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"\x89PNGnot a picture")
        self.assertEqual(again.content, b"\x89PNGnot a picture")
        self.assertEqual(len(logged.records), 1, "tried once, not on every draw")

    def test_a_size_nobody_draws_is_refused_naming_the_two(self) -> None:
        response = self._get("playfield", "?size=300")

        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid_request")
        self.assertEqual(error["details"]["known"], [256, 1024])

    def test_a_size_on_a_video_is_refused(self) -> None:
        response = self._get("playfield_video", "?size=256")

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_a_table_s_own_file_comes_in_sizes_too(self) -> None:
        response = self._get("playfield", "?size=256", table="tbl0000002")

        self.assertEqual(_opened(response).size, (256, 128))

    def test_a_changed_file_is_copied_again_and_the_old_copy_goes(self) -> None:
        before = self._version("playfield")
        self._get("playfield", "?size=256")
        replaced = self.folder / "medias" / "table.png"
        replaced.write_bytes(_picture(1000, 1000))
        os.utime(replaced, ns=(1, 1))

        after = self._version("playfield")
        response = self._get("playfield", "?size=256")

        self.assertNotEqual(before, after)
        self.assertEqual(_opened(response).size, (256, 256))
        self.assertEqual([path.name for path in self.copies.rglob("256-*.webp")],
                         [f"256-{after}.webp"])

    # --- the version ------------------------------------------------------

    def test_the_listed_version_is_kept_for_good(self) -> None:
        version = self._version("playfield")

        for query in (f"?v={version}", f"?v={version}&size=256"):
            with self.subTest(query=query):
                response = self._get("playfield", query)
                self.assertEqual(response.headers["cache-control"], FOREVER)

    def test_a_stale_version_or_none_is_asked_about_every_time(self) -> None:
        for query in ("?v=stale", "", "?size=256"):
            with self.subTest(query=query):
                self.assertEqual(self._get("playfield", query).headers["cache-control"],
                                 "no-cache")

    def test_a_listing_row_names_the_version_its_route_serves(self) -> None:
        rows = self.client.get(f"/media?game={GAME_ID}&kind=playfield").json()["media"]
        own = next(row for row in rows if row["table"] == "tbl0000002")

        response = self._get("playfield", f"?v={own['version']}", table="tbl0000002")

        self.assertEqual(response.headers["cache-control"], FOREVER)

    def test_an_absent_kind_has_no_version(self) -> None:
        self.assertIsNone(self._version("topper"))


class CollectionImageSizeTests(_Sized):
    def setUp(self) -> None:
        super().setUp()
        manager = CollectionStore(str(self.root / "collections.ini"))
        for target in ("common.games.collection_ops.get_collections_manager",
                       "common.games.collections_service.get_collections_manager"):
            patcher = patch(target, lambda: manager)
            patcher.start()
            self.addCleanup(patcher.stop)
        catalog: Any = patch("common.games.game_repository.catalog", lambda: {})
        catalog.start()
        self.addCleanup(catalog.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.client.post("/collections", json={"name": "Bally"})
        self.held = self.client.put(
            "/collections/Bally/image",
            files={"file": ("bally.png", io.BytesIO(_picture(800, 400)), "image/png")},
        ).json()

    def test_the_image_comes_in_sizes(self) -> None:
        response = self.client.get("/collections/Bally/image?size=256")

        self.assertEqual(_opened(response).size, (256, 128))

    def test_its_version_is_kept_for_good(self) -> None:
        version = self.held["image_version"]

        response = self.client.get(f"/collections/Bally/image?v={version}")

        self.assertTrue(version)
        self.assertEqual(response.headers["cache-control"], FOREVER)

    def test_no_image_has_no_version(self) -> None:
        self.client.delete("/collections/Bally/image")

        self.assertIsNone(self.client.get("/collections/Bally").json()["image_version"])


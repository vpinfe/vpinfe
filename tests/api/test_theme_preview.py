"""A theme's preview over HTTP: fetched once per release, kept, and sized."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import cast
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient
from PIL import Image

import httpapi
from common.i18n import t
from common.media import sized_media
from common.online import theme_ops, theme_previews
from common.online.themes import ThemeRegistry
from common.security import http_client
from tests.support.library import TempTree

URL = "https://raw.example.net/someone/theme/main/preview.png"


def _png(edge: int = 1600, color: str = "navy") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (edge, edge), color).save(out, format="PNG")
    return out.getvalue()


class _Registry(ThemeRegistry):
    """The few things the preview reads from a registry."""

    def __init__(self, themes_dir: Path) -> None:
        self.themes_dir = str(themes_dir)
        self.version = "1.0.0"
        self.preview_image = "preview.png"
        self.layouts: dict | None = None
        self.folder: str | None = None
        self.local = False

    def _manifest(self) -> dict:
        manifest: dict = {"name": "Any", "version": self.version}
        if self.layouts:
            manifest["layouts"] = self.layouts
        elif self.preview_image:
            manifest["preview_image"] = self.preview_image
        return manifest

    def get_themes(self) -> dict:
        if self.local:
            return {}
        info ={"theme_base_url": "https://raw.example.net/someone/theme", "ref": "main"}
        return {"Any": {"manifest": self._manifest(), "registry_info": info, "source": "x"}}

    def get_installed_folder(self, key: str) -> str | None:
        return self.folder

    def local_themes(self) -> dict:
        return {"Any": self._manifest()} if self.local else {}


class ThemePreviewTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.registry = _Registry(self.root / "themes")
        for patcher in (patch.object(theme_previews, "ROOT", self.root / "kept"),
                        patch.object(sized_media, "ROOT", self.root / "sized"),
                        patch.object(theme_ops, "_known", return_value=self.registry),
                        patch.object(theme_ops, "_preview",
                                     return_value=URL)):
            self.enterContext(patcher)
        self.fetches: list[tuple[str, str]] = []
        self.answer: tuple[bytes, str] | None = (_png(), '"one"')
        self.down = False
        self.enterContext(patch.object(http_client, "get_if_changed", self._fetch))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _fetch(self, url: str, etag: str = "", **_: object) -> tuple[bytes, str] | None:
        self.fetches.append((url, etag))
        if self.down:
            raise requests.ConnectionError("down")
        return self.answer

    def _edge(self, answer) -> int:
        return max(Image.open(io.BytesIO(answer.content)).size)

    def test_a_picture_is_fetched_once_and_served_at_the_size_asked(self) -> None:
        first = self.client.get("/themes/Any/preview", params={"size": 256})
        second = self.client.get("/themes/Any/preview", params={"size": 256})
        large = self.client.get("/themes/Any/preview", params={"size": 1024})

        self.assertEqual([one.status_code for one in (first, second, large)], [200] * 3)
        self.assertEqual((self._edge(first), self._edge(large)), (256, 1024))
        self.assertEqual(self.fetches, [(URL, "")])

    def test_a_new_release_is_fetched_again_and_the_old_copy_goes(self) -> None:
        self.client.get("/themes/Any/preview", params={"size": 256})
        before = theme_ops.preview_version(self.registry, "Any")

        self.registry.version = "1.1.0"
        self.answer = (_png(color="red"), '"two"')
        again = self.client.get("/themes/Any/preview", params={"size": 256})

        kept = [one for one in (self.root / "kept").rglob("source-*")
                if not one.name.endswith(".json")]
        self.assertEqual(len(self.fetches), 2)
        self.assertEqual(len(kept), 1)
        self.assertNotEqual(theme_ops.preview_version(self.registry, "Any"), before)
        pixel = cast(tuple, Image.open(io.BytesIO(again.content)).convert("RGB").getpixel((5, 5)))
        self.assertEqual(pixel[0], 255)

    def test_the_same_release_is_asked_about_again_only_when_the_copy_is_old(self) -> None:
        self.client.get("/themes/Any/preview", params={"size": 256})
        later = time.time() + theme_previews.RECHECK_AFTER + 60
        self.answer = None
        with patch.object(theme_previews.time, "time", return_value=later):
            held = self.client.get("/themes/Any/preview", params={"size": 256})

        self.assertEqual(held.status_code, 200)
        self.assertEqual(self.fetches, [(URL, ""), (URL, '"one"')])

    def test_a_picture_changed_under_the_same_release_replaces_the_copy(self) -> None:
        before = self.client.get("/themes/Any/preview", params={"size": 256})
        version = theme_ops.preview_version(self.registry, "Any")
        later = time.time() + theme_previews.RECHECK_AFTER + 60
        self.answer = (_png(color="red"), '"two"')
        with patch.object(theme_previews.time, "time", return_value=later):
            after = self.client.get("/themes/Any/preview", params={"size": 256})

        self.assertNotEqual(before.content, after.content)
        self.assertNotEqual(theme_ops.preview_version(self.registry, "Any"), version)

    def test_a_host_that_does_not_answer_leaves_the_kept_copy_serving(self) -> None:
        self.client.get("/themes/Any/preview", params={"size": 256})
        self.down = True
        later = time.time() + theme_previews.RECHECK_AFTER + 60
        with patch.object(theme_previews.time, "time", return_value=later):
            held = self.client.get("/themes/Any/preview", params={"size": 256})

        self.assertEqual(held.status_code, 200)

    def test_a_picture_never_fetched_is_not_found_when_the_host_is_down(self) -> None:
        self.down = True
        gone = self.client.get("/themes/Any/preview", params={"size": 256})

        self.assertEqual((gone.status_code, gone.json()["error"]["message"]),
                         (404, t("error.themes.no_preview", key="Any")))

    def test_a_theme_that_names_no_picture_has_none(self) -> None:
        self.registry.preview_image = ""

        self.assertEqual(self.client.get("/themes/Any/preview").status_code, 404)
        self.assertEqual(theme_ops.preview_version(self.registry, "Any"), "")
        self.assertEqual(self.fetches, [])

    def test_a_size_the_api_does_not_make_is_refused(self) -> None:
        self.assertEqual(self.client.get("/themes/Any/preview",
                                         params={"size": 300}).status_code, 400)

    def test_the_version_the_listing_gave_is_kept_for_good(self) -> None:
        self.client.get("/themes/Any/preview", params={"size": 256})
        version = theme_ops.preview_version(self.registry, "Any")

        held = self.client.get("/themes/Any/preview", params={"size": 256, "v": version})
        unversioned = self.client.get("/themes/Any/preview", params={"size": 256})

        self.assertIn("immutable", held.headers["cache-control"])
        self.assertNotIn("immutable", unversioned.headers["cache-control"])

    def test_an_installed_theme_serves_its_own_file_without_asking_the_host(self) -> None:
        folder = self.root / "themes" / "Any"
        folder.mkdir(parents=True)
        (folder / "preview.png").write_bytes(_png(color="green"))
        self.registry.folder = "Any"
        before = theme_ops.preview_version(self.registry, "Any")

        served = self.client.get("/themes/Any/preview", params={"size": 256})
        (folder / "preview.png").write_bytes(_png(900, color="red"))

        self.assertEqual((served.status_code, self.fetches), (200, []))
        self.assertNotEqual(theme_ops.preview_version(self.registry, "Any"), before)

    def test_a_local_theme_that_names_an_address_is_served_from_a_kept_copy(self) -> None:
        self.registry.local = True
        self.registry.preview_image = URL

        served = self.client.get("/themes/Any/preview", params={"size": 256})

        self.assertEqual((served.status_code, self.fetches), (200, [(URL, "")]))

    def test_a_host_that_did_not_answer_is_not_asked_again_at_once(self) -> None:
        self.down = True
        for _ in range(3):
            self.client.get("/themes/Any/preview", params={"size": 256})
        self.assertEqual(len(self.fetches), 1)

        self.down = False
        later = time.time() + theme_previews.RETRY_AFTER + 60
        with patch.object(theme_previews.time, "time", return_value=later):
            again = self.client.get("/themes/Any/preview", params={"size": 256})

        self.assertEqual((again.status_code, len(self.fetches)), (200, 2))

    def test_a_copy_that_cannot_be_written_leaves_the_old_one_serving(self) -> None:
        self.client.get("/themes/Any/preview", params={"size": 256})
        self.registry.version = "1.1.0"
        with patch.object(theme_previews, "staged_for", side_effect=OSError("disk full")):
            held = self.client.get("/themes/Any/preview", params={"size": 256})
        kept = [one for one in (self.root / "kept").rglob("source-*")
                if not one.name.endswith(".json")]

        self.assertEqual((held.status_code, len(kept)), (404, 1))

    def test_an_address_that_is_not_public_https_is_not_fetched(self) -> None:
        for address in ("http://raw.example.net/p.png", "https://localhost/p.png",
                        "https://127.0.0.1/p.png", "https://192.168.1.5/p.png",
                        "https://nas.localhost/p.png"):
            with patch.object(theme_ops, "_preview", return_value=address):
                gone = self.client.get("/themes/Any/preview")
            self.assertEqual(gone.status_code, 404, address)
        self.assertEqual(self.fetches, [])

    def test_an_address_that_moved_replaces_the_old_copy(self) -> None:
        self.client.get("/themes/Any/preview", params={"size": 256})
        moved = "https://raw.example.net/someone/theme/v3/preview.png"
        with patch.object(theme_ops, "_preview", return_value=moved):
            self.client.get("/themes/Any/preview", params={"size": 256})
        kept = [one for one in (self.root / "kept").rglob("source-*")
                if not one.name.endswith(".json")]

        self.assertEqual([one.suffix for one in kept], [".png"])

    def test_a_name_that_leaves_the_theme_folder_is_not_served(self) -> None:
        folder = self.root / "themes" / "Any"
        folder.mkdir(parents=True)
        (self.root / "themes" / "secret.png").write_bytes(_png())
        self.registry.folder = "Any"
        self.registry.preview_image = "../secret.png"

        with patch.object(theme_ops, "_preview", return_value=""):
            gone = self.client.get("/themes/Any/preview")

        self.assertEqual(gone.status_code, 404)

    def test_each_layout_has_its_own_picture_kept_and_versioned_apart(self) -> None:
        other = "https://raw.example.net/someone/theme/main/desktop.png"
        self.registry.layouts = {"cab": "cab.png", "desktop": "desktop.png"}
        pictures = {"cab": URL, "desktop": other}
        answers = {URL: (_png(color="navy"), '"a"'), other: (_png(color="red"), '"b"')}
        self.fetches.clear()
        with patch.object(theme_ops, "_preview",
                          side_effect=lambda key, manifest, info, installed, layout="": (
                              pictures.get(layout, URL))), \
                patch.object(http_client, "get_if_changed",
                             side_effect=lambda url, etag="", **_: answers[url]):
            cab = self.client.get("/themes/Any/preview", params={"size": 256, "layout": "cab"})
            desktop = self.client.get("/themes/Any/preview",
                                      params={"size": 256, "layout": "desktop"})
            again = self.client.get("/themes/Any/preview",
                                    params={"size": 256, "layout": "cab"})

        self.assertEqual([one.status_code for one in (cab, desktop, again)], [200] * 3)
        self.assertNotEqual(cab.content, desktop.content)
        self.assertEqual(cab.content, again.content)
        self.assertNotEqual(theme_ops.preview_version(self.registry, "Any", "cab"),
                            theme_ops.preview_version(self.registry, "Any", "desktop"))
        self.assertEqual(len(list((self.root / "kept").glob("*/source-*.png"))), 2)

    def test_the_picture_that_leads_is_kept_once_for_the_theme_and_its_layout(self) -> None:
        self.registry.layouts = {"cab": "cab.png", "desktop": "desktop.png"}

        self.client.get("/themes/Any/preview", params={"size": 256})
        self.client.get("/themes/Any/preview", params={"size": 256, "layout": "cab"})

        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(theme_ops.preview_version(self.registry, "Any", "cab"),
                         theme_ops.preview_version(self.registry, "Any"))

    def test_an_installed_theme_serves_each_layout_from_its_own_file(self) -> None:
        folder = self.root / "themes" / "Any"
        folder.mkdir(parents=True)
        (folder / "cab.png").write_bytes(_png(color="green"))
        (folder / "desktop.png").write_bytes(_png(900, color="red"))
        self.registry.folder = "Any"
        self.registry.layouts = {"cab": "cab.png", "desktop": "desktop.png"}

        cab = self.client.get("/themes/Any/preview", params={"layout": "cab"})
        desktop = self.client.get("/themes/Any/preview", params={"layout": "desktop"})

        self.assertEqual(([cab.status_code, desktop.status_code], self.fetches), ([200, 200], []))
        self.assertNotEqual(cab.content, desktop.content)

    def test_a_layout_with_no_picture_of_its_own_gets_the_main_one(self) -> None:
        served = self.client.get("/themes/Any/preview",
                                 params={"size": 256, "layout": "desktop"})

        self.assertEqual(served.status_code, 200)
        self.assertEqual(theme_ops.preview_version(self.registry, "Any", "desktop"),
                         theme_ops.preview_version(self.registry, "Any"))

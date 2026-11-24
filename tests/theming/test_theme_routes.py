"""What the HTTP app serves to a frontend window.

The routes are served for real where the behavior is HTTP's own (what a mount answers,
the words endpoint) and through a test client where the caller has to be someone else.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from fastapi import FastAPI
from PIL import Image
from starlette.testclient import TestClient

from common import i18n
from common.games.export_bundle import bundle_paths
from common.games.library_folders import Location
from common.media import sized_media
from frontend import theme_routes
from httpapi import scopes
from tests.support.library import fake_game, game_info, write_game
from tests.support.theme_server import ThemeServer


def _get(port: int, path: str, headers: dict | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read()


class PinballPrimerTests(unittest.TestCase):
    def test_allows_only_pinball_primer_urls(self) -> None:
        allowed = theme_routes._is_allowed_pinball_primer_url

        self.assertTrue(allowed("https://pinballprimer.github.io/paddock_GR0W9.html"))
        self.assertFalse(allowed("http://pinballprimer.github.io/paddock_GR0W9.html"))
        self.assertFalse(allowed("https://example.com/tutorial.html"))

    def test_the_error_page_shows_markup_in_the_message_and_url_as_text(self) -> None:
        page = theme_routes._pinball_primer_error_html(
            "<script>alert(1)</script>", 'https://x/"><img src=x onerror=alert(2)>')

        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_a_base_address_cannot_close_its_own_tag(self) -> None:
        updated = theme_routes._inject_base_tag(
            "<html><head></head></html>",
            'https://pinballprimer.github.io/x?"><script>alert(1)</script>')

        self.assertNotIn("<script>", updated)

    def test_injects_base_tag_into_head(self) -> None:
        html = "<html><head><title>Test</title></head><body>Hello</body></html>"

        updated = theme_routes._inject_base_tag(
            html, "https://pinballprimer.github.io/paddock_GR0W9.html")

        self.assertIn('<base href="https://pinballprimer.github.io/paddock_GR0W9.html">',
                      updated)
        self.assertIn("<title>Test</title>", updated)

    def test_injects_base_tag_when_head_missing(self) -> None:
        updated = theme_routes._inject_base_tag(
            "<html><body>Hello</body></html>",
            "https://pinballprimer.github.io/paddock_GR0W9.html")

        self.assertTrue(updated.startswith(
            '<head><base href="https://pinballprimer.github.io/paddock_GR0W9.html"></head>'))

    def test_a_url_off_the_allow_list_is_a_403_and_never_fetched(self) -> None:
        with mock.patch.object(theme_routes, "get_text") as fetched:
            server = ThemeServer().start()
            self.addCleanup(server.stop)
            status, _ = _get(server.port, "/proxy/pinballprimer?url=https://example.com/x")

        self.assertEqual(status, 403)
        fetched.assert_not_called()


class MountTests(unittest.TestCase):
    """A mount serves its folder and nothing else. A path that matches no mount is a 404,
    never a file somewhere else on the install."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "themes"
        (self.root / "Example").mkdir(parents=True)
        (self.root / "Example" / "index.html").write_text("<html></html>", encoding="utf-8")
        server = ThemeServer({"/themes/": str(self.root)}).start()
        self.addCleanup(server.stop)
        self.port = server.port

    def test_a_mounted_file_is_served_and_revalidated(self) -> None:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/themes/Example/index.html", timeout=5) as page:
            status, cache = page.status, page.headers["Cache-Control"]

        self.assertEqual((status, cache), (200, "no-cache"))

    def test_an_unmounted_path_is_not_looked_for_in_the_working_directory(self) -> None:
        for path in ("/main.py", "/.git/config", "/README.md", "/"):
            self.assertEqual(_get(self.port, path)[0], 404, path)

    def test_a_miss_inside_a_mount_is_a_404(self) -> None:
        self.assertEqual(_get(self.port, "/themes/Example/nope.html")[0], 404)

    def test_climbing_out_of_a_mount_is_refused(self) -> None:
        for path in ("/themes/../../../etc/passwd", "/themes/Example/..%2f..%2f..%2fetc/hosts"):
            self.assertIn(_get(self.port, path)[0], (400, 404), path)


class ThemeWordsTests(unittest.TestCase):
    """`/core/i18n.json?theme=` adds a theme's `locales/` words under `theme.`."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.themes = tmp.name
        mine = Path(tmp.name, "mine", "locales")
        mine.mkdir(parents=True)
        mine.joinpath("en.json").write_text(
            '{"authors": "Authors",'
            ' "tables": {"one": "{count} table", "other": "{count} tables"},'
            ' "frontend.theme.launching": "Mine"}')
        mine.joinpath("de.json").write_text('{"authors": "Autoren"}')
        Path(tmp.name, "bare").mkdir()
        broken = Path(tmp.name, "broken", "locales")
        broken.mkdir(parents=True)
        broken.joinpath("en.json").write_text("{not json")
        server = ThemeServer({"/themes/": tmp.name}).start()
        self.addCleanup(server.stop)
        self.port = server.port
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("en")

    def words(self, query: str = "") -> dict:
        url = f"http://127.0.0.1:{self.port}/core/i18n.json{query}"
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.load(response)

    def test_a_theme_with_locales_adds_its_words_under_theme(self) -> None:
        words = self.words("?theme=mine")
        self.assertEqual(words["theme.authors"], "Authors")
        self.assertEqual(words["theme.tables"]["one"], "{count} table")

    def test_a_theme_cannot_replace_a_core_word(self) -> None:
        core = self.words()["frontend.theme.launching"]
        words = self.words("?theme=mine")
        self.assertEqual(words["frontend.theme.launching"], core)
        self.assertEqual(words["theme.frontend.theme.launching"], "Mine")

    def test_the_language_in_use_wins_and_english_fills_the_rest(self) -> None:
        i18n.set_language("de")
        words = self.words("?theme=mine")
        self.assertEqual(words["theme.authors"], "Autoren")
        self.assertEqual(words["theme.tables"]["other"], "{count} tables")

    def test_a_theme_without_locales_changes_nothing(self) -> None:
        self.assertEqual(self.words("?theme=bare"), self.words())

    def test_a_malformed_file_or_an_unknown_theme_changes_nothing(self) -> None:
        self.assertEqual(self.words("?theme=broken"), self.words())
        self.assertEqual(self.words("?theme=nobody"), self.words())

    def test_an_entry_t_could_not_use_is_dropped_so_the_english_shows(self) -> None:
        for shape in ('[]', '"x"', 'null', '{"a": ""}', '{"a": {"one": 5, "other": "x"}}',
                      '{"a": {"one": "x"}}', '{"a": {"one": "", "other": "x"}}',
                      '{"a": null, "b": ["x"]}'):
            Path(self.themes, "broken", "locales", "en.json").write_text(shape, encoding="utf-8")
            self.assertEqual(self.words("?theme=broken"), self.words(), shape)

    def test_a_folder_name_with_a_space_is_found(self) -> None:
        folder = Path(self.themes, "My Theme", "locales")
        folder.mkdir(parents=True)
        folder.joinpath("en.json").write_text('{"authors": "Authors"}')
        self.assertEqual(self.words("?theme=My%20Theme")["theme.authors"], "Authors")

    def test_a_name_that_leaves_the_themes_folder_is_refused(self) -> None:
        for name in ("..", "../mine", "mine/..", "%2e%2e"):
            self.assertEqual(self.words(f"?theme={name}"), self.words(), name)


class WindowPageTests(unittest.TestCase):
    def test_a_window_the_frontend_has_is_bootstrapped(self) -> None:
        server = ThemeServer().start()
        self.addCleanup(server.stop)

        status, body = _get(server.port, "/app/playfield")

        self.assertEqual(status, 200)
        self.assertIn(b"/core/common/vpinfe-core.js", body)

    def test_a_name_that_could_not_be_a_window_is_a_404(self) -> None:
        server = ThemeServer().start()
        self.addCleanup(server.stop)

        self.assertEqual(_get(server.port, "/app/not%20a%20window")[0], 404)


class OtherDeviceTests(unittest.TestCase):
    """A browser on another device reads the library only with the scope for it, and a
    game's whole folder only with the one for that."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        game_dir = write_game(root, "Only Here", info=game_info("Only Here"),
                              medias={"wheel.png": b"wheel"})
        (game_dir / "extra.bin").write_bytes(b"outside the bundle")
        self.in_bundle = next(p for p, _ in bundle_paths(game_dir)
                              if p.name.lower().endswith(".info"))
        game = fake_game(game_dir, "Only Here")
        game.location_id = "l1"
        patches = [
            mock.patch("common.games.game_repository.all_games", return_value=[game]),
            mock.patch("common.games.game_repository.by_folder_name", return_value=game),
            mock.patch("common.games.library_folders.configured",
                       return_value=[Location("l1", str(root))]),
            mock.patch.object(theme_routes.auth, "caller_is_local", return_value=False),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.folder = game_dir.name
        app = FastAPI()
        theme_routes.register(app, {})
        self.client = TestClient(app)

    def _as(self, *granted: str) -> None:
        identity = SimpleNamespace(can=lambda scope: scope in granted)
        policy: Any = SimpleNamespace(identify=lambda request: identity)
        patcher = mock.patch.object(theme_routes.auth, "current_policy", return_value=policy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_stranger_reads_nothing_of_the_library(self) -> None:
        self._as()

        self.assertEqual(self.client.get(f"/tables/{self.folder}/medias/wheel.png").status_code,
                         403)

    def test_a_reader_gets_the_default_bundle_and_not_the_rest_of_the_folder(self) -> None:
        self._as(scopes.GAMES_READ)

        bundled = self.client.get(f"/tables/{self.folder}/{self.in_bundle.name}")
        extra = self.client.get(f"/tables/{self.folder}/extra.bin")

        self.assertEqual((bundled.status_code, extra.status_code), (200, 403))

    def test_the_whole_folder_needs_the_export_scope(self) -> None:
        self._as(scopes.GAMES_READ, scopes.GAMES_EXPORT_FULL)

        self.assertEqual(self.client.get(f"/tables/{self.folder}/extra.bin").status_code, 200)

    def test_media_needs_the_read_scope(self) -> None:
        self._as()

        self.assertEqual(self.client.get("/media/anything/wheel").status_code, 403)


class SizedMediaTests(unittest.TestCase):
    """The route a theme's pictures come from answers the small copy a wheel draws."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.wheel = root / "wheel.png"
        Image.new("RGB", (600, 600), (200, 40, 40)).save(self.wheel)
        patches: list[Any] = [
            mock.patch("common.games.media_lookup.media_path", return_value=self.wheel),
            mock.patch("common.games.game_repository.all_games", return_value=[]),
            mock.patch.object(sized_media, "ROOT", root / "sized"),
            mock.patch.object(theme_routes.auth, "caller_is_local", return_value=True),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        theme_routes.register(app, {})
        self.client = TestClient(app)

    def test_a_picture_without_a_size_is_the_file_itself(self) -> None:
        self.assertEqual(self.client.get("/media/t1/wheel").content, self.wheel.read_bytes())

    def test_a_size_sends_a_copy_no_longer_than_that(self) -> None:
        response = self.client.get("/media/t1/wheel?size=256")

        self.assertEqual(Image.open(io.BytesIO(response.content)).size, (256, 256))

    def test_a_size_nobody_draws_is_refused(self) -> None:
        self.assertEqual(self.client.get("/media/t1/wheel?size=300").status_code, 400)


if __name__ == "__main__":
    unittest.main()

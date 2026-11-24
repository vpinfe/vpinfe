"""The /tables/<folder>/<file> route the contract 1 media paths become."""

from __future__ import annotations

import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from common.games.library_folders import Location
from tests.support.library import fake_game, write_game
from tests.support.theme_server import ThemeServer


class TablesRouteTests(unittest.TestCase):
    _tmp: TemporaryDirectory[str]
    _patches: list[Any]
    server: ThemeServer
    port: int

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        root = Path(cls._tmp.name)
        first, second = root / "first", root / "second"
        games = []
        for location_id, folder, name, wheel in (
                ("l1", first, "Shared Name", b"first copy"),
                ("l2", second, "Shared Name", b"second copy"),
                ("l2", second, "Only Here", b"only here")):
            game_dir = write_game(folder, name, medias={"wheel.png": wheel})
            game = fake_game(game_dir, name)
            game.location_id = location_id
            games.append(game)
        (root / "secret.txt").write_bytes(b"not a game's")

        cls._patches = [
            mock.patch("common.games.game_repository.all_games", return_value=games),
            mock.patch("common.games.library_folders.configured", return_value=[
                Location("l1", str(first)), Location("l2", str(second))]),
        ]
        for patcher in cls._patches:
            patcher.start()

        cls.server = ThemeServer().start()
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        for patcher in cls._patches:
            patcher.stop()
        cls._tmp.cleanup()

    def _get(self, path: str, headers: dict | None = None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def test_a_game_in_the_second_library_folder_is_served(self) -> None:
        self.assertEqual(self._get("/tables/Only%20Here/medias/wheel.png"),
                         (200, b"only here"))

    def test_the_higher_library_folder_answers_for_a_shared_name(self) -> None:
        self.assertEqual(self._get("/tables/Shared%20Name/medias/wheel.png"),
                         (200, b"first copy"))

    def test_a_path_out_of_the_game_folder_is_refused(self) -> None:
        status, _ = self._get("/tables/Only%20Here/..%2F..%2Fsecret.txt")
        self.assertEqual(status, 404)

    def test_a_folder_no_game_has_is_a_404(self) -> None:
        status, _ = self._get("/tables/Nobody/medias/wheel.png")
        self.assertEqual(status, 404)

    def test_a_range_request_is_answered_partially(self) -> None:
        """Contract 1 video comes through here too, and a video element seeks by range."""
        status, body = self._get("/tables/Only%20Here/medias/wheel.png",
                                 {"Range": "bytes=0-3"})
        self.assertEqual((status, body), (206, b"only"))


if __name__ == "__main__":
    unittest.main()

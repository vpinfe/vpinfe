"""The /media/<game_id>/<kind> route the contract-2 payload addresses.

Contract 2 names the kinds a game has and leaves the bytes to be fetched, so this route
is what makes that payload usable. The lookup is tested directly; the handler is tested
through a real request, because the parts that matter - conditional GET, Range, the 404 -
only exist at the HTTP layer.
"""

from __future__ import annotations

import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from common.games import media_lookup
from common.games.game import Game
from common.games.game_parser import GameParser
from tests.support.library import TempTree, write_game
from tests.support.theme_server import ThemeServer


def _library(root: Path) -> None:
    write_game(root, "Example Game (Bally 1990)", info={
        "Info": {"Title": "Example Game", "Manufacturer": "Bally", "Year": "1990"},
        "vpinfe": {"game_id": "tbl0000001", "schema": 2,
                   "default_table": "tbl0000001"},
        "tables": {"tbl0000001": {"id": "tbl0000001",
                                  "filename": "Example Game (Bally 1990).vpx"}},
    }, medias={"wheel.png": b"\x89PNG wheel bytes",
               "bg.png": b"\x89PNG backglass bytes"})


class LookupTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        _library(self.root)
        self.games = GameParser(str(self.root)).get_all_games()

    def test_it_finds_the_file_behind_a_kind(self) -> None:
        path = media_lookup.media_path(self.games, "tbl0000001", "wheel")

        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(path.name, "wheel.png")

    def test_an_unknown_game_or_kind_is_none_rather_than_an_error(self) -> None:
        self.assertIsNone(media_lookup.media_path(self.games, "nosuchtable", "wheel"))
        self.assertIsNone(media_lookup.media_path(self.games, "tbl0000001", "nosuchkind"))

    def test_a_kind_with_no_file_is_none(self) -> None:
        self.assertIsNone(media_lookup.media_path(self.games, "tbl0000001", "topper"))

    def test_resolved_kinds_lists_only_what_exists(self) -> None:
        kinds = media_lookup.resolved_kinds(self.games[0])

        self.assertIn("wheel", kinds)
        self.assertIn("backglass", kinds)
        self.assertNotIn("topper", kinds)


class PerTableResolutionTests(TempTree):
    """Tier 1 keys off the table, and the scan runs it for every .vpx in the folder. The
    URL was already addressed by table, so closing this moved nothing a theme built."""

    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, "Two Tables (Bally 1990)", vpx=False, info={
            "Info": {"Title": "Two Tables"},
            "vpinfe": {"game_id": "two0000001", "schema": 2, "default_table": "t1"},
            "tables": {"t1": {"id": "t1", "filename": "Default.vpx"},
                       "t2": {"id": "t2", "filename": "Other.vpx"}},
        }, files={"Default.vpx": b"vpx", "Other.vpx": b"vpx"},
            # A tier-1 wheel named for the table that is NOT the default.
            medias={"(Wheel) Other.png": b"\x89PNG other wheel"})
        self.games = GameParser(str(self.root)).get_all_games()

    def test_the_table_the_file_is_named_for_gets_it(self) -> None:
        found = media_lookup.media_path(self.games, "t2", "wheel")

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.name, "(Wheel) Other.png")

    def test_the_other_table_does_not_borrow_it(self) -> None:
        """Nothing resolves a wheel for Default at any tier, and a tier-1 file named for
        its sibling is not a fallback - it is that sibling's art."""
        self.assertIsNone(media_lookup.media_path(self.games, "t1", "wheel"))


class UnparsedGameTests(TempTree):
    """A folder no metadata build has touched has no table ids, but it still has art."""

    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, "Untouched (Bally 1985)", info={
            "Info": {"Title": "Untouched"},
            "vpinfe": {"game_id": "untouched1", "schema": 2},
        }, medias={"wheel.png": b"\x89PNG"})
        self.games = GameParser(str(self.root)).get_all_games()

    def test_media_is_reachable_by_the_game_id(self) -> None:
        path = media_lookup.media_path(self.games, "untouched1", "wheel")

        self.assertIsNotNone(path, "no table id yet, so the game's id addresses it")
        assert path is not None
        self.assertEqual(path.name, "wheel.png")


class RouteTests(unittest.TestCase):
    """Served for real, because conditional GET and Range are HTTP behavior."""

    _tmp: TemporaryDirectory[str]
    root: Path
    games: list[Game]
    _patch: Any
    server: ThemeServer
    port: int

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        _library(cls.root)

        # The route asks the shared repository for the library. Patched rather than
        # injected: all_games rebuilds its parser when the configured games
        # root does not match, which it would not here.
        cls.games = GameParser(str(cls.root)).get_all_games()
        cls._patch = mock.patch("common.games.game_repository.all_games",
                                return_value=cls.games)
        cls._patch.start()

        cls.server = ThemeServer().start()
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        cls._patch.stop()
        cls._tmp.cleanup()

    def _get(self, path, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            with exc:                      # closing it keeps the warnings quiet
                return exc.code, exc.headers, exc.read()

    def test_it_serves_the_file(self) -> None:
        status, headers, body = self._get("/media/tbl0000001/wheel")

        self.assertEqual(status, 200)
        self.assertEqual(body, b"\x89PNG wheel bytes")
        self.assertEqual(headers["Content-Type"], "image/png")

    def test_it_carries_an_etag_and_honors_it(self) -> None:
        _, headers, _ = self._get("/media/tbl0000001/wheel")
        etag = headers["ETag"]

        status, _, body = self._get("/media/tbl0000001/wheel",
                                    {"If-None-Match": etag})

        self.assertEqual(status, 304, "an unchanged file should not be sent twice")
        self.assertEqual(body, b"")

    def test_it_asks_to_be_revalidated(self) -> None:
        """Replacing art in the Manager has to show up without a hard refresh."""
        _, headers, _ = self._get("/media/tbl0000001/wheel")

        self.assertEqual(headers["Cache-Control"], "no-cache")

    def test_a_range_request_is_answered_partially(self) -> None:
        status, headers, body = self._get("/media/tbl0000001/wheel",
                                          {"Range": "bytes=0-3"})

        self.assertEqual(status, 206)
        self.assertEqual(body, b"\x89PNG")
        self.assertEqual(headers["Content-Range"], "bytes 0-3/16")

    def test_an_unknown_table_is_a_404(self) -> None:
        status, _, _ = self._get("/media/nosuchtable/wheel")

        self.assertEqual(status, 404)

    def test_a_kind_this_game_lacks_is_a_404(self) -> None:
        status, _, _ = self._get("/media/tbl0000001/topper")

        self.assertEqual(status, 404)

    def test_an_unrelated_media_path_still_falls_through(self) -> None:
        """The route must not swallow /media/ paths that are not its shape."""
        status, _, _ = self._get("/media/")

        self.assertEqual(status, 404, "a 404 from the static mounts, not a crash")


if __name__ == "__main__":
    unittest.main()

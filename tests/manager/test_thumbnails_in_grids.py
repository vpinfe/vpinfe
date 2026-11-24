"""The picture beside a name in the Games, Tables, Media, Assets and Collections grids.

A real library on disk, read over the API the way the Manager reads it, and each grid
drawn in-process from what that read gave.
"""

from __future__ import annotations

import asyncio
import io
import os
import unittest
from collections.abc import Callable
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from nicegui import core, ui
from PIL import Image
from starlette.testclient import TestClient

import httpapi
from common import icons
from common.config.paths import get_ini_config
from common.games import collection_ops, collections_service, game_repository
from common.games.collection_store import CollectionStore
from common.games.library_folders import KIND_ROOT, Location
from manager import page
from manager.frontend import themes
from manager.kit import grid
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.library import assets, collections, games, media
from tests.support.library import TempTree, game_info, write_game

TWO = "Two Tables (Maker 2001)"
PLAIN = "Plain Wheel (Maker 2002)"
BARE = "No Art (Maker 2003)"
TWO_ID, PLAIN_ID, BARE_ID = "gameTwo001", "gamePlain1", "gameBare01"
PICTURED, UNPICTURED = "Pictured", "Unpictured"
RED, BLUE, GREEN, WHITE = (200, 0, 0), (0, 0, 200), (0, 160, 0), (255, 255, 255)
GRIDS = ("games", "tables", "media", "assets", "collections")
# A wheel's frame, square and 40px at the smallest height, and the 12px gap after it.
ROOM_PX = 52


def _png(color: tuple[int, int, int]) -> bytes:
    held = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(held, "PNG")
    return held.getvalue()


def _tables(**ids: str) -> dict:
    return {table_id: {"id": table_id, "filename": filename}
            for table_id, filename in ids.items()}


def _answered(*_args: Any, **_kwargs: Any) -> asyncio.Future:
    """What the grid says back, with no browser to say it: nothing hidden, no filter."""
    said = asyncio.get_running_loop().create_future()
    said.set_result([])
    return said


class _OverTheApi(ApiClient):
    """The Manager's own client, answered by the API in-process. Keeps each path it
    reads, and apart from those, each one read on an event loop."""

    def __init__(self, api: TestClient) -> None:
        super().__init__("http://testserver")
        self._api = api
        self.read: list[str] = []
        self.on_loop: list[str] = []

    def _get(self, path: str, timeout: int = 15) -> dict:
        self.read.append(path)
        try:
            asyncio.get_running_loop()
            self.on_loop.append(path)
        except RuntimeError:
            pass
        answer = self._api.get(path or "/")
        answer.raise_for_status()
        return answer.json()


class _Drawn(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, TWO, vpx=False,
                   info=game_info("Two", game_id=TWO_ID,
                                  tables=_tables(tblDesktop=f"{TWO}.vpx",
                                                 tblVRbuild=f"{TWO} - VR.vpx"),
                                  vpinfe={"default_table": "tblVRbuild"}),
                   files={f"{TWO}.vpx": b"vpx", f"{TWO} - VR.vpx": b"vpx"},
                   medias={"wheel.png": _png(RED), f"(Wheel) {TWO} - VR.png": _png(BLUE)})
        write_game(self.root, PLAIN,
                   info=game_info("Plain", game_id=PLAIN_ID,
                                  tables=_tables(tblPlain01=f"{PLAIN}.vpx")),
                   medias={"wheel.png": _png(GREEN)})
        write_game(self.root, BARE,
                   info=game_info("Bare", game_id=BARE_ID,
                                  tables=_tables(tblBare001=f"{BARE}.vpx")))

        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)
        for patcher in (
                mock.patch.object(game_repository.library_folders, "configured",
                                  return_value=[Location(location_id="test",
                                                         path=str(self.root),
                                                         kind=KIND_ROOT)]),
                mock.patch.object(ui, "run_javascript"),
                mock.patch.object(ui, "notify"),
                mock.patch.object(ui.aggrid, "run_grid_method", side_effect=_answered)):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.http = TestClient(httpapi.create_api_app())
        self.client = _OverTheApi(self.http)
        self._collections()

    def _collections(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        store = CollectionStore(os.path.join(held.name, "collections.ini"))
        for module in (collection_ops, collections_service):
            patcher = mock.patch.object(module, "get_collections_manager", lambda: store)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in (PICTURED, UNPICTURED):
            self.http.post("/collections", json={"name": name}).raise_for_status()
        self.http.put(f"/collections/{PICTURED}/image",
                      files={"file": ("pictured.png", _png(RED), "image/png")}) \
            .raise_for_status()
        self.addCleanup(self.http.delete, f"/collections/{PICTURED}/image")

    def _thumbnail(self, value: str, key: str = "thumbnail") -> None:
        self._write_thumbnail(value, key)
        self.addCleanup(self._write_thumbnail, "", key)

    @staticmethod
    def _write_thumbnail(value: str, key: str = "thumbnail") -> None:
        store = get_ini_config()
        store.set_value("manager", key, value)
        store.save()

    def _hide_media_kinds(self, *kinds: str) -> None:
        def hidden(kinds: list[str]) -> dict:
            return {"settings": {"library": {"hidden_media_kinds": kinds}}}

        self.http.put("/config", json=hidden(list(kinds))).raise_for_status()
        self.addCleanup(self.http.put, "/config", json=hidden([]))

    def _library(self, view: str) -> Library:
        """Read as a page reads before it draws `view`."""
        library = Library(self.client)
        library.load()
        self._read_before_drawing(view, library)
        return library

    @staticmethod
    def _read_before_drawing(view: str, library: Library) -> None:
        reads = page.reads_before_drawing(view, library)
        if reads is not None:
            reads()

    def _grid(self, view: str, library: Library) -> ui.aggrid:
        draws: dict[str, Callable[[], Any]] = {
            "games": lambda: games.build(library.game_rows(), library.kinds_present(),
                                         library, lambda _row: None, {"view": "games"}),
            "tables": lambda: games.build_tables(library.table_rows(), library,
                                                 lambda _row: None, {"view": "tables"}),
            "media": lambda: media.build(library.media_rows(), library,
                                         lambda _row: None, {"view": "media"}),
            "assets": lambda: assets.build(library.asset_rows(), library,
                                           lambda _row: None, {"view": "assets"}),
            "collections": lambda: collections.build(library.collections(), library,
                                                     lambda _row: None,
                                                     {"view": "collections"}),
        }
        holder = ui.card()

        async def inside() -> None:
            was = core.loop
            core.loop = asyncio.get_running_loop()
            try:
                with holder:
                    draws[view]()
            finally:
                core.loop = was

        asyncio.run(inside())
        return next(one for one in holder.descendants() if isinstance(one, ui.aggrid))

    def _drawn(self, view: str) -> ui.aggrid:
        return self._grid(view, self._library(view))

    @staticmethod
    def _name_column(table: ui.aggrid) -> dict[str, Any]:
        return next(one for one in table.options["columnDefs"]
                    if grid.IDENTIFIER_CLASS in str(one.get("cellClass") or ""))

    @staticmethod
    def _rows(table: ui.aggrid, key: str = "id") -> dict[str, dict[str, Any]]:
        return {str(row[key]): row for row in table.options["rowData"]}

    def _color(self, address: str) -> tuple[int, ...]:
        """Which of the four colors the picture `address` serves is. The nearest, since
        the cell-size copy is re-encoded lossily."""
        answer = self.http.get(address.removeprefix("/api/v1"))
        self.assertEqual(answer.status_code, 200, address)
        with Image.open(io.BytesIO(answer.content)) as image:
            seen = image.convert("RGB").getpixel((0, 0))
        assert isinstance(seen, tuple)
        return min((RED, BLUE, GREEN, WHITE),
                   key=lambda named: sum(abs(int(a) - int(b))
                                         for a, b in zip(named, seen, strict=True)))


class ArtBesideTheName(_Drawn):
    def test_every_grid_draws_56px_rows(self) -> None:
        for view in GRIDS:
            with self.subTest(grid=view):
                table = self._drawn(view)
                self.assertEqual(table.options["rowHeight"], 56)
                self.assertIn("manager-grid-two-line", table.classes)

    def test_the_name_column_grows_by_the_frame_and_its_gap(self) -> None:
        on = {view: self._name_column(self._drawn(view))["width"] for view in GRIDS}
        self._thumbnail("none")
        off = {view: self._name_column(self._drawn(view))["width"] for view in GRIDS}

        self.assertEqual({view: on[view] - off[view] for view in GRIDS},
                         dict.fromkeys(GRIDS, ROOM_PX))

    def test_each_grid_gives_that_room_back_where_it_has_none_down_to_the_name_alone(
            self) -> None:
        on = {view: self._drawn(view).options["context"].get("fit") for view in GRIDS}
        self._thumbnail("none")
        drawn = {view: self._drawn(view) for view in GRIDS}

        self.assertEqual(on, {view: {"col": self._name_column(table)["field"],
                                     "least": self._name_column(table)["width"]}
                              for view, table in drawn.items()})
        self.assertEqual({view: table.options["context"].get("fit")
                          for view, table in drawn.items()}, dict.fromkeys(GRIDS))

    def test_a_width_somebody_set_stays_theirs(self) -> None:
        columns = self._drawn("games").options["columnDefs"]

        state = grid.applied_state(columns, grid.Layout("games", widths={"name": 333}))

        self.assertEqual(next(one["width"] for one in state if one["colId"] == "name"), 333)

    def test_a_game_shows_its_default_table_s_art(self) -> None:
        rows = self._rows(self._drawn("games"))

        self.assertEqual(self._color(rows[TWO_ID]["art"]), BLUE)
        self.assertEqual(self._color(rows[PLAIN_ID]["art"]), GREEN)

    def test_a_table_shows_its_own(self) -> None:
        rows = self._rows(self._drawn("tables"))

        self.assertEqual(self._color(rows["tblDesktop"]["art"]), RED)
        self.assertEqual(self._color(rows["tblVRbuild"]["art"]), BLUE)

    def test_a_media_or_asset_row_shows_its_game_s(self) -> None:
        shown = {game_id: row["art"] for game_id, row in self._rows(self._drawn("games")).items()}
        for view in ("media", "assets"):
            with self.subTest(grid=view):
                rows = self._drawn(view).options["rowData"]
                self.assertTrue(rows)
                self.assertEqual({(row["game_id"], row["art"]) for row in rows},
                                 {(row["game_id"], shown[row["game_id"]]) for row in rows})

    def test_a_row_with_no_art_asks_for_none_and_its_grid_draws_the_glyph(self) -> None:
        glyphs = {"games": icons.GAMES, "tables": icons.TABLES,
                  "media": icons.GAMES, "assets": icons.GAMES}
        for view, glyph in glyphs.items():
            with self.subTest(grid=view):
                table = self._drawn(view)
                bare = [row for row in table.options["rowData"]
                        if row.get("game_id", row["id"]) == BARE_ID]
                self.assertTrue(bare)
                self.assertEqual({row["art"] for row in bare}, {""})
                self.assertIn(glyph, self._name_column(table)[":cellRenderer"])

    def test_none_draws_no_frame(self) -> None:
        self._thumbnail("none")
        for view in GRIDS:
            with self.subTest(grid=view):
                rows = self._drawn(view).options["rowData"]
                self.assertEqual([row for row in rows if "art" in row], [])
        self.assertEqual([path for path in self.client.read if "art=" in path], [])

    def test_a_kind_media_kinds_switches_off_draws_no_frame(self) -> None:
        self._hide_media_kinds("wheel")

        for view in ("games", "collections"):
            with self.subTest(grid=view):
                rows = self._drawn(view).options["rowData"]
                self.assertEqual([row for row in rows if "art" in row], [])


class ACollectionCarriesItsOwnPicture(_Drawn):
    def test_whatever_kind_is_chosen(self) -> None:
        self._thumbnail("backglass")

        rows = self._rows(self._drawn("collections"))

        self.assertEqual(self._color(rows[PICTURED]["art"]), RED)

    def test_or_its_bookmark_where_it_has_none(self) -> None:
        table = self._drawn("collections")

        self.assertEqual(self._rows(table)[UNPICTURED]["art"], "")
        self.assertIn("collections_bookmark", self._name_column(table)[":cellRenderer"])

    def test_its_rows_go_back_to_42px_with_art_off(self) -> None:
        self._thumbnail("none")

        table = self._drawn("collections")

        self.assertEqual(table.options["rowHeight"], 42)
        self.assertNotIn("manager-grid-two-line", table.classes)


class TheArtworkSettingsDrawEveryGrid(_Drawn):
    """Each grid's frame, rows and name column as Shape, Height and Frame set them."""

    def _room(self, table: ui.aggrid) -> int:
        """How much wider the name column is for the frame and its gap."""
        return self._name_column(table)["width"] - table.options["context"]["fit"]["least"]

    def test_automatic_draws_a_wheel_square_and_the_other_kinds_wide(self) -> None:
        square = {view: self._room(self._drawn(view)) for view in GRIDS}
        self._thumbnail("backglass")
        wide = {view: self._room(self._drawn(view)) for view in GRIDS}

        self.assertEqual((square, wide), (dict.fromkeys(GRIDS, 52), dict.fromkeys(GRIDS, 92)))

    def test_square_or_wide_holds_whatever_the_kind(self) -> None:
        self._thumbnail("wide", "thumbnail_shape")
        wide_wheel = self._room(self._drawn("games"))
        self._thumbnail("square", "thumbnail_shape")
        self._thumbnail("backglass")
        square_backglass = self._room(self._drawn("games"))

        self.assertEqual((wide_wheel, square_backglass), (92, 52))

    def test_each_height_sets_the_rows_their_class_and_the_room(self) -> None:
        drawn = {}
        for height in ("medium", "large"):
            self._thumbnail(height, "thumbnail_height")
            drawn[height] = {view: self._drawn(view) for view in GRIDS}

        for height, rows, row_class, room in (("medium", 64, "manager-grid-medium-art", 60),
                                              ("large", 88, "manager-grid-pictured", 84)):
            for view, table in drawn[height].items():
                with self.subTest(height=height, grid=view):
                    self.assertEqual((table.options["rowHeight"], self._room(table)),
                                     (rows, room))
                    self.assertIn(row_class, table.classes)

    def test_the_grid_carries_the_look_its_frames_are_sized_from(self) -> None:
        square = self._drawn("games")
        self._thumbnail("large", "thumbnail_height")
        self._thumbnail("false", "thumbnail_frame")
        self._thumbnail("wide", "thumbnail_shape")
        bare = self._drawn("games")

        self.assertEqual((square._style.get("--art-h"), bare._style.get("--art-h")),
                         ("40px", "72px"))
        self.assertIn("manager-art-square", square.classes)
        self.assertNotIn("manager-art-bare", square.classes)
        self.assertIn("manager-art-bare", bare.classes)
        self.assertNotIn("manager-art-square", bare.classes)


class TheArtIsReadBeforeTheDraw(_Drawn):
    def test_the_games_grid_reads_the_listing_off_the_loop(self) -> None:
        self._drawn("games")

        self.assertIn("/tables?media=wheel", self.client.read)
        self.assertEqual(self.client.on_loop, [])

    def test_a_kind_chosen_since_is_read_before_the_next_draw(self) -> None:
        library = self._library("games")
        self._thumbnail("backglass")

        self._read_before_drawing("games", library)

        self.assertIn("/tables?media=backglass", self.client.read)

    def test_a_media_write_reads_that_game_s_art_again_and_no_other(self) -> None:
        library = self._library("games")
        wheel = self.root / PLAIN / "medias" / "wheel.png"
        held = wheel.stat()
        wheel.write_bytes(_png(WHITE))
        os.utime(wheel, ns=(held.st_atime_ns, held.st_mtime_ns + 10**9))
        library.forget_media(PLAIN_ID)
        before = len(self.client.read)

        self._read_before_drawing("games", library)

        self.assertIn(f"/tables?media=wheel&game={PLAIN_ID}", self.client.read[before:])
        self.assertNotIn("/tables?media=wheel", self.client.read[before:])
        rows = self._rows(self._grid("games", library))
        self.assertEqual(self._color(rows[PLAIN_ID]["art"]), WHITE)


class ThemesKeepTheirPreview(unittest.TestCase):
    def test_at_88px_with_the_palette_where_a_theme_has_none(self) -> None:
        renderer = next(one for one in themes.COLUMNS
                        if grid.IDENTIFIER_CLASS in one["cellClass"])[":cellRenderer"]

        self.assertEqual(grid.base_row_px(themes.COLUMNS), 88)
        self.assertIn("palette", renderer)
        self.assertNotIn("image_not_supported", renderer)


if __name__ == "__main__":
    unittest.main()

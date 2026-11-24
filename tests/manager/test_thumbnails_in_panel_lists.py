"""The picture beside a name in the panel's lists: a collection's games, a tag's games
and tables, and the picker that adds a game to a collection.

A real library on disk, read over the API the way the Manager reads it, and each list
drawn in-process by the panel that holds it.
"""

from __future__ import annotations

import asyncio
import html
import io
import os
import re
import unittest
from collections.abc import Awaitable, Callable
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from nicegui import core, ui
from PIL import Image
from starlette.testclient import TestClient

import httpapi
from common import icons
from common.config.paths import get_ini_config
from common.games import collection_ops, collections_service, game_repository, library_ops
from common.games.collection_store import CollectionStore
from common.games.library_folders import KIND_ROOT, Location
from manager.kit import panel
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.workbench import builds
from tests.support.library import TempTree, game_info, write_game

TWO = "Two Tables (Maker 2001)"
PLAIN = "Plain Wheel (Maker 2002)"
BARE = "No Art (Maker 2003)"
TWO_ID, PLAIN_ID, BARE_ID = "gameTwo001", "gamePlain1", "gameBare01"
HELD, LOVED = "Held", "Loved"
RED, BLUE, GREEN, WHITE = (200, 0, 0), (0, 0, 200), (0, 160, 0), (255, 255, 255)
GLYPH = "glyph"
FRAME = "manager-cell-art-box"
SOURCE = re.compile(r'src="([^"]+)"')


def _png(color: tuple[int, int, int]) -> bytes:
    held = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(held, "PNG")
    return held.getvalue()


def _tables(**ids: str) -> dict:
    return {table_id: {"id": table_id, "filename": filename}
            for table_id, filename in ids.items()}


class _OverTheApi(ApiClient):
    """The Manager's own client, reading from the API in-process. Keeps each path read."""

    def __init__(self, api: TestClient) -> None:
        super().__init__("http://testserver")
        self._api = api
        self.read: list[str] = []

    def _get(self, path: str, timeout: int = 15) -> dict:
        self.read.append(path)
        answer = self._api.get(path or "/")
        answer.raise_for_status()
        return answer.json()


class _Panel(TempTree):
    """Three games: two tables with a wheel each, one wheel, and none. `Held` holds the
    first by its desktop table and again by its game, then the other two, in that order;
    `Loved` is on the second and third games and the first game's desktop table."""

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
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        store = CollectionStore(os.path.join(held.name, "collections.json"))
        patcher: Any
        for patcher in (
                mock.patch.object(game_repository.library_folders, "configured",
                                  return_value=[Location(location_id="test",
                                                         path=str(self.root),
                                                         kind=KIND_ROOT)]),
                *(mock.patch.object(module, "get_collections_manager", lambda: store)
                  for module in (collection_ops, collections_service, library_ops)),
                mock.patch.object(ui, "run_javascript"),
                mock.patch.object(ui, "notify")):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.http = TestClient(httpapi.create_api_app())
        self.client = _OverTheApi(self.http)
        self._write("post", "/collections", {"name": HELD})
        for game_id, table in ((TWO_ID, "tblDesktop"), (TWO_ID, ""), (PLAIN_ID, ""),
                               (BARE_ID, "")):
            self._write("put", f"/collections/{HELD}/games/{game_id}", {"table": table})
        self._write("patch", f"/collections/{HELD}", {"order_by": "manual"})
        for game_id in (PLAIN_ID, BARE_ID):
            self._write("put", f"/games/{game_id}/tags", {"tags": [LOVED]})
        self._write("put", f"/games/{TWO_ID}/tables/tblDesktop/tags", {"tags": [LOVED]})

    def _write(self, verb: str, path: str, body: dict) -> None:
        getattr(self.http, verb)(path, json=body).raise_for_status()

    def _thumbnail(self, value: str, key: str = "thumbnail") -> None:
        self._write_thumbnail(value, key)
        self.addCleanup(self._write_thumbnail, "", key)

    @staticmethod
    def _write_thumbnail(value: str, key: str = "thumbnail") -> None:
        store = get_ini_config()
        store.set_value("manager", key, value)
        store.save()

    def _drawn(self, build: Callable[[ui.column, ui.column, Library],
                                     Awaitable[None]]) -> ui.card:
        library = Library(self.client)
        library.load()
        holder = ui.card()

        async def inside() -> None:
            was = core.loop
            core.loop = asyncio.get_running_loop()
            try:
                with holder:
                    container, title = ui.column(), ui.column()
                await build(container, title, library)
            finally:
                core.loop = was

        asyncio.run(inside())
        return holder

    def _collection(self) -> ui.card:
        return self._drawn(lambda container, title, library: builds.build_collection(
            container, title, library, HELD, {"section": "collection_games"}))

    def _tag(self) -> ui.card:
        return self._drawn(lambda container, title, library: builds.build_tag(
            container, title, library, LOVED, {"section": "tag_games"}))

    @staticmethod
    def _rows(holder: ui.card) -> list[ui.element]:
        return [one for one in holder.descendants() if "manager-member-row" in one.classes]

    @staticmethod
    def _frames(row: ui.element) -> list[str]:
        return [str(one.content) for one in row.descendants()
                if isinstance(one, ui.html) and FRAME in str(one.content)]

    def _shown(self, frame: str) -> Any:
        """The color the frame's picture is, or GLYPH where it holds the glyph."""
        found = SOURCE.search(frame)
        if found is None:
            return GLYPH
        answer = self.http.get(html.unescape(found.group(1)).removeprefix("/api/v1"))
        self.assertEqual(answer.status_code, 200, found.group(1))
        with Image.open(io.BytesIO(answer.content)) as image:
            seen = image.convert("RGB").getpixel((0, 0))
        assert isinstance(seen, tuple)
        return min((RED, BLUE, GREEN, WHITE),
                   key=lambda named: sum(abs(int(a) - int(b))
                                         for a, b in zip(named, seen, strict=True)))

    def _each_row(self, holder: ui.card) -> list[Any]:
        shown = []
        for row in self._rows(holder):
            frames = self._frames(row)
            self.assertLessEqual(len(frames), 1)
            shown.append(self._shown(frames[0]) if frames else None)
        return shown

    @staticmethod
    def _picker(holder: ui.card) -> panel.GamePicker:
        return next(one for one in holder.descendants() if isinstance(one, panel.GamePicker))


class ACollectionsGames(_Panel):
    def test_an_entry_shows_the_table_it_names_else_its_game_s(self) -> None:
        self.assertEqual(self._each_row(self._collection()), [RED, BLUE, GREEN, GLYPH])

    def test_the_glyph_is_the_game_s(self) -> None:
        bare = self._frames(self._rows(self._collection())[-1])[0]

        self.assertIn(html.escape(icons.GAMES), bare)
        self.assertIn('aria-hidden="true"', bare)

    def test_the_picture_is_not_a_control(self) -> None:
        pictured = self._frames(self._rows(self._collection())[0])[0]

        self.assertIn('alt=""', pictured)
        self.assertIn('draggable="false"', pictured)
        self.assertNotIn("href", pictured)

    def test_none_draws_no_frame_and_reads_no_art(self) -> None:
        self._thumbnail("none")

        self.assertEqual(self._each_row(self._collection()), [None] * 4)
        self.assertEqual([path for path in self.client.read if "art=" in path], [])


class ATagsGames(_Panel):
    def test_a_game_shows_its_art_and_a_table_its_own(self) -> None:
        self.assertEqual(self._each_row(self._tag()), [GLYPH, GREEN, RED])

    def test_the_frame_goes_where_the_name_does(self) -> None:
        for row in self._rows(self._tag()):
            with self.subTest(row=row.id):
                name = next(one for one in row.descendants() if isinstance(one, ui.link))
                frame = self._frames(row)[0]
                self.assertIn(f'href="{html.escape(str(name.props["href"]))}"', frame)
                self.assertIn('tabindex="-1"', frame)

    def test_a_table_holds_the_table_glyph(self) -> None:
        tables = self._frames(self._rows(self._tag())[-1])[0]

        self.assertIn(html.escape(icons.TABLES), tables)

    def test_none_draws_no_frame_and_reads_no_art(self) -> None:
        self._thumbnail("none")

        self.assertEqual(self._each_row(self._tag()), [None] * 3)
        self.assertEqual([path for path in self.client.read if "art=" in path], [])


class TheGamePicker(_Panel):
    def test_each_option_carries_its_game_s_art(self) -> None:
        picker = self._picker(self._collection())

        art = {str(picker._values[one["value"]]): one["art"]
               for one in picker._props["options"]}

        self.assertEqual({game: self._shown(f'src="{html.escape(address)}"')
                          if address else GLYPH for game, address in art.items()},
                         {TWO_ID: BLUE, PLAIN_ID: GREEN, BARE_ID: GLYPH})

    def test_its_option_draws_the_frame_from_the_option_s_art(self) -> None:
        template = self._picker(self._collection()).slots["option"].template or ""

        self.assertIn(FRAME, template)
        self.assertIn('v-if="props.opt.art"', template)
        self.assertIn(html.escape(icons.GAMES), template)

    def test_none_leaves_the_options_without_art(self) -> None:
        self._thumbnail("none")

        options = self._picker(self._collection())._props["options"]

        self.assertEqual([one for one in options if "art" in one], [])


class ThePanelTakesTheShapeAndFrameAtTheSmallestHeight(_Panel):
    def setUp(self) -> None:
        super().setUp()
        self._thumbnail("large", "thumbnail_height")
        self._thumbnail("false", "thumbnail_frame")

    def test_a_row_of_a_collection_or_a_tag(self) -> None:
        pictured = [next(one for one in row.descendants()
                         if "manager-cell-pictured" in one.classes)
                    for holder in (self._collection(), self._tag())
                    for row in self._rows(holder)]

        self.assertEqual(len(pictured), 7)
        for one in pictured:
            with self.subTest(row=one.id):
                self.assertIn("manager-art-square", one.classes)
                self.assertIn("manager-art-bare", one.classes)
                self.assertEqual(one._style.get("--art-h"), "40px")

    def test_the_picker_s_options(self) -> None:
        props = self._picker(self._collection())._props

        self.assertIn("manager-art-square", props["popup-content-class"])
        self.assertIn("manager-art-bare", props["popup-content-class"])
        self.assertIn("manager-picker-popup", props["popup-content-class"])
        self.assertEqual(props["popup-content-style"], "--art-h: 40px")


if __name__ == "__main__":
    unittest.main()

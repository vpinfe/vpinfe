"""`GET /tables?media=<kind>` says which file each table shows, as the cabinet resolves it.

Read from a real library through the API. Every version the listing gives is checked
against the route it addresses: the version is only worth having if that route serves
the shown file under it, kept for good.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.config.paths import get_ini_config
from common.games import asset_resolver, game_repository, media_service, table_lens
from common.games.library_folders import KIND_ROOT, Location
from httpapi.responses import FOREVER
from tests.support.library import TempTree, game_info, write_game

NAMED = "Named For A Table (Maker 2001)"
IN_A_SET = "In A Set (Maker 2002)"
ONLY_A_LOGO = "Only A Logo (Maker 2003)"
ONLY_AN_FSS = "Only An FSS (Maker 2004)"
NOTHING = "Nothing At All (Maker 2005)"
NO_IDS = "No Ids Yet (Maker 2006)"


def _tables(**ids: str) -> dict:
    return {table_id: {"id": table_id, "filename": filename}
            for table_id, filename in ids.items()}


def _rewrite(path: Path, data: bytes) -> None:
    """New bytes and a later mtime, as a replaced file has."""
    path.write_bytes(data)
    held = path.stat()
    os.utime(path, ns=(held.st_atime_ns, held.st_mtime_ns + 10**9))


class _Library(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, NAMED, vpx=False,
                   info=game_info("Named", game_id="gameNamed1", tables=_tables(
                       tblDesktop=f"{NAMED}.vpx", tblVRbuild=f"{NAMED} - VR.vpx")),
                   files={f"{NAMED}.vpx": b"vpx", f"{NAMED} - VR.vpx": b"vpx"},
                   medias={"wheel.png": b"shared wheel",
                           f"(Wheel) {NAMED} - VR.png": b"the VR build's wheel"})
        write_game(self.root, IN_A_SET,
                   info=game_info("Set", game_id="gameInSet1", tables=_tables(
                       tblInASet1=f"{IN_A_SET}.vpx")),
                   medias={"wheel.png": b"plain wheel",
                           "wheels/tarcisio/wheel.png": b"the set's wheel",
                           "logo.png": b"the game's logo"})
        write_game(self.root, ONLY_A_LOGO,
                   info=game_info("Logo", game_id="gameLogo01", tables=_tables(
                       tblLogoOnl=f"{ONLY_A_LOGO}.vpx")),
                   medias={"logo.png": b"a logo and no wheel"})
        write_game(self.root, ONLY_AN_FSS,
                   info=game_info("FSS", game_id="gameFSS001", tables=_tables(
                       tblFSSOnly=f"{ONLY_AN_FSS}.vpx")),
                   medias={"fss.png": b"the FSS render"})
        write_game(self.root, NOTHING,
                   info=game_info("Nothing", game_id="gameNone01", tables=_tables(
                       tblNothing=f"{NOTHING}.vpx")))
        write_game(self.root, NO_IDS, info=game_info("No ids", game_id="gameNoIds1"),
                   medias={"wheel.png": b"the game's wheel"})

        held = mock.patch.object(
            game_repository.library_folders, "configured",
            return_value=[Location(location_id="test", path=str(self.root),
                                   kind=KIND_ROOT)])
        held.start()
        self.addCleanup(held.stop)
        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _set_wheels(self, name: str) -> None:
        self._write_wheelset(name)
        self.addCleanup(self._write_wheelset, "")

    @staticmethod
    def _write_wheelset(name: str) -> None:
        store = get_ini_config()
        store.set_value("media", "wheelset", name)
        store.save()

    def _rows(self, art: str) -> dict[str, dict]:
        answer = self.client.get("/tables", params={"media": art})
        self.assertEqual(answer.status_code, 200, answer.text)
        return {row["id"]: row for row in answer.json()["tables"]}

    def _shown(self, row: dict) -> bytes:
        """The bytes the row's version addresses, which must be kept for good."""
        answer = self.client.get(
            f"/games/{row['game_id']}/tables/{row['id']}/media/{row['media_kind']}",
            params={"v": row["media_version"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.headers["cache-control"], FOREVER)
        return answer.content


class TheArtEachTableShowsTests(_Library):
    def test_a_file_named_for_a_table_is_shown_for_that_table_alone(self) -> None:
        rows = self._rows("wheel")

        self.assertEqual(self._shown(rows["tblVRbuild"]), b"the VR build's wheel")
        self.assertEqual(self._shown(rows["tblDesktop"]), b"shared wheel")

    def test_the_active_wheel_set_is_shown_over_the_plain_wheel(self) -> None:
        self._set_wheels("tarcisio")

        self.assertEqual(self._shown(self._rows("wheel")["tblInASet1"]), b"the set's wheel")

    def test_the_logo_set_shows_the_logo_where_there_is_a_wheel(self) -> None:
        self._set_wheels("logo")

        self.assertEqual(self._shown(self._rows("wheel")["tblInASet1"]), b"the game's logo")

    def test_a_wheel_falls_to_the_logo(self) -> None:
        self.assertEqual(self._shown(self._rows("wheel")["tblLogoOnl"]),
                         b"a logo and no wheel")

    def test_a_playfield_falls_to_the_fss_render_and_names_it(self) -> None:
        row = self._rows("playfield")["tblFSSOnly"]

        self.assertEqual(row["media_kind"], "playfield_fss")
        self.assertEqual(self._shown(row), b"the FSS render")

    def test_nothing_that_resolves_is_null(self) -> None:
        row = self._rows("backglass")["tblNothing"]

        self.assertIsNone(row["media_version"])
        self.assertIsNone(row["media_kind"])

    def test_the_version_changes_with_the_file(self) -> None:
        before = self._rows("wheel")["tblDesktop"]["media_version"]
        _rewrite(self.root / NAMED / "medias" / "wheel.png", b"a new shared wheel")
        game_repository.refresh_game(self.root / NAMED)

        row = self._rows("wheel")["tblDesktop"]

        self.assertNotEqual(row["media_version"], before)
        self.assertEqual(self._shown(row), b"a new shared wheel")

    def test_a_file_added_to_the_folder_shows_on_the_next_listing(self) -> None:
        """The listing is held between requests; a folder that changed is read again."""
        before = self._rows("")["tblDesktop"]["assets"]["script"]["resolution"]

        (self.root / NAMED / f"{NAMED}.vbs").write_bytes(b"Option Explicit")
        after = self._rows("")["tblDesktop"]["assets"]["script"]["resolution"]

        self.assertEqual((before, after), ("none", "shared"))

    def test_without_art_no_row_carries_a_version(self) -> None:
        answer = self.client.get("/tables")

        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual({row["media_version"] for row in answer.json()["tables"]}, {None})

    def test_art_reads_no_folder_twice(self) -> None:
        real = asset_resolver.folder_listing
        read: list[str] = []

        def counted(game_dir):
            read.append(Path(game_dir).name)
            return real(game_dir)

        with mock.patch.object(asset_resolver, "folder_listing", counted):
            table_lens._rows_held.clear()
            self.client.get("/tables")
            plain = sorted(read)
            read.clear()
            table_lens._rows_held.clear()
            self._rows("wheel")

        self.assertEqual(sorted(read), plain)

    def test_a_kind_a_list_does_not_show_is_refused(self) -> None:
        for art in ("cab", "bg", "nonsense"):
            with self.subTest(art=art):
                answer = self.client.get("/tables", params={"media": art})

                self.assertEqual(answer.status_code, 400, answer.text)
                self.assertEqual(answer.json()["error"]["code"], "invalid_request")


class AGameWhoseTablesHaveNoIdTests(_Library):
    """No row until a refresh gives its tables ids, and the game's own route meanwhile."""

    def _game_shows(self) -> media_service.ShownArt:
        shown = media_service.shown_media(self.root / NO_IDS, "wheel")
        assert shown is not None
        return shown

    def test_it_has_no_row(self) -> None:
        listed = {row["game_id"] for row in self._rows("wheel").values()}

        self.assertNotIn("gameNoIds1", listed)
        self.assertIn("gameNamed1", listed)

    def test_the_game_s_route_serves_what_it_shows_under_its_version(self) -> None:
        shown = self._game_shows()
        answer = self.client.get("/games/gameNoIds1/media/wheel",
                                 params={"v": shown.version})

        self.assertEqual(shown.kind, "wheel")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.headers["cache-control"], FOREVER)
        self.assertEqual(answer.content, b"the game's wheel")

    def test_its_version_changes_with_the_file(self) -> None:
        before = self._game_shows().version
        _rewrite(self.root / NO_IDS / "medias" / "wheel.png", b"a new wheel for the game")

        self.assertNotEqual(self._game_shows().version, before)
